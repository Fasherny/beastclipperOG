#!/usr/bin/env python3
"""
TikTok upload module for BeastClipper
Handles automated uploads with anti-bot measures
"""

import os
import json
import time
import logging
import random

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from PyQt6.QtCore import QThread, pyqtSignal

# Configure logger
logger = logging.getLogger("BeastClipper")

# TikTok URL
TIKTOK_URL = "https://www.tiktok.com/upload"


# =================
# TikTok Uploader
# =================

class TikTokUploader(QThread):
    """Uploads videos to TikTok with anti-bot measures and community pattern sharing."""
    
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    upload_finished = pyqtSignal(bool, str)  # Success status, clip path
    error_occurred = pyqtSignal(str)
    
    def __init__(self, video_file, caption="", hashtags=None, username="", password=""):
        super().__init__()
        self.video_file = video_file
        self.caption = caption
        self.hashtags = hashtags or []
        self.username = username
        self.password = password
        self.driver = None
        self.selectors = {
            "upload_page": "https://www.tiktok.com/upload",
            "file_input": "input[type='file']",
            "upload_progress": "div[data-e2e='upload-progress-done']",
            "caption_input": "div[data-e2e='caption-input']",
            "upload_button": "button[data-e2e='upload-post']", 
            "upload_success": "div[data-e2e='upload-success']"
        }
        self.load_selectors()
    
    def load_selectors(self):
        """Load stored selectors from file if available."""
        selector_file = os.path.join(os.path.expanduser("~"), ".beastclipper", "tiktok_selectors.json")
        if os.path.exists(selector_file):
            try:
                with open(selector_file, 'r') as f:
                    saved_selectors = json.load(f)
                self.selectors.update(saved_selectors)
                logger.info(f"Loaded TikTok selectors from file")
            except Exception as e:
                logger.error(f"Error loading TikTok selectors: {str(e)}")
    
    def save_selectors(self):
        """Save current selectors to file."""
        selector_dir = os.path.join(os.path.expanduser("~"), ".beastclipper")
        os.makedirs(selector_dir, exist_ok=True)
        
        selector_file = os.path.join(selector_dir, "tiktok_selectors.json")
        try:
            with open(selector_file, 'w') as f:
                json.dump(self.selectors, f, indent=4)
            logger.info(f"Saved TikTok selectors to file")
        except Exception as e:
            logger.error(f"Error saving TikTok selectors: {str(e)}")
    
    def verify_selectors(self):
        """Verify existing selectors and find alternatives if needed."""
        if not self.driver:
            return False
        
        working_selectors = {}
        for key, selector in self.selectors.items():
            if key == "upload_page":
                working_selectors[key] = selector
                continue
            
            try:
                # Test if selector works
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                working_selectors[key] = selector
                logger.info(f"TikTok selector '{key}' is working")
            except (TimeoutException, NoSuchElementException):
                logger.info(f"TikTok selector '{key}' is not working, searching for alternative...")
                
                # Try to find alternative selector
                new_selector = self.find_alternative_selector(key)
                if new_selector:
                    working_selectors[key] = new_selector
                    logger.info(f"Found new TikTok selector for '{key}': {new_selector}")
                else:
                    # Keep old selector if no alternative found
                    working_selectors[key] = selector
                    logger.info(f"No alternative found for '{key}', keeping original")
        
        # Update selectors
        if working_selectors != self.selectors:
            self.selectors = working_selectors
            self.save_selectors()
            return True
        
        return False
    
    def find_alternative_selector(self, selector_key):
        """Find alternative selector based on common patterns."""
        if not self.driver:
            return None
        
        # Common attribute patterns used by TikTok
        common_attributes = ["data-e2e", "data-testid", "aria-label", "placeholder", "name"]
        
        # Mapping of selector keys to likely attribute patterns
        key_patterns = {
            "file_input": ["upload", "file", "media", "video"],
            "caption_input": ["caption", "text", "description", "input"],
            "upload_button": ["post", "upload", "submit", "publish"],
            "upload_progress": ["progress", "uploading", "processing"],
            "upload_success": ["success", "complete", "done", "uploaded"]
        }
        
        patterns = key_patterns.get(selector_key, [])
        if not patterns:
            return None
        
        # Try to find elements with matching attributes
        for attr in common_attributes:
            for pattern in patterns:
                try:
                    # Find elements with the attribute containing the pattern
                    elements = self.driver.find_elements(By.CSS_SELECTOR, f"[{attr}*='{pattern}']")
                    for element in elements:
                        # Verify this is likely the correct element based on tag, type, etc.
                        if self.verify_element_match(element, selector_key):
                            # Get the CSS selector for this element
                            if attr == "data-e2e":
                                return f"[data-e2e='{element.get_attribute(attr)}']"
                            elif attr == "data-testid":
                                return f"[data-testid='{element.get_attribute(attr)}']"
                            else:
                                # Construct a more specific selector
                                tag = element.tag_name
                                return f"{tag}[{attr}*='{pattern}']"
                except Exception as e:
                    logger.error(f"Error finding alternative for '{selector_key}' with {attr}: {str(e)}")
                    continue
        
        return None
    
    def verify_element_match(self, element, selector_key):
        """Verify if an element is likely to match the required functionality."""
        tag = element.tag_name.lower()
        
        if selector_key == "file_input":
            return tag == "input" and element.get_attribute("type") == "file"
        
        elif selector_key == "caption_input":
            return tag in ["input", "textarea", "div"] and element.is_displayed()
        
        elif selector_key == "upload_button":
            return tag in ["button", "a"] and element.is_displayed()
        
        elif selector_key == "upload_progress" or selector_key == "upload_success":
            return element.is_displayed()
        
        return False
    
    def run(self):
        try:
            self.status_update.emit("Initializing TikTok uploader...")
            self.progress_update.emit(5)
            
            # Setup Chrome options with random user agent to avoid fingerprinting
            chrome_options = Options()
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-notifications")
            chrome_options.add_argument("--window-size=1920,1080")
            
            # Add random user agent
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36 Edg/92.0.902.55"
            ]
            chrome_options.add_argument(f"user-agent={random.choice(user_agents)}")
            
            # Use existing profile if available
            user_data_dir = os.path.join(os.path.expanduser("~"), ".beastclipper", "browser_profile")
            os.makedirs(user_data_dir, exist_ok=True)
            chrome_options.add_argument(f"user-data-dir={user_data_dir}")
            
            # Initialize Chrome driver
            self.status_update.emit("Starting Chrome browser...")
            self.progress_update.emit(10)
            self.driver = webdriver.Chrome(options=chrome_options)
            
            # Go to TikTok upload page
            self.status_update.emit("Navigating to TikTok...")
            self.progress_update.emit(20)
            self.driver.get(self.selectors["upload_page"])
            
            # Wait for upload page to load with randomized wait time
            wait_time = random.uniform(2.0, 5.0)
            time.sleep(wait_time)
            
            # Check if login is required
            if "login" in self.driver.current_url.lower():
                self.status_update.emit("Login required. Attempting to log in...")
                
                try:
                    # Find and fill username field
                    username_field = WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.NAME, "username"))
                    )
                    
                    # Type with random delays
                    for char in self.username:
                        username_field.send_keys(char)
                        time.sleep(random.uniform(0.05, 0.2))
                    
                    # Find and fill password field
                    password_field = self.driver.find_element(By.NAME, "password")
                    
                    # Type with random delays
                    for char in self.password:
                        password_field.send_keys(char)
                        time.sleep(random.uniform(0.05, 0.2))
                    
                    # Click login button
                    login_button = self.driver.find_element(By.XPATH, "//button[@type='submit']")
                    login_button.click()
                    
                    # Wait for login to complete
                    self.status_update.emit("Logging in...")
                    
                    # Wait for redirect to upload page
                    WebDriverWait(self.driver, 60).until(
                        lambda d: "upload" in d.current_url.lower()
                    )
                    
                    self.status_update.emit("Login successful")
                    
                except Exception as e:
                    logger.error(f"Login error: {str(e)}")
                    self.error_occurred.emit(f"Login error: {str(e)}")
                    self.upload_finished.emit(False, self.video_file)
                    return
            
            # Verify selectors
            self.verify_selectors()
            
            # Find file input
            try:
                file_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, self.selectors["file_input"]))
                )
                
                # Add random delay before uploading file to mimic human behavior
                time.sleep(random.uniform(0.5, 2.0))
                
                # Upload the file
                file_input.send_keys(os.path.abspath(self.video_file))
                self.status_update.emit(f"Selected video file: {self.video_file}")
                self.progress_update.emit(40)
                
                # Wait for upload to process with dynamic wait
                try:
                    WebDriverWait(self.driver, 180).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, self.selectors["upload_progress"]))
                    )
                    self.status_update.emit("Video upload processing completed")
                    self.progress_update.emit(60)
                except TimeoutException:
                    self.status_update.emit("Timeout waiting for upload to process, but continuing...")
                    self.progress_update.emit(60)
                
                # Add random delay before caption input
                time.sleep(random.uniform(1.0, 3.0))
                
                # Find and fill caption field
                try:
                    caption_field = WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, self.selectors["caption_input"]))
                    )
                    
                    # Clear existing text if any
                    try:
                        caption_field.clear()
                    except:
                        pass  # Some caption fields can't be cleared
                    
                    # Prepare caption text with hashtags
                    caption_text = self.caption
                    if self.hashtags:
                        caption_text += " " + " ".join([f"#{tag}" for tag in self.hashtags])
                    
                    # Type caption with random delays between characters to mimic human typing
                    for char in caption_text:
                        caption_field.send_keys(char)
                        time.sleep(random.uniform(0.01, 0.1))  # Random delay between keypresses
                    
                    self.status_update.emit(f"Added caption: {caption_text}")
                    self.progress_update.emit(80)
                    
                    # Add random delay before clicking upload button
                    time.sleep(random.uniform(1.0, 3.0))
                    
                    # Find and click upload button
                    upload_button = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, self.selectors["upload_button"]))
                    )
                    upload_button.click()
                    self.status_update.emit("Clicked upload button")
                    self.progress_update.emit(90)
                    
                    # Wait for upload success
                    try:
                        WebDriverWait(self.driver, 180).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, self.selectors["upload_success"]))
                        )
                        self.status_update.emit("Video uploaded successfully")
                        self.progress_update.emit(100)
                        self.upload_finished.emit(True, self.video_file)
                    except TimeoutException:
                        self.status_update.emit("Upload timeout - couldn't confirm if upload was successful")
                        self.progress_update.emit(100)
                        self.upload_finished.emit(False, self.video_file)
                except Exception as e:
                    logger.error(f"Error during caption/upload: {str(e)}")
                    self.error_occurred.emit(f"Error during caption/upload: {str(e)}")
                    self.upload_finished.emit(False, self.video_file)
            except Exception as e:
                logger.error(f"Error finding file input: {str(e)}")
                self.error_occurred.emit(f"Error finding file input: {str(e)}")
                self.upload_finished.emit(False, self.video_file)
        except Exception as e:
            logger.error(f"Error in TikTok upload: {str(e)}")
            self.error_occurred.emit(f"Upload Error: {str(e)}")
            self.upload_finished.emit(False, self.video_file)
            
        finally:
            # Clean up
            if self.driver:
                self.driver.quit()
    
    def stop(self):
        if self.driver:
            self.driver.quit()
