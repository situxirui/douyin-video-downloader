#!/usr/bin/env python3
"""
微信视频号下载脚本（Windows / macOS / Linux）

用法：
  python3 wx_channels_download.py "https://weixin.qq.com/sph/xxxx"
  python3 wx_channels_download.py "分享文字里带链接也行"
  python3 wx_channels_download.py 链接1 链接2 链接3
  python3 wx_channels_download.py --input-file links.txt
  echo "分享文字" | python3 wx_channels_download.py -
  python3 wx_channels_download.py menu
  python3 wx_channels_download.py login
  python3 wx_channels_download.py status
  python3 wx_channels_download.py logout

双击启动：
  Windows：双击 wx_channels_download.bat
  macOS：  双击 wx_channels_download.command

可选参数：
  --out-dir DIR        保存目录（默认：~/Downloads，没有则用当前目录）
  --input-file FILE    从 UTF-8 文本文件读取链接或分享文字（可用 # 开头写注释）
  --cookie-file FILE   从文件读取元宝 Cookie（整段 Cookie 头）
  --quality h264|h265  优先编码（默认 h264，缺失时自动换另一种）

说明：
  微信官方分享页（weixin.qq.com/sph/...）只会跳到预览页，给出封面、标题和
  「可扫码前往微信观看」，普通浏览器拦不到播放地址，这一点和抖音不同。
  本脚本先向官方预览接口取标题和作者；要下载视频，需要登录一次腾讯元宝
  （yuanbao.tencent.com），用它的解析接口换到播放地址后再下载。
  Cookie 只保存在本机（权限尽量设为仅本人可读），不会打印出来。

依赖：
  Python 3.8+
  登录时需要 Google Chrome、Microsoft Edge 或 Chromium（需带图形界面，方便扫码）
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "1.4"

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SPH_RE = re.compile(
    r"https?://(?:weixin\.qq\.com/sph/|channels\.weixin\.qq\.com/finder-preview/pages/sph[^\s<>\"\u3000]*)[^\s<>\"\u3000]*",
    re.I,
)
SHORT_ID_RE = re.compile(r"(?:/sph/|\bid=)([A-Za-z0-9]+)")
BARE_ID_RE = re.compile(r"^[A-Za-z0-9]{6,32}$")
YUANBAO_PARSE = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
FEED_INFO = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
AUTH_COMMANDS = {"login", "status", "logout", "menu"}
END_MARKERS = {"END", "end", "结束"}

DOWNLOAD_DIR = None
COOKIE_FILE = None
INPUT_FILE = None
QUALITY = "h264"


class DownloadError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def configure_stdio():
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def default_download_dir():
    downloads = os.path.expanduser("~/Downloads")
    return downloads if os.path.isdir(downloads) else os.getcwd()


def auth_dir():
    override = os.environ.get("WX_CHANNELS_AUTH_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "wx-channels")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(xdg, "wx-channels")
    return os.path.join(os.path.expanduser("~/.config"), "wx-channels")


def auth_file():
    return os.path.join(auth_dir(), "auth.json")


def has_display():
    if os.environ.get("WX_CHANNELS_FORCE_HEADLESS"):
        return False
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def iter_browser_candidates():
    """Yield existing Chrome / Edge / Chromium executables, Chrome first."""
    home = os.path.expanduser("~")
    names = []
    if sys.platform == "win32":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA") or ""
        names = [
            shutil.which("chrome"),
            shutil.which("chrome.exe"),
            os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
            shutil.which("msedge"),
            shutil.which("msedge.exe"),
            os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(local, r"Microsoft\Edge\Application\msedge.exe"),
            shutil.which("chromium"),
            shutil.which("chromium.exe"),
        ]
    elif sys.platform == "darwin":
        names = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.join(home, "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            shutil.which("microsoft-edge"),
            shutil.which("msedge"),
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            os.path.join(home, "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            shutil.which("chromium"),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            os.path.join(home, "Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:
        names = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            shutil.which("microsoft-edge"),
            shutil.which("microsoft-edge-stable"),
            shutil.which("microsoft-edge-beta"),
        ]
    seen = set()
    for path in names:
        if not path:
            continue
        path = os.path.normpath(path)
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(path):
            yield path


def find_browser():
    return next(iter_browser_candidates(), None)


def browser_display_name(path):
    lower = os.path.basename(path).lower()
    if "edge" in lower or lower == "msedge" or lower == "msedge.exe":
        return "Microsoft Edge"
    if "chromium" in lower:
        return "Chromium"
    return "Google Chrome"


def browser_requirements_message():
    if sys.platform == "win32":
        return (
            "找不到 Google Chrome 或 Microsoft Edge。\n"
            "请先安装其中一个浏览器后再运行 login：\n"
            "  Chrome：https://www.google.com/chrome/\n"
            "  Edge：通常已随 Windows 安装，也可到 https://www.microsoft.com/edge"
        )
    if sys.platform == "darwin":
        return (
            "找不到 Google Chrome 或 Microsoft Edge。\n"
            "请先把浏览器装到「应用程序」文件夹后再运行 login：\n"
            "  Chrome：https://www.google.com/chrome/\n"
            "  Edge：https://www.microsoft.com/edge"
        )
    return (
        "找不到 Google Chrome、Chromium 或 Microsoft Edge。\n"
        "请先安装其中一个浏览器后再运行 login，例如：\n"
        "  sudo apt install google-chrome-stable   或   sudo apt install chromium"
    )


def needs_chrome_no_sandbox():
    if os.environ.get("WX_CHANNELS_NO_SANDBOX"):
        return True
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def pick_free_port():
    import socket

    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def parse_args(argv=None):
    global DOWNLOAD_DIR, COOKIE_FILE, INPUT_FILE, QUALITY
    leftover = []
    args = list(sys.argv[1:] if argv is None else argv)
    i = 0
    while i < len(args):
        if args[i] == "--out-dir" and i + 1 < len(args):
            DOWNLOAD_DIR = args[i + 1]
            i += 2
        elif args[i].startswith("--out-dir="):
            DOWNLOAD_DIR = args[i].split("=", 1)[1]
            i += 1
        elif args[i] in ("--input-file", "-i") and i + 1 < len(args):
            INPUT_FILE = args[i + 1]
            i += 2
        elif args[i].startswith("--input-file="):
            INPUT_FILE = args[i].split("=", 1)[1]
            i += 1
        elif args[i] == "--cookie-file" and i + 1 < len(args):
            COOKIE_FILE = args[i + 1]
            i += 2
        elif args[i].startswith("--cookie-file="):
            COOKIE_FILE = args[i].split("=", 1)[1]
            i += 1
        elif args[i] == "--quality" and i + 1 < len(args):
            QUALITY = args[i + 1].lower()
            i += 2
        elif args[i].startswith("--quality="):
            QUALITY = args[i].split("=", 1)[1].lower()
            i += 1
        elif args[i] in ("-h", "--help"):
            print(__doc__.strip())
            sys.exit(0)
        elif args[i] in ("-V", "--version"):
            print(__version__)
            sys.exit(0)
        else:
            leftover.append(args[i])
            i += 1
    if DOWNLOAD_DIR is None:
        DOWNLOAD_DIR = default_download_dir()
    if QUALITY not in ("h264", "h265"):
        print("quality 只能是 h264 或 h265")
        sys.exit(1)
    return leftover


def http_json(url, method="GET", data=None, headers=None, timeout=30):
    hdrs = {"User-Agent": DESKTOP_UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", "replace")
            try:
                parsed = json.loads(text) if text else {}
            except json.JSONDecodeError:
                parsed = {"_raw": text}
            return resp.status, dict(resp.headers), parsed, text
    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode("utf-8", "replace") if raw else ""
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError:
            parsed = {"_raw": text}
        return e.code, dict(e.headers or {}), parsed, text


def normalize_share_url(url):
    return url.rstrip(".,;)]}>\"'`，。；、）】》")


def short_id_from_url(url):
    m = SHORT_ID_RE.search(url or "")
    return m.group(1) if m else None


def share_key(url):
    return (short_id_from_url(url) or url).lower()


def public_url(url):
    """Return a URL safe to print: drop query and fragment that may contain tokens."""
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))
    return url


def iter_raw_share_urls(text):
    """Yield share URLs in the order they appear, including duplicates."""
    for match in SPH_RE.finditer(text or ""):
        url = normalize_share_url(match.group(0).strip())
        if url:
            yield url
    for line in (text or "").splitlines():
        token = line.strip()
        if not token or token.startswith("#"):
            continue
        if BARE_ID_RE.fullmatch(token):
            yield "https://weixin.qq.com/sph/" + token


def find_share_urls(text):
    """Extract unique video-share URLs from pasted text, keeping first-seen order."""
    found = []
    seen = set()
    for url in iter_raw_share_urls(text):
        key = share_key(url)
        if key in seen:
            continue
        seen.add(key)
        found.append(url)
    return found


def extract_share_url(raw):
    """Backward-compatible helper: return the first share URL or exit."""
    if raw == "-":
        raw = sys.stdin.read()
    urls = find_share_urls(raw or "")
    if urls:
        return urls[0]
    token = (raw or "").strip()
    if BARE_ID_RE.fullmatch(token):
        return f"https://weixin.qq.com/sph/{token}"
    sample = re.sub(r"\s+", " ", (raw or "").strip())[:120]
    if sample:
        print(f"没有在输入里找到视频号链接：{sample}")
    else:
        print('用法: python3 wx_channels_download.py "<分享文字 | 链接>"')
    sys.exit(1)


def read_text_file(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except UnicodeDecodeError:
        raise DownloadError(f"输入文件不是有效的 UTF-8 文本：{path}", 1)
    except OSError:
        raise DownloadError(f"无法读取输入文件：{path}", 1)


def collect_share_urls(leftover, input_file=None, stdin_text=None):
    """
    Collect unique share URLs from CLI args, an optional file, and stdin text.
    leftover items equal to '-' mean "read stdin".
    Returns (urls, skipped_duplicates).
    """
    chunks = []
    read_stdin = False
    for item in leftover or []:
        if item == "-":
            read_stdin = True
        else:
            chunks.append(item)
    if input_file:
        chunks.append(read_text_file(input_file))
    if read_stdin:
        if stdin_text is None:
            stdin_text = sys.stdin.read()
        chunks.append(stdin_text or "")

    found = []
    seen = set()
    skipped = 0
    for chunk in chunks:
        for url in iter_raw_share_urls(chunk if chunk is not None else ""):
            key = share_key(url)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            found.append(url)
    return found, skipped


def resolve_short_url(url, max_hops=6):
    current = url
    for _ in range(max_hops):
        p = urllib.parse.urlparse(current)
        if "channels.weixin.qq.com" in p.netloc and "id=" in (p.query or ""):
            return current
        conn_cls = http.client.HTTPSConnection if p.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(p.netloc, timeout=20)
        path = p.path + (("?" + p.query) if p.query else "")
        try:
            conn.request("GET", path, headers={"User-Agent": DESKTOP_UA})
            resp = conn.getresponse()
            loc = resp.getheader("Location")
            resp.read()
            status = resp.status
        finally:
            conn.close()
        if status in (301, 302, 303, 307, 308) and loc:
            current = urllib.parse.urljoin(current, loc)
        else:
            break
    return current


def get_feed_info(short_id=None, export_id=None, token=""):
    payload = {"baseReq": {"generalToken": token or ""}}
    if short_id:
        payload["shortUri"] = short_id
    if export_id:
        payload["exportId"] = export_id
    referer = "https://channels.weixin.qq.com/finder-preview/pages/sph"
    if short_id:
        referer += f"?id={short_id}"
    qs = []
    if token:
        qs.append("token=" + urllib.parse.quote(token))
    if export_id:
        qs.append("eid=" + urllib.parse.quote(export_id))
    if qs:
        referer += ("&" if "?" in referer else "?") + "&".join(qs)
    status, _, data, _ = http_json(
        FEED_INFO,
        method="POST",
        data=payload,
        headers={
            "Origin": "https://channels.weixin.qq.com",
            "Referer": referer,
        },
    )
    return status, data


def feed_meta(data):
    d = (data or {}).get("data") or {}
    feed = d.get("feedInfo") or {}
    author = d.get("authorInfo") or {}
    scene = d.get("sceneInfo") or {}
    err = d.get("errMsg") or {}
    return {
        "title": (feed.get("description") or "").strip(),
        "author": (author.get("nickname") or "").strip(),
        "cover": feed.get("coverUrl") or "",
        "pics": feed.get("picInfo") or [],
        "export_id": scene.get("dynamicExportId") or "",
        "err_title": err.get("title") or "",
        "err_type": err.get("type") or 0,
        "h264": ((feed.get("h264VideoInfo") or {}).get("videoUrl") or feed.get("videoUrl") or ""),
        "h265": ((feed.get("h265VideoInfo") or {}).get("videoUrl") or ""),
        "raw_feed": feed,
    }


def pick_video_url(meta, quality=None):
    preferred = quality or QUALITY
    other = "h265" if preferred == "h264" else "h264"
    url = meta.get(preferred) or meta.get(other) or ""
    kind = preferred if meta.get(preferred) else (other if meta.get(other) else "")
    return url, kind


def safe_filename(name, fallback):
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", name).strip(" ._")
    name = name[:80] if name else fallback
    return name or fallback


def load_cookie_header():
    if COOKIE_FILE:
        text = open(COOKIE_FILE, encoding="utf-8").read().strip()
        if text.lower().startswith("cookie:"):
            text = text.split(":", 1)[1].strip()
        return text
    env = os.environ.get("YUANBAO_COOKIE") or os.environ.get("WX_CHANNELS_COOKIE")
    if env:
        return env.strip()
    path = auth_file()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return ""
        header = (data.get("cookie") or "").strip()
        if header:
            return header
        cookies = data.get("cookies") or {}
        if isinstance(cookies, dict) and cookies:
            return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    return ""


def cookie_source_label():
    if COOKIE_FILE:
        return "--cookie-file"
    if os.environ.get("YUANBAO_COOKIE") or os.environ.get("WX_CHANNELS_COOKIE"):
        return "环境变量"
    if os.path.isfile(auth_file()):
        return "本机凭据文件"
    return "无"


def cookie_has_login(header):
    if not header:
        return False
    parts = {}
    for item in header.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            parts[k.strip()] = v.strip()
    return bool(parts.get("hy_user") and parts.get("hy_token"))


def save_auth(cookie_header, source="login"):
    directory = auth_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    payload = {
        "cookie": cookie_header,
        "saved_at": int(time.time()),
        "source": source,
    }
    path = auth_file()
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def cmd_status():
    header = load_cookie_header()
    path = auth_file()
    exists = os.path.isfile(path)
    print(f"凭据文件：{path}")
    print(f"文件存在：{'是' if exists else '否'}")
    print(f"已保存元宝登录：{'是' if cookie_has_login(header) else '否'}")
    print(f"凭据来源：{cookie_source_label()}")
    if exists:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            ts = data.get("saved_at")
            if ts:
                print("保存时间：", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)))
        except Exception:
            pass
    print("服务端是否仍有效，要到实际下载时才能确认。")


def cmd_logout():
    path = auth_file()
    if os.path.isfile(path):
        os.remove(path)
        print("已删除本机保存的元宝登录信息，没有注销元宝网页上的会话。")
    else:
        print("本机没有保存的登录信息。")


def _chrome_cookies(ws, url):
    mid = int(time.time() * 1000) % 1000000
    ws.send(json.dumps({"id": mid, "method": "Network.getCookies", "params": {"urls": [url]}}))
    deadline = time.time() + 10
    while time.time() < deadline:
        ws.settimeout(2)
        try:
            data = json.loads(ws.recv())
        except Exception:
            continue
        if data.get("id") == mid:
            return data.get("result", {}).get("cookies") or []
    return []


def subprocess_popen(args):
    import subprocess

    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_cdp(port, proc, timeout=20):
    start = time.time()
    url = f"http://127.0.0.1:{port}/json/version"
    last_exc = None
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return None
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:
            last_exc = exc
            time.sleep(0.25)
    _ = last_exc
    return None


def cmd_login():
    browser = find_browser()
    if not browser:
        print(browser_requirements_message())
        sys.exit(1)
    try:
        import websocket  # noqa: F401
    except ImportError:
        print("缺少 websocket-client：请运行  pip install websocket-client")
        print("不会自动安装任何软件包。")
        sys.exit(1)

    if not has_display():
        print("登录需要图形界面，以便打开浏览器并用微信扫码。")
        print("请在有桌面的 Windows、macOS 或 Linux 上运行 login。")
        sys.exit(1)

    name = browser_display_name(browser)
    print(f"即将用 {name} 打开腾讯元宝登录页。请用微信扫码或按页面提示登录。")
    print("登录成功后脚本会自动保存凭据并关闭窗口，请在 5 分钟内完成。")

    profile = tempfile.mkdtemp(prefix="chrome_yuanbao_login_")
    port = pick_free_port()
    args = [
        browser,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1100,800",
        f"--user-agent={DESKTOP_UA}",
    ]
    if needs_chrome_no_sandbox():
        args.append("--no-sandbox")
        args.append("--disable-gpu")
    args.append("https://yuanbao.tencent.com/")
    proc = subprocess_popen(args)
    try:
        if wait_for_cdp(port, proc) is None:
            print(f"无法连接 {name} 调试端口，登录没有开始。请确认浏览器可以正常启动后重试。")
            sys.exit(1)
        tabs = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5).read().decode("utf-8", "replace")
        )
        tab = next((t for t in tabs if t.get("type") == "page"), None)
        if not tab or not tab.get("webSocketDebuggerUrl"):
            print(f"无法连接 {name} 调试端口。")
            sys.exit(1)
        import websocket

        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        try:
            ws.recv()
        except Exception:
            pass
        start = time.time()
        header = ""
        while time.time() - start < 300:
            cookies = _chrome_cookies(ws, "https://yuanbao.tencent.com/")
            parts = {c.get("name"): c.get("value") for c in cookies if c.get("name")}
            if parts.get("hy_user") and parts.get("hy_token"):
                header = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
                break
            time.sleep(2)
            print(f"\r等待登录…… {int(time.time() - start)} 秒", end="", flush=True)
        print()
        ws.close()
        if not cookie_has_login(header):
            print("超时还没有检测到登录，没有保存凭据。请再运行 login 重试。")
            sys.exit(1)
        saved = save_auth(header, source="login")
        print(f"登录已保存到 {saved}")
    finally:
        try:
            proc.kill()
            proc.wait(timeout=8)
        except Exception:
            pass
        shutil.rmtree(profile, ignore_errors=True)


def parse_playable(data):
    """Extract token and export id from yuanbao get_parse_result payload."""
    root = data or {}
    payload = root.get("data") if isinstance(root.get("data"), dict) else root
    playable = payload.get("playable_url") or payload.get("playableUrl") or payload.get("url") or ""
    export_id = (
        payload.get("wx_export_id")
        or payload.get("wxExportId")
        or payload.get("exportId")
        or payload.get("eid")
        or ""
    )
    token = payload.get("token") or payload.get("generalToken") or ""
    if playable:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(playable).query)
        token = token or (q.get("token") or q.get("generalToken") or [""])[0]
        # playable_url 里的 eid 与 token 配对，优先用它；wx_export_id 单独用会报“暂时无法播放”
        export_id = (q.get("eid") or q.get("exportId") or q.get("id") or [""])[0] or export_id
    return token, export_id, playable


def yuanbao_parse(share_url, cookie_header):
    status, _, data, text = http_json(
        YUANBAO_PARSE,
        method="POST",
        data={"type": "video_channel_url", "url": share_url, "scene": 1},
        headers={
            "Origin": "https://yuanbao.tencent.com",
            "Referer": "https://yuanbao.tencent.com/",
            "Cookie": cookie_header,
        },
    )
    return status, data, text


def safe_error_text(exc):
    """Describe a network/download failure without leaking signed URLs."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "网络错误"
    if isinstance(exc, TimeoutError):
        return "连接超时"
    name = type(exc).__name__
    if name in {"HTTPError", "URLError", "timeout", "TimeoutError", "OSError"}:
        return name
    return "下载失败"


def download_file(url, filepath, referer="https://channels.weixin.qq.com/"):
    headers = {
        "User-Agent": DESKTOP_UA,
        "Referer": referer,
        "Origin": "https://channels.weixin.qq.com",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except Exception as exc:
        raise DownloadError(f"下载失败：{safe_error_text(exc)}", 1) from None
    try:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        ctype = resp.headers.get("Content-Type", "")
        if total:
            print(f"  大小：{total / 1024 / 1024:.1f} MB")
        with open(filepath, "wb") as fh:
            downloaded = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"\r  进度：{downloaded * 100 / total:.0f}%", end="", flush=True)
                else:
                    print(f"\r  已下载：{downloaded / 1024 / 1024:.1f} MB", end="", flush=True)
        print(" 完成")
        return ctype
    finally:
        try:
            resp.close()
        except Exception:
            pass


def script_hint():
    return os.path.abspath(sys.argv[0] if sys.argv else "wx_channels_download.py")


def download_one(share, out_dir=None):
    """Download a single share URL. Raises DownloadError on failure."""
    out_dir = out_dir or DOWNLOAD_DIR or default_download_dir()
    print(f"分享链接：{public_url(share) or share}")
    try:
        resolved = resolve_short_url(share)
    except Exception:
        raise DownloadError("解析短链接失败：网络错误", 1) from None
    short_id = short_id_from_url(resolved) or short_id_from_url(share)
    if resolved != share:
        print(f"解析后：{public_url(resolved)}")
    if not short_id:
        raise DownloadError("无法从链接里取出视频号短 ID。", 1)
    print(f"短 ID：{short_id}")

    print("\n--- 读取官方预览信息 ---")
    try:
        status, data = get_feed_info(short_id=short_id)
    except Exception as exc:
        raise DownloadError(f"读取预览信息失败：{safe_error_text(exc)}", 1) from None
    meta = feed_meta(data)
    if meta["title"] or meta["author"]:
        if meta["author"]:
            print(f"作者：{meta['author']}")
        if meta["title"]:
            print(f"标题：{meta['title']}")
    elif status >= 400:
        print(f"预览接口返回 HTTP {status}")

    video_url, kind = pick_video_url(meta)
    if not video_url:
        cookie = load_cookie_header()
        if not cookie_has_login(cookie):
            raise DownloadError(
                "官方预览页不提供播放地址（页面只会提示去微信里观看）。\n"
                "要下载视频，需要先登录一次腾讯元宝：\n"
                f"  python3 {script_hint()} login\n"
                "也可以把元宝 Cookie 放到 --cookie-file 或环境变量 YUANBAO_COOKIE。",
                2,
            )
        print("\n--- 通过腾讯元宝解析播放地址 ---")
        parse_url = share if share.startswith("http") else f"https://weixin.qq.com/sph/{short_id}"
        try:
            status, pdata, _ = yuanbao_parse(parse_url, cookie)
        except Exception as exc:
            raise DownloadError(f"元宝解析失败：{safe_error_text(exc)}", 1) from None
        err_code = None
        if isinstance(pdata, dict):
            err_code = pdata.get("code") or pdata.get("errorCode") or pdata.get("errCode")
        if status in (401, 403) or err_code in (20000, 20001, 401, 403):
            raise DownloadError("元宝登录已失效，请重新运行 login。", 3)
        if status >= 400:
            msg = ""
            if isinstance(pdata, dict):
                msg = str(pdata.get("msg") or pdata.get("message") or pdata.get("errMsg") or "")
                if any(token in msg.lower() for token in ("http://", "https://", "token=", "sid=")):
                    msg = ""
            raise DownloadError(f"元宝解析失败（HTTP {status}{('：' + msg) if msg else ''}）。", 1)
        token, export_id, _playable = parse_playable(pdata)
        if not token and not export_id:
            raise DownloadError("元宝返回里没有找到播放凭证，可能是接口改了，或这条内容无法解析。", 1)
        try:
            status, data = get_feed_info(
                short_id=None if export_id else short_id,
                export_id=export_id or None,
                token=token or "",
            )
        except Exception as exc:
            raise DownloadError(f"读取播放信息失败：{safe_error_text(exc)}", 1) from None
        meta2 = feed_meta(data)
        if meta2["title"]:
            meta["title"] = meta2["title"]
        if meta2["author"]:
            meta["author"] = meta2["author"]
        meta["h264"] = meta2["h264"]
        meta["h265"] = meta2["h265"]
        meta["pics"] = meta2["pics"] or meta["pics"]
        meta["err_title"] = meta2["err_title"]
        video_url, kind = pick_video_url(meta)
        if not video_url:
            if meta["pics"]:
                raise DownloadError("这是图文作品，不是视频，当前脚本只下载视频。", 2)
            extra = f"（{meta['err_title']}）" if meta["err_title"] else ""
            raise DownloadError(f"解析成功但没有拿到视频地址{extra}。付费、私密或已失效的内容通常会这样。", 1)
        print(f"已拿到播放地址（{kind or 'unknown'}）")

    if not video_url and meta.get("pics"):
        raise DownloadError("这是图文作品，不是视频，当前脚本只下载视频。", 2)
    if not video_url:
        raise DownloadError("没有拿到视频地址。", 1)

    os.makedirs(out_dir, exist_ok=True)
    if meta.get("title"):
        base = safe_filename(meta.get("title") or "", f"wx_{short_id}")
        output_file = os.path.join(out_dir, f"wxchannels_{short_id}_{base}.mp4")
    else:
        output_file = os.path.join(out_dir, f"wxchannels_{short_id}.mp4")

    print("\n--- 下载视频 ---")
    download_file(video_url, output_file)
    size = os.path.getsize(output_file)
    if size < 64 * 1024:
        try:
            os.remove(output_file)
        except OSError:
            pass
        raise DownloadError(f"下载的文件只有 {size} 字节，可能不是有效视频，请检查后重试。", 1)
    print("\n=== 下载完成 ===")
    print(f"文件：{output_file}")
    print(f"大小：{size / 1024 / 1024:.1f} MB")
    if meta.get("author"):
        print(f"作者：{meta['author']}")
    if meta.get("title"):
        print(f"标题：{meta['title']}")
    return {
        "ok": True,
        "code": 0,
        "share": share,
        "short_id": short_id,
        "path": output_file,
        "size": size,
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "error": "",
    }


def result_from_error(share, exc):
    if isinstance(exc, DownloadError):
        message = str(exc)
        code = exc.code
    else:
        message = f"未预期的错误：{safe_error_text(exc)}"
        code = 1
    return {
        "ok": False,
        "code": code,
        "share": share,
        "short_id": short_id_from_url(share) or "",
        "path": "",
        "size": 0,
        "title": "",
        "author": "",
        "error": message,
    }


def batch_exit_code(results):
    if not results:
        return 2
    failures = [item for item in results if not item.get("ok")]
    if not failures:
        return 0
    codes = [item.get("code", 1) for item in failures]
    if codes and all(code == 3 for code in codes):
        return 3
    if codes and all(code == 2 for code in codes):
        return 2
    return 1


def print_batch_summary(results, skipped=0):
    ok_n = sum(1 for item in results if item.get("ok"))
    fail_n = len(results) - ok_n
    print("\n======== 汇总 ========")
    extra = f"，跳过重复 {skipped} 条" if skipped else ""
    print(f"成功 {ok_n} 条，失败 {fail_n} 条{extra}")
    for item in results:
        label = item.get("short_id") or item.get("share") or "?"
        if item.get("ok"):
            print(f"  成功：{label} -> {item.get('path')}")
        else:
            err = re.sub(r"\s+", " ", (item.get("error") or "失败").strip())
            if len(err) > 160:
                err = err[:157] + "..."
            print(f"  失败：{label} — {err}")


def run_downloads(urls, out_dir=None, skipped=0, always_summary=False):
    out_dir = out_dir or DOWNLOAD_DIR or default_download_dir()
    results = []
    total = len(urls)
    for index, share in enumerate(urls, 1):
        if total > 1:
            print(f"\n======== [{index}/{total}] {share} ========")
        try:
            results.append(download_one(share, out_dir=out_dir))
        except (DownloadError, Exception) as exc:
            item = result_from_error(share, exc)
            print(item["error"])
            results.append(item)
    if always_summary or total > 1 or skipped:
        print_batch_summary(results, skipped=skipped)
    return results, batch_exit_code(results)


def cmd_download(raw, out_dir=None):
    urls, skipped = collect_share_urls([raw], stdin_text=None if raw != "-" else None)
    if not urls:
        extract_share_url(raw)
        return 1
    results, code = run_downloads(urls, out_dir=out_dir, skipped=skipped)
    return code


def read_menu_links():
    print("请粘贴一条或多条视频号分享链接，也可以直接粘贴微信分享文字（可多行）。")
    print("输入完成后，单独一行输入 END 再按回车结束。")
    print("也可以直接按 Ctrl+Z 再回车（Windows）或 Ctrl+D（macOS / Linux）。")
    print("----")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() in END_MARKERS:
            break
        lines.append(line)
    return "\n".join(lines)


def safe_input(prompt=""):
    try:
        return input(prompt)
    except EOFError:
        return None


def cmd_menu():
    out_dir = DOWNLOAD_DIR or default_download_dir()
    print(f"微信视频号下载  v{__version__}")
    while True:
        print()
        print("1. 下载视频（可一次粘贴多条链接）")
        print("2. 登录腾讯元宝")
        print("3. 查看登录状态")
        print("4. 退出登录（只删除本机保存的登录）")
        print(f"5. 设置保存目录（当前：{out_dir}）")
        print("0. 退出")
        choice = safe_input("请选择：")
        if choice is None:
            print()
            return
        choice = choice.strip()
        if choice in {"0", "q", "Q", "quit", "exit"}:
            return
        try:
            if choice == "1":
                text = read_menu_links()
                urls, skipped = collect_share_urls([text])
                if not urls:
                    print("没有在输入里找到视频号链接。")
                    continue
                if skipped:
                    print(f"已去重，跳过 {skipped} 条重复链接，共 {len(urls)} 条待下载。")
                else:
                    print(f"共 {len(urls)} 条待下载。")
                run_downloads(urls, out_dir=out_dir, skipped=skipped, always_summary=True)
            elif choice == "2":
                cmd_login()
            elif choice == "3":
                cmd_status()
            elif choice == "4":
                cmd_logout()
            elif choice == "5":
                entered = safe_input("请输入保存目录（直接回车保持当前目录）：")
                if entered is None:
                    return
                entered = entered.strip().strip('"')
                if entered:
                    out_dir = os.path.abspath(os.path.expanduser(entered))
                    print(f"保存目录已设为：{out_dir}")
            else:
                print("请输入 0–5 之间的选项。")
        except SystemExit as exc:
            code = exc.code
            if code not in (None, 0):
                # login / logout already printed the reason
                pass
        except KeyboardInterrupt:
            print("\n已取消。")
        except Exception as exc:
            print(f"操作失败：{safe_error_text(exc)}")


def main(argv=None):
    configure_stdio()
    leftover = parse_args(argv)
    cmd = leftover[0] if leftover else ""
    if cmd in AUTH_COMMANDS:
        if len(leftover) > 1:
            print(f"{cmd} 命令后面不需要再跟链接。")
            return 1
        if cmd == "login":
            cmd_login()
            return 0
        if cmd == "status":
            cmd_status()
            return 0
        if cmd == "logout":
            cmd_logout()
            return 0
        cmd_menu()
        return 0

    use_stdin = (not leftover and INPUT_FILE is None) or ("-" in leftover)
    if not leftover and INPUT_FILE is None and sys.stdin.isatty() and sys.stdout.isatty():
        # Interactive with no args: don't sit waiting on a blank stdin.
        print(__doc__.strip())
        print("\n提示：双击 wx_channels_download.bat（Windows）或")
        print("wx_channels_download.command（macOS）可以打开交互菜单。")
        print("也可以运行：python3 wx_channels_download.py menu")
        return 2

    stdin_text = None
    sources = list(leftover)
    if use_stdin:
        if "-" not in sources:
            sources.append("-")
        stdin_text = sys.stdin.read()
    try:
        urls, skipped = collect_share_urls(sources, input_file=INPUT_FILE, stdin_text=stdin_text)
    except DownloadError as exc:
        print(exc)
        return exc.code
    if not urls:
        sample = " ".join(leftover).strip() if leftover else (stdin_text or "")
        extract_share_url(sample or "")
        return 1
    if skipped:
        print(f"已去重，跳过 {skipped} 条重复链接，共 {len(urls)} 条待下载。")
    results, code = run_downloads(urls, skipped=skipped)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
