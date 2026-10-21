import logging
import tempfile
import os
import math
import gc
from typing import List, Tuple, Callable, Optional
from moviepy.editor import VideoFileClip

# Shortest kept tail, seconds.
MIN_TAIL_SEGMENT_SEC = 0.5
CHUNK_TRIM_TOLERANCE_SEC = 0.01


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
                    write_kw = {
                        "codec": self.output_codec,
                        "audio": has_audio,
                        "logger": None,
                        "preset": self.settings.ffmpeg_preset,
                        "ffmpeg_params": [
                            "-crf",
                            str(self.settings.video_crf),
                            "-movflags",
                            "+faststart",
                        ],
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

