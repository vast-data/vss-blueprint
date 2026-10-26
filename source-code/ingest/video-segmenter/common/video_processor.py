import logging
import re
import subprocess
import tempfile
import os
import math
import gc
from typing import List, Tuple, Callable, Optional
from moviepy.editor import VideoFileClip

# Shortest kept tail, seconds.
MIN_TAIL_SEGMENT_SEC = 0.5
CHUNK_TRIM_TOLERANCE_SEC = 0.01

# First stored size on a Video stream line, plus the sample-aspect tag when present.
# 1280x720 has no tag. 1280x1080 [SAR 3:2 DAR 16:9] is anamorphic.
_DIM_SAR = re.compile(
    r"(\d{2,5})x(\d{2,5})(?:\s*\[SAR\s+(\d+):(\d+)\s+DAR\s+\d+:\d+\])?"
)
# idet prints a running total. The last line is the one that covers the sample.
_IDET_MULTI = re.compile(
    r"Multi frame detection:\s*TFF:\s*(\d+)\s+BFF:\s*(\d+)\s+Progressive:\s*(\d+)"
)


def _even(value: float) -> int:
    """Nearest even size. libx264 yuv420p rejects an odd side."""
    return max(2, int(round(float(value) / 2.0)) * 2)


def square_pixel_size(
    width: int, height: int, sar_num: int, sar_den: int
) -> Optional[Tuple[int, int]]:
    """Size to store so a player can treat every pixel as square.

    None means the stored frame is already square and should be left alone.
    """
    if width < 2 or height < 2 or sar_num <= 0 or sar_den <= 0 or sar_num == sar_den:
        return None
    target = (_even(width * sar_num / float(sar_den)), _even(height))
    if target == (width, height):
        return None
    return target


def parse_square_pixel_size(ffmpeg_stderr: str) -> Optional[Tuple[int, int]]:
    """Scale target from `ffmpeg -i` stderr. None when no stretch is required."""
    for line in ffmpeg_stderr.splitlines():
        if "Video:" not in line:
            continue
        match = _DIM_SAR.search(line)
        if not match:
            continue
        width, height = int(match.group(1)), int(match.group(2))
        if match.group(3):
            sar_num, sar_den = int(match.group(3)), int(match.group(4))
        else:
            sar_num, sar_den = 1, 1
        return square_pixel_size(width, height, sar_num, sar_den)
    return None


def parse_field_order(ffmpeg_stderr: str) -> Optional[str]:
    """'tff' or 'bff' when the sample is interlaced. None for progressive pictures.

    The container often leaves field order unset, so this reads idet's count.
    A still look at one frame hides it. Fast motion shows the two fields at once.
    """
    matches = _IDET_MULTI.findall(ffmpeg_stderr)
    if not matches:
        return None
    tff, bff, progressive = (int(part) for part in matches[-1])
    interlaced = tff + bff
    if interlaced < 8 or interlaced <= progressive:
        return None
    return "tff" if tff >= bff else "bff"


def output_video_filters(
    square_size: Optional[Tuple[int, int]],
    field_order: Optional[str],
) -> Optional[str]:
    """ffmpeg -vf chain. Deinterlace before any scale so the field lines stay paired."""
    filters = []
    if field_order == "tff":
        filters.append("yadif=0:0:0")
    elif field_order == "bff":
        filters.append("yadif=0:1:0")
    if square_size is not None:
        filters.append(
            f"scale={square_size[0]}:{square_size[1]}:flags=lanczos"
        )
        filters.append("setsar=1")
    if not filters:
        return None
    return ",".join(filters)


def _bundled_ffmpeg() -> Optional[str]:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        logging.warning("No bundled ffmpeg (%s)", exc)
        return None


def probe_square_pixel_size(path: str) -> Optional[Tuple[int, int]]:
    """Read the source sample aspect with the ffmpeg binary MoviePy bundles."""
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        logging.warning("No ffmpeg for aspect probe (%s); keeping stored pixels", exc)
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.warning("Aspect probe failed for %s: %s", path, exc)
        return None
    return parse_square_pixel_size(proc.stderr or "")


def probe_field_order(path: str) -> Optional[str]:
    """Sample the first pictures. Returns 'tff', 'bff', or None."""
    ffmpeg = _bundled_ffmpeg()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-i",
                path,
                "-vf",
                "idet",
                "-frames:v",
                "48",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.warning("Field-order probe failed for %s: %s", path, exc)
        return None
    return parse_field_order(proc.stderr or "")


class VideoProcessor:
    """Cut a video into segments."""
    
    def __init__(self, settings):
        self.settings = settings
        self.segment_duration = settings.segment_duration
        self.output_codec = settings.output_codec
        self.output_format = settings.output_format
    
    def process_video_segments(
        self,
        video_content: bytes,
        original_filename: str,
        upload_callback: Optional[Callable] = None,
        max_duration_sec: Optional[float] = None,
    ) -> List[Tuple[bytes, int, int, float, float, float]]:
        """Returns (bytes, number, total, duration, start, end) per segment."""
        segments = []
        segments_written = 0
        
        with tempfile.NamedTemporaryFile(suffix=f".{self.output_format}", delete=False) as temp_input:
            temp_input.write(video_content)
            temp_input.flush()
            temp_input_path = temp_input.name
        
        try:
            logging.info(f"Loading video file: {original_filename}")
            probe = VideoFileClip(temp_input_path)
            probed_duration = probe.duration
            probe.close()
            del probe
            gc.collect()

            total_duration = probed_duration
            if max_duration_sec is not None and max_duration_sec > 0:
                total_duration = min(probed_duration, float(max_duration_sec))
                if total_duration < probed_duration - CHUNK_TRIM_TOLERANCE_SEC:
                    logging.info(
                        "Capping segmentation at chunk_duration_sec=%.3fs (probed=%.3fs)",
                        max_duration_sec,
                        probed_duration,
                    )

            total_segments = math.ceil(total_duration / self.segment_duration)
            if total_segments > 1:
                last_start = (total_segments - 1) * self.segment_duration
                tail = total_duration - last_start
                if tail < MIN_TAIL_SEGMENT_SEC:
                    total_segments -= 1

            # MoviePy writes the coded pixel grid and drops the sample-aspect tag.
            # Stretch to square pixels here so Search and Explore show 16:9.
            square_size = probe_square_pixel_size(temp_input_path)
            if square_size is not None:
                logging.info(
                    "Storing square pixels at %dx%d (source sample aspect is not 1:1)",
                    square_size[0],
                    square_size[1],
                )
            field_order = probe_field_order(temp_input_path)
            if field_order is not None:
                logging.info(
                    "Separating %s fields before encode so fast pans stay a single picture",
                    field_order,
                )
            video_filters = output_video_filters(square_size, field_order)

            logging.info(
                f"Video duration: {total_duration:.2f}s, creating {total_segments} segments "
                f"of {self.segment_duration}s each"
            )

            # One clip per segment.
            for i in range(total_segments):
                start_time = i * self.segment_duration
                if i == total_segments - 1:
                    end_time = total_duration
                else:
                    end_time = min((i + 1) * self.segment_duration, total_duration)
                segment_duration = end_time - start_time

                logging.info(
                    f"Creating segment {i + 1}/{total_segments}: {start_time:.2f}s - {end_time:.2f}s "
                    f"(duration: {segment_duration:.2f}s)"
                )

                clip = None
                segment_clip = None
                temp_output_path = None

                try:
                    clip = VideoFileClip(temp_input_path)
                    segment_clip = clip.subclip(start_time, end_time)

                    with tempfile.NamedTemporaryFile(suffix=f".{self.output_format}", delete=False) as temp_output:
                        temp_output_path = temp_output.name

                    # Write segment to file (preserve AAC audio; CRF/preset from settings)
                    has_audio = segment_clip.audio is not None
                    ffmpeg_params = [
                        "-crf",
                        str(self.settings.video_crf),
                        "-movflags",
                        "+faststart",
                    ]
                    if video_filters is not None:
                        ffmpeg_params = ["-vf", video_filters, *ffmpeg_params]
                    write_kw = {
                        "codec": self.output_codec,
                        "audio": has_audio,
                        "logger": None,
                        "preset": self.settings.ffmpeg_preset,
                        "ffmpeg_params": ffmpeg_params,
                    }
                    if has_audio:
                        write_kw["audio_codec"] = "aac"
                        write_kw["audio_bitrate"] = self.settings.audio_bitrate
                    segment_clip.write_videofile(temp_output_path, **write_kw)
                    segments_written += 1

                    with open(temp_output_path, "rb") as f:
                        segment_content = f.read()

                    segment_info = (
                        segment_content,
                        i + 1,
                        total_segments,
                        segment_duration,
                        start_time,
                        end_time,
                    )

                    logging.info(
                        f"Created segment {i + 1}/{total_segments}: {len(segment_content)} bytes, "
                        f"duration: {segment_duration:.2f}s"
                    )

                    if upload_callback:
                        try:
                            upload_callback(segment_info, original_filename)
                            logging.info(f"Uploaded segment {i + 1}/{total_segments}")
                            del segment_content
                        except Exception as e:
                            logging.error(f"Failed to upload segment {i + 1}/{total_segments}: {e}")
                    else:
                        segments.append(segment_info)

                except Exception:
                    raise
                finally:
                    if temp_output_path and os.path.exists(temp_output_path):
                        os.unlink(temp_output_path)
                    if segment_clip is not None:
                        try:
                            segment_clip.close()
                        except Exception:
                            pass
                    if clip is not None:
                        try:
                            clip.close()
                        except Exception:
                            pass
                    gc.collect()
            
        finally:
            # Clean up input file
            if os.path.exists(temp_input_path):
                os.unlink(temp_input_path)
        
        logging.info(
            f"Successfully encoded {segments_written} video segment(s)"
            + (" (uploads via callback)" if upload_callback else "")
        )
        return segments
    
    def get_video_info(self, video_content: bytes) -> Tuple[float, int, int]:
        """(duration, width, height)."""
        with tempfile.NamedTemporaryFile(suffix=f".{self.output_format}", delete=False) as temp_input:
            temp_input.write(video_content)
            temp_input.flush()
            temp_input_path = temp_input.name
        
        try:
            clip = VideoFileClip(temp_input_path)
            duration = clip.duration
            width = clip.w
            height = clip.h
            clip.close()
            
            logging.info(f"Video info - Duration: {duration:.2f}s, Resolution: {width}x{height}")
            return duration, width, height
            
        finally:
            # Clean up input file
            if os.path.exists(temp_input_path):
                os.unlink(temp_input_path)

