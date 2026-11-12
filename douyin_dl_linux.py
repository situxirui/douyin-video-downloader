#!/usr/bin/env python3
"""
抖音视频下载脚本（Linux + Chrome，无需登录）v1.1

用法：
  python3 douyin_dl_linux.py 7701340688763342115
  python3 douyin_dl_linux.py https://www.douyin.com/video/7701340688763342115
  python3 douyin_dl_linux.py "https://www.douyin.com/jingxuan?modal_id=7701340688763342115"

可选参数：
  --out-dir DIR        保存目录（默认：~/Downloads，没有则用当前目录）
  --duration SECONDS   手动指定目标时长，用来排除页面上自动播放的广告

依赖：
  Python 3.8+、Google Chrome 或 Chromium
  pip install websocket-client imageio-ffmpeg

说明：
  根据视频 ID 或 douyin.com 链接，用无头 Chrome 打开
  https://www.douyin.com/jingxuan?modal_id=<id>
  （未登录时唯一能打开视频的地址），拦截播放流（自动按时长排除广告），
  再用 ffmpeg 合并成 MP4。
  图文帖（/note/、/slides/）会提示后退出，不会当成视频下载。
  如需从 App 分享口令或 v.douyin.com 短链接下载，请使用 douyin_download.py。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:iesdouyin|douyin)\.com/[^\s<>\"\u3000]+"
)

CHROME_PATH = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
DEBUG_PORT = 9227
CAPTURE_TIMEOUT = 45
STARTUP_WAIT = 3
EXPECT_DURATION = None
DOWNLOAD_DIR = None


def default_download_dir():
    downloads = os.path.expanduser("~/Downloads")
    return downloads if os.path.isdir(downloads) else os.getcwd()


def parse_args():
    """Parse --out-dir / --duration, return remaining text (ID or URL)."""
    global EXPECT_DURATION, DOWNLOAD_DIR
    leftover = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--duration" and i + 1 < len(args):
            EXPECT_DURATION = float(args[i + 1])
            i += 2
        elif args[i].startswith("--duration="):
            EXPECT_DURATION = float(args[i].split("=", 1)[1])
            i += 1
        elif args[i] == "--out-dir" and i + 1 < len(args):
            DOWNLOAD_DIR = args[i + 1]
            i += 2
        elif args[i].startswith("--out-dir="):
            DOWNLOAD_DIR = args[i].split("=", 1)[1]
            i += 1
        elif args[i] in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            leftover.append(args[i])
            i += 1
    if DOWNLOAD_DIR is None:
        DOWNLOAD_DIR = default_download_dir()
    return " ".join(leftover).strip()


def extract_video_id(url):
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"modal_id=(\d+)", url)
    if m:
        return m.group(1)
    return None


def get_video_url(raw):
    if not raw:
        print('用法: python3 douyin_dl_linux.py "<视频ID | douyin.com 链接>"')
        sys.exit(1)
    if "v.douyin.com" in raw:
        print("检测到 v.douyin.com 短链接。本脚本只支持视频 ID 或 douyin.com 完整链接。")
        print("请改用 douyin_download.py（v1.2）来解析分享口令和短链接。")
        sys.exit(1)
    if raw.isdigit():
        return f"https://www.douyin.com/jingxuan?modal_id={raw}", raw
    m = URL_RE.search(raw)
    if not m:
        print(f"没有在输入里找到视频 ID 或 douyin.com 链接：{raw[:100]}")
        sys.exit(1)
    url = m.group(0).rstrip(".,;)]}>\"'")
    mm = re.search(r"/(note|slides)/(\d+)", url)
    if mm:
        print(f"这是图文帖（/{mm.group(1)}/{mm.group(2)}），不是视频，无法下载为 MP4。")
        sys.exit(2)
    vid = extract_video_id(url)
    if not vid:
        print(f"地址里找不到视频 ID：{url}")
        sys.exit(1)
    return f"https://www.douyin.com/jingxuan?modal_id={vid}", vid


def is_media_url(req_url):
    hosts = ("douyinvod.com", "zjcdn.com", "bytecdn", "douyinstatic", "/video/tos/")
    return any(h in req_url for h in hosts) and ("media-video" in req_url or "media-audio" in req_url)


def capture_video_urls(video_url, debug_port=DEBUG_PORT):
    global EXPECT_DURATION
    if not CHROME_PATH:
        print("找不到 Google Chrome 或 Chromium，请先安装后再运行。")
        sys.exit(1)

    temp_profile = tempfile.mkdtemp(prefix="chrome_douyin_dl_")
    proc = subprocess.Popen(
        [
            CHROME_PATH,
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={temp_profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--remote-allow-origins=*",
            "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",
            f"--user-agent={DESKTOP_UA}",
            "--window-size=1280,720",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(STARTUP_WAIT)
    captured_urls = []

    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json")
        tabs = json.loads(resp.read().decode())
        target_tab = next((t for t in tabs if t.get("type") == "page"), None)
        if not target_tab or not target_tab.get("webSocketDebuggerUrl"):
            print("无法连接 Chrome 调试端口。")
            return None, None

        ws = websocket.create_connection(target_tab["webSocketDebuggerUrl"], timeout=30)
        ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {"maxPostDataSize": 65536}}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {"url": video_url}}))
        ws.recv()

        print("正在打开页面并拦截播放流……")
        start_time = time.time()
        while time.time() - start_time < CAPTURE_TIMEOUT:
            try:
                ws.settimeout(2)
                data = json.loads(ws.recv())
                if data.get("method") == "Network.requestWillBeSent":
                    req_url = data["params"]["request"]["url"]
                    if is_media_url(req_url):
                        captured_urls.append(req_url)
                        kind = "视频" if "media-video" in req_url else "音频"
                        print(f"  已捕获{kind}流")
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break

        try:
            ws.settimeout(10)
            ws.send(json.dumps({
                "id": 99,
                "method": "Runtime.evaluate",
                "params": {
                    "returnByValue": True,
                    "expression": "JSON.stringify({t:document.title,v:[...document.querySelectorAll('video')].map(v=>({d:v.duration,p:v.paused,c:v.currentTime}))})",
                },
            }))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == 99:
                    info = json.loads(r["result"]["result"]["value"])
                    break
            print(f"页面标题：{info['t']}")
            playing = [v for v in info["v"] if not v["p"] and v["d"] and v["d"] == v["d"]]
            if EXPECT_DURATION is None and playing:
                EXPECT_DURATION = max(playing, key=lambda v: v["c"])["d"]
                print(f"自动识别目标时长：{EXPECT_DURATION:.2f} 秒")
        except Exception as e:
            print(f"自动识别时长失败：{e}")
        ws.close()
        return pick_streams(captured_urls)
    except Exception as e:
        print(f"拦截播放流出错：{e}")
        return None, None
    finally:
        proc.kill()
        proc.wait()
        shutil.rmtree(temp_profile, ignore_errors=True)


def download_file(url, filepath):
    headers = {"User-Agent": DESKTOP_UA + " Edg/120.0.0.0", "Referer": "https://www.douyin.com/"}
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=120)
    total = int(resp.headers.get("Content-Length", 0))
    print(f"  大小：{total / 1024 / 1024:.1f} MB")
    with open(filepath, "wb") as f:
        downloaded = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            pct = downloaded * 100 / total if total else 0
            print(f"\r  进度：{pct:.0f}%", end="", flush=True)
    print(" 完成")


def probe_duration(url):
    import imageio_ffmpeg

    fd, tmp = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        download_file(url, tmp)
        out = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-i", tmp],
            capture_output=True,
            text=True,
        ).stderr
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    finally:
        os.remove(tmp)


def pick_streams(urls):
    uniq = {}
    for u in urls:
        uniq.setdefault(u.split("?")[0], u)
    vids = [u for u in uniq.values() if "media-video" in u]
    auds = [u for u in uniq.values() if "media-audio" in u]
    if EXPECT_DURATION and (len(vids) > 1 or len(auds) > 1):
        key = lambda u: abs(probe_duration(u) - EXPECT_DURATION)
        return (min(vids, key=key) if vids else None), (min(auds, key=key) if auds else None)
    return (vids[-1] if vids else None), (auds[-1] if auds else None)


def merge_av(video_file, audio_file, output_file):
    import imageio_ffmpeg

    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-i", video_file,
        "-i", audio_file,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        "-y",
        output_file,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result.returncode == 0


def main():
    raw = parse_args()
    video_url, video_id = get_video_url(raw)
    print(f"\n视频地址：{video_url}")
    print(f"视频 ID：{video_id}")

    print("\n--- 启动无头 Chrome，拦截播放流 ---")
    video_cdn, audio_cdn = capture_video_urls(video_url)
    if not video_cdn:
        print("没有捕获到视频流。")
        sys.exit(1)
    print(f"\n视频流：已找到")
    print(f"音频流：{'已找到' if audio_cdn else '未找到'}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    v_file = os.path.join(DOWNLOAD_DIR, f"douyin_{video_id}_v.mp4")
    a_file = os.path.join(DOWNLOAD_DIR, f"douyin_{video_id}_a.mp4")
    output_file = os.path.join(DOWNLOAD_DIR, f"douyin_{video_id}.mp4")

    print("\n--- 下载视频流 ---")
    download_file(video_cdn, v_file)
    if audio_cdn:
        print("\n--- 下载音频流 ---")
        download_file(audio_cdn, a_file)

    print("\n--- 合并音视频 ---")
    if audio_cdn and os.path.exists(a_file):
        if merge_av(v_file, a_file, output_file):
            os.remove(v_file)
            os.remove(a_file)
            print("合并成功")
        else:
            os.rename(v_file, output_file)
            print("合并失败，只保留了视频轨")
    else:
        os.rename(v_file, output_file)

    size_mb = os.path.getsize(output_file) / 1024 / 1024
    print(f"\n=== 下载完成 ===")
    print(f"文件：{output_file}")
    print(f"大小：{size_mb:.1f} MB")


if __name__ == "__main__":
    main()
