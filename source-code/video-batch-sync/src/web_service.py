#!/usr/bin/env python3
"""
Video Batch Sync Web Service

REST API service for batch copying MP4 files from source S3 bucket to destination S3 bucket.
Supports server-side S3 copy operations with rate limiting and progress tracking.

Author: AI Assistant
Date: 2024
"""

import time
import logging
import threading
import os
import tempfile
import uuid
import shutil
import sys
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify

_here = Path(__file__).resolve()
_shared = _here.parents[2] / "shared" if len(_here.parents) > 2 else None
if _shared is not None and _shared.is_dir():
    sys.path.insert(0, str(_shared))
import boto3
from botocore.exceptions import ClientError
from typing import Dict, List, Optional, Tuple
from boto3.s3.transfer import TransferConfig

from ingest_metadata import build_s3_ingest_metadata
from video_chunker import (
    DEFAULT_CHUNK_DURATION_SEC,
    chunk_s3_metadata,
    estimate_chunk_count,
    iter_video_chunks,
    normalize_chunk_duration,
    probe_duration_sec,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

class BatchSyncJob:
    """Represents a batch sync job for a user"""
    def __init__(self, job_id: str, username: str, config: dict):
        self.job_id = job_id
        self.username = username
        self.config = config
        self.status = 'running'  # running, completed, failed
        self.total_files = 0
        self.completed_files = 0
        self.failed_files = 0
        self.failed_file_list = []
        self.start_time = datetime.utcnow()
        self.end_time = None
        self.current_file = None
        self.source_files_total = 0
        self.source_files_completed = 0

class BatchSyncService:
    def __init__(self):
        self.jobs: Dict[str, BatchSyncJob] = {}  # job_id -> BatchSyncJob
        self.user_jobs: Dict[str, str] = {}  # username -> job_id (one active job per user)
        self._lock = threading.Lock()  # Thread safety for job tracking
        
    def setup_s3_client(self, access_key: str, secret_key: str, s3_endpoint: str, use_ssl: bool = False):
        """Setup S3 client with provided credentials"""
        try:
            client = boto3.client(
                's3',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                endpoint_url=s3_endpoint,
                use_ssl=use_ssl,
                verify=False
            )
            logger.info(f"S3 client configured for endpoint: {s3_endpoint}")
            return client
        except Exception as e:
            logger.error(f"Failed to setup S3 client: {e}")
            return None
    
    def list_mp4_files(self, s3_client, source_bucket: str, prefix: str) -> List[Dict]:
        """List all MP4 files in source bucket with given prefix"""
        try:
            mp4_files = []
            paginator = s3_client.get_paginator('list_objects_v2')
            
            for page in paginator.paginate(Bucket=source_bucket, Prefix=prefix):
                if 'Contents' not in page:
                    continue
                    
                for obj in page['Contents']:
                    key = obj['Key']
                    if key.lower().endswith('.mp4'):
                        mp4_files.append({
                            'key': key,
                            'size': obj.get('Size', 0),
                            'last_modified': obj.get('LastModified')
                        })
            
            logger.info(f"Found {len(mp4_files)} MP4 files in s3://{source_bucket}/{prefix}")
            return mp4_files
            
        except ClientError as e:
            logger.error(f"Error listing S3 objects: {e}")
            raise
    
    def copy_file_with_metadata(
        self,
        source_client,
        dest_client,
        source_bucket: str,
        source_key: str,
        dest_bucket: str,
        dest_key: str,
        metadata: Dict[str, str],
        source_endpoint: str,
        dest_endpoint: str
    ) -> Tuple[bool, Optional[str]]:
        """Copy with metadata. Server-side copy on the same endpoint, otherwise stream. Returns (ok, error)."""
        try:
            same_endpoint = source_endpoint == dest_endpoint
            
            if same_endpoint:
                try:
                    source_metadata = source_client.head_object(Bucket=source_bucket, Key=source_key)
                    
                    copy_source = {
                        'Bucket': source_bucket,
                        'Key': source_key
                    }
                    
                    dest_metadata = {}
                    if 'Metadata' in source_metadata:
                        dest_metadata.update(source_metadata['Metadata'])
                    dest_metadata.update(metadata)
                    
                    dest_client.copy_object(
                        CopySource=copy_source,
                        Bucket=dest_bucket,
                        Key=dest_key,
                        Metadata=dest_metadata,
                        MetadataDirective='REPLACE'
                    )
                    
                    logger.info(f"Server-side copied: s3://{source_bucket}/{source_key} -> s3://{dest_bucket}/{dest_key}")
                    return True, None
                    
                except ClientError as e:
                    logger.warning(f"Server-side copy failed, falling back to download/upload: {e}")
                    same_endpoint = False
            
            logger.info(f"Streaming copy from source: s3://{source_bucket}/{source_key} (endpoint: {source_endpoint})")
            logger.info(f"Uploading to destination: s3://{dest_bucket}/{dest_key} (endpoint: {dest_endpoint})")
            source_response = source_client.get_object(Bucket=source_bucket, Key=source_key)
            
            file_size = source_response.get('ContentLength', 0)
            
            class StreamingBodyWrapper:
                """Wrapper to make S3 streaming body work with upload_fileobj"""
                def __init__(self, body):
                    self.body = body
                
                def read(self, size=-1):
                    if size == -1:
                        return self.body.read()
                    return self.body.read(size)
                
                def __enter__(self):
                    return self
                
                def __exit__(self, *args):
                    pass
            
            streaming_body = StreamingBodyWrapper(source_response['Body'])
            
            transfer_config = TransferConfig(
                multipart_threshold=8 * 1024 * 1024,
                multipart_chunksize=8 * 1024 * 1024,
                max_concurrency=1
            )
            
            logger.info(f"Streaming upload to destination: s3://{dest_bucket}/{dest_key} ({file_size} bytes)")
            
            dest_client.upload_fileobj(
                streaming_body,
                dest_bucket,
                dest_key,
                ExtraArgs={'Metadata': metadata},
                Config=transfer_config
            )
            
            logger.info(f"Streaming copy completed: s3://{source_bucket}/{source_key} -> s3://{dest_bucket}/{dest_key}")
            return True, None
            
        except ClientError as e:
            error_msg = f"S3 error: {str(e)}"
            logger.error(f"Error copying file {source_key}: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"Error copying file {source_key}: {error_msg}")
            return False, error_msg
    
    def generate_dest_key(
        self,
        username: str,
        original_key: str,
        chunk_index: Optional[int] = None,
    ) -> str:
        """Generate destination key: {username}/{timestamp}_{name}[_chunk_NNNN].mp4"""
        original_name = original_key.split('/')[-1]
        if original_name.lower().endswith('.mp4'):
            original_name = original_name[:-4]

        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        if chunk_index is not None:
            return f"{username}/{timestamp}_{original_name}_chunk_{chunk_index:04d}.mp4"
        return f"{username}/{timestamp}_{original_name}.mp4"

    def _download_source_to_temp(
        self,
        source_client,
        source_bucket: str,
        source_key: str,
    ) -> str:
        suffix = os.path.splitext(source_key)[1] or ".mp4"
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="vss-src-")
        os.close(fd)
        logger.info("Downloading s3://%s/%s -> %s", source_bucket, source_key, path)
        source_client.download_file(source_bucket, source_key, path)
        return path

    def _build_chunk_metadata(
        self,
        base_metadata: Dict[str, str],
        *,
        stream_id: str,
        chunk_index: int,
        chunk_start_sec: float,
        chunk_duration_sec: float,
        capture_interval_sec: float,
        original_filename: str,
    ) -> Dict[str, str]:
        meta = dict(base_metadata)
        meta.update(chunk_s3_metadata(
            stream_id=stream_id,
            chunk_index=chunk_index,
            chunk_start_sec=chunk_start_sec,
            chunk_duration_sec=chunk_duration_sec,
            capture_interval_sec=capture_interval_sec,
            ingest_kind="batch_chunk",
            original_filename=original_filename,
        ))
        return meta

    def _should_split_chunks(self, config: dict) -> bool:
        raw = config.get("chunk_duration_sec", DEFAULT_CHUNK_DURATION_SEC)
        try:
            return float(raw) > 0
        except (TypeError, ValueError):
            return True

    def _chunk_duration_sec(self, config: dict) -> float:
        return normalize_chunk_duration(config.get("chunk_duration_sec", DEFAULT_CHUNK_DURATION_SEC))

    def _upload_delay_sec(self, config: dict) -> float:
        """UI batch_size slider: seconds to wait between chunk/source uploads."""
        try:
            delay = float(config.get("batch_size", 1.0))
        except (TypeError, ValueError):
            delay = 1.0
        return max(0.0, delay)

    def _paced_wait(self, job: "BatchSyncJob", delay_sec: float) -> bool:
        """Sleep up to delay_sec unless the job is stopped. Returns False if stopped."""
        if delay_sec <= 0:
            return True
        deadline = time.time() + delay_sec
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return True
            with self._lock:
                current_job = self.jobs.get(job.job_id)
                if not current_job or current_job.status != "running":
                    job.status = "stopped"
                    return False
            time.sleep(min(1.0, remaining))

    def _process_source_file(
        self,
        job: BatchSyncJob,
        source_client,
        dest_client,
        config: dict,
        source_key: str,
        base_metadata: Dict[str, str],
    ) -> None:
        """Upload one source MP4 (split into chunks or copy whole). Updates job counters."""
        split_chunks = self._should_split_chunks(config)
        chunk_duration = self._chunk_duration_sec(config)

        if not split_chunks:
            job.total_files += 1
            dest_key = self.generate_dest_key(job.username, source_key)
            success, error_msg = self.copy_file_with_metadata(
                source_client,
                dest_client,
                config['source_bucket'],
                source_key,
                config['dest_bucket'],
                dest_key,
                base_metadata,
                config['source_s3_endpoint'],
                config['dest_s3_endpoint'],
            )
            if success:
                job.completed_files += 1
            else:
                job.failed_files += 1
                job.failed_file_list.append({
                    'source_key': source_key,
                    'error': error_msg or 'Copy operation failed',
                })
            return

        local_path = None
        chunk_dir = None
        stream_id = str(uuid.uuid4())
        original_filename = source_key.split('/')[-1]

        try:
            local_path = self._download_source_to_temp(
                source_client, config['source_bucket'], source_key
            )
            duration = probe_duration_sec(local_path)
            total_chunks = estimate_chunk_count(duration, chunk_duration)
            job.total_files += total_chunks
            upload_delay = self._upload_delay_sec(config)

            chunk_dir = tempfile.mkdtemp(prefix="vss-chunk-out-")
            chunks_produced = 0
            for chunk in iter_video_chunks(local_path, chunk_duration, work_dir=chunk_dir):
                with self._lock:
                    current_job = self.jobs.get(job.job_id)
                    if not current_job or current_job.status != 'running':
                        job.status = 'stopped'
                        break

                dest_key = self.generate_dest_key(job.username, source_key, chunk.chunk_index)
                chunk_metadata = self._build_chunk_metadata(
                    base_metadata,
                    stream_id=stream_id,
                    chunk_index=chunk.chunk_index,
                    chunk_start_sec=chunk.chunk_start_sec,
                    chunk_duration_sec=chunk.chunk_duration_sec,
                    capture_interval_sec=chunk_duration,
                    original_filename=original_filename,
                )

                try:
                    with open(chunk.path, "rb") as chunk_file:
                        dest_client.upload_fileobj(
                            chunk_file,
                            config['dest_bucket'],
                            dest_key,
                            ExtraArgs={'Metadata': chunk_metadata},
                            Config=TransferConfig(
                                multipart_threshold=8 * 1024 * 1024,
                                multipart_chunksize=8 * 1024 * 1024,
                                max_concurrency=1,
                            ),
                        )
                    job.completed_files += 1
                    chunks_produced += 1
                    if (
                        upload_delay > 0
                        and chunk.chunk_index + 1 < total_chunks
                        and job.status == "running"
                    ):
                        logger.info(
                            "Pacing: waiting %.1fs before next chunk upload (rate limit)",
                            upload_delay,
                        )
                        if not self._paced_wait(job, upload_delay):
                            break
                except Exception as exc:
                    job.failed_files += 1
                    job.failed_file_list.append({
                        'source_key': source_key,
                        'chunk_index': chunk.chunk_index,
                        'error': str(exc),
                    })
                finally:
                    if chunk.owns_path:
                        try:
                            os.remove(chunk.path)
                        except OSError:
                            pass

            if chunks_produced == 0 and job.status == 'running':
                job.failed_files += 1
                job.failed_file_list.append({
                    'source_key': source_key,
                    'error': 'No chunks produced from source video',
                })
        except Exception as exc:
            job.failed_files += 1
            job.failed_file_list.append({'source_key': source_key, 'error': str(exc)})
            logger.error("Chunking failed for %s: %s", source_key, exc, exc_info=True)
        finally:
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass
            if chunk_dir and os.path.isdir(chunk_dir):
                shutil.rmtree(chunk_dir, ignore_errors=True)
    
    def run_batch_sync(self, job: BatchSyncJob):
        """Run batch sync operation in background thread"""
        try:
            config = job.config
            
            # Setup S3 clients
            source_client = self.setup_s3_client(
                config['source_access_key'],
                config['source_secret_key'],
                config['source_s3_endpoint'],
                use_ssl=config.get('source_use_ssl', False)
            )
            
            dest_client = self.setup_s3_client(
                config['dest_access_key'],
                config['dest_secret_key'],
                config['dest_s3_endpoint'],
                use_ssl=config.get('dest_use_ssl', False)
            )
            
            if not source_client or not dest_client:
                job.status = 'failed'
                job.end_time = datetime.utcnow()
                return
            
            # List MP4 files
            mp4_files = self.list_mp4_files(
                source_client,
                config['source_bucket'],
                config['source_prefix']
            )
            
            job.total_files = 0
            job.source_files_total = len(mp4_files)
            
            if job.source_files_total == 0:
                logger.warning(f"No MP4 files found in s3://{config['source_bucket']}/{config['source_prefix']}")
                job.status = 'completed'
                job.end_time = datetime.utcnow()
                return
            
            allowed_users = [job.username]
            if config.get('allowed_users'):
                allowed_users.extend([u for u in config['allowed_users'] if u and u != job.username])

            metadata = build_s3_ingest_metadata(
                owner=job.username,
                is_public=config.get('is_public', True),
                upload_timestamp=datetime.utcnow().isoformat(),
                tags=config.get('tags') or None,
                allowed_users=allowed_users,
                camera_id=config.get('camera_id'),
                capture_type=config.get('capture_type'),
                location=config.get('location'),
                scenario=config.get('scenario'),
                custom_prompt=config.get('custom_prompt'),
            )
            
            upload_delay = self._upload_delay_sec(config)

            for file_info in mp4_files:
                with self._lock:
                    current_job = self.jobs.get(job.job_id)
                    if not current_job or current_job.status != 'running':
                        logger.info(f"Batch sync job {job.job_id} was stopped")
                        job.status = 'stopped'
                        job.end_time = datetime.utcnow()
                        break
                
                source_key = file_info['key']
                job.current_file = source_key
                
                self._process_source_file(
                    job,
                    source_client,
                    dest_client,
                    config,
                    source_key,
                    metadata,
                )
                job.source_files_completed += 1

                if upload_delay > 0 and job.source_files_completed < job.source_files_total:
                    logger.info(
                        "Pacing: waiting %.1fs before next source video (rate limit)",
                        upload_delay,
                    )
                    if not self._paced_wait(job, upload_delay):
                        break
            
            # Mark job as completed
            job.status = 'completed'
            job.end_time = datetime.utcnow()
            job.current_file = None
            
            logger.info(
                "Batch sync completed for %s: %s chunks uploaded, %s failed (%s/%s source files)",
                job.username,
                job.completed_files,
                job.failed_files,
                job.source_files_completed,
                job.source_files_total,
            )
            
        except Exception as e:
            logger.error(f"Error in batch sync job {job.job_id}: {e}", exc_info=True)
            job.status = 'failed'
            job.end_time = datetime.utcnow()
    
    def start_batch_sync(self, username: str, config: dict) -> Tuple[bool, str, Optional[str]]:
        """Start a new batch sync job"""
        with self._lock:
            if username in self.user_jobs:
                existing_job_id = self.user_jobs[username]
                existing_job = self.jobs.get(existing_job_id)
                if existing_job and existing_job.status == 'running':
                    return False, "User already has an active batch sync job", None
            
            # Validate: source and destination should not be identical
            if (config.get('source_bucket') == config.get('dest_bucket') and
                config.get('source_s3_endpoint') == config.get('dest_s3_endpoint') and
                config.get('source_prefix', '').strip() == ''):
                return False, "Source and destination cannot be the same", None
            
            # Generate job ID
            job_id = f"{username}_{int(time.time())}"
            
            job = BatchSyncJob(job_id, username, config)
            self.jobs[job_id] = job
            self.user_jobs[username] = job_id
        
        # Start sync in background thread
        thread = threading.Thread(target=self.run_batch_sync, args=(job,))
        thread.daemon = True
        thread.start()
        
        logger.info(f"Started batch sync job {job_id} for {username}")
        return True, "Batch sync started", job_id
    
    def get_job_status(self, username: str) -> Optional[Dict]:
        """Get status of user's active batch sync job"""
        with self._lock:
            if username not in self.user_jobs:
                return None
            
            job_id = self.user_jobs[username]
            job = self.jobs.get(job_id)
            
            if not job:
                return None
            
            return {
                'job_id': job.job_id,
                'status': job.status,
                'total_files': job.total_files,
                'completed_files': job.completed_files,
                'failed_files': job.failed_files,
                'failed_file_list': job.failed_file_list,
                'start_time': job.start_time.isoformat(),
                'end_time': job.end_time.isoformat() if job.end_time else None,
                'current_file': job.current_file,
                'source_bucket': job.config.get('source_bucket'),
                'source_prefix': job.config.get('source_prefix'),
                'dest_bucket': job.config.get('dest_bucket'),
                'source_files_total': job.source_files_total,
                'source_files_completed': job.source_files_completed,
                'chunk_duration_sec': job.config.get('chunk_duration_sec', DEFAULT_CHUNK_DURATION_SEC),
            }
    
    def stop_batch_sync(self, username: str) -> Tuple[bool, str]:
        """Stop user's active batch sync job"""
        with self._lock:
            if username not in self.user_jobs:
                return False, "No active batch sync job found"
            
            job_id = self.user_jobs[username]
            job = self.jobs.get(job_id)
            
            if not job:
                return False, "Job not found"
            
            if job.status != 'running':
                return False, f"Job is not running (current status: {job.status})"
            
            # Mark job as stopped
            job.status = 'stopped'
            job.end_time = datetime.utcnow()
            job.current_file = None
            
            logger.info(f"Stopped batch sync job {job_id} for {username}")
            return True, "Batch sync stopped successfully"
    
    def check_objects(self, s3_client, bucket: str, prefix: str) -> Dict:
        """Check and count MP4 files in bucket/prefix"""
        try:
            mp4_files = self.list_mp4_files(s3_client, bucket, prefix)
            return {
                'success': True,
                'count': len(mp4_files),
                'files': mp4_files[:10] if len(mp4_files) > 10 else mp4_files  # Return first 10 for preview
            }
        except Exception as e:
            logger.error(f"Error checking objects: {e}")
            return {
                'success': False,
                'error': str(e),
                'count': 0
            }

# Global service instance
batch_sync_service = BatchSyncService()

@app.route('/check-objects', methods=['POST'])
def check_objects():
    """Check MP4 files in source S3 bucket"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400
        
        required_fields = ['access_key', 'secret_key', 's3_endpoint', 'bucket', 'prefix']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
        
        # Setup S3 client
        s3_client = batch_sync_service.setup_s3_client(
            data['access_key'],
            data['secret_key'],
            data['s3_endpoint'],
            use_ssl=data.get('use_ssl', False)
        )
        
        if not s3_client:
            return jsonify({'success': False, 'error': 'Failed to setup S3 client'}), 400
        
        result = batch_sync_service.check_objects(
            s3_client,
            data['bucket'],
            data['prefix']
        )
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Error in check-objects endpoint: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/start', methods=['POST'])
def start_batch_sync():
    """Start batch sync operation"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400
        
        required_fields = [
            'username',
            'source_access_key', 'source_secret_key', 'source_s3_endpoint', 'source_bucket', 'source_prefix',
            'dest_access_key', 'dest_secret_key', 'dest_s3_endpoint', 'dest_bucket'
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
        
        # Start batch sync
        success, message, job_id = batch_sync_service.start_batch_sync(
            data['username'],
            data
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'job_id': job_id
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        logger.error(f"Error in start endpoint: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Get batch sync status for a user"""
    try:
        username = request.args.get('username')
        if not username:
            return jsonify({'success': False, 'error': 'Missing username parameter'}), 400
        
        status = batch_sync_service.get_job_status(username)
        
        if status:
            return jsonify({
                'success': True,
                'status': status
            }), 200
        else:
            return jsonify({
                'success': True,
                'status': None,
                'message': 'No active batch sync job'
            }), 200
            
    except Exception as e:
        logger.error(f"Error in status endpoint: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_batch_sync():
    """Stop batch sync operation"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400
        
        username = data.get('username')
        if not username:
            return jsonify({'success': False, 'error': 'Missing username parameter'}), 400
        
        # Stop batch sync
        success, message = batch_sync_service.stop_batch_sync(username)
        
        if success:
            return jsonify({
                'success': True,
                'message': message
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        logger.error(f"Error in stop endpoint: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    """Simple health check endpoint"""
    return jsonify({'status': 'OK'}), 200

if __name__ == '__main__':
    logger.info("Starting Video Batch Sync Web Service")
    app.run(host='0.0.0.0', port=5000, debug=False)

