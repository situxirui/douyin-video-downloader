# Douyin Video Downloader (抖音视频下载器)

A lightweight Python tool to download Douyin (TikTok China) videos without relying on third-party API signature reverse-engineering.

## Why This Tool?

Popular tools like `yt-dlp` and `f2` frequently break because Douyin constantly updates its anti-crawler signature algorithms (`a_bogus`, `msToken`, etc.). When these signatures change, all API-based tools return `403 Forbidden`.

This tool takes a different approach: **it lets the real Edge browser handle all authentication and token generation**, then simply intercepts the resulting media CDN URLs via Chrome DevTools Protocol (CDP). This means it keeps working even when Douyin updates their signature algorithms.

## How It Works

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────┐
│  Resolve URL │───▶│ Launch Headless   │───▶│ CDP Network     │───▶│ Download │
│  (arg/history)│    │ Edge Browser      │    │ Interception    │    │ & Merge  │
└─────────────┘    └──────────────────┘    └─────────────────┘    └──────────┘
                           │                       │                     │
                    Visits video page       Captures CDN URLs      Downloads video
                    Real browser handles    from douyinvod.com     + audio streams,
                    all auth & signatures   (DASH: video + audio)  merges with ffmpeg
```

1. **Launch headless Edge** — starts a headless Microsoft Edge instance with remote debugging enabled
2. **Navigate to video page** — the real browser handles all cookies, tokens, and signature generation
3. **Intercept network requests** — via CDP `Network.requestWillBeSent` events, captures CDN URLs from `douyinvod.com`
4. **Download streams** — Douyin uses DASH streaming (video and audio are separate streams), both are downloaded with proper `Referer` header
5. **Merge** — ffmpeg combines the video and audio streams into a single MP4 file

## Requirements

- **Python 3.8+**
- **Microsoft Edge browser** (installed on Windows by default)
- **ffmpeg** — provided automatically via `imageio-ffmpeg` (no separate install needed)

## Installation

```bash
pip install websocket-client imageio-ffmpeg
```

## Usage

### Download by video ID

```bash
python douyin_dl.py 7529381510349180223
```

### Download by full URL

```bash
python douyin_dl.py https://www.douyin.com/video/7529381510349180223
```

### Auto-detect from Edge history

If you recently watched a Douyin video in Edge, just run without arguments:

```bash
python douyin_dl.py
```

The tool will search your Edge browser history for the most recently visited Douyin video URL.

## Output

Downloaded files are saved to:

```
~/Downloads/douyin_<video_id>.mp4
```

## Technical Details

### DASH Streaming

Douyin serves video and audio as separate streams:
- **Video stream**: URL contains `media-video-avc1` (H.264 encoded)
- **Audio stream**: URL contains `media-audio-und-mp4a` (AAC encoded)

Both streams are downloaded separately and merged using:
```
ffmpeg -i video.mp4 -i audio.mp4 -c:v copy -c:a aac -shortest -y output.mp4
```

### CDP Interception

The tool uses Chrome DevTools Protocol to:
1. Enable `Network.enable` to monitor all network requests
2. Navigate to the video page via `Page.navigate`
3. Listen for `Network.requestWillBeSent` events
4. Filter for `douyinvod.com` URLs containing media stream identifiers

### Edge History Detection

When no argument is provided, the tool reads Edge's SQLite history database (`%LOCALAPPDATA%/Microsoft/Edge/User Data/Default/History`) to find the most recently visited Douyin video URL. It uses Win32 API with shared read access to bypass Edge's file lock.

## Limitations

- **Windows only** — relies on Microsoft Edge and Windows-specific APIs for history detection
- **Requires Edge browser** — the tool launches Edge for CDP interception
- **No batch download** — downloads one video at a time
- **Login-required videos** — for videos that require login, you may need to log in to Douyin in the headless Edge profile first

## License

MIT
