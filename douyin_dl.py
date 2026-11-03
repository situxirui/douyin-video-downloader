#!/usr/bin/env python3
"""
Douyin Video Downloader (抖音视频下载器)
=========================================

A lightweight tool to download Douyin (TikTok China) videos without relying on
third-party API signature reverse-engineering.

How it works:
  1. Launches a headless Microsoft Edge browser to visit the video page.
  2. Uses Chrome DevTools Protocol (CDP) to intercept network requests.
  3. Captures CDN URLs for video and audio streams from douyinvod.com.
  4. Downloads both streams separately (with proper Referer header).
  5. Merges them into a single MP4 file using ffmpeg.

This approach bypasses Douyin's anti-crawler signature mechanisms (a_bogus,
msToken, etc.) by letting the real browser handle all authentication and
token generation, then simply intercepting the resulting media URLs.

Requirements:
  - Python 3.8+
  - Microsoft Edge browser installed
  - pip install websocket-client imageio-ffmpeg

Usage:
  python douyin_dl.py <video_url_or_id>    # Download by URL or video ID
  python douyin_dl.py                       # Auto-find latest Douyin video from Edge history

Examples:
  python douyin_dl.py https://www.douyin.com/video/7529381510349180223
  python douyin_dl.py 7529381510349180223
  python douyin_dl.py                       # Auto-detect from browser history

Output:
  Saved to ~/Downloads/douyin_<video_id>.mp4

Author: william
License: MIT
"""

import subprocess
import time
import json
import os
import sys
import urllib.request
import re
import websocket


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Edge browser executable path (Windows default)
EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# CDP debugging port for the headless Edge instance
DEBUG_PORT = 9227

# Download output directory
DOWNLOAD_DIR = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'Downloads')

# How long to wait for network requests (seconds)
CAPTURE_TIMEOUT = 25

# How long to wait for Edge to start (seconds)
EDGE_STARTUP_WAIT = 3


# ---------------------------------------------------------------------------
# URL & ID helpers
# ---------------------------------------------------------------------------

def get_video_url_from_arg():
    """
    Resolve the video URL from command-line arguments.
    
    - If a full URL is given, use it directly.
    - If a numeric ID is given, construct the full URL.
    - If no argument is given, auto-detect from Edge browser history.
    
    Returns:
        str: The full Douyin video URL.
    """
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.startswith('http'):
            return arg
        elif arg.isdigit():
            return f"https://www.douyin.com/video/{arg}"
        else:
            print(f"Invalid argument: {arg}")
            print("Expected a Douyin video URL or numeric video ID.")
            sys.exit(1)
    else:
        return find_latest_douyin_from_edge()


def find_latest_douyin_from_edge():
    """
    Search the Edge browser history database for the most recently visited
    Douyin video URL.
    
    Uses a file copy approach to avoid conflicts with the running browser
    (Edge locks its History SQLite database while running).
    
    Returns:
        str: The most recently visited Douyin video URL.
    """
    import sqlite3
    import shutil
    import ctypes

    edge_profile = os.path.join(
        os.environ.get('LOCALAPPDATA', ''),
        'Microsoft', 'Edge', 'User Data', 'Default'
    )
    history_db = os.path.join(edge_profile, 'History')
    temp_copy = os.path.join(os.environ.get('TEMP', ''), 'edge_history_tmp')

    try:
        shutil.copy2(history_db, temp_copy)
    except PermissionError:
        # Edge locks the file; use Win32 API with shared read access
        GENERIC_READ = 0x80000000
        FILE_SHARE_ALL = 0x07  # READ | WRITE | DELETE
        OPEN_EXISTING = 3
        handle = ctypes.windll.kernel32.CreateFileW(
            history_db, GENERIC_READ, FILE_SHARE_ALL,
            None, OPEN_EXISTING, 0, None
        )
        if handle != -1:
            ctypes.windll.kernel32.CloseHandle(handle)
            shutil.copy2(history_db, temp_copy)
        else:
            raise PermissionError("Cannot read Edge history database")

    conn = sqlite3.connect(temp_copy)
    c = conn.cursor()

    # Try /video/ format first, then modal_id format
    c.execute(
        "SELECT url FROM urls WHERE url LIKE '%douyin%' AND url LIKE '%/video/%' "
        "ORDER BY last_visit_time DESC LIMIT 1"
    )
    row = c.fetchone()
    if not row:
        c.execute(
            "SELECT url FROM urls WHERE url LIKE '%douyin%' AND url LIKE '%modal_id%' "
            "ORDER BY last_visit_time DESC LIMIT 1"
        )
        row = c.fetchone()
    conn.close()
    os.remove(temp_copy)

    if row:
        url = row[0]
        print(f"Found in Edge history: {url}")
        return url
    else:
        print("No Douyin video found in Edge history.")
        sys.exit(1)


def extract_video_id(url):
    """
    Extract the numeric video ID from a Douyin URL.
    
    Supports both /video/<id> and ?modal_id=<id> formats.
    
    Args:
        url: A Douyin video URL.
        
    Returns:
        str: The numeric video ID, or 'unknown' if not found.
    """
    m = re.search(r'/video/(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'modal_id=(\d+)', url)
    if m:
        return m.group(1)
    return 'unknown'


# ---------------------------------------------------------------------------
# CDP network interception
# ---------------------------------------------------------------------------

def capture_video_urls(video_url, debug_port=DEBUG_PORT):
    """
    Launch a headless Edge browser, navigate to the video page, and intercept
    the CDN URLs for video and audio streams via Chrome DevTools Protocol.
    
    Douyin uses DASH streaming: video and audio are served as separate streams
    from douyinvod.com. This function captures both.
    
    Args:
        video_url: The full Douyin video page URL.
        debug_port: The port for Edge's remote debugging (default: 9227).
        
    Returns:
        tuple: (video_cdn_url, audio_cdn_url) or (None, None) on failure.
    """
    temp_profile = os.path.join(os.environ.get('TEMP', ''), 'edge_douyin_dl')
    os.makedirs(temp_profile, exist_ok=True)

    # Launch headless Edge with remote debugging enabled
    proc = subprocess.Popen([
        EDGE_PATH,
        f'--remote-debugging-port={debug_port}',
        f'--user-data-dir={temp_profile}',
        '--no-first-run',
        '--no-default-browser-check',
        '--headless=new',
        '--disable-gpu',
        '--window-size=1280,720',
        'about:blank'
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    time.sleep(EDGE_STARTUP_WAIT)

    captured_urls = []

    try:
        # Connect to Edge's CDP endpoint
        resp = urllib.request.urlopen(f'http://127.0.0.1:{debug_port}/json')
        tabs = json.loads(resp.read().decode())
        target_tab = next((t for t in tabs if t.get('type') == 'page'), None)

        if not target_tab or not target_tab.get('webSocketDebuggerUrl'):
            print("Failed to connect to Edge debugging endpoint.")
            return None, None

        ws = websocket.create_connection(target_tab['webSocketDebuggerUrl'], timeout=30)

        # Enable network monitoring and page navigation
        ws.send(json.dumps({"id": 1, "method": "Network.enable",
                            "params": {"maxPostDataSize": 65536}}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        ws.recv()

        # Navigate to the video page
        ws.send(json.dumps({"id": 3, "method": "Page.navigate",
                            "params": {"url": video_url}}))
        ws.recv()

        print("Waiting for page to load, monitoring network requests...")
        start_time = time.time()

        while time.time() - start_time < CAPTURE_TIMEOUT:
            try:
                ws.settimeout(2)
                msg = ws.recv()
                data = json.loads(msg)

                if data.get('method') == 'Network.requestWillBeSent':
                    req_url = data['params']['request']['url']

                    # Filter for Douyin CDN media stream URLs
                    if 'douyinvod.com' in req_url:
                        if 'media-video' in req_url or 'media-audio' in req_url:
                            captured_urls.append(req_url)
                            stream_type = 'video' if 'media-video' in req_url else 'audio'
                            print(f"  Captured {stream_type} stream URL")

            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break

        ws.close()

        # Pick the last captured URL for each stream type
        video_cdn = None
        audio_cdn = None
        for u in captured_urls:
            if 'media-video' in u:
                video_cdn = u
            elif 'media-audio' in u:
                audio_cdn = u

        return video_cdn, audio_cdn

    except Exception as e:
        print(f"Network interception error: {e}")
        return None, None
    finally:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# Download & merge
# ---------------------------------------------------------------------------

def download_file(url, filepath):
    """
    Download a file from the given URL with proper headers.
    
    The Referer header is required by Douyin's CDN — requests without it
    will receive a 403 Forbidden response.
    
    Args:
        url: The CDN URL to download from.
        filepath: The local path to save the file.
    """
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0'
        ),
        'Referer': 'https://www.douyin.com/',
    }
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=120)
    total = int(resp.headers.get('Content-Length', 0))
    print(f"  Size: {total / 1024 / 1024:.1f} MB")

    with open(filepath, 'wb') as f:
        downloaded = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            pct = downloaded * 100 / total if total else 0
            print(f"\r  Progress: {pct:.0f}%", end='', flush=True)
    print(" done")


def merge_av(video_file, audio_file, output_file):
    """
    Merge separate video and audio files into a single MP4 using ffmpeg.
    
    Video stream is copied (no re-encoding), audio is encoded as AAC.
    The -shortest flag ensures output duration matches the shorter stream.
    
    Args:
        video_file: Path to the downloaded video-only stream.
        audio_file: Path to the downloaded audio-only stream.
        output_file: Path for the merged output file.
        
    Returns:
        bool: True if merge succeeded, False otherwise.
    """
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, '-i', video_file, '-i', audio_file,
        '-c:v', 'copy', '-c:a', 'aac',
        '-shortest', '-y', output_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    """Main download workflow: resolve URL → capture streams → download → merge."""
    # Step 1: Get the video URL
    video_url = get_video_url_from_arg()
    video_id = extract_video_id(video_url)
    print(f"\nVideo URL: {video_url}")
    print(f"Video ID: {video_id}")

    # Step 2: Capture CDN stream URLs via headless Edge + CDP
    print("\n--- Launching headless Edge to capture stream URLs ---")
    video_cdn, audio_cdn = capture_video_urls(video_url)

    if not video_cdn:
        print("Failed to capture video stream URL!")
        sys.exit(1)

    print(f"\nVideo stream: {'found' if video_cdn else 'not found'}")
    print(f"Audio stream: {'found' if audio_cdn else 'not found'}")

    # Step 3: Download the streams
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    v_file = os.path.join(DOWNLOAD_DIR, f'douyin_{video_id}_v.mp4')
    a_file = os.path.join(DOWNLOAD_DIR, f'douyin_{video_id}_a.mp4')
    output_file = os.path.join(DOWNLOAD_DIR, f'douyin_{video_id}.mp4')

    print("\n--- Downloading video stream ---")
    download_file(video_cdn, v_file)

    if audio_cdn:
        print("\n--- Downloading audio stream ---")
        download_file(audio_cdn, a_file)

    # Step 4: Merge video + audio
    print("\n--- Merging audio and video ---")
    if audio_cdn and os.path.exists(a_file):
        success = merge_av(v_file, a_file, output_file)
        if success:
            os.remove(v_file)
            os.remove(a_file)
            print("Merge successful")
        else:
            # Fallback: keep video-only if merge fails
            os.rename(v_file, output_file)
            print("Merge failed, keeping video-only stream")
    else:
        os.rename(v_file, output_file)

    size_mb = os.path.getsize(output_file) / 1024 / 1024
    print(f"\n=== Download complete ===")
    print(f"File: {output_file}")
    print(f"Size: {size_mb:.1f} MB")


if __name__ == '__main__':
    main()
