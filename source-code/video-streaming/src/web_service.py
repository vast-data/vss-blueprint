#!/usr/bin/env python3
"""
Video Stream Capture Web Service

REST API service for capturing YouTube videos and uploading to S3.
Supports start, stop, and ping endpoints.

Author: AI Assistant
Date: 2024
"""

import time
import os
import json
import shutil
import threading
import logging
import subprocess
import tempfile
import re
from datetime import datetime
from flask import Flask, request, jsonify
import boto3
from botocore.exceptions import ClientError
import uuid

from ingest_metadata import build_s3_ingest_metadata

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DEFAULT_CAPTURE_INTERVAL_SEC = 30
CHUNK_DURATION_TOLERANCE_SEC = 0.05
CHUNK_TRIM_TOLERANCE_SEC = 0.01

_cv2 = None


def ffprobe_duration_sec(path: str) -> float | None:
    """Return container duration in seconds, or None if ffprobe fails."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe timed out for %s", path)
        return None
    if result.returncode != 0:
        return None
    try:
        value = float((result.stdout or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def is_browser_mp4(path: str) -> bool:
    """True when the file is an ISO BMFF container (ftyp box)."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(12)
    except OSError:
        return False
    return len(header) >= 8 and header[4:8] == b"ftyp"


def _lazy_cv2():
    """
    Load OpenCV only when needed. The full opencv-python wheel pulls large native
    libs that can interact badly with OpenSSL/FIPS on some Kubernetes nodes; deferring
    import lets Flask/S3 (TLS) initialize first and keeps /ping healthy for non-OpenCV paths.
    """
    global _cv2
    if _cv2 is None:
        import cv2 as cv2_module

        _cv2 = cv2_module
    return _cv2

def ytdlp_segment_download_timeout_sec(capture_interval: int, segment_start: int = 0) -> int:
    """
    Wall-clock limit for one yt-dlp+ffmpeg segment (live / fallback path).
    Scales with seek depth because each invocation re-fetches YouTube DASH.
    Default: max(5 min, 10× segment length, 90s + half the seek offset).
    Override: YTDLP_SEGMENT_TIMEOUT_SEC.
    """
    default = max(300, int(capture_interval * 10), 90 + int(segment_start * 0.5))
    raw = os.environ.get("YTDLP_SEGMENT_TIMEOUT_SEC", "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
        return max(60, n)
    except ValueError:
        return default


def ytdlp_full_download_timeout_sec(duration: float) -> int:
    """Wall-clock limit for downloading a full YouTube VOD once. Override: YTDLP_FULL_DOWNLOAD_TIMEOUT_SEC."""
    if duration > 0:
        default = max(600, int(duration * 3) + 180)
    else:
        default = 3600
    default = min(default, 7200)
    raw = os.environ.get("YTDLP_FULL_DOWNLOAD_TIMEOUT_SEC", "").strip()
    if not raw:
        return default
    try:
        return max(120, int(raw))
    except ValueError:
        return default


def ytdlp_segment_max_retries() -> int:
    raw = os.environ.get("YTDLP_SEGMENT_MAX_RETRIES", "").strip()
    if not raw:
        return 2
    try:
        return max(0, int(raw))
    except ValueError:
        return 2


def youtube_is_live_stream(video_info: dict) -> bool:
    if video_info.get("is_live"):
        return True
    return str(video_info.get("live_status") or "").lower() == "is_live"


def ytdlp_runtime_flags() -> list:
    """
    Recent yt-dlp only enables Deno unless a runtime is passed with an absolute path.
    The warning "Only deno is enabled by default" means neither deno nor node was found.
    """
    flags = []
    deno = shutil.which("deno")
    if deno:
        flags.extend(["--js-runtimes", f"deno:{deno}"])
    for name in ("node", "nodejs"):
        path = shutil.which(name)
        if path:
            flags.extend(["--js-runtimes", f"node:{path}"])
            break
    if not flags:
        logger.error(
            "No deno or node binary on PATH — YouTube extraction will fail. "
            "Rebuild the streaming image (Deno and Node are installed in the Dockerfile)."
        )
    return flags


def build_ytdlp_cmd(args: list) -> list:
    """
    Build argv for yt-dlp: JS runtime + YouTube EJS challenge solver + optional cookies.

    YouTube often returns "Sign in to confirm you're not a bot" for datacenter IPs.
    Export cookies from a logged-in browser to a file, then set:
      YTDLP_COOKIES_FILE=/path/to/cookies.txt
    (Netscape format; e.g. browser extension, or: yt-dlp --cookies-from-browser chrome --print-to-file ...)
    """
    cmd = ["yt-dlp", "--remote-components", "ejs:github", *ytdlp_runtime_flags()]
    cookies_path = os.environ.get("YTDLP_COOKIES_FILE") or os.environ.get("YT_DLP_COOKIES_FILE")
    if cookies_path:
        if os.path.isfile(cookies_path):
            cmd.extend(["--cookies", cookies_path])
        else:
            logger.warning(
                "YTDLP_COOKIES_FILE is set but not a readable file (%s) — YouTube may block",
                cookies_path,
            )
    cmd.extend(args)
    return cmd


app = Flask(__name__)

class VideoCaptureService:
    def __init__(self):
        self.is_running = False
        self.capture_thread = None
        self.current_config = None
        self.s3_client = None
        self.temp_files = []
        self._chunk_index = 0

    @staticmethod
    def _capture_interval_sec(config) -> float:
        """User-configured chunk size (seconds) from stream start payload."""
        try:
            return max(0.1, float(config.get("capture_interval", DEFAULT_CAPTURE_INTERVAL_SEC)))
        except (TypeError, ValueError):
            return float(DEFAULT_CAPTURE_INTERVAL_SEC)

    def _attach_stream_metadata(self, config, s3_metadata, chunk_index, chunk_start_sec):
        """Add stream session indexing fields for downstream segment/VastDB rows."""
        stream_id = config.get("stream_id")
        if not stream_id:
            return s3_metadata
        s3_metadata["stream_id"] = str(stream_id)
        s3_metadata["chunk_index"] = str(chunk_index)
        s3_metadata["chunk_start_sec"] = f"{float(chunk_start_sec):.3f}"
        s3_metadata["capture_interval"] = f"{self._capture_interval_sec(config):.3f}"
        s3_metadata["ingest_kind"] = "stream_chunk"
        return s3_metadata

    def _build_capture_s3_metadata(
        self,
        config,
        camera_id,
        capture_type,
        location,
        scenario,
        custom_prompt,
        capture_timestamp,
        chunk_index,
        chunk_start_sec,
        chunk_duration_sec=None,
    ):
        s3_metadata = build_s3_ingest_metadata(
            camera_id=camera_id,
            capture_type=capture_type,
            location=location,
            scenario=scenario,
            custom_prompt=custom_prompt,
            capture_timestamp=capture_timestamp,
        )
        s3_metadata = self._attach_stream_metadata(
            config, s3_metadata, chunk_index, chunk_start_sec
        )
        if chunk_duration_sec is not None:
            s3_metadata["chunk_duration_sec"] = f"{float(chunk_duration_sec):.3f}"
        return s3_metadata
        
    def is_youtube_url(self, url):
        """Check if the URL is a YouTube URL."""
        youtube_patterns = [
            r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=',
            r'(?:https?://)?(?:www\.)?youtu\.be/',
            r'(?:https?://)?(?:www\.)?youtube\.com/embed/',
            r'(?:https?://)?(?:www\.)?youtube\.com/v/'
        ]
        
        for pattern in youtube_patterns:
            if re.search(pattern, url):
                return True
        return False
    
    def get_youtube_stream_url(self, youtube_url):
        """
        Get the direct stream URL from a YouTube video using yt-dlp.
        
        For VOD videos, we need a direct video URL (not HLS .m3u8) that OpenCV can read.
        We try multiple format options in order of preference.
        """
        try:
            logger.info(f"Extracting stream URL from YouTube: {youtube_url}")
            
            # Format options to try (in order of preference)
            # We want direct video URLs, NOT HLS playlists (.m3u8)
            format_options = [
                # Option 1: Best MP4 with video+audio up to 720p (direct URL)
                'best[height<=720][ext=mp4]/best[height<=720][ext=webm]',
                # Option 2: Specific format that's usually direct (not HLS)
                'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]',
                # Option 3: Any best format up to 720p
                'best[height<=720]',
                # Option 4: Fallback - just get something
                'best'
            ]
            
            last_stderr = ""
            for fmt in format_options:
                logger.info(f"Trying format: {fmt}")
                
                cmd = build_ytdlp_cmd(
                    [
                        "--get-url",
                        "--format",
                        fmt,
                        "--no-playlist",
                        youtube_url,
                    ]
                )
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.stderr:
                    last_stderr = result.stderr
                
                if result.returncode == 0 and result.stdout.strip():
                    stream_url = result.stdout.strip().split('\n')[0]  # Take first URL if multiple
                    
                    # Check if it's an HLS playlist (which OpenCV can't handle well)
                    if '.m3u8' in stream_url or 'manifest' in stream_url.lower():
                        logger.warning(f"Got HLS playlist URL with format {fmt}, trying next...")
                        continue
                    
                    logger.info(f"Successfully extracted direct video URL with format: {fmt}")
                    logger.debug(f"URL preview: {stream_url[:100]}...")
                    return stream_url
                else:
                    logger.warning(f"Format {fmt} failed: {result.stderr[:200] if result.stderr else 'no output'}")
            
            # If all formats returned HLS, try one more thing: force protocol
            logger.info("All formats returned HLS, trying to force https protocol...")
            cmd = build_ytdlp_cmd(
                [
                    "--get-url",
                    "--format",
                    "best[height<=720][protocol=https]/best[protocol=https]",
                    "--no-playlist",
                    youtube_url,
                ]
            )
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.stderr:
                last_stderr = result.stderr
            if result.returncode == 0 and result.stdout.strip():
                stream_url = result.stdout.strip().split('\n')[0]
                if '.m3u8' not in stream_url:
                    logger.info("Successfully got direct URL with protocol filter")
                    return stream_url
            
            # Last resort: Accept HLS but warn
            logger.warning("Could not get direct URL, falling back to HLS (may not work with OpenCV)")
            cmd = build_ytdlp_cmd(
                ["--get-url", "--format", "best[height<=720]", youtube_url]
            )
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.stderr:
                last_stderr = result.stderr
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split('\n')[0]
            
            logger.error("Failed to extract any YouTube stream URL")
            if last_stderr and (
                "sign in" in last_stderr.lower() or "not a bot" in last_stderr.lower()
            ):
                logger.error(
                    "YouTube returned a bot check. Server/datacenter IPs are often blocked. "
                    "Mount Netscape-format cookies and set env YTDLP_COOKIES_FILE. "
                    "See: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
                )
            return None
                
        except Exception as e:
            logger.error(f"Error extracting YouTube stream: {e}")
            return None
    
    def setup_s3_client(self, access_key, secret_key, s3_endpoint):
        """Setup S3 client with provided credentials."""
        try:
            # For HTTP endpoints, disable SSL completely
            use_ssl = s3_endpoint.startswith('https://')
            
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                endpoint_url=s3_endpoint,
                use_ssl=False,  # Always disable SSL for HTTP endpoints
                verify=False    # Disable SSL verification
            )
            logger.info(f"S3 client configured for endpoint: {s3_endpoint}")
            return True
        except Exception as e:
            logger.error(f"Failed to setup S3 client: {e}")
            return False
    
    def upload_to_s3(self, file_path, bucket_name, s3_key, metadata=None):
        """Upload file to S3 with optional metadata."""
        try:
            if not self.s3_client:
                logger.error("S3 client not configured")
                return False
                
            logger.info(f"Uploading {file_path} to s3://{bucket_name}/{s3_key}")
            
            # Prepare extra args for metadata
            extra_args = {}
            if str(s3_key).lower().endswith(".mp4"):
                extra_args["ContentType"] = "video/mp4"
            if metadata:
                extra_args['Metadata'] = metadata
                logger.info(f"Adding S3 metadata: {metadata}")
            
            self.s3_client.upload_file(file_path, bucket_name, s3_key, ExtraArgs=extra_args if extra_args else None)
            logger.info(f"Successfully uploaded to s3://{bucket_name}/{s3_key}")
            return True
            
        except ClientError as e:
            logger.error(f"S3 upload failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False
    
    def test_stream(self, stream_url):
        """Test if a stream URL is accessible and working."""
        cv2 = _lazy_cv2()
        logger.info(f"Testing stream: {stream_url}")
        
        if self.is_youtube_url(stream_url):
            # Capture uses yt-dlp downloads, not OpenCV on a googlevideo URL.
            # A failed direct-URL probe must not reject POST /start.
            logger.info(
                "YouTube URL: skipping direct-URL/OpenCV probe (yt-dlp %s)",
                " ".join(ytdlp_runtime_flags()) or "NO JS RUNTIME",
            )
            return True

        # Handle regular streams
        cap = cv2.VideoCapture(stream_url)
        
        if not cap.isOpened():
            logger.error(f"Cannot open stream: {stream_url}")
            return False
            
        ret, frame = cap.read()
        cap.release()
        
        if ret and frame is not None:
            logger.info(f"Stream is working: {stream_url}")
            return True
        else:
            logger.error(f"Cannot read frames from: {stream_url}")
            return False
    
    def get_stream_source(self, stream_url):
        """Get the actual stream source (direct URL or YouTube stream)."""
        if self.is_youtube_url(stream_url):
            return self.get_youtube_stream_url(stream_url)
        return stream_url
    
    def _get_youtube_video_info(self, youtube_url: str) -> dict | None:
        info_cmd = build_ytdlp_cmd(["--dump-json", "--no-playlist", youtube_url])
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)
        if info_result.returncode != 0:
            logger.error(f"Failed to get video info: {info_result.stderr}")
            return None
        try:
            return json.loads(info_result.stdout)
        except json.JSONDecodeError as exc:
            logger.error(f"Invalid video info JSON: {exc}")
            return None

    def _run_ffmpeg(self, cmd: list, timeout_sec: int, context: str) -> bool:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg timed out after %ss (%s)", timeout_sec, context)
            return False
        if result.returncode != 0:
            stderr = (result.stderr or "")[:400]
            logger.warning("ffmpeg failed (%s): %s", context, stderr)
            return False
        return True

    def _trim_chunk_tail_copy(self, path: str, target_sec: float) -> bool:
        """Drop tail past target_sec via stream-copy remux (fast, keyframe-aligned)."""
        tmp_path = f"{path}.trim.mp4"
        trim_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            path,
            "-t",
            f"{float(target_sec):.3f}",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            tmp_path,
        ]
        timeout_sec = max(30, int(target_sec * 2))
        if not self._run_ffmpeg(trim_cmd, timeout_sec, f"tail-trim {os.path.basename(path)}"):
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            return False
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) <= 0:
            return False
        try:
            os.replace(tmp_path, path)
        except OSError as exc:
            logger.error("Failed to replace trimmed chunk %s: %s", path, exc)
            return False
        return True

    def _ensure_browser_mp4(self, path: str) -> bool:
        """Remux a non-MP4 capture (live yt-dlp writes MPEG-TS into a .mp4 name).

        Explore plays this parent file. A transport stream makes the video element fail
        even though the backend returns 206. Search plays the segmenter MP4s instead.
        """
        if is_browser_mp4(path):
            return True
        logger.info("Capture is not a browser MP4; remuxing: %s", os.path.basename(path))
        tmp_path = f"{path}.browser.mp4"
        commands = (
            [
                "ffmpeg",
                "-y",
                "-i",
                path,
                "-c",
                "copy",
                "-bsf:a",
                "aac_adtstoasc",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                tmp_path,
            ],
            [
                "ffmpeg",
                "-y",
                "-i",
                path,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                tmp_path,
            ],
        )
        wrote = False
        for cmd in commands:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            if not self._run_ffmpeg(cmd, 120, f"browser-mp4 {os.path.basename(path)}"):
                continue
            if is_browser_mp4(tmp_path) and os.path.getsize(tmp_path) > 0:
                wrote = True
                break
        if not wrote:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            return False
        try:
            os.replace(tmp_path, path)
        except OSError as exc:
            logger.error("Failed to replace remuxed chunk %s: %s", path, exc)
            return False
        logger.info("Remuxed capture to browser MP4: %s", os.path.basename(path))
        return True

    def _normalize_chunk_duration(self, path: str, target_sec: float) -> bool:
        """When probed duration exceeds nominal, drop the tail (copy remux to target_sec)."""
        probed = ffprobe_duration_sec(path)
        if probed is None:
            return False
        if target_sec <= 0:
            return True

        if probed <= target_sec + CHUNK_TRIM_TOLERANCE_SEC:
            if probed < target_sec - CHUNK_DURATION_TOLERANCE_SEC:
                logger.warning(
                    "Chunk shorter than target (%.3fs vs %.3fs): %s",
                    probed,
                    target_sec,
                    os.path.basename(path),
                )
            return True

        logger.info(
            "Dropping tail %.3fs → %.3fs (copy remux): %s",
            probed,
            target_sec,
            os.path.basename(path),
        )
        if not self._trim_chunk_tail_copy(path, target_sec):
            logger.warning(
                "Tail trim failed; segmenter caps at %.3fs: %s",
                target_sec,
                os.path.basename(path),
            )
            return True

        final = ffprobe_duration_sec(path)
        if final is not None:
            logger.info(
                "After tail trim probed=%.3fs nominal=%.3fs: %s",
                final,
                target_sec,
                os.path.basename(path),
            )
        return True

    def _ffmpeg_extract_segment(
        self,
        source_path: str,
        segment_start: float,
        duration_sec: float,
        output_path: str,
    ) -> bool:
        duration_sec = max(0.1, float(duration_sec))
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{segment_start:.3f}",
            "-t",
            f"{duration_sec:.3f}",
            "-i",
            source_path,
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            output_path,
        ]
        timeout_sec = max(60, int(duration_sec * 4))
        if not self._run_ffmpeg(
            ffmpeg_cmd,
            timeout_sec,
            f"extract start={segment_start:.1f}s len={duration_sec:.1f}s",
        ):
            return False
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    def _download_youtube_full_vod(self, youtube_url: str, output_path: str, duration: float) -> bool:
        ytdlp_cmd = build_ytdlp_cmd(
            [
                "-f",
                "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
                "--merge-output-format",
                "mp4",
                "--no-playlist",
                "-o",
                output_path,
                youtube_url,
            ]
        )
        timeout_sec = ytdlp_full_download_timeout_sec(duration)
        logger.info(
            "Downloading full VOD with yt-dlp (timeout=%ss, duration≈%.0fs)",
            timeout_sec,
            duration,
        )
        try:
            result = subprocess.run(ytdlp_cmd, capture_output=True, text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            logger.error("Full VOD download timed out after %ss", timeout_sec)
            return False
        ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "")[:800]
            if ok:
                logger.warning(
                    "yt-dlp exited %s but wrote a file; continuing. %s",
                    result.returncode,
                    detail,
                )
                return True
            logger.error("Full VOD download failed: %s", detail)
            return False
        return ok

    def _download_youtube_segment_chunk(
        self,
        youtube_url: str,
        segment_start: int,
        capture_interval: int,
        output_path: str,
    ) -> bool:
        ytdlp_cmd = build_ytdlp_cmd(
            [
                "-f",
                "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
                "--merge-output-format",
                "mp4",
                "--no-playlist",
                "--external-downloader",
                "ffmpeg",
                "--external-downloader-args",
                f"-ss {segment_start} -t {capture_interval}",
                "-o",
                output_path,
                youtube_url,
            ]
        )
        max_retries = ytdlp_segment_max_retries()
        for attempt in range(max_retries + 1):
            timeout_sec = ytdlp_segment_download_timeout_sec(capture_interval, segment_start)
            if attempt > 0:
                timeout_sec = int(timeout_sec * 1.5)
                logger.info(
                    "Retrying yt-dlp segment (attempt %s/%s, timeout=%ss, start=%ss)",
                    attempt + 1,
                    max_retries + 1,
                    timeout_sec,
                    segment_start,
                )
            else:
                logger.debug(
                    "yt-dlp segment timeout=%ss (interval=%ss, start=%ss)",
                    timeout_sec,
                    capture_interval,
                    segment_start,
                )
            try:
                result = subprocess.run(
                    ytdlp_cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "yt-dlp segment timed out after %ss (start=%ss, attempt %s/%s)",
                    timeout_sec,
                    segment_start,
                    attempt + 1,
                    max_retries + 1,
                )
                continue
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                if result.returncode != 0:
                    logger.warning(
                        "yt-dlp segment exited %s but wrote a file (start=%ss); using it",
                        result.returncode,
                        segment_start,
                    )
                return True
            logger.warning(
                "yt-dlp segment failed (start=%ss): %s",
                segment_start,
                (result.stderr or "")[:400] or "unknown error",
            )
        return False

    def _upload_capture_chunk(
        self,
        temp_path: str,
        filename: str,
        config,
        bucket_name: str,
        s3_prefix: str,
        camera_id: str,
        capture_type: str,
        location: str,
        scenario: str,
        custom_prompt: str,
        capture_count: int,
        chunk_start_sec: float,
        timestamp: str,
        nominal_chunk_sec: float,
    ) -> bool:
        nominal_chunk_sec = max(0.1, float(nominal_chunk_sec))
        if not self._ensure_browser_mp4(temp_path):
            logger.error("Cannot remux chunk to browser MP4 for %s", filename)
            return False
        if not self._normalize_chunk_duration(temp_path, nominal_chunk_sec):
            logger.error("Cannot normalize chunk duration for %s", filename)
            return False
        probed_sec = ffprobe_duration_sec(temp_path)
        if probed_sec is not None:
            logger.info(
                "Chunk duration probed=%.3fs nominal=%.3fs (capture_interval=%.3fs): %s",
                probed_sec,
                nominal_chunk_sec,
                self._capture_interval_sec(config),
                filename,
            )
        s3_metadata = self._build_capture_s3_metadata(
            config,
            camera_id,
            capture_type,
            location,
            scenario,
            custom_prompt,
            timestamp,
            capture_count,
            chunk_start_sec,
            chunk_duration_sec=nominal_chunk_sec,
        )
        s3_key = f"{s3_prefix}/{filename}"
        if not self.upload_to_s3(temp_path, bucket_name, s3_key, metadata=s3_metadata):
            logger.error(f"Failed to upload {filename} to S3")
            return False
        logger.info(f"✓ Uploaded to S3: s3://{bucket_name}/{s3_key}")
        try:
            os.remove(temp_path)
            if temp_path in self.temp_files:
                self.temp_files.remove(temp_path)
        except OSError:
            pass
        return True

    def _capture_with_ytdlp_download(self, config, capture_interval, bucket_name, s3_prefix,
                                     camera_id, capture_type, location, scenario, custom_prompt, max_duration):
        """
        Download YouTube segments with yt-dlp (muxed video+audio).

        VOD: download the full video once, then cut chunks locally with ffmpeg (fast seeks).
        Live: per-chunk yt-dlp download with retries; one failure does not stop the session.
        """
        logger.info("Using yt-dlp segment downloads (muxed video+audio)")
        capture_count = 0
        youtube_url = config["youtube_url"]

        video_info = self._get_youtube_video_info(youtube_url)
        if not video_info:
            return 0

        raw_dur = video_info.get("duration")
        duration = float(raw_dur) if raw_dur is not None else 0.0
        is_live = youtube_is_live_stream(video_info)
        logger.info(
            "YouTube %s | duration=%.1fs",
            "LIVE" if is_live else "VOD",
            duration,
        )

        if is_live or duration <= 0:
            return self._capture_ytdlp_live_chunks(
                config,
                capture_interval,
                bucket_name,
                s3_prefix,
                camera_id,
                capture_type,
                location,
                scenario,
                custom_prompt,
                max_duration,
                youtube_url,
                duration,
            )
        return self._capture_ytdlp_vod_local(
            config,
            capture_interval,
            bucket_name,
            s3_prefix,
            camera_id,
            capture_type,
            location,
            scenario,
            custom_prompt,
            max_duration,
            youtube_url,
            duration,
        )

    def _capture_ytdlp_vod_local(
        self,
        config,
        capture_interval,
        bucket_name,
        s3_prefix,
        camera_id,
        capture_type,
        location,
        scenario,
        custom_prompt,
        max_duration,
        youtube_url,
        duration,
    ) -> int:
        capture_count = 0
        full_path = os.path.join(
            tempfile.gettempdir(),
            f"{config['name']}_full_{uuid.uuid4().hex[:8]}.mp4",
        )
        self.temp_files.append(full_path)

        try:
            if not self._download_youtube_full_vod(youtube_url, full_path, duration):
                logger.warning(
                    "Full VOD download failed; falling back to per-chunk yt-dlp downloads"
                )
                return self._capture_ytdlp_live_chunks(
                    config,
                    capture_interval,
                    bucket_name,
                    s3_prefix,
                    camera_id,
                    capture_type,
                    location,
                    scenario,
                    custom_prompt,
                    max_duration,
                    youtube_url,
                    duration,
                )

            effective_end = min(duration, float(max_duration))
            segment_start = 0.0
            consecutive_failures = 0
            max_consecutive_failures = 3

            while self.is_running and segment_start < effective_end:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{config['name']}_{timestamp}_{uuid.uuid4().hex[:8]}.mp4"
                temp_path = os.path.join(tempfile.gettempdir(), filename)
                self.temp_files.append(temp_path)

                logger.info(
                    "Extracting segment #%s (%.1f–%.1fs): %s",
                    capture_count + 1,
                    segment_start,
                    min(segment_start + capture_interval, effective_end),
                    filename,
                )

                chunk_target_sec = min(
                    float(capture_interval),
                    max(0.0, effective_end - segment_start),
                )
                uploaded = False
                if self._ffmpeg_extract_segment(
                    full_path, segment_start, chunk_target_sec, temp_path
                ):
                    logger.info(f"✓ Extracted segment #{capture_count + 1}: {filename}")
                    if self._upload_capture_chunk(
                        temp_path,
                        filename,
                        config,
                        bucket_name,
                        s3_prefix,
                        camera_id,
                        capture_type,
                        location,
                        scenario,
                        custom_prompt,
                        capture_count,
                        segment_start,
                        timestamp,
                        chunk_target_sec,
                    ):
                        capture_count += 1
                        consecutive_failures = 0
                        uploaded = True
                    else:
                        consecutive_failures += 1
                else:
                    consecutive_failures += 1
                    if segment_start >= effective_end - capture_interval:
                        break

                if consecutive_failures >= max_consecutive_failures:
                    logger.error(
                        "%s consecutive segment failures — stopping VOD capture",
                        consecutive_failures,
                    )
                    break

                segment_start += capture_interval
                if uploaded and self.is_running and segment_start < effective_end:
                    logger.info(
                        "Pacing: waiting %ss before next chunk (real-time interval)",
                        capture_interval,
                    )
                    time.sleep(capture_interval)

            logger.info(f"VOD capture finished. Total segments: {capture_count}")
            return capture_count
        finally:
            try:
                if os.path.exists(full_path):
                    os.remove(full_path)
                if full_path in self.temp_files:
                    self.temp_files.remove(full_path)
            except OSError:
                pass

    def _capture_ytdlp_live_chunks(
        self,
        config,
        capture_interval,
        bucket_name,
        s3_prefix,
        camera_id,
        capture_type,
        location,
        scenario,
        custom_prompt,
        max_duration,
        youtube_url,
        duration,
    ) -> int:
        capture_count = 0
        session_start = time.time()
        segment_start = 0
        consecutive_failures = 0
        max_consecutive_failures = 3

        try:
            while self.is_running:
                session_elapsed = time.time() - session_start
                if duration > 0 and session_elapsed >= min(max_duration, duration):
                    logger.info("Reached max duration or video end. Stopping.")
                    break
                if duration > 0 and segment_start >= duration:
                    logger.info(f"Video ended. Total segments: {capture_count}")
                    break
                if session_elapsed >= max_duration:
                    logger.info(f"Max session duration ({max_duration}s) reached. Stopping.")
                    break

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{config['name']}_{timestamp}_{uuid.uuid4().hex[:8]}.mp4"
                temp_path = os.path.join(tempfile.gettempdir(), filename)
                self.temp_files.append(temp_path)

                logger.info(f"Downloading segment #{capture_count + 1}: {filename}")

                if self._download_youtube_segment_chunk(
                    youtube_url, segment_start, capture_interval, temp_path
                ):
                    logger.info(f"✓ Downloaded segment #{capture_count + 1}: {filename}")
                    if self._upload_capture_chunk(
                        temp_path,
                        filename,
                        config,
                        bucket_name,
                        s3_prefix,
                        camera_id,
                        capture_type,
                        location,
                        scenario,
                        custom_prompt,
                        capture_count,
                        float(segment_start),
                        timestamp,
                        float(capture_interval),
                    ):
                        capture_count += 1
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                else:
                    consecutive_failures += 1
                    if duration > 0 and segment_start >= duration - capture_interval:
                        break

                if consecutive_failures >= max_consecutive_failures:
                    logger.error(
                        "%s consecutive segment failures — stopping live capture",
                        consecutive_failures,
                    )
                    break

                segment_start += capture_interval
                time.sleep(0.5)
        except Exception as e:
            logger.error(f"Error in yt-dlp live capture: {e}", exc_info=True)
        finally:
            logger.info(f"yt-dlp download capture stopped. Total segments: {capture_count}")
        return capture_count
    
    def continuous_capture(self, config):
        """
        Continuous capture thread function.
        
        Works for both LIVE streams and regular VOD videos:
        - Keeps VideoCapture open across chunks (no re-opening)
        - For VOD: automatically stops when video ends
        - For VOD: max_duration timeout (default 1 hour) as safety
        - For live: runs until stopped manually
        """
        cap = None
        out = None
        capture_count = 0
        
        try:
            stream_url = config['youtube_url']
            capture_interval = self._capture_interval_sec(config)
            bucket_name = config.get('bucket_name', 'rawlivevideos')
            s3_prefix = config.get('s3_prefix', 'captures')
            
            # Max duration for VOD videos (default 1 hour = 3600 seconds)
            # Live streams ignore this (run until manually stopped)
            max_duration = config.get('max_duration', 3600)
            
            # Metadata fields
            camera_id = config.get('camera_id', '')
            capture_type = config.get('capture_type', '')  # traffic, streets, crowds, malls
            location = config.get('location', '')
            scenario = config.get('scenario', '')  # analysis scenario (nhl, surveillance, etc.)
            custom_prompt = config.get('custom_prompt', '')  # custom prompt (overrides scenario)
            
            logger.info(f"Starting continuous capture every {capture_interval} seconds")
            logger.info(f"Stream URL: {stream_url}")
            logger.info(f"S3 bucket: {bucket_name}")
            logger.info(f"Max duration: {max_duration}s ({max_duration/60:.1f} min)")
            if camera_id:
                logger.info(f"Camera ID: {camera_id}")
            if capture_type:
                logger.info(f"Capture Type: {capture_type}")
            if location:
                logger.info(f"Location: {location}")
            if scenario:
                logger.info(f"Scenario: {scenario}")
            if custom_prompt:
                logger.info(f"Custom Prompt: set ({len(custom_prompt)} chars)")
            
            # YouTube: always use yt-dlp (muxed H.264 + AAC). OpenCV only writes video frames
            # to mp4v with no audio and visibly worse quality.
            if self.is_youtube_url(stream_url):
                logger.info(
                    "YouTube URL: using yt-dlp segment downloads (muxed A/V, up to 1080p); "
                    "skipping OpenCV frame capture (silent, lower quality)."
                )
                capture_count = self._capture_with_ytdlp_download(
                    config,
                    capture_interval,
                    bucket_name,
                    s3_prefix,
                    camera_id,
                    capture_type,
                    location,
                    scenario,
                    custom_prompt,
                    max_duration,
                ) or 0
                return
            
            cv2 = _lazy_cv2()
            # Get the actual stream source
            actual_stream_url = self.get_stream_source(stream_url)
            if not actual_stream_url:
                logger.error("Could not get stream source")
                return
            
            # Open video capture ONCE and keep it open
            # Use FFmpeg backend explicitly for better compatibility with various streams
            logger.info("Opening video capture with FFmpeg backend...")
            cap = cv2.VideoCapture(actual_stream_url, cv2.CAP_FFMPEG)
            
            if not cap.isOpened():
                # Fallback to default backend
                logger.warning("FFmpeg backend failed, trying default backend...")
                cap = cv2.VideoCapture(actual_stream_url)
            
            if not cap.isOpened():
                # Final fallback: Use yt-dlp to download segments directly
                # This works for videos that OpenCV can't stream (complex URLs, HLS playlists, etc.)
                if self.is_youtube_url(stream_url):
                    logger.warning("OpenCV failed to open stream, using yt-dlp download method as fallback")
                    capture_count = self._capture_with_ytdlp_download(
                        config, capture_interval, bucket_name, s3_prefix,
                        camera_id, capture_type, location, scenario, custom_prompt, max_duration,
                    ) or 0
                    return
                else:
                    logger.error(f"Cannot open stream: {stream_url}")
                    return
            
            # Get stream properties
            fps = int(cap.get(cv2.CAP_PROP_FPS)) or 30
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            # Check if VOD (has finite frame count) or live stream
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            is_live = total_frames <= 0  # Live streams report 0 or -1 frames
            video_duration_sec = total_frames / fps if total_frames > 0 else 0
            
            if is_live:
                logger.info(f"🔴 LIVE STREAM detected - will run until stopped manually")
            else:
                logger.info(f"📹 VOD detected - {total_frames} frames, ~{video_duration_sec:.1f}s duration")
                # For VOD, use actual video duration, but cap at max_duration as safety
                effective_duration = min(video_duration_sec, max_duration) if video_duration_sec > 0 else max_duration
                logger.info(f"   Will auto-stop after video ends (~{effective_duration:.1f}s)")
            
            logger.info(f"Stream properties: {width}x{height} @ {fps}fps")
            
            # Track overall session start time
            session_start = time.time()
            consecutive_failures = 0
            max_consecutive_failures = 30  # ~3 seconds of failures = video ended
            
            # For VOD, use actual video duration, but cap at max_duration as absolute safety limit
            # This allows videos shorter than max_duration to complete fully
            effective_duration = None
            if not is_live and video_duration_sec > 0:
                # Use video duration, but never exceed max_duration (safety limit)
                effective_duration = min(video_duration_sec, max_duration)
                if video_duration_sec > max_duration:
                    logger.warning(f"⚠️  Video duration ({video_duration_sec:.1f}s) exceeds max_duration ({max_duration}s). Will stop at {max_duration}s for safety.")
                else:
                    logger.info(f"✓ Video duration ({video_duration_sec:.1f}s) is within max_duration limit. Will capture full video.")
            
            while self.is_running:
                # Check duration timeout
                session_elapsed = time.time() - session_start
                
                # For VOD: stop at effective duration (video end or max_duration safety limit)
                if not is_live and effective_duration and session_elapsed >= effective_duration:
                    logger.info(f"⏱ Video duration reached ({effective_duration:.1f}s). Stopping capture.")
                    break
                
                # For live streams: only check max_duration as absolute limit
                if is_live and session_elapsed >= max_duration:
                    logger.info(f"⏱ Max duration ({max_duration}s) reached for live stream. Stopping capture.")
                    break
                
                # Generate filename for this chunk
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{config['name']}_{timestamp}_{uuid.uuid4().hex[:8]}.mp4"
                temp_path = os.path.join(tempfile.gettempdir(), filename)
                self.temp_files.append(temp_path)
                
                logger.info(f"Starting capture #{capture_count + 1}: {filename}")

                chunk_start_sec = 0.0
                if not is_live and fps > 0:
                    pos_frames = cap.get(cv2.CAP_PROP_POS_FRAMES)
                    chunk_start_sec = float(pos_frames) / float(fps)
                else:
                    chunk_start_sec = float(capture_count * capture_interval)
                
                # Create VideoWriter for this chunk
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out = cv2.VideoWriter(temp_path, fourcc, fps, (width, height))
                
                if not out.isOpened():
                    logger.error("Cannot create video writer")
                    time.sleep(1)
                    continue
                
                # Capture frames for this chunk
                chunk_start = time.time()
                frame_count = 0
                video_ended = False
                
                while time.time() - chunk_start < capture_interval and self.is_running:
                    ret, frame = cap.read()
                    
                    if not ret:
                        consecutive_failures += 1
                        
                        # For VOD: check if we've reached the end of the video
                        if not is_live:
                            # Get current position in video
                            current_pos = cap.get(cv2.CAP_PROP_POS_FRAMES)
                            current_time = current_pos / fps if fps > 0 else 0
                            
                            # Check if we're at or past the end
                            if total_frames > 0 and current_pos >= total_frames - 1:
                                logger.info(f"📼 Reached end of VOD video ({current_time:.1f}s / {video_duration_sec:.1f}s)")
                                video_ended = True
                                break
                        
                        # Also check consecutive failures (for stream errors or actual end)
                        if consecutive_failures >= max_consecutive_failures:
                            # Video has ended (VOD) or stream died
                            logger.info(f"📼 Video stream ended (no frames for {consecutive_failures} attempts)")
                            video_ended = True
                            break
                        
                        time.sleep(0.1)
                        continue
                    
                    # Reset failure counter on successful read
                    consecutive_failures = 0
                    
                    # Write frame to output video
                    out.write(frame)
                    frame_count += 1
                    
                    # Small delay to prevent overwhelming the system
                    elapsed = time.time() - chunk_start
                    remaining_time = capture_interval - elapsed
                    if remaining_time > 0.1:
                        time.sleep(min(1/fps, remaining_time/2))
                
                # Release writer for this chunk
                out.release()
                out = None
                
                # Upload if we got any frames
                actual_duration = time.time() - chunk_start
                if frame_count > 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    logger.info(f"✓ Captured #{capture_count + 1}: {filename} ({frame_count} frames, {actual_duration:.2f}s)")
                    if self._upload_capture_chunk(
                        temp_path,
                        filename,
                        config,
                        bucket_name,
                        s3_prefix,
                        camera_id,
                        capture_type,
                        location,
                        scenario,
                        custom_prompt,
                        capture_count,
                        chunk_start_sec,
                        timestamp,
                        float(capture_interval),
                    ):
                        capture_count += 1
                    else:
                        logger.error(f"Failed to upload {filename} to S3")
                else:
                    logger.warning(f"Skipped chunk #{capture_count + 1} - no frames captured")
                    # Clean up empty file
                    try:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                        if temp_path in self.temp_files:
                            self.temp_files.remove(temp_path)
                    except:
                        pass
                
                # Check if video ended
                if video_ended:
                    logger.info(f"🏁 Video ended after {capture_count} chunks ({session_elapsed:.1f}s total)")
                    break
                
                # Small delay before next chunk
                if self.is_running:
                    time.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Error during continuous capture: {e}", exc_info=True)
        finally:
            # Clean up
            if out is not None:
                out.release()
            if cap is not None:
                cap.release()
            # Natural end / error / max-duration: clear session so /status and /start
            # do not stay stuck "active" until a manual /stop.
            self._clear_session(cleanup_temps=True)
            logger.info(f"Continuous capture stopped. Total captures: {capture_count}")

    def _capture_thread_alive(self) -> bool:
        return bool(self.capture_thread and self.capture_thread.is_alive())

    def _heal_stale_running_flag(self) -> None:
        """If is_running but the capture thread is dead, clear sticky session state."""
        if self.is_running and not self._capture_thread_alive():
            logger.warning(
                "Capture marked running but thread is not alive; clearing stale session"
            )
            self._clear_session(cleanup_temps=True)

    def _clear_session(self, *, cleanup_temps: bool = True) -> None:
        """Mark capture idle and optionally remove leftover temp files."""
        self.is_running = False
        self.current_config = None
        if not cleanup_temps:
            return
        for temp_file in list(self.temp_files):
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError:
                pass
        self.temp_files.clear()
    
    def start_capture(self, config):
        """Start the video capture process."""
        self._heal_stale_running_flag()
        if self.is_running:
            return False, "Capture is already running"
        
        # Validate required fields
        required_fields = ['youtube_url', 'access_key', 'secret_key', 's3_endpoint']
        for field in required_fields:
            if field not in config:
                return False, f"Missing required field: {field}"
        
        # Set default name if not provided
        if 'name' not in config:
            config['name'] = 'capture'

        config['stream_id'] = str(uuid.uuid4())
        config['stream_started_at'] = datetime.utcnow().isoformat() + 'Z'
        self._chunk_index = 0
        
        # Setup S3 client
        if not self.setup_s3_client(config['access_key'], config['secret_key'], config['s3_endpoint']):
            return False, "Failed to setup S3 client"
        
        # Test stream
        if not self.test_stream(config['youtube_url']):
            return False, "Stream test failed"
        
        # Start capture thread
        self.is_running = True
        self.current_config = config
        self.capture_thread = threading.Thread(target=self.continuous_capture, args=(config,))
        self.capture_thread.daemon = True
        self.capture_thread.start()
        
        return True, "Capture started successfully"
    
    def stop_capture(self):
        """Stop the video capture process."""
        was_stale = self.is_running and not self._capture_thread_alive()
        if was_stale:
            self._clear_session(cleanup_temps=True)
            return True, "Capture was already stopped (cleared stale session)"

        if not self.is_running:
            return False, "Capture is not running"
        
        self.is_running = False
        
        if self._capture_thread_alive():
            self.capture_thread.join(timeout=10)
        
        # Thread finally also clears; clear again here for the join-timeout case.
        self._clear_session(cleanup_temps=True)
        return True, "Capture stopped successfully"
    
    def _sanitize_config(self, config):
        """Sanitize config to hide sensitive information."""
        if not config:
            return None
        
        sanitized = config.copy()
        # Mask secret key
        if 'secret_key' in sanitized:
            sanitized['secret_key'] = '***REDACTED***'
        
        return sanitized
    
    def get_status(self):
        """Get current status."""
        self._heal_stale_running_flag()
        return {
            'is_running': self.is_running,
            'current_config': self._sanitize_config(self.current_config),
            'temp_files_count': len(self.temp_files)
        }

# Global service instance
video_service = VideoCaptureService()

@app.route('/start', methods=['POST'])
def start_capture():
    """Start video capture."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400
        
        success, message = video_service.start_capture(data)
        
        if success:
            return jsonify({'success': True, 'message': message}), 200
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Error in start endpoint: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_capture():
    """Stop video capture."""
    try:
        success, message = video_service.stop_capture()
        
        if success:
            return jsonify({'success': True, 'message': message}), 200
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Error in stop endpoint: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    """Simple health check endpoint."""
    return jsonify({'status': 'OK'}), 200

@app.route('/status', methods=['GET'])
def get_status():
    """Get detailed service status."""
    try:
        status = video_service.get_status()
        return jsonify({
            'success': True,
            'status': status,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Error in status endpoint: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    logger.info("Starting Video Capture Web Service")
    logger.info("yt-dlp JS runtimes: %s", " ".join(ytdlp_runtime_flags()) or "NONE")
    app.run(host='0.0.0.0', port=5000, debug=False)
