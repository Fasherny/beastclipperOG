#!/usr/bin/env python3
"""
Content analysis module for BeastClipper
Handles viral moment detection and chat activity monitoring
"""

import time
import logging
import re
import subprocess
from collections import deque

import cv2
import numpy as np
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from PyQt6.QtCore import QThread, pyqtSignal

# Configure logger
logger = logging.getLogger("BeastClipper")


# ======================
# Content Analyzer
# ======================

class ContentAnalyzer(QThread):
    """Analyzes video content to detect potentially viral moments."""
    
    analysis_complete = pyqtSignal(list)  # List of potential viral moments (start_time, end_time, score)
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    
    def __init__(self, video_file, sensitivity=0.7, min_clip_length=10, max_clip_length=60):
        """
        Initialize content analyzer for viral moment detection.
        
        Args:
            video_file: Path to video file to analyze
            sensitivity: Detection sensitivity (0.0 to 1.0)
            min_clip_length: Minimum viral clip length in seconds
            max_clip_length: Maximum viral clip length in seconds
        """
        super().__init__()
        self.video_file = video_file
        self.sensitivity = sensitivity
        self.min_clip_length = min_clip_length
        self.max_clip_length = max_clip_length
    
    def run(self):
        try:
            self.status_update.emit("Loading video for analysis...")
            self.progress_update.emit(5)
            
            # Open video file
            cap = cv2.VideoCapture(self.video_file)
            if not cap.isOpened():
                raise Exception("Could not open video file")
            
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                fps = 30  # Default FPS if not detected
            
            video_duration = total_frames / fps
            logger.info(f"Analyzing video: {total_frames} frames, {fps} FPS, {video_duration:.2f} seconds")
            
            # Initialize variables for analysis
            self.status_update.emit("Analyzing video for viral moments...")
            prev_frame = None
            frame_diff_history = deque(maxlen=int(fps * 5))  # Store 5 seconds of frame differences
            audio_peaks = []
            scene_changes = []
            interesting_moments = []
            
            # Process video frames
            frame_count = 0
            skip_frames = max(1, int(fps / 10))  # Analyze at most 10 frames per second
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                # Process only every Nth frame for performance
                if frame_count % skip_frames != 0:
                    frame_count += 1
                    continue
                
                # Update progress
                progress = min(int((frame_count / total_frames) * 80) + 5, 85)
                if frame_count % (skip_frames * 10) == 0:
                    self.progress_update.emit(progress)
                    self.status_update.emit(f"Analyzing frame {frame_count}/{total_frames}...")
                
                # Convert to grayscale for analysis
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                
                # Detect scene changes
                if prev_frame is not None:
                    # Calculate frame difference
                    diff = cv2.absdiff(gray, prev_frame)
                    diff_mean = np.mean(diff)
                    frame_diff_history.append(diff_mean)
                    
                    # Check for scene change
                    if len(frame_diff_history) >= 10:
                        avg_diff = sum(frame_diff_history) / len(frame_diff_history)
                        if diff_mean > avg_diff * (1 + self.sensitivity):
                            timestamp = frame_count / fps
                            scene_changes.append((timestamp, diff_mean / 255.0))
                            logger.debug(f"Detected scene change at {timestamp:.2f}s (score: {diff_mean / 255.0:.2f})")
                    
                    # Check for motion (interesting moments)
                    if frame_count % (skip_frames * 5) == 0:  # Less frequent check
                        # Apply thresholding to find areas with significant change
                        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        
                        # Look for large motion areas (potentially interesting)
                        significant_motion = False
                        for contour in contours:
                            area = cv2.contourArea(contour)
                            if area > (frame.shape[0] * frame.shape[1] * 0.05):  # At least 5% of frame
                                significant_motion = True
                                break
                        
                        if significant_motion:
                            timestamp = frame_count / fps
                            motion_score = sum(cv2.contourArea(c) for c in contours) / (frame.shape[0] * frame.shape[1])
                            interesting_moments.append((timestamp, min(motion_score, 1.0)))
                            logger.debug(f"Detected significant motion at {timestamp:.2f}s (score: {motion_score:.2f})")
                
                # Store current frame for next iteration
                prev_frame = gray
                frame_count += 1
            
            # Extract audio for analysis
            self.status_update.emit("Analyzing audio...")
            self.progress_update.emit(90)
            
            # Use ffmpeg to extract audio levels
            audio_analysis_cmd = [
                "ffmpeg",
                "-i", self.video_file,
                "-af", "loudnorm=print_format=json,volumedetect",
                "-f", "null", "-"
            ]
            
            result = subprocess.run(audio_analysis_cmd, capture_output=True, text=True)
            
            # Parse audio level information from output
            output = result.stderr
            level_pattern = re.compile(r"max_volume: ([-\d.]+) dB")
            matches = level_pattern.findall(output)
            
            # Also look for "silence" sections (could be interesting transition points)
            silence_pattern = re.compile(r"silence_start: ([\d.]+)")
            silence_matches = silence_pattern.findall(output)
            
            # Process audio results
            if matches:
                # Convert dB levels to normalized scores and create timestamp mapping
                # This is simplified; ideally we'd have timestamps for each measurement
                if len(matches) > 1:
                    # Estimate timestamps by distributing evenly across the video
                    step = video_duration / len(matches)
                    for i, level_db in enumerate(matches):
                        timestamp = i * step
                        # Convert dB to a score (normalize -20dB to 0dB range to 0-1)
                        db = float(level_db)
                        score = min(max((db + 30) / 30, 0), 1)  # Normalize to 0-1
                        if score > 0.6:  # Only track significant audio peaks
                            audio_peaks.append((timestamp, score))
            
            # Add silence start points (could be interesting transitions)
            for silence_time in silence_matches:
                timestamp = float(silence_time)
                audio_peaks.append((timestamp, 0.7))  # Moderate score for silence transitions
            
            # Combine scene changes, motion and audio peaks to identify potential viral moments
            self.status_update.emit("Identifying potential viral moments...")
            self.progress_update.emit(95)
            
            viral_moments = []
            
            # Process scene changes as primary indicators
            for timestamp, score in scene_changes:
                # Check if there are nearby audio peaks to boost the score
                for audio_time, audio_score in audio_peaks:
                    if abs(timestamp - audio_time) < 3:  # Within 3 seconds
                        # Boost score if audio peak nearby
                        score = min(1.0, score + (audio_score * 0.3))
                
                # Check if there's significant motion around this time
                for motion_time, motion_score in interesting_moments:
                    if abs(timestamp - motion_time) < 2:  # Within 2 seconds
                        score = min(1.0, score + (motion_score * 0.2))
                
                # Add to viral moments if score is high enough
                if score > self.sensitivity:
                    # Create a window around the detected moment
                    start_time = max(0, timestamp - 1)  # Start 1 second before scene change
                    end_time = min(start_time + self.max_clip_length, video_duration)
                    
                    # Ensure minimum clip length
                    if end_time - start_time < self.min_clip_length:
                        end_time = min(start_time + self.min_clip_length, video_duration)
                    
                    # Check if this moment overlaps with existing ones
                    overlaps = False
                    for i, (existing_start, existing_end, _) in enumerate(viral_moments):
                        if (start_time <= existing_end and end_time >= existing_start):
                            # If higher score, replace the existing moment
                            overlaps = True
                            if score > viral_moments[i][2]:
                                viral_moments[i] = (min(start_time, existing_start),
                                                   max(end_time, existing_end),
                                                   score)
                            break
                    
                    if not overlaps:
                        viral_moments.append((start_time, end_time, score))
            
            # Add any significant motion moments that weren't already covered
            for timestamp, score in interesting_moments:
                if score > self.sensitivity * 1.2:  # Higher threshold for motion-only moments
                    start_time = max(0, timestamp - 2)
                    end_time = min(start_time + self.max_clip_length, video_duration)
                    
                    # Ensure minimum clip length
                    if end_time - start_time < self.min_clip_length:
                        end_time = min(start_time + self.min_clip_length, video_duration)
                    
                    # Check for overlap
                    overlaps = False
                    for existing_start, existing_end, _ in viral_moments:
                        if (start_time <= existing_end and end_time >= existing_start):
                            overlaps = True
                            break
                    
                    if not overlaps:
                        viral_moments.append((start_time, end_time, score))
            
            # Sort by score (descending)
            viral_moments.sort(key=lambda x: x[2], reverse=True)
            
            # Clean up
            cap.release()
            
            # Log results
            logger.info(f"Found {len(viral_moments)} potential viral moments")
            for i, (start, end, score) in enumerate(viral_moments):
                logger.info(f"Moment {i+1}: {start:.2f}s to {end:.2f}s (Score: {score:.2f})")
            
            self.progress_update.emit(100)
            self.status_update.emit("Analysis complete")
            
            # If no viral moments found but we have scene changes, lower threshold and try again
            if not viral_moments and scene_changes:
                reduced_sensitivity = max(self.sensitivity * 0.7, 0.3)  # Reduce by 30% but not below 0.3
                logger.info(f"No viral moments found with sensitivity {self.sensitivity}, trying with {reduced_sensitivity}")
                
                # Process again with lower threshold
                for timestamp, score in scene_changes:
                    if score > reduced_sensitivity:
                        start_time = max(0, timestamp - 1)
                        end_time = min(start_time + self.max_clip_length, video_duration)
                        
                        if end_time - start_time < self.min_clip_length:
                            end_time = min(start_time + self.min_clip_length, video_duration)
                        
                        viral_moments.append((start_time, end_time, score))
                
                viral_moments.sort(key=lambda x: x[2], reverse=True)
            
            self.analysis_complete.emit(viral_moments)
            
        except Exception as e:
            logger.error(f"Error in content analysis: {str(e)}")
            self.status_update.emit(f"Analysis Error: {str(e)}")
            self.analysis_complete.emit([])


# =================
# Chat Monitor
# =================

class ChatMonitor(QThread):
    """Monitors stream chat for activity spikes that might indicate viral moments."""
    
    chat_activity_update = pyqtSignal(int)  # Recent message count
    viral_moment_detected = pyqtSignal(int)  # Peak score (0-100)
    status_update = pyqtSignal(str)
    
    def __init__(self, stream_url, threshold=20, check_interval=5):
        super().__init__()
        self.stream_url = stream_url
        self.threshold = threshold
        self.check_interval = check_interval
        self.running = True
        self.driver = None
        self.recent_message_count = 0
        self.message_history = deque(maxlen=12)  # Store 1 minute of message counts
    
    def run(self):
        try:
            self.status_update.emit(f"Starting chat monitor for {self.stream_url}")
            
            # Setup browser for chat monitoring
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--mute-audio")
            
            self.driver = webdriver.Chrome(options=options)
            
            # Navigate to the stream URL - modify for platform (Twitch, YouTube, etc.)
            self.driver.get(self.stream_url)
            
            # Wait for chat to load
            time.sleep(10)
            
            previous_messages = set()
            
            # Main monitoring loop
            while self.running:
                try:
                    # Different platforms have different chat selectors
                    messages = []
                    
                    # Try different chat element selectors (YouTube, Twitch, etc.)
                    if "youtube.com" in self.stream_url:
                        # YouTube Live chat
                        chat_elements = self.driver.find_elements(By.CSS_SELECTOR, "yt-live-chat-text-message-renderer")
                        for elem in chat_elements:
                            message_text = elem.text
                            if message_text:
                                messages.append(message_text)
                    
                    elif "twitch.tv" in self.stream_url:
                        # Twitch chat
                        chat_elements = self.driver.find_elements(By.CSS_SELECTOR, ".chat-line__message")
                        for elem in chat_elements:
                            message_text = elem.text
                            if message_text:
                                messages.append(message_text)
                    
                    else:
                        # Generic approach - look for common chat elements
                        chat_elements = self.driver.find_elements(By.CSS_SELECTOR, 
                            ".chat-message, .chat-line, .message, .chat-item, .comment-item")
                        for elem in chat_elements:
                            message_text = elem.text
                            if message_text:
                                messages.append(message_text)
                    
                    # Find new messages
                    message_set = set(messages)
                    new_messages = message_set - previous_messages
                    previous_messages = message_set
                    
                    # Calculate recent message count
                    self.recent_message_count = len(new_messages)
                    self.message_history.append(self.recent_message_count)
                    
                    # Calculate average and detect spikes
                    if len(self.message_history) >= 6:
                        avg_count = sum(self.message_history) / len(self.message_history)
                        if self.recent_message_count > avg_count * 2 and self.recent_message_count > self.threshold:
                            # Potential viral moment - calculate score
                            score = min(int((self.recent_message_count / self.threshold) * 100), 100)
                            self.viral_moment_detected.emit(score)
                            self.status_update.emit(f"Viral moment detected! Chat activity: {self.recent_message_count} messages")
                    
                    # Emit activity update
                    self.chat_activity_update.emit(self.recent_message_count)
                    
                    # Wait before checking again
                    time.sleep(self.check_interval)
                    
                except Exception as e:
                    logger.error(f"Error monitoring chat: {str(e)}")
                    self.status_update.emit(f"Chat monitor error: {str(e)}")
                    time.sleep(15)  # Longer delay after error
            
        except Exception as e:
            logger.error(f"Error in chat monitor: {str(e)}")
            self.status_update.emit(f"Chat monitor error: {str(e)}")
        
        finally:
            if self.driver:
                self.driver.quit()
    
    def stop(self):
        self.running = False
        if self.driver:
            self.driver.quit()
