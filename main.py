#!/usr/bin/env python3
"""
BeastClipper v3.0 Ultimate Edition - Main Application
A comprehensive streaming clip automation tool with TikTok upload capabilities
"""

import sys
import os
import time
import logging
import subprocess
from datetime import datetime
from pathlib import Path

# PyQt6 imports
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                            QLabel, QLineEdit, QComboBox, QPushButton, QFileDialog, QProgressBar,
                            QSlider, QCheckBox, QFrame, QTabWidget, QSpinBox, QTextEdit,
                            QMessageBox, QListWidget, QStatusBar)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon

# Import modules
from config import ConfigManager, TempFileManager, QTextEditLogger, DEFAULT_CLIPS_DIR
from stream import StreamBuffer, StreamMonitor, ClipCreator, ClipEditor  
from analysis import ContentAnalyzer, ChatMonitor
from upload import TikTokUploader

# Configure logger
logger = logging.getLogger("BeastClipper")
logger.setLevel(logging.DEBUG)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

# Constants
APP_NAME = "BeastClipper"
APP_VERSION = "3.0 Ultimate"


class BeastClipperApp(QMainWindow):
    """Main application window."""
    
    # Custom signals
    log_message = pyqtSignal(str, str)  # level, message
    
    def __init__(self):
        super().__init__()
        
        # Initialize configuration
        self.config_manager = ConfigManager()
        
        # Initialize managers and monitors
        self.temp_manager = TempFileManager(self.config_manager)
        self.stream_buffer = None
        self.stream_monitor = StreamMonitor(self.config_manager)
        self.chat_monitor = None
        self.content_analyzer = None
        self.tiktok_uploader = None
        
        # Connect log signal
        self.log_message.connect(self._handle_log_message)
        
        # Setup UI
        self.setup_ui()
        
        # Load configuration to UI
        self.load_config_to_ui()
        
        # List of clips
        self.clips = []
        self.selected_clip = None
        
        # Start stream monitor
        self.stream_monitor.stream_live.connect(self.on_stream_live)
        self.stream_monitor.stream_offline.connect(self.on_stream_offline)
        self.stream_monitor.status_update.connect(self.update_status)
        self.stream_monitor.start()
        
        # Setup timers
        self.setup_timers()
        
        # Load existing clips
        self.load_clips()
        
        # Check system requirements
        self.check_requirements()
        
        # Log startup
        self.log_info(f"{APP_NAME} v{APP_VERSION} started successfully")
    
    def setup_ui(self):
        """Set up the user interface."""
        # Window settings
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1200, 800)
        
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Create main tab widget
        self.tab_widget = QTabWidget()
        main_layout = QVBoxLayout(central_widget)
        main_layout.addWidget(self.tab_widget)
        
        # Create tabs
        self.clip_tab = QWidget()
        self.clips_tab = QWidget()
        self.tiktok_tab = QWidget()
        self.settings_tab = QWidget()
        self.logs_tab = QWidget()
        
        # Add tabs
        self.tab_widget.addTab(self.clip_tab, "📹 Clip Recorder")
        self.tab_widget.addTab(self.clips_tab, "🎬 Clips Library")
        self.tab_widget.addTab(self.tiktok_tab, "📱 TikTok Upload")
        self.tab_widget.addTab(self.settings_tab, "⚙️ Settings")
        self.tab_widget.addTab(self.logs_tab, "📝 Logs")
        
        # Setup individual tabs
        self.setup_clip_tab()
        self.setup_clips_tab()
        self.setup_tiktok_tab()
        self.setup_settings_tab()
        self.setup_logs_tab()
        
        # Create status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # Status label
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)
        
        # Buffer status
        self.buffer_status_label = QLabel("Buffer: Not Active")
        self.status_bar.addPermanentWidget(self.buffer_status_label)
        
        # Apply dark theme
        self.apply_dark_theme()
    
    def setup_clip_tab(self):
        """Setup the clip recording tab."""
        layout = QVBoxLayout(self.clip_tab)
        
        # Stream URL input section
        stream_group = QFrame()
        stream_group.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 5px; padding: 10px; }")
        stream_layout = QVBoxLayout(stream_group)
        
        # URL input
        url_layout = QHBoxLayout()
        url_label = QLabel("Stream URL:")
        url_label.setMinimumWidth(100)
        url_layout.addWidget(url_label)
        
        self.stream_url_input = QLineEdit()
        self.stream_url_input.setPlaceholderText("Enter Twitch channel name (e.g., xqc) or URL")
        url_layout.addWidget(self.stream_url_input)
        
        stream_layout.addLayout(url_layout)
        
        # Stream settings
        settings_layout = QHBoxLayout()
        
        # Format selection
        format_label = QLabel("Format:")
        settings_layout.addWidget(format_label)
        
        self.format_combo = QComboBox()
        self.format_combo.addItems(["mp4", "webm", "mkv"])
        self.format_combo.setMinimumWidth(80)
        settings_layout.addWidget(self.format_combo)
        
        settings_layout.addSpacing(20)
        
        # Resolution selection
        resolution_label = QLabel("Resolution:")
        settings_layout.addWidget(resolution_label)
        
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["1080p", "720p", "480p", "360p"])
        self.resolution_combo.setMinimumWidth(100)
        settings_layout.addWidget(self.resolution_combo)
        
        settings_layout.addSpacing(20)
        
        # Buffer duration
        buffer_label = QLabel("Buffer (seconds):")
        settings_layout.addWidget(buffer_label)
        
        self.buffer_duration_spin = QSpinBox()
        self.buffer_duration_spin.setRange(60, 600)
        self.buffer_duration_spin.setValue(300)
        self.buffer_duration_spin.setSuffix(" sec")
        settings_layout.addWidget(self.buffer_duration_spin)
        
        settings_layout.addStretch()
        stream_layout.addLayout(settings_layout)
        
        # Buffer control button
        self.buffer_button = QPushButton("Start Buffer")
        self.buffer_button.setMinimumHeight(40)
        self.buffer_button.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: white;
                font-weight: bold;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
            QPushButton:pressed {
                background-color: #1b5e20;
            }
        """)
        self.buffer_button.clicked.connect(self.toggle_buffer)
        stream_layout.addWidget(self.buffer_button)
        
        # Buffer progress
        self.buffer_progress = QProgressBar()
        self.buffer_progress.setMinimumHeight(25)
        stream_layout.addWidget(self.buffer_progress)
        
        layout.addWidget(stream_group)
        
        # Clip creation section
        clip_group = QFrame()
        clip_group.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 5px; padding: 10px; }")
        clip_layout = QVBoxLayout(clip_group)
        
        # Time selection
        time_layout = QHBoxLayout()
        
        time_ago_label = QLabel("Start Time (seconds ago):")
        time_layout.addWidget(time_ago_label)
        
        self.time_ago_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_ago_slider.setRange(0, 300)
        self.time_ago_slider.setValue(30)
        self.time_ago_slider.valueChanged.connect(self.update_time_display)
        time_layout.addWidget(self.time_ago_slider)
        
        self.time_ago_label = QLabel("30s")
        self.time_ago_label.setMinimumWidth(50)
        time_layout.addWidget(self.time_ago_label)
        
        time_layout.addSpacing(20)
        
        duration_label = QLabel("Duration:")
        time_layout.addWidget(duration_label)
        
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(5, 60)
        self.duration_spin.setValue(30)
        self.duration_spin.setSuffix(" sec")
        time_layout.addWidget(self.duration_spin)
        
        clip_layout.addLayout(time_layout)
        
        # Output directory
        output_layout = QHBoxLayout()
        
        output_label = QLabel("Output Directory:")
        output_label.setMinimumWidth(100)
        output_layout.addWidget(output_label)
        
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setText(self.config_manager.get("output_directory", DEFAULT_CLIPS_DIR))
        output_layout.addWidget(self.output_dir_input)
        
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(browse_button)
        
        clip_layout.addLayout(output_layout)
        
        # Create clip button
        self.create_clip_button = QPushButton("Create Clip")
        self.create_clip_button.setMinimumHeight(40)
        self.create_clip_button.setEnabled(False)
        self.create_clip_button.setStyleSheet("""
            QPushButton {
                background-color: #1976d2;
                color: white;
                font-weight: bold;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #1e88e5;
            }
            QPushButton:pressed {
                background-color: #0d47a1;
            }
            QPushButton:disabled {
                background-color: #555;
                color: #999;
            }
        """)
        self.create_clip_button.clicked.connect(self.create_clip)
        clip_layout.addWidget(self.create_clip_button)
        
        # Clip progress
        self.clip_progress = QProgressBar()
        self.clip_progress.setMinimumHeight(25)
        clip_layout.addWidget(self.clip_progress)
        
        layout.addWidget(clip_group)
        layout.addStretch()
    
    def setup_clips_tab(self):
        """Setup the clips library tab."""
        layout = QVBoxLayout(self.clips_tab)
        
        # Clips list
        self.clips_list = QListWidget()
        self.clips_list.itemSelectionChanged.connect(self.on_clip_selected)
        layout.addWidget(self.clips_list)
        
        # Control buttons
        controls_layout = QHBoxLayout()
        
        self.play_button = QPushButton("▶ Play")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.play_clip)
        controls_layout.addWidget(self.play_button)
        
        self.analyze_button = QPushButton("🔍 Analyze")
        self.analyze_button.setEnabled(False)
        self.analyze_button.clicked.connect(self.analyze_clip)
        controls_layout.addWidget(self.analyze_button)
        
        self.upload_button = QPushButton("📤 Upload to TikTok")
        self.upload_button.setEnabled(False)
        self.upload_button.clicked.connect(self.prepare_upload)
        controls_layout.addWidget(self.upload_button)
        
        self.delete_button = QPushButton("🗑 Delete")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_clip)
        controls_layout.addWidget(self.delete_button)
        
        layout.addLayout(controls_layout)
    
    def setup_tiktok_tab(self):
        """Setup the TikTok upload tab."""
        layout = QVBoxLayout(self.tiktok_tab)
        
        # Account credentials
        creds_group = QFrame()
        creds_group.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 5px; padding: 10px; }")
        creds_layout = QVBoxLayout(creds_group)
        
        # Username
        username_layout = QHBoxLayout()
        username_label = QLabel("Username:")
        username_label.setMinimumWidth(100)
        username_layout.addWidget(username_label)
        
        self.tiktok_username = QLineEdit()
        username_layout.addWidget(self.tiktok_username)
        creds_layout.addLayout(username_layout)
        
        # Password
        password_layout = QHBoxLayout()
        password_label = QLabel("Password:")
        password_label.setMinimumWidth(100)
        password_layout.addWidget(password_label)
        
        self.tiktok_password = QLineEdit()
        self.tiktok_password.setEchoMode(QLineEdit.EchoMode.Password)
        password_layout.addWidget(self.tiktok_password)
        creds_layout.addLayout(password_layout)
        
        layout.addWidget(creds_group)
        
        # Upload details
        upload_group = QFrame()
        upload_group.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 5px; padding: 10px; }")
        upload_layout = QVBoxLayout(upload_group)
        
        # Selected clip
        clip_layout = QHBoxLayout()
        clip_label = QLabel("Selected Clip:")
        clip_label.setMinimumWidth(100)
        clip_layout.addWidget(clip_label)
        
        self.selected_clip_label = QLineEdit()
        self.selected_clip_label.setReadOnly(True)
        clip_layout.addWidget(self.selected_clip_label)
        upload_layout.addLayout(clip_layout)
        
        # Caption
        caption_label = QLabel("Caption:")
        upload_layout.addWidget(caption_label)
        
        self.caption_input = QTextEdit()
        self.caption_input.setMaximumHeight(100)
        self.caption_input.setPlaceholderText("Enter your caption here...")
        upload_layout.addWidget(self.caption_input)
        
        # Upload button
        self.upload_to_tiktok_button = QPushButton("Upload to TikTok")
        self.upload_to_tiktok_button.setMinimumHeight(40)
        self.upload_to_tiktok_button.clicked.connect(self.upload_to_tiktok)
        upload_layout.addWidget(self.upload_to_tiktok_button)
        
        # Upload progress
        self.upload_progress = QProgressBar()
        upload_layout.addWidget(self.upload_progress)
        
        layout.addWidget(upload_group)
        layout.addStretch()
    
    def setup_settings_tab(self):
        """Setup the settings tab."""
        layout = QVBoxLayout(self.settings_tab)
        
        # General settings
        general_group = QFrame()
        general_group.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 5px; padding: 10px; }")
        general_layout = QVBoxLayout(general_group)
        
        # Buffer settings
        buffer_layout = QHBoxLayout()
        buffer_label = QLabel("Default Buffer Duration:")
        buffer_label.setMinimumWidth(150)
        buffer_layout.addWidget(buffer_label)
        
        self.default_buffer_spin = QSpinBox()
        self.default_buffer_spin.setRange(60, 600)
        self.default_buffer_spin.setValue(self.config_manager.get("buffer_duration", 300))
        self.default_buffer_spin.setSuffix(" seconds")
        buffer_layout.addWidget(self.default_buffer_spin)
        general_layout.addLayout(buffer_layout)
        
        # Auto-upload settings
        auto_upload_layout = QHBoxLayout()
        self.auto_upload_check = QCheckBox("Auto-upload clips to TikTok")
        self.auto_upload_check.setChecked(self.config_manager.get("auto_upload", False))
        auto_upload_layout.addWidget(self.auto_upload_check)
        general_layout.addLayout(auto_upload_layout)
        
        layout.addWidget(general_group)
        
        # Save button
        save_button = QPushButton("Save Settings")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button)
        
        layout.addStretch()
    
    def setup_logs_tab(self):
        """Setup the logs tab."""
        layout = QVBoxLayout(self.logs_tab)
        
        # Log text area
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("QTextEdit { font-family: Consolas, monospace; }")
        layout.addWidget(self.log_text)
        
        # Control buttons
        controls_layout = QHBoxLayout()
        
        clear_button = QPushButton("Clear Logs")
        clear_button.clicked.connect(lambda: self.log_text.clear())
        controls_layout.addWidget(clear_button)
        
        controls_layout.addStretch()
        layout.addLayout(controls_layout)
        
        # Setup log handler
        text_handler = QTextEditLogger(self.log_text)
        text_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(text_handler)
    
    def setup_timers(self):
        """Setup application timers."""
        # Buffer status timer
        self.buffer_timer = QTimer()
        self.buffer_timer.timeout.connect(self.update_buffer_status)
        self.buffer_timer.start(1000)  # Update every second
        
        # Clips refresh timer
        self.clips_timer = QTimer()
        self.clips_timer.timeout.connect(self.load_clips)
        self.clips_timer.start(30000)  # Refresh every 30 seconds
    
    def apply_dark_theme(self):
        """Apply dark theme to the application."""
        dark_theme = """
            QMainWindow, QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QTabWidget::pane {
                border: 1px solid #444;
                background-color: #2d2d2d;
            }
            QTabBar::tab {
                background-color: #2d2d2d;
                color: #ffffff;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #404040;
            }
            QLineEdit, QTextEdit, QSpinBox, QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #444;
                color: #ffffff;
                padding: 5px;
                border-radius: 3px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #555;
                color: #ffffff;
                padding: 8px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            QPushButton:pressed {
                background-color: #353535;
            }
            QProgressBar {
                border: 1px solid #444;
                border-radius: 3px;
                text-align: center;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #2196F3;
                border-radius: 3px;
            }
            QListWidget {
                background-color: #2d2d2d;
                border: 1px solid #444;
                color: #ffffff;
            }
            QListWidget::item:selected {
                background-color: #404040;
            }
            QSlider::groove:horizontal {
                border: 1px solid #444;
                height: 8px;
                background: #2d2d2d;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #2196F3;
                border: 1px solid #444;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
        """
        self.setStyleSheet(dark_theme)
    
    def load_config_to_ui(self):
        """Load configuration values to UI elements."""
        # Load TikTok credentials
        self.tiktok_username.setText(self.config_manager.get("tiktok_credentials.username", ""))
        self.tiktok_password.setText(self.config_manager.get("tiktok_credentials.password", ""))
        
        # Load default settings
        self.format_combo.setCurrentText(self.config_manager.get("format", "mp4"))
        self.resolution_combo.setCurrentText(self.config_manager.get("resolution", "1080p"))
        self.buffer_duration_spin.setValue(self.config_manager.get("buffer_duration", 300))
    
    def check_requirements(self):
        """Check if required tools are available."""
        required_tools = ["streamlink"]
        recommended_tools = ["ffmpeg", "ffprobe"]
        missing_required = []
        missing_recommended = []
        
        # Check required tools
        for tool in required_tools:
            try:
                result = subprocess.run([tool, "--version"], capture_output=True, text=True)
                if result.returncode != 0:
                    missing_required.append(tool)
            except FileNotFoundError:
                missing_required.append(tool)
        
        # Check recommended tools
        for tool in recommended_tools:
            try:
                result = subprocess.run([tool, "-version"], capture_output=True, text=True)
                if result.returncode != 0:
                    missing_recommended.append(tool)
            except FileNotFoundError:
                missing_recommended.append(tool)
        
        if missing_required:
            self.log_error(f"Missing required tools: {', '.join(missing_required)}")
            QMessageBox.critical(
                self,
                "Missing Requirements",
                f"The following required tools are missing: {', '.join(missing_required)}\n"
                "BeastClipper cannot run without these tools.\n\n"
                "Install with: pip install streamlink"
            )
            sys.exit(1)
        
        if missing_recommended:
            self.log_warning(f"Missing recommended tools: {', '.join(missing_recommended)}")
            QMessageBox.warning(
                self,
                "Missing Recommended Tools",
                f"The following tools are missing: {', '.join(missing_recommended)}\n"
                "Clip creation may not work without FFmpeg.\n\n"
                "Install FFmpeg from: https://ffmpeg.org/download.html"
            )
    
    def toggle_buffer(self):
        """Start or stop the stream buffer."""
        if self.stream_buffer and self.stream_buffer.isRunning():
            # Stop buffer
            self.log_info("Stopping stream buffer...")
            self.stream_buffer.stop()
            self.stream_buffer.wait()
            self.stream_buffer = None
            
            self.buffer_button.setText("Start Buffer")
            self.buffer_button.setStyleSheet(self.buffer_button.styleSheet().replace("#d32f2f", "#2e7d32"))
            self.buffer_progress.setValue(0)
            self.create_clip_button.setEnabled(False)
            self.time_ago_slider.setMaximum(0)
            
            self.update_status("Buffer stopped")
        else:
            # Start buffer
            stream_url = self.stream_url_input.text().strip()
            
            if not stream_url:
                self.show_error("Please enter a Twitch stream URL or channel name")
                return
            
            # Validate Twitch URL/channel
            if not any(x in stream_url.lower() for x in ["twitch.tv", "twitch"]):
                # Assume it's just a channel name
                stream_url = f"https://twitch.tv/{stream_url}"
            
            # Ensure it's a Twitch URL
            if "twitch.tv" not in stream_url.lower():
                self.show_error("Only Twitch streams are supported. Enter a channel name or twitch.tv URL")
                return
            
            self.log_info(f"Starting Twitch stream buffer for: {stream_url}")
            
            # Create buffer
            self.stream_buffer = StreamBuffer(
                stream_url=stream_url,
                buffer_duration=self.buffer_duration_spin.value(),
                resolution=self.resolution_combo.currentText(),
                temp_manager=self.temp_manager
            )
            
            # Connect signals
            self.stream_buffer.buffer_progress.connect(self.on_buffer_progress)
            self.stream_buffer.status_update.connect(self.update_status)
            self.stream_buffer.error_occurred.connect(self.on_buffer_error)
            self.stream_buffer.stream_info_updated.connect(self.on_stream_info_updated)
            
            # Start buffer
            self.stream_buffer.start()
            
            self.buffer_button.setText("Stop Buffer")
            self.buffer_button.setStyleSheet(self.buffer_button.styleSheet().replace("#2e7d32", "#d32f2f"))
            self.create_clip_button.setEnabled(True)
            
            self.update_status("Buffer started")
    
    def create_clip(self):
        """Create a clip from the buffer."""
        if not self.stream_buffer:
            self.show_error("No active buffer")
            return
        
        # Get clip parameters
        time_ago = self.time_ago_slider.value()
        duration = self.duration_spin.value()
        output_dir = self.output_dir_input.text()
        format_type = self.format_combo.currentText()
        
        # Create output filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"clip_{timestamp}.{format_type}"
        output_path = os.path.join(output_dir, filename)
        
        self.log_info(f"Creating clip: {filename}")
        
        # Create clip creator
        self.clip_creator = ClipCreator(
            stream_buffer=self.stream_buffer,
            start_time_ago=time_ago,
            duration=duration,
            output_path=output_path,
            format_type=format_type
        )
        
        # Connect signals
        self.clip_creator.progress_update.connect(self.on_clip_progress)
        self.clip_creator.status_update.connect(self.update_status)
        self.clip_creator.clip_created.connect(self.on_clip_created)
        self.clip_creator.error_occurred.connect(self.on_clip_error)
        
        # Start creation
        self.clip_creator.start()
        
        self.create_clip_button.setEnabled(False)
        self.update_status("Creating clip...")
    
    def load_clips(self):
        """Load clips from the output directory."""
        output_dir = self.output_dir_input.text()
        
        if not os.path.exists(output_dir):
            return
        
        self.clips = []
        
        # Find all video files
        video_extensions = ['.mp4', '.webm', '.mkv', '.avi', '.mov']
        
        for filename in os.listdir(output_dir):
            if any(filename.endswith(ext) for ext in video_extensions):
                file_path = os.path.join(output_dir, filename)
                file_stats = os.stat(file_path)
                
                self.clips.append({
                    'name': filename,
                    'path': file_path,
                    'size': file_stats.st_size,
                    'created': file_stats.st_ctime
                })
        
        # Sort by creation time (newest first)
        self.clips.sort(key=lambda x: x['created'], reverse=True)
        
        # Update list widget
        self.clips_list.clear()
        
        for clip in self.clips:
            size_mb = clip['size'] / (1024 * 1024)
            created_time = datetime.fromtimestamp(clip['created']).strftime("%Y-%m-%d %H:%M")
            item_text = f"{clip['name']} ({size_mb:.1f} MB) - {created_time}"
            self.clips_list.addItem(item_text)
    
    def browse_output_dir(self):
        """Browse for output directory."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            self.output_dir_input.text()
        )
        
        if directory:
            self.output_dir_input.setText(directory)
            self.config_manager.set("output_directory", directory)
            self.load_clips()
    
    def update_time_display(self, value):
        """Update time ago display."""
        self.time_ago_label.setText(f"{value}s")
    
    def update_buffer_status(self):
        """Update buffer status display."""
        if self.stream_buffer and self.stream_buffer.isRunning():
            status = self.stream_buffer.get_buffer_status()
            duration = status['duration']
            max_duration = status['max_duration']
            
            self.buffer_status_label.setText(f"Buffer: {duration}s / {max_duration}s")
            self.time_ago_slider.setMaximum(duration)
            
            if duration > 0 and not self.create_clip_button.isEnabled():
                self.create_clip_button.setEnabled(True)
        else:
            self.buffer_status_label.setText("Buffer: Not Active")
    
    def save_settings(self):
        """Save settings to configuration."""
        # Save general settings
        self.config_manager.set("buffer_duration", self.default_buffer_spin.value())
        self.config_manager.set("auto_upload", self.auto_upload_check.isChecked())
        
        # Save TikTok credentials
        self.config_manager.set("tiktok_credentials.username", self.tiktok_username.text())
        self.config_manager.set("tiktok_credentials.password", self.tiktok_password.text())
        
        # Save other settings
        self.config_manager.set("format", self.format_combo.currentText())
        self.config_manager.set("resolution", self.resolution_combo.currentText())
        self.config_manager.set("output_directory", self.output_dir_input.text())
        
        self.log_info("Settings saved")
        self.update_status("Settings saved")
    
    # Event handlers
    def on_stream_live(self, url):
        """Handle stream going live."""
        self.log_info(f"Stream went live: {url}")
        self.stream_url_input.setText(url)
        self.update_status(f"Stream live: {url}")
    
    def on_stream_offline(self, url):
        """Handle stream going offline."""
        self.log_info(f"Stream went offline: {url}")
        self.update_status(f"Stream offline: {url}")
    
    def on_buffer_progress(self, current, total):
        """Handle buffer progress update."""
        if total > 0:
            progress = int((current / total) * 100)
            self.buffer_progress.setValue(progress)
    
    def on_buffer_error(self, error_message):
        """Handle buffer error."""
        self.log_error(f"Buffer error: {error_message}")
        self.show_error(error_message)
        
        # Stop buffer if critical error
        if "critical" in error_message.lower() or "fatal" in error_message.lower():
            if self.stream_buffer:
                self.toggle_buffer()
    
    def on_stream_info_updated(self, info):
        """Handle stream info update from Twitch."""
        channel = info.get('channel', 'Unknown')
        qualities = info.get('qualities', [])
        
        self.log_info(f"Connected to Twitch channel: {channel}")
        if qualities:
            self.log_info(f"Available qualities: {', '.join(qualities)}")
        
        self.update_status(f"Buffering: {channel}")
    
    def on_clip_progress(self, progress):
        """Handle clip creation progress."""
        self.clip_progress.setValue(progress)
    
    def on_clip_created(self, file_path):
        """Handle successful clip creation."""
        self.log_info(f"Clip created: {file_path}")
        self.clip_progress.setValue(100)
        self.create_clip_button.setEnabled(True)
        self.update_status(f"Clip created: {os.path.basename(file_path)}")
        
        # Reload clips
        self.load_clips()
        
        # Auto-upload if enabled
        if self.auto_upload_check.isChecked():
            self.selected_clip = file_path
            self.tab_widget.setCurrentWidget(self.tiktok_tab)
            self.prepare_upload()
    
    def on_clip_error(self, error_message):
        """Handle clip creation error."""
        self.log_error(f"Clip error: {error_message}")
        self.show_error(error_message)
        self.create_clip_button.setEnabled(True)
        self.clip_progress.setValue(0)
    
    def on_clip_selected(self):
        """Handle clip selection in list."""
        selected_items = self.clips_list.selectedItems()
        
        if selected_items:
            index = self.clips_list.row(selected_items[0])
            self.selected_clip = self.clips[index]['path']
            
            # Enable action buttons
            self.play_button.setEnabled(True)
            self.analyze_button.setEnabled(True)
            self.upload_button.setEnabled(True)
            self.delete_button.setEnabled(True)
        else:
            self.selected_clip = None
            
            # Disable action buttons
            self.play_button.setEnabled(False)
            self.analyze_button.setEnabled(False)
            self.upload_button.setEnabled(False)
            self.delete_button.setEnabled(False)
    
    def play_clip(self):
        """Play the selected clip."""
        if self.selected_clip and os.path.exists(self.selected_clip):
            self.log_info(f"Playing clip: {self.selected_clip}")
            
            # Use default system player
            if sys.platform == "win32":
                os.startfile(self.selected_clip)
            elif sys.platform == "darwin":
                subprocess.run(["open", self.selected_clip])
            else:
                subprocess.run(["xdg-open", self.selected_clip])
    
    def analyze_clip(self):
        """Analyze the selected clip."""
        if not self.selected_clip:
            return
        
        self.log_info(f"Analyzing clip: {self.selected_clip}")
        
        # Create content analyzer
        self.content_analyzer = ContentAnalyzer(
            video_file=self.selected_clip,
            sensitivity=self.config_manager.get("viral_detection.sensitivity", 0.7)
        )
        
        # Connect signals
        self.content_analyzer.analysis_complete.connect(self.on_analysis_complete)
        self.content_analyzer.progress_update.connect(lambda p: self.update_status(f"Analyzing... {p}%"))
        self.content_analyzer.status_update.connect(self.update_status)
        
        # Start analysis
        self.content_analyzer.start()
        self.update_status("Analyzing clip...")
    
    def on_analysis_complete(self, viral_moments):
        """Handle analysis completion."""
        if viral_moments:
            self.log_info(f"Found {len(viral_moments)} viral moments")
            self.update_status(f"Analysis complete: {len(viral_moments)} viral moments found")
            
            # Show results
            moments_text = "\n".join([
                f"Moment {i+1}: {start:.1f}s - {end:.1f}s (Score: {score:.2f})"
                for i, (start, end, score) in enumerate(viral_moments)
            ])
            
            QMessageBox.information(
                self,
                "Viral Moments Found",
                f"Found {len(viral_moments)} potential viral moments:\n\n{moments_text}"
            )
        else:
            self.log_info("No viral moments found")
            self.update_status("Analysis complete: No viral moments found")
    
    def prepare_upload(self):
        """Prepare clip for upload to TikTok."""
        if not self.selected_clip:
            self.show_error("No clip selected")
            return
        
        # Switch to TikTok tab
        self.tab_widget.setCurrentWidget(self.tiktok_tab)
        
        # Set selected clip
        self.selected_clip_label.setText(os.path.basename(self.selected_clip))
        
        # Load last caption if available
        last_caption = self.config_manager.get("last_caption", "")
        self.caption_input.setText(last_caption)
    
    def upload_to_tiktok(self):
        """Upload clip to TikTok."""
        if not self.selected_clip:
            self.show_error("No clip selected")
            return
        
        # Get upload parameters
        username = self.tiktok_username.text().strip()
        password = self.tiktok_password.text().strip()
        caption = self.caption_input.toPlainText().strip()
        
        if not username or not password:
            self.show_error("Please enter TikTok credentials")
            return
        
        self.log_info(f"Starting TikTok upload: {self.selected_clip}")
        
        # Save caption for next time
        self.config_manager.set("last_caption", caption)
        
        # Create uploader
        self.tiktok_uploader = TikTokUploader(
            video_file=self.selected_clip,
            caption=caption,
            username=username,
            password=password
        )
        
        # Connect signals
        self.tiktok_uploader.progress_update.connect(self.on_upload_progress)
        self.tiktok_uploader.status_update.connect(self.update_status)
        self.tiktok_uploader.upload_finished.connect(self.on_upload_finished)
        self.tiktok_uploader.error_occurred.connect(self.on_upload_error)
        
        # Start upload
        self.tiktok_uploader.start()
        
        self.upload_to_tiktok_button.setEnabled(False)
        self.update_status("Uploading to TikTok...")
    
    def on_upload_progress(self, progress):
        """Handle upload progress."""
        self.upload_progress.setValue(progress)
    
    def on_upload_finished(self, success):
        """Handle upload completion."""
        self.upload_to_tiktok_button.setEnabled(True)
        self.upload_progress.setValue(100 if success else 0)
        
        if success:
            self.log_info("Upload to TikTok successful")
            self.update_status("Upload successful!")
            
            QMessageBox.information(
                self,
                "Upload Successful",
                "Your clip has been uploaded to TikTok successfully!"
            )
        else:
            self.log_error("Upload to TikTok failed")
            self.update_status("Upload failed")
    
    def on_upload_error(self, error_message):
        """Handle upload error."""
        self.log_error(f"Upload error: {error_message}")
        self.show_error(error_message)
        self.upload_to_tiktok_button.setEnabled(True)
        self.upload_progress.setValue(0)
    
    def delete_clip(self):
        """Delete the selected clip."""
        if not self.selected_clip:
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Delete Clip",
            f"Are you sure you want to delete:\n{os.path.basename(self.selected_clip)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                os.remove(self.selected_clip)
                self.log_info(f"Deleted clip: {self.selected_clip}")
                self.update_status("Clip deleted")
                
                # Reload clips
                self.load_clips()
            except Exception as e:
                self.log_error(f"Failed to delete clip: {e}")
                self.show_error(f"Failed to delete clip: {e}")
    
    # Utility methods
    def update_status(self, message):
        """Update status bar message."""
        self.status_label.setText(message)
    
    def show_error(self, message):
        """Show error message."""
        QMessageBox.critical(self, "Error", message)
    
    def log_info(self, message):
        """Log info message."""
        logger.info(message)
        self.log_message.emit("INFO", message)
    
    def log_warning(self, message):
        """Log warning message."""
        logger.warning(message)
        self.log_message.emit("WARNING", message)
    
    def log_error(self, message):
        """Log error message."""
        logger.error(message)
        self.log_message.emit("ERROR", message)
    
    def _handle_log_message(self, level, message):
        """Handle log message display."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Color code by level
        if level == "ERROR":
            color = "#ff5252"
        elif level == "WARNING":
            color = "#ffc107"
        else:
            color = "#ffffff"
        
        # Add to log text
        self.log_text.append(
            f'<span style="color: {color}">[{timestamp}] {level}: {message}</span>'
        )
    
    def closeEvent(self, event):
        """Handle application close."""
        # Stop all running processes
        if self.stream_buffer and self.stream_buffer.isRunning():
            self.stream_buffer.stop()
            self.stream_buffer.wait()
        
        if self.stream_monitor and self.stream_monitor.isRunning():
            self.stream_monitor.stop()
            self.stream_monitor.wait()
        
        # Save settings
        self.save_settings()
        
        # Stop temp file manager
        self.temp_manager.stop_cleanup_timer()
        
        event.accept()


def main():
    """Application entry point."""
    # Create log directory
    log_dir = os.path.join(os.path.expanduser("~"), ".beastclipper", "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # Set up file logging
    log_file = os.path.join(log_dir, f"beastclipper_{datetime.now().strftime('%Y%m%d')}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    
    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    
    # Create and show main window
    window = BeastClipperApp()
    window.show()
    
    # Run application
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
