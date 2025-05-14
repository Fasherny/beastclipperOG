#!/usr/bin/env python3
"""
Configuration management and utility classes for BeastClipper
"""

import os
import json
import logging
import time
import threading
import shutil
import tempfile

from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtCore import Qt

# Configure logger
logger = logging.getLogger("BeastClipper")

# Constants
DEFAULT_CLIPS_DIR = os.path.join(os.path.expanduser("~"), "beastclipper_clips")
DEFAULT_CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".beastclipper_config.json")
DEFAULT_TEMP_DIR = os.path.join(tempfile.gettempdir(), "beastclipper_temp")

# Create directories if they don't exist
os.makedirs(DEFAULT_CLIPS_DIR, exist_ok=True)
os.makedirs(DEFAULT_TEMP_DIR, exist_ok=True)


# =====================
# Configuration Manager
# =====================

class ConfigManager:
    """Manages application configuration and persistent settings."""
    
    def __init__(self, config_file=DEFAULT_CONFIG_FILE):
        self.config_file = config_file
        self.config = self.load_config()
    
    def load_config(self):
        """Load configuration from file or return defaults if not found."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.error("Invalid config file. Loading defaults.")
                return self.get_default_config()
        else:
            return self.get_default_config()
    
    def save_config(self):
        """Save current configuration to file."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving config: {str(e)}")
    
    def get_default_config(self):
        """Get default configuration settings."""
        return {
            "output_directory": DEFAULT_CLIPS_DIR,
            "temp_directory": DEFAULT_TEMP_DIR,
            "buffer_duration": 300,  # 5 minutes in seconds
            "segment_length": 30,    # 30 seconds per buffer segment
            "clip_duration": 30,
            "resolution": "1080p",
            "format": "mp4",
            "fps": 30,
            "auto_upload": False,
            "auto_delete_after_upload": False,
            "auto_delete_settings": {
                "delete_original": True,
                "delete_edited": False,
                "keep_viral_moments": True,
                "use_recycle_bin": True,
                "confirm_deletion": True
            },
            "remember_caption": True,
            "last_caption": "",
            "monitored_streams": [],
            "tiktok_credentials": {
                "username": "",
                "password": ""
            },
            "viral_detection": {
                "enabled": True,
                "sensitivity": 0.7,
                "min_clip_length": 10,
                "max_clip_length": 60
            },
            "cleanup_settings": {
                "auto_cleanup_enabled": True,
                "cleanup_interval": 60,  # seconds
                "max_temp_file_age": 3600  # 1 hour in seconds
            },
            "ui": {
                "theme": "dark",
                "font_size": 10,
                "show_tooltips": True
            }
        }
    
    def get(self, key, default=None):
        """Get a config value with optional nested keys using dot notation."""
        if "." in key:
            parts = key.split(".")
            temp_config = self.config
            for part in parts:
                if part in temp_config:
                    temp_config = temp_config[part]
                else:
                    return default
            return temp_config
        return self.config.get(key, default)
    
    def set(self, key, value):
        """Set a config value with optional nested keys using dot notation."""
        if "." in key:
            parts = key.split(".")
            temp_config = self.config
            for i, part in enumerate(parts[:-1]):
                if part not in temp_config:
                    temp_config[part] = {}
                temp_config = temp_config[part]
            temp_config[parts[-1]] = value
        else:
            self.config[key] = value
        self.save_config()


# =====================
# Temp File Manager
# =====================

class TempFileManager:
    """Manages temporary files and ensures proper cleanup."""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.temp_dir = self.config_manager.get("temp_directory", DEFAULT_TEMP_DIR)
        self.temp_files = {}  # {file_path: expiry_time}
        self.cleanup_timer = None
        self.mutex = threading.Lock()
        
        # Ensure temp directory exists
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # Initial cleanup of any orphaned files
        self.cleanup_orphaned_files()
        
        # Start cleanup timer if enabled
        if self.config_manager.get("cleanup_settings.auto_cleanup_enabled", True):
            self.start_cleanup_timer()
    
    def register_temp_file(self, file_path, lifetime=3600):
        """Register a temporary file for automatic cleanup."""
        with self.mutex:
            expiry_time = time.time() + lifetime
            self.temp_files[file_path] = expiry_time
    
    def unregister_temp_file(self, file_path):
        """Unregister a temporary file (e.g., if manually deleted)."""
        with self.mutex:
            if file_path in self.temp_files:
                del self.temp_files[file_path]
    
    def delete_temp_file(self, file_path, force=False):
        """Delete a temporary file."""
        try:
            if os.path.exists(file_path):
                use_recycle_bin = self.config_manager.get("auto_delete_settings.use_recycle_bin", True)
                
                if use_recycle_bin and not force:
                    # Move to recycle bin using platform-specific methods
                    try:
                        import send2trash
                        send2trash.send2trash(file_path)
                    except ImportError:
                        # If send2trash is not available, use direct deletion
                        if os.path.isdir(file_path):
                            shutil.rmtree(file_path)
                        else:
                            os.remove(file_path)
                else:
                    # Direct deletion
                    if os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                    else:
                        os.remove(file_path)
                
                # Unregister from temp files list
                self.unregister_temp_file(file_path)
                return True
        except Exception as e:
            logger.error(f"Error deleting temp file {file_path}: {e}")
            return False
    
    def cleanup_expired_files(self):
        """Delete temporary files that have expired."""
        with self.mutex:
            current_time = time.time()
            expired_files = [f for f, t in self.temp_files.items() if t <= current_time]
            
            for file_path in expired_files:
                self.delete_temp_file(file_path, force=True)
                logger.debug(f"Deleted expired temp file: {file_path}")
    
    def cleanup_orphaned_files(self):
        """Clean up any orphaned temporary files from previous sessions."""
        try:
            max_age = self.config_manager.get("cleanup_settings.max_temp_file_age", 3600)
            cutoff_time = time.time() - max_age
            
            # Scan temp directory for old files
            for root, dirs, files in os.walk(self.temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    if os.path.getmtime(file_path) < cutoff_time:
                        try:
                            os.remove(file_path)
                            logger.debug(f"Deleted orphaned temp file: {file_path}")
                        except Exception as e:
                            logger.error(f"Error deleting orphaned file {file_path}: {e}")
                            
                # Also clean up empty directories
                for dir in dirs:
                    dir_path = os.path.join(root, dir)
                    if not os.listdir(dir_path):
                        try:
                            os.rmdir(dir_path)
                            logger.debug(f"Removed empty temp directory: {dir_path}")
                        except Exception as e:
                            logger.error(f"Error removing empty directory {dir_path}: {e}")
        except Exception as e:
            logger.error(f"Error during orphaned file cleanup: {e}")
    
    def start_cleanup_timer(self):
        """Start the automatic cleanup timer."""
        interval = self.config_manager.get("cleanup_settings.cleanup_interval", 60)
        
        def cleanup_task():
            self.cleanup_expired_files()
            # Reschedule the timer
            self.cleanup_timer = threading.Timer(interval, cleanup_task)
            self.cleanup_timer.daemon = True
            self.cleanup_timer.start()
        
        # Start initial timer
        self.cleanup_timer = threading.Timer(interval, cleanup_task)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()
    
    def stop_cleanup_timer(self):
        """Stop the automatic cleanup timer."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None
    
    def get_temp_dir_size(self):
        """Get the current size of temp directory in bytes."""
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(self.temp_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        return total_size


# ======================
# QTextEdit Logger
# ======================

class QTextEditLogger(logging.Handler):
    """Handler to redirect log messages to a QTextEdit widget."""
    
    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit
    
    def emit(self, record):
        msg = self.format(record)
        self.text_edit.append(msg)
