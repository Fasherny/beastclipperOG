#!/usr/bin/env python3
"""
Twitch-specific stream management for BeastClipper
Uses streamlink for recording and FFmpeg for clip creation
"""

import os
import time
import logging
import subprocess
import threading
import json
import shutil
import traceback
from collections import deque
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal, QTimer, QObject

# Configure logger
logger = logging.getLogger("BeastClipper")

# Find FFmpeg executables - improved detection
def find_ffmpeg():
    """Find FFmpeg and FFprobe executables."""
    potential_paths = [
        # Windows common locations
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Users\Ahsan Ali\Downloads\ffmpeg-7.1.1-essentials_build\bin\ffmpeg.exe",
        os.path.join(os.environ.get('PROGRAMFILES', 'C:\\Program Files'), "FFmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), "FFmpeg", "bin", "ffmpeg.exe"),
        # Add more potential paths here
    ]
    
    # Check if in PATH
    try:
        result = subprocess.run(["where", "ffmpeg"], capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split('\n')[0], result.stdout.strip().split('\n')[0].replace('ffmpeg', 'ffprobe')
    except:
        pass
    
    # Check potential paths
    for path in potential_paths:
        if os.path.exists(path):
            probe_path = path.replace('ffmpeg', 'ffprobe')
            return path, probe_path
    
    # If we've reached here, return default and log warning
    logger.warning("FFmpeg not found in common locations. Using 'ffmpeg' command directly.")
    return "ffmpeg", "ffprobe"

# Set FFmpeg paths
FFMPEG_PATH, FFPROBE_PATH = find_ffmpeg()


class ProgressEmitter(QObject):
    """Helper class to emit progress signals from non-QThread contexts"""
    progress = pyqtSignal(int, int)  # current, total
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def emit_progress(self, current, total):
        self.progress.emit(current, total)
    
    def emit_status(self, message):
        self.status.emit(message)
    
    def emit_error(self, message):
        self.error.emit(message)


class StreamBuffer(QThread):
    """Manages Twitch stream buffering using streamlink."""
    
    # Signals
    buffer_progress = pyqtSignal(int, int)  # current, total
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    stream_info_updated = pyqtSignal(dict)
    
    def __init__(self, stream_url, buffer_duration=300, resolution="best", 
                 segment_length=10, temp_manager=None):
        """Initialize Twitch stream buffer.
        
        Args:
            stream_url: Twitch stream URL (e.g., https://twitch.tv/username)
            buffer_duration: Maximum buffer duration in seconds
            resolution: Stream quality (best, 1080p, 720p, etc.)
            segment_length: Length of each buffer segment
            temp_manager: Temporary file manager instance
        """
        super().__init__()
        
        self.stream_url = self._format_twitch_url(stream_url)
        self.buffer_duration = buffer_duration
        self.resolution = resolution
        self.segment_length = segment_length
        self.temp_manager = temp_manager
        
        # Buffer management
        self.segments = deque()
        self.segment_lock = threading.RLock()  # Using RLock instead of Lock to avoid deadlocks
        self.running = False
        self.process = None
        
        # Progress emitter for background threads
        self.progress_emitter = ProgressEmitter()
        self.progress_emitter.progress.connect(self.buffer_progress)
        self.progress_emitter.status.connect(self.status_update)
        self.progress_emitter.error.connect(self.error_occurred)
        
        # Temporary directory
        self.temp_dir = os.path.join(
            os.path.expanduser("~"), 
            ".beastclipper", 
            "buffer", 
            datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # Error handling
        self.consecutive_errors = 0
        self.max_errors = 3
        
        # Add watchdog timer to avoid freezes
        self.watchdog_timer = QTimer()
        self.watchdog_timer.timeout.connect(self.check_health)
        self.last_activity = time.time()
        
        logger.info(f"StreamBuffer initialized for Twitch: {self.stream_url}")
    
    def check_health(self):
        """Watchdog function to check if buffer is still running properly"""
        if not self.running:
            return
            
        # If no activity for 30 seconds, something's wrong
        if time.time() - self.last_activity > 30:
            logger.warning("Buffer appears to be stalled - attempting recovery")
            self.consecutive_errors += 1
            
            if self.consecutive_errors >= self.max_errors:
                self.progress_emitter.emit_error("Buffer stalled - stopping buffer")
                self.stop()
    
    def _format_twitch_url(self, url):
        """Format Twitch URL to ensure compatibility."""
        try:
            url = url.strip().lower()
            
            # Handle various Twitch URL formats
            if "twitch.tv" not in url:
                # Assume it's just a channel name
                return f"https://twitch.tv/{url}"
            
            # Extract channel name from URL
            if "/videos/" in url:
                raise ValueError("VOD URLs are not supported. Please use a live stream URL.")
            
            # Clean up the URL
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            
            # Extract just the channel part
            parts = url.split("twitch.tv/")
            if len(parts) > 1:
                channel = parts[1].split('/')[0].split('?')[0]
                return f"https://twitch.tv/{channel}"
            
            return url
        except Exception as e:
            logger.error(f"Error formatting URL: {e}")
            return url  # Return original if any error
    
    def run(self):
        """Main buffer thread execution."""
        self.running = True
        self.consecutive_errors = 0
        self.last_activity = time.time()
        
        # Start the watchdog timer
        self.watchdog_timer.start(5000)  # Check every 5 seconds
        
        try:
            # Validate stream
            self.status_update.emit("Checking Twitch stream...")
            
            if not self._validate_stream():
                self.error_occurred.emit("Stream is offline or invalid. Please check the channel name.")
                return
            
            # Get stream information
            self.status_update.emit("Getting stream information...")
            stream_info = self._get_stream_info()
            
            if stream_info:
                self.stream_info_updated.emit(stream_info)
            
            # Start buffering
            self.status_update.emit("Starting buffer...")
            segment_index = 0
            
            # Use a separate recording thread to avoid blocking the QThread
            self.recorder_thread = threading.Thread(target=self._buffer_loop, args=(segment_index,))
            self.recorder_thread.daemon = True
            self.recorder_thread.start()
            
            # This thread will now just monitor and wait
            while self.running:
                time.sleep(0.1)  # Reduce CPU usage
                
        except Exception as e:
            logger.error(f"Buffer thread error: {traceback.format_exc()}")
            self.error_occurred.emit(f"Buffer error: {str(e)}")
        
        finally:
            self.running = False
            self._cleanup()
            self.watchdog_timer.stop()
            self.status_update.emit("Buffer stopped")
    
    def _buffer_loop(self, segment_index):
        """Main recording loop in a separate thread"""
        try:
            while self.running:
                try:
                    # Create segment filename
                    segment_file = os.path.join(
                        self.temp_dir, 
                        f"segment_{segment_index:06d}.ts"
                    )
                    
                    # Record segment using streamlink
                    success = self._record_segment(segment_file)
                    
                    if success and os.path.exists(segment_file) and os.path.getsize(segment_file) > 1000:
                        # Update activity timestamp
                        self.last_activity = time.time()
                        
                        # Add to buffer
                        with self.segment_lock:
                            self.segments.append({
                                'file': segment_file,
                                'index': segment_index,
                                'timestamp': time.time(),
                                'duration': self.segment_length
                            })
                            
                            # Remove old segments
                            self._prune_old_segments()
                        
                        # Update progress
                        current_duration = self._get_buffer_duration()
                        self.progress_emitter.emit_progress(int(current_duration), self.buffer_duration)
                        
                        # Reset error count on success
                        self.consecutive_errors = 0
                        
                        # Status update (less frequently to reduce overhead)
                        if segment_index % 5 == 0:
                            self.progress_emitter.emit_status(
                                f"Buffering: {int(current_duration)}s / {self.buffer_duration}s"
                            )
                        
                        segment_index += 1
                    
                    else:
                        # Recording failed
                        self.consecutive_errors += 1
                        logger.warning(f"Failed to record segment {segment_index}")
                        
                        if self.consecutive_errors >= self.max_errors:
                            self.progress_emitter.emit_error("Stream may have ended or connection lost.")
                            break
                        
                        # Wait before retry
                        time.sleep(2)
                
                except Exception as e:
                    logger.error(f"Error in buffer loop: {e}")
                    self.consecutive_errors += 1
                    
                    if self.consecutive_errors >= self.max_errors:
                        self.progress_emitter.emit_error(f"Critical error: {str(e)}")
                        break
                    
                    time.sleep(2)
                    
        except Exception as e:
            logger.error(f"Buffer thread fatal error: {traceback.format_exc()}")
            self.progress_emitter.emit_error(f"Buffer fatal error: {str(e)}")
    
    def _prune_old_segments(self):
        """Remove old segments when buffer is full"""
        try:
            # Must be called with segment_lock held
            while self._get_buffer_duration() > self.buffer_duration:
                old_segment = self.segments.popleft()
                try:
                    # Check if file exists before trying to remove
                    if os.path.exists(old_segment['file']):
                        os.remove(old_segment['file'])
                except Exception as e:
                    logger.error(f"Error removing old segment: {e}")
        except Exception as e:
            logger.error(f"Error pruning segments: {e}")
    
    def _validate_stream(self):
        """Check if the Twitch stream is live."""
        try:
            # Simplified streamlink command with only essential parameters
            cmd = ["streamlink", self.stream_url, "--json"]
            
            # Use a timeout to avoid hanging
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    if "streams" in data and data["streams"]:
                        logger.info(f"Stream is live with qualities: {list(data['streams'].keys())}")
                        return True
                except json.JSONDecodeError:
                    # JSON parsing failed, try alternative approach
                    pass
            
            # Alternative check
            stream_url_cmd = ["streamlink", self.stream_url, "best", "--stream-url"]
            try:
                result = subprocess.run(stream_url_cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and "http" in result.stdout:
                    logger.info("Stream is live (verified via --stream-url)")
                    return True
            except:
                pass
            
            # Check stderr for specific messages
            if result.stderr and "error: No playable streams found" in result.stderr:
                logger.warning("Stream is offline or unavailable")
            
            return False
        
        except subprocess.TimeoutExpired:
            logger.error("Stream validation timeout")
            return False
        except Exception as e:
            logger.error(f"Error validating stream: {e}")
            return False
    
    def _get_stream_info(self):
        """Get information about the Twitch stream."""
        try:
            cmd = ["streamlink", "--json", self.stream_url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    
                    stream_info = {
                        'url': self.stream_url,
                        'channel': self.stream_url.split('/')[-1],
                        'qualities': list(data.get('streams', {}).keys()),
                        'title': data.get('title', 'Unknown')
                    }
                    
                    return stream_info
                except json.JSONDecodeError:
                    logger.error("Invalid JSON from streamlink")
            
            # Fallback approach if json fails
            if result.returncode != 0:
                cmd = ["streamlink", self.stream_url, "best", "--stream-url"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    return {
                        'url': self.stream_url,
                        'channel': self.stream_url.split('/')[-1],
                        'qualities': ['best'],
                        'title': 'Unknown'
                    }
            
            return None
        
        except Exception as e:
            logger.error(f"Error getting stream info: {e}")
            return None
    
    def _record_segment(self, output_file):
        """Record a single segment using streamlink with compatibility for different versions."""
        try:
            # Map resolution to streamlink quality
            quality_map = {
                "1080p": "1080p,1080p60,best",
                "720p": "720p,720p60,1080p,best",
                "480p": "480p,720p,best",
                "360p": "360p,480p,720p,best",
                "best": "best"
            }
            
            quality = quality_map.get(self.resolution, "best")
            
            # Streamlink command - minimal version that works with most Streamlink versions
            cmd = [
                "streamlink",
                self.stream_url,
                quality,
                "-o", output_file,
                "--force"  # Overwrite existing file
            ]
            
            # Add hls-duration parameter conditionally to make clip last only segment_length
            # Some versions use different parameter formats
            try:
                # First try with --hls-duration
                duration_cmd = cmd.copy()
                duration_cmd.extend(["--hls-duration", str(self.segment_length)])
                
                # Execute command with a timeout slightly longer than segment length
                result = subprocess.run(
                    duration_cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.segment_length * 2
                )
                
                # Check if recording was successful
                if result.returncode == 0 and os.path.exists(output_file):
                    file_size = os.path.getsize(output_file)
                    
                    if file_size > 1000:  # At least 1KB
                        logger.debug(f"Recorded segment: {output_file} ({file_size} bytes)")
                        return True
                
                # If this fails, it may be due to parameter incompatibility
                if "error: unrecognized arguments" in (result.stderr or ""):
                    # Try without the hls-duration parameter
                    simple_cmd = cmd.copy()
                    
                    # Use process control to limit recording time instead
                    proc = subprocess.Popen(
                        simple_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    
                    # Wait for segment_length and then terminate
                    try:
                        time.sleep(self.segment_length)
                        proc.terminate()
                        proc.wait(timeout=5)
                        
                        # Check result
                        if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
                            logger.debug(f"Recorded segment via process control: {output_file}")
                            return True
                    except:
                        if proc:
                            proc.kill()
            
            except subprocess.TimeoutExpired:
                logger.error("Recording timeout")
                return False
            
            return False
        
        except Exception as e:
            logger.error(f"Error recording segment: {e}")
            return False
    
    def _get_buffer_duration(self):
        """Get current buffer duration in seconds."""
        # No need for lock here as a rough estimate is fine
        return len(self.segments) * self.segment_length
    
    def get_buffer_status(self):
        """Get buffer status information."""
        with self.segment_lock:
            return {
                'segments': len(self.segments),
                'duration': self._get_buffer_duration(),
                'max_duration': self.buffer_duration,
                'segment_length': self.segment_length
            }
    
    def get_segments_for_clip(self, start_time_ago, duration):
        """Get segments for creating a clip."""
        with self.segment_lock:
            if not self.segments:
                return []
            
            # Calculate segment indices
            current_time = time.time()
            
            # Find segments within the time range
            clip_segments = []
            
            for segment in self.segments:
                segment_age = current_time - segment['timestamp']
                
                # Check if segment is within the requested time range
                if segment_age <= start_time_ago and segment_age >= (start_time_ago - duration):
                    clip_segments.append(segment)
            
            # Sort by timestamp
            clip_segments.sort(key=lambda x: x['timestamp'])
            
            logger.info(f"Found {len(clip_segments)} segments for clip")
            return clip_segments
    
    def stop(self):
        """Stop the buffer thread."""
        self.running = False
        
        # Stop watchdog
        if hasattr(self, 'watchdog_timer'):
            self.watchdog_timer.stop()
        
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                self.process.kill()
    
    def _cleanup(self):
        """Clean up resources."""
        try:
            # Stop any running process
            if self.process and self.process.poll() is None:
                self.process.kill()
            
            # Register temp directory for cleanup
            if self.temp_manager:
                self.temp_manager.register_temp_file(self.temp_dir, lifetime=3600)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


class StreamMonitor(QThread):
    """Monitors configured Twitch streams for status changes."""
    
    # Signals
    stream_live = pyqtSignal(str)
    stream_offline = pyqtSignal(str)
    status_update = pyqtSignal(str)
    
    def __init__(self, config_manager):
        """Initialize stream monitor."""
        super().__init__()
        
        self.config_manager = config_manager
        self.running = False
        self.monitored_streams = self.config_manager.get("monitored_streams", [])
        self.stream_status = {}  # URL -> is_live
        self.check_interval = 60  # seconds
    
    def run(self):
        """Monitor thread execution."""
        self.running = True
        
        while self.running:
            try:
                for stream_url in self.monitored_streams:
                    try:
                        # Ensure it's a Twitch URL
                        if "twitch.tv" not in stream_url:
                            stream_url = f"https://twitch.tv/{stream_url}"
                        
                        is_live = self._check_stream_status(stream_url)
                        
                        # Check for status change
                        if stream_url in self.stream_status:
                            was_live = self.stream_status[stream_url]
                            
                            if is_live and not was_live:
                                self.stream_live.emit(stream_url)
                                logger.info(f"Stream went live: {stream_url}")
                            elif not is_live and was_live:
                                self.stream_offline.emit(stream_url)
                                logger.info(f"Stream went offline: {stream_url}")
                        
                        self.stream_status[stream_url] = is_live
                    
                    except Exception as e:
                        logger.error(f"Error checking stream {stream_url}: {e}")
                
                # Wait before next check
                for _ in range(self.check_interval):
                    if not self.running:
                        break
                    time.sleep(1)
            
            except Exception as e:
                logger.error(f"Monitor thread error: {e}")
                time.sleep(10)
    
    def _check_stream_status(self, stream_url):
        """Check if a Twitch stream is live."""
        try:
            # Simple streamlink command that works with all versions
            cmd = ["streamlink", stream_url, "best", "--stream-url"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout and "http" in result.stdout:
                return True
            
            # If that didn't work, try the JSON approach as backup
            cmd = ["streamlink", "--json", stream_url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    return bool(data.get("streams"))
                except:
                    pass
            
            return False
        
        except Exception as e:
            logger.error(f"Error checking stream status: {e}")
            return False
    
    def add_stream(self, stream_url):
        """Add a Twitch stream to monitor."""
        # Format the URL
        if "twitch.tv" not in stream_url:
            stream_url = f"https://twitch.tv/{stream_url}"
        
        if stream_url not in self.monitored_streams:
            self.monitored_streams.append(stream_url)
            self.config_manager.set("monitored_streams", self.monitored_streams)
            logger.info(f"Added stream to monitor: {stream_url}")
    
    def remove_stream(self, stream_url):
        """Remove a stream from monitoring."""
        if stream_url in self.monitored_streams:
            self.monitored_streams.remove(stream_url)
            self.config_manager.set("monitored_streams", self.monitored_streams)
            
            if stream_url in self.stream_status:
                del self.stream_status[stream_url]
            
            logger.info(f"Removed stream from monitor: {stream_url}")
    
    def stop(self):
        """Stop the monitor thread."""
        self.running = False


class ClipCreator(QThread):
    """Creates clips from Twitch stream buffer."""
    
    # Signals
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    clip_created = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, stream_buffer, start_time_ago, duration, output_path, format_type="mp4"):
        """Initialize clip creator.
        
        Args:
            stream_buffer: StreamBuffer instance
            start_time_ago: How many seconds ago to start the clip
            duration: Clip duration in seconds
            output_path: Output file path
            format_type: Output format (mp4, webm, mkv)
        """
        super().__init__()
        
        self.stream_buffer = stream_buffer
        self.start_time_ago = start_time_ago
        self.duration = duration
        self.output_path = output_path
        self.format_type = format_type
        
        self.process = None
    
    def run(self):
        """Create the clip."""
        try:
            self.status_update.emit("Getting segments for clip...")
            
            # Get segments for the requested time range
            segments = self.stream_buffer.get_segments_for_clip(
                self.start_time_ago, 
                self.duration
            )
            
            if not segments:
                self.error_occurred.emit("No segments available for the requested time range")
                return
            
            # Create temporary file list
            file_list_path = os.path.join(
                os.path.dirname(segments[0]['file']), 
                f"clip_list_{int(time.time())}.txt"
            )
            
            with open(file_list_path, 'w') as f:
                for segment in segments:
                    # FFmpeg concat format - escape any special characters in path
                    safe_path = segment['file'].replace('\\', '\\\\').replace("'", "\\'")
                    f.write(f"file '{safe_path}'\n")
            
            self.status_update.emit("Creating clip...")
            
            # Ensure output directory exists
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            
            # Build FFmpeg command
            cmd = [
                FFMPEG_PATH,
                "-y",  # Overwrite output
                "-f", "concat",
                "-safe", "0",
                "-i", file_list_path,
                "-t", str(self.duration),  # Limit duration
            ]
            
            # Add codec settings based on format
            if self.format_type == "mp4":
                cmd.extend([
                    "-c:v", "libx264",  # H.264 video
                    "-preset", "fast",   # Fast encoding
                    "-crf", "23",        # Quality (lower = better)
                    "-c:a", "aac",       # AAC audio
                    "-b:a", "128k",      # Audio bitrate
                    "-movflags", "+faststart"  # Optimize for streaming
                ])
            else:
                # For other formats, just copy
                cmd.extend(["-c", "copy"])
            
            cmd.append(self.output_path)
            
            logger.info(f"Creating clip with command: {' '.join(cmd)}")
            
            # Execute FFmpeg
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            # Monitor progress 
            duration_seconds = self.duration
            progress_found = False
            
            for line in self.process.stderr:
                # Parse FFmpeg progress
                if "time=" in line:
                    progress_found = True
                    try:
                        time_str = line.split("time=")[1].split()[0]
                        # Parse time format HH:MM:SS.ss
                        parts = time_str.split(':')
                        if len(parts) == 3:
                            hours = int(parts[0])
                            minutes = int(parts[1])
                            seconds = float(parts[2])
                            
                            current_seconds = hours * 3600 + minutes * 60 + seconds
                            progress = min(int((current_seconds / duration_seconds) * 100), 100)
                            
                            self.progress_update.emit(progress)
                    except:
                        pass
            
            # If no progress was reported, send 50% to indicate work in progress
            if not progress_found:
                self.progress_update.emit(50)
            
            # Wait for completion
            return_code = self.process.wait()
            
            # Clean up
            try:
                os.remove(file_list_path)
            except:
                pass
            
            # Check result
            if return_code == 0 and os.path.exists(self.output_path):
                file_size = os.path.getsize(self.output_path)
                
                if file_size > 10000:  # At least 10KB
                    self.progress_update.emit(100)
                    self.status_update.emit("Clip created successfully")
                    self.clip_created.emit(self.output_path)
                    logger.info(f"Clip created: {self.output_path} ({file_size} bytes)")
                else:
                    self.error_occurred.emit("Created clip is too small")
            else:
                error_output = ""
                if self.process.stderr:
                    error_output = self.process.stderr.read()
                self.error_occurred.emit(f"FFmpeg failed with code {return_code}: {error_output[:200]}")
        
        except Exception as e:
            logger.error(f"Clip creation error: {traceback.format_exc()}")
            self.error_occurred.emit(f"Failed to create clip: {str(e)}")
        
        finally:
            self.process = None
    
    def stop(self):
        """Stop clip creation."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                self.process.kill()


class ClipEditor(QThread):
    """Edits existing video clips."""
    
    # Signals
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    edit_complete = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, input_path, output_path, edits):
        """Initialize clip editor.
        
        Args:
            input_path: Input video file path
            output_path: Output video file path
            edits: Dictionary of edit operations
                - trim_start: Start time in seconds
                - trim_end: End time in seconds
                - text_overlay: Text to overlay
                - speed: Playback speed multiplier
        """
        super().__init__()
        
        self.input_path = input_path
        self.output_path = output_path
        self.edits = edits
        self.process = None
    
    def run(self):
        """Perform video editing."""
        try:
            self.status_update.emit("Starting video edit...")
            
            # Build FFmpeg command
            cmd = [FFMPEG_PATH, "-y", "-i", self.input_path]
            
            # Apply trim
            if "trim_start" in self.edits:
                cmd.extend(["-ss", str(self.edits["trim_start"])])
            
            if "trim_end" in self.edits:
                cmd.extend(["-to", str(self.edits["trim_end"])])
            
            # Build filter complex
            filters = []
            
            # Text overlay
            if "text_overlay" in self.edits:
                text = self.edits["text_overlay"]
                filters.append(
                    f"drawtext=text='{text}':x=(w-text_w)/2:y=h-50:"
                    f"fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5"
                )
            
            # Speed change
            if "speed" in self.edits:
                speed = self.edits["speed"]
                filters.append(f"setpts={1/speed}*PTS")
            
            # Apply filters
            if filters:
                cmd.extend(["-vf", ",".join(filters)])
            
            # Output settings
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                self.output_path
            ])
            
            # Execute FFmpeg
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            # Monitor progress (simplified with intermediate updates)
            progress = 0
            self.progress_update.emit(progress)
            
            while self.process.poll() is None:
                if progress < 95:
                    progress += 5
                    self.progress_update.emit(progress)
                time.sleep(0.5)
            
            # Check result
            return_code = self.process.returncode
            
            if return_code == 0 and os.path.exists(self.output_path):
                self.progress_update.emit(100)
                self.status_update.emit("Edit complete")
                self.edit_complete.emit(self.output_path)
            else:
                error_output = ""
                if self.process.stderr:
                    error_output = self.process.stderr.read()
                self.error_occurred.emit(f"Edit failed with code {return_code}: {error_output[:200]}")
        
        except Exception as e:
            logger.error(f"Edit error: {traceback.format_exc()}")
            self.error_occurred.emit(f"Edit failed: {str(e)}")
        
        finally:
            self.process = None
    
    def stop(self):
        """Stop the editing process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                self.process.kill()
