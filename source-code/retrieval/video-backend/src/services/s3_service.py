"""
S3 service for video uploads and streaming
"""
import logging
import boto3
import re
from botocore.exceptions import ClientError
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union, Literal
import uuid
from datetime import datetime
from urllib.parse import unquote
from src.config import get_settings
from src.models.user import User
from src.ingest_metadata import build_s3_ingest_metadata

logger = logging.getLogger(__name__)
settings = get_settings()

# S3 → client copy; larger chunks help high-bitrate video (same bytes, less overhead).
STREAM_CHUNK_SIZE = 256 * 1024


@dataclass(frozen=True)
class S3ObjectStreamMeta:
    """Head object fields used for video streaming (one HEAD per request)."""
    content_length: int
    content_type: str
    etag: Optional[str]


def normalize_etag(etag: str) -> str:
    """Strip weak prefix and quotes for comparison."""
    e = etag.strip()
    if e.upper().startswith("W/"):
        e = e[2:].strip()
    if e.startswith('"') and e.endswith('"'):
        e = e[1:-1]
    return e


def if_none_match_satisfied(header_value: Optional[str], s3_etag: Optional[str]) -> bool:
    """
    True if the client If-None-Match value matches the object's ETag (HTTP 304).
    """
    if not header_value or not s3_etag:
        return False
    hv = header_value.strip()
    if hv == "*":
        return False
    n_s3 = normalize_etag(s3_etag)
    for part in header_value.split(","):
        if normalize_etag(part) == n_s3:
            return True
    return False


class S3Service:
    """Service for S3 operations"""
    
    def __init__(self):
        self.settings = settings
        self.client = boto3.client(
            's3',
            endpoint_url=self.settings.s3_endpoint,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            region_name=self.settings.s3_region,
            use_ssl=self.settings.s3_use_ssl
        )
        logger.info("S3 client initialized")
    
    def generate_upload_url(
        self,
        filename: str,
        user: User,
        is_public: bool,
        tags: list[str],
        allowed_users: list[str],
        expires_in: int = 3600
    ) -> Dict[str, any]:
        """
        Generate presigned URL for video upload
        
        Args:
            filename: Original filename
            user: Uploading user
            is_public: Whether video is public
            tags: Video tags
            allowed_users: Additional allowed users
            expires_in: URL expiration time in seconds
            
        Returns:
            Dict with upload_url, object_key, expires_in, metadata
        """
        try:
            # Generate unique object key
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            file_extension = filename.split('.')[-1] if '.' in filename else 'mp4'
            object_key = f"{user.normalized_username}/{timestamp}_{unique_id}.{file_extension}"
            
            # Lowercase S3 metadata keys.
            metadata = {
                'owner': user.username,
                'is-public': str(is_public).lower(),
                'original-filename': filename,
                'upload-timestamp': datetime.utcnow().isoformat()
            }
            
            # Add tags as comma-separated string
            if tags:
                metadata['tags'] = ','.join(tags)
            
            if not is_public and allowed_users:
                all_allowed = [user.username] + [u for u in allowed_users if u != user.username]
                metadata['allowed-users'] = ','.join(all_allowed)
            elif not is_public:
                metadata['allowed-users'] = user.username
            
            logger.info(f"Generating upload URL for {object_key}")
            logger.debug(f"Metadata: {metadata}")
            
            # Presigned POST metadata fields.
            s3_fields = {
                'x-amz-meta-owner': metadata['owner'],
                'x-amz-meta-is-public': metadata['is-public'],
                'x-amz-meta-original-filename': metadata['original-filename'],
                'x-amz-meta-upload-timestamp': metadata['upload-timestamp']
            }
            
            # Add tags if present
            if 'tags' in metadata:
                s3_fields['x-amz-meta-tags'] = metadata['tags']
            
            # Add allowed-users if present
            if 'allowed-users' in metadata:
                s3_fields['x-amz-meta-allowed-users'] = metadata['allowed-users']
            
            # Generate presigned POST URL
            presigned_post = self.client.generate_presigned_post(
                Bucket=self.settings.s3_upload_bucket,
                Key=object_key,
                Fields=s3_fields,
                Conditions=[
                    ['content-length-range', 1, self.settings.max_upload_size_mb * 1024 * 1024]
                ],
                ExpiresIn=expires_in
            )
            
            logger.info(f"Upload URL generated for {object_key}, expires in {expires_in}s")
            
            return {
                'upload_url': presigned_post['url'],
                'fields': presigned_post['fields'],
                'object_key': object_key,
                'expires_in': expires_in,
                'metadata': metadata
            }
            
        except ClientError as e:
            logger.error(f"Error generating upload URL: {str(e)}")
            raise
    
    def object_key_for(
        self,
        username: str,
        filename: str,
        *,
        chunk_index: Optional[int] = None,
        stamp: Optional[str] = None,
        unique_id: Optional[str] = None,
    ) -> str:
        """S3 key. A chunk index adds `_chunk_NNNN` so pieces of one upload stay grouped."""
        timestamp = stamp or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        uid = unique_id or uuid.uuid4().hex[:8]
        file_ext = filename.split(".")[-1] if filename and "." in filename else "mp4"
        suffix = f"_chunk_{chunk_index:04d}" if chunk_index is not None else ""
        return f"{username}/{timestamp}_{uid}{suffix}.{file_ext}"

    def upload_metadata(
        self,
        user: User,
        *,
        is_public: bool,
        tags: list[str],
        allowed_users: list[str],
        scenario: str = "",
        custom_prompt: Optional[str] = None,
        camera_id: Optional[str] = None,
        capture_type: Optional[str] = None,
        location: Optional[str] = None,
        original_filename: Optional[str] = None,
        extra_metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        username = user.username
        all_allowed_users = [username]
        if allowed_users:
            all_allowed_users.extend([u for u in allowed_users if u and u != username])
        return build_s3_ingest_metadata(
            is_public=is_public,
            allowed_users=all_allowed_users,
            original_filename=original_filename,
            upload_timestamp=datetime.utcnow().isoformat(),
            tags=tags or None,
            scenario=scenario or None,
            custom_prompt=custom_prompt,
            camera_id=camera_id,
            capture_type=capture_type,
            location=location,
            extra=extra_metadata,
        )

    def put_local_video(
        self,
        user: User,
        path: str,
        filename: str,
        *,
        is_public: bool,
        tags: list[str],
        allowed_users: list[str],
        scenario: str = "",
        custom_prompt: Optional[str] = None,
        camera_id: Optional[str] = None,
        capture_type: Optional[str] = None,
        location: Optional[str] = None,
        extra_metadata: Optional[Dict[str, str]] = None,
        chunk_index: Optional[int] = None,
        stamp: Optional[str] = None,
        unique_id: Optional[str] = None,
    ) -> str:
        """Upload a file already on disk. Returns the object key."""
        object_key = self.object_key_for(
            user.username,
            filename,
            chunk_index=chunk_index,
            stamp=stamp,
            unique_id=unique_id,
        )
        metadata = self.upload_metadata(
            user,
            is_public=is_public,
            tags=tags,
            allowed_users=allowed_users,
            scenario=scenario,
            custom_prompt=custom_prompt,
            camera_id=camera_id,
            capture_type=capture_type,
            location=location,
            original_filename=filename,
            extra_metadata=extra_metadata,
        )
        with open(path, "rb") as handle:
            body = handle.read()
        logger.info(
            "Uploading %s (%s bytes) to s3://%s/%s",
            filename,
            len(body),
            self.settings.s3_upload_bucket,
            object_key,
        )
        try:
            self.client.put_object(
                Bucket=self.settings.s3_upload_bucket,
                Key=object_key,
                Body=body,
                Metadata=metadata,
            )
        except ClientError as exc:
            logger.error("Error uploading file to S3: %s", exc)
            raise
        return object_key

    async def upload_file(
        self,
        file,
        user: User,
        is_public: bool,
        tags: list[str],
        allowed_users: list[str],
        scenario: str = "",
        custom_prompt: Optional[str] = None,
        camera_id: Optional[str] = None,
        capture_type: Optional[str] = None,
        location: Optional[str] = None,
        extra_metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Upload file directly to S3 (backend proxy)

        Args:
            file: UploadFile from FastAPI
            user: Uploading user
            is_public: Whether video is public
            tags: Video tags
            allowed_users: Additional allowed users
            scenario: Analysis scenario (ignored if custom_prompt is set)
            custom_prompt: Custom prompt for video reasoning (overrides scenario, max 800 chars, URL-encoded for S3)
            camera_id: Optional camera identifier (for ingest pipeline)
            capture_type: Optional capture type (traffic, streets, crowds, malls, etc.)
            location: Optional area/location (for ingest pipeline)

        Returns:
            S3 object key
        """
        try:
            object_key = self.object_key_for(user.username, file.filename or "video.mp4")
            metadata = self.upload_metadata(
                user,
                is_public=is_public,
                tags=tags,
                allowed_users=allowed_users,
                scenario=scenario,
                custom_prompt=custom_prompt,
                camera_id=camera_id,
                capture_type=capture_type,
                location=location,
                original_filename=file.filename,
                extra_metadata=extra_metadata,
            )

            logger.info(f"Uploading {file.filename} to s3://{self.settings.s3_upload_bucket}/{object_key}")
            logger.info(f"Metadata to set: {metadata}")

            await file.seek(0)
            file_content = await file.read()
            logger.info(f"Read {len(file_content)} bytes from uploaded file")

            response = self.client.put_object(
                Bucket=self.settings.s3_upload_bucket,
                Key=object_key,
                Body=file_content,
                Metadata=metadata
            )

            logger.info(f"File uploaded to S3: {object_key} ({len(file_content)} bytes)")
            logger.info(f"S3 put_object response ETag: {response.get('ETag')}")

            return object_key

        except ClientError as e:
            logger.error(f"Error uploading file to S3: {str(e)}")
            raise
    
    def generate_download_url(self, bucket: str, key: str, expires_in: int = 3600) -> str:
        """
        Generate presigned URL for video download/streaming
        
        Args:
            bucket: S3 bucket name
            key: S3 object key
            expires_in: URL expiration time in seconds
            
        Returns:
            Presigned URL string
        """
        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket, 'Key': key},
                ExpiresIn=expires_in
            )
            logger.info(f"Generated download URL for s3://{bucket}/{key}")
            logger.info(f"Presigned URL endpoint: {url.split('?')[0]}")  # Log URL without signature
            return url
            
        except ClientError as e:
            logger.error(f"Error generating download URL: {str(e)}")
            raise
    
    def get_object_metadata(self, bucket: str, key: str) -> Dict:
        """
        Get metadata for an S3 object
        
        Args:
            bucket: S3 bucket name
            key: S3 object key
            
        Returns:
            Metadata dict
        """
        try:
            response = self.client.head_object(Bucket=bucket, Key=key)
            return response.get('Metadata', {})
        except ClientError as e:
            logger.error(f"Error getting object metadata: {str(e)}")
            raise
    
    def head_object_content_length(self, bucket: str, key: str) -> int:
        """Return object size in bytes (for Content-Length / Range responses)."""
        return self.head_object_for_stream(bucket, key).content_length

    def head_object_for_stream(self, bucket: str, key: str) -> S3ObjectStreamMeta:
        """
        Single HEAD for stream path: size, Content-Type (for correct MIME), ETag (caching / 304).
        """
        try:
            response = self.client.head_object(Bucket=bucket, Key=key)
            ct = response.get("ContentType") or "video/mp4"
            return S3ObjectStreamMeta(
                content_length=int(response["ContentLength"]),
                content_type=ct,
                etag=response.get("ETag"),
            )
        except ClientError as e:
            logger.error(f"Error head_object for s3://{bucket}/{key}: {str(e)}")
            raise

    def stream_video(self, bucket: str, key: str) -> Iterator[bytes]:
        """
        Stream full object from S3 as an iterator (no Range).
        """
        try:
            logger.info(f"Streaming video from s3://{bucket}/{key}")
            
            response = self.client.get_object(Bucket=bucket, Key=key)
            
            for chunk in response["Body"].iter_chunks(chunk_size=STREAM_CHUNK_SIZE):
                yield chunk
            
            logger.debug(f"Finished streaming s3://{bucket}/{key}")
            
        except ClientError as e:
            logger.error(f"Error streaming video from S3: {str(e)}")
            raise

    def list_keys(self, bucket: str, prefix: str, limit: int = 500) -> List[str]:
        """List object keys under a prefix. Used to catch segment leftovers of one upload."""
        if not prefix:
            return []
        keys: List[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj.get("Key")
                if key:
                    keys.append(key)
                if len(keys) >= limit:
                    return keys
        return keys

    def delete_keys(self, bucket: str, keys: Iterable[str]) -> Tuple[int, List[str]]:
        """Delete keys in one bucket. Missing keys are not errors. Returns (deleted, errors)."""
        unique = [key for key in dict.fromkeys(keys) if key]
        deleted = 0
        errors: List[str] = []
        for start in range(0, len(unique), 1000):
            chunk = unique[start:start + 1000]
            try:
                response = self.client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "ClientError")
                errors.append(f"s3://{bucket}: {code}")
                continue
            failed = set()
            for err in response.get("Errors") or []:
                code = err.get("Code") or "Error"
                key = err.get("Key") or ""
                if code in ("NoSuchKey", "404", "NotFound"):
                    continue
                failed.add(key)
                errors.append(f"s3://{bucket}/{key}: {code}")
            deleted += len(chunk) - len(failed)
        return deleted, errors

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        """Download full object bytes (e.g. gzipped detection sidecar)."""
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except ClientError as e:
            logger.error(f"Error reading object s3://{bucket}/{key}: {str(e)}")
            raise

    def count_mp4_objects(self, bucket: str) -> int:
        """Count .mp4 objects recursively under all prefixes in a bucket."""
        total = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents") or []:
                key = str(obj.get("Key") or "")
                if key.lower().endswith(".mp4"):
                    total += 1
        return total

    def stream_video_range(
        self, bucket: str, key: str, start: int, end: int
    ) -> Iterator[bytes]:
        """
        Stream a byte range (inclusive start/end) for HTTP 206 partial content.
        """
        try:
            rng = f"bytes={start}-{end}"
            logger.info(f"Streaming range {rng} from s3://{bucket}/{key}")
            response = self.client.get_object(Bucket=bucket, Key=key, Range=rng)
            for chunk in response["Body"].iter_chunks(chunk_size=STREAM_CHUNK_SIZE):
                yield chunk
        except ClientError as e:
            logger.error(f"Error range-streaming video from S3: {str(e)}")
            raise


StreamRangeResult = Union[
    Literal["full"],
    Literal["unsatisfiable"],
    Tuple[int, int],
]


def parse_stream_range(
    range_header: Optional[str], total_size: int
) -> StreamRangeResult:
    """
    Parse RFC 7233 Range for video streaming.

    Returns:
        "full" — no Range header; send whole object
        "unsatisfiable" — valid Range syntax but no overlap with object (HTTP 416)
        (start, end) — inclusive byte range for HTTP 206
    """
    if not range_header:
        return "full"
    m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip(), re.IGNORECASE)
    if not m:
        return "full"
    start_s, end_s = m.group(1), m.group(2)
    if total_size <= 0:
        return "unsatisfiable"

    # bytes=-500  (last N bytes)
    if start_s == "" and end_s != "":
        suffix = int(end_s)
        if suffix <= 0:
            return "full"
        start = max(0, total_size - suffix)
        end = total_size - 1
        return (start, end)

    if start_s == "":
        return "full"

    start = int(start_s)
    if start >= total_size:
        return "unsatisfiable"

    if end_s == "":
        end = total_size - 1
    else:
        end = int(end_s)
        if end < start:
            return "unsatisfiable"
        end = min(end, total_size - 1)

    return (start, end)


# Global S3 service instance
_s3_service: S3Service | None = None


def get_s3_service() -> S3Service:
    """Get or create global S3 service instance"""
    global _s3_service
    if _s3_service is None:
        _s3_service = S3Service()
    return _s3_service

