#!/usr/bin/env python3
"""
微信视频号下载脚本（Linux + Chrome）

用法：
  python3 wx_channels_download.py "https://weixin.qq.com/sph/xxxx"
  python3 wx_channels_download.py "分享文字里带链接也行"
  echo "分享文字" | python3 wx_channels_download.py -
  python3 wx_channels_download.py login
  python3 wx_channels_download.py status
  python3 wx_channels_download.py logout

可选参数：
  --out-dir DIR        保存目录（默认：~/Downloads，没有则用当前目录）
  --cookie-file FILE   从文件读取元宝 Cookie（整段 Cookie 头）
  --quality h264|h265  优先编码（默认 h264，缺失时自动换另一种）

说明：
  微信官方分享页（weixin.qq.com/sph/...）只会跳到预览页，给出封面、标题和
  「可扫码前往微信观看」，普通浏览器拦不到播放地址，这一点和抖音不同。
  本脚本先向官方预览接口取标题和作者；要下载视频，需要登录一次腾讯元宝
  （yuanbao.tencent.com），用它的解析接口换到播放地址后再下载。
  Cookie 只保存在本机 ~/.config/wx-channels/auth.json（权限 600），不会打印出来。

依赖：
  Python 3.8+、Google Chrome 或 Chromium（仅 login 时需要）
"""

from __future__ import annotations

import http.cookiejar
import http.client
import json
import os
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SPH_RE = re.compile(
    r"https?://(?:weixin\.qq\.com/sph/|channels\.weixin\.qq\.com/finder-preview/pages/sph[^\s<>\"\u3000]*)[^\s<>\"\u3000]*",
    re.I,
)
SHORT_ID_RE = re.compile(r"(?:/sph/|\bid=)([A-Za-z0-9]+)")
AUTH_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "wx-channels")
AUTH_FILE = os.path.join(AUTH_DIR, "auth.json")
YUANBAO_PARSE = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
FEED_INFO = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
CHROME_PATH = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
DEBUG_PORT = 9228

DOWNLOAD_DIR = None
COOKIE_FILE = None
QUALITY = "h264"


def default_download_dir():
    downloads = os.path.expanduser("~/Downloads")
    return downloads if os.path.isdir(downloads) else os.getcwd()


def parse_args():
    global DOWNLOAD_DIR, COOKIE_FILE, QUALITY
    leftover = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--out-dir" and i + 1 < len(args):
            DOWNLOAD_DIR = args[i + 1]
            i += 2
        elif args[i].startswith("--out-dir="):
            DOWNLOAD_DIR = args[i].split("=", 1)[1]
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
            print(__doc__)
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


def extract_share_url(raw):
    if raw == "-":
        raw = sys.stdin.read().strip()
    if not raw:
        print('用法: python3 wx_channels_download.py "<分享文字 | 链接>"')
        sys.exit(1)
    m = SPH_RE.search(raw)
    if not m:
        # bare short id
        token = raw.strip()
        if re.fullmatch(r"[A-Za-z0-9]{6,32}", token):
            return f"https://weixin.qq.com/sph/{token}"
        print(f"没有在输入里找到视频号链接：{raw[:120]}")
        sys.exit(1)
    return m.group(0).rstrip(".,;)]}>\"'")


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
        finally:
            conn.close()
        if resp.status in (301, 302, 303, 307, 308) and loc:
            current = urllib.parse.urljoin(current, loc)
        else:
            break
    return current


def short_id_from_url(url):
    m = SHORT_ID_RE.search(url)
    return m.group(1) if m else None


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


def pick_video_url(meta):
    preferred = QUALITY
    other = "h265" if preferred == "h264" else "h264"
    url = meta.get(preferred) or meta.get(other) or ""
    kind = preferred if meta.get(preferred) else (other if meta.get(other) else "")
    return url, kind


def safe_filename(name, fallback):
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", name).strip(" ._")
    name = name[:80] if name else fallback
    return name or fallback


def load_cookie_header():
    # 1) --cookie-file
    if COOKIE_FILE:
        text = open(COOKIE_FILE, encoding="utf-8").read().strip()
        if text.lower().startswith("cookie:"):
            text = text.split(":", 1)[1].strip()
        return text
    # 2) env
    env = os.environ.get("YUANBAO_COOKIE") or os.environ.get("WX_CHANNELS_COOKIE")
    if env:
        return env.strip()
    # 3) saved auth.json
    if os.path.isfile(AUTH_FILE):
        try:
            data = json.load(open(AUTH_FILE, encoding="utf-8"))
        except json.JSONDecodeError:
            return ""
        header = (data.get("cookie") or "").strip()
        if header:
            return header
        cookies = data.get("cookies") or {}
        if isinstance(cookies, dict) and cookies:
            return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    return ""


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
    os.makedirs(AUTH_DIR, mode=0o700, exist_ok=True)
    payload = {
        "cookie": cookie_header,
        "saved_at": int(time.time()),
        "source": source,
    }
    tmp = AUTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_FILE)


def cmd_status():
    header = load_cookie_header()
    exists = os.path.isfile(AUTH_FILE)
    print(f"凭据文件：{AUTH_FILE}")
    print(f"文件存在：{'是' if exists else '否'}")
    print(f"已保存元宝登录：{'是' if cookie_has_login(header) else '否'}")
    if exists:
        try:
            data = json.load(open(AUTH_FILE, encoding="utf-8"))
            ts = data.get("saved_at")
            if ts:
                print("保存时间：", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)))
        except Exception:
            pass
    print("服务端是否仍有效，要到实际下载时才能确认。")


def cmd_logout():
    if os.path.isfile(AUTH_FILE):
        os.remove(AUTH_FILE)
        print("已删除本机保存的元宝登录信息，没有注销元宝网页上的会话。")
    else:
        print("本机没有保存的登录信息。")


def _chrome_cookies(ws, url):
    import websocket  # noqa: F401  (imported by caller)

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


def cmd_login():
    if not CHROME_PATH:
        print("找不到 Google Chrome 或 Chromium，请先安装后再运行 login。")
        sys.exit(1)
    try:
        import websocket
    except ImportError:
        print("缺少 websocket-client：pip install websocket-client")
        sys.exit(1)

    headed = bool(os.environ.get("DISPLAY"))
    print("即将打开腾讯元宝登录页。请用微信扫码或按页面提示登录。")
    print("登录成功后脚本会自动保存凭据并关闭窗口，请在 5 分钟内完成。")
    if not headed:
        print("当前没有图形界面。如果这是无头环境，请先打开本机桌面，或在有界面的电脑上运行 login。")

    profile = tempfile.mkdtemp(prefix="chrome_yuanbao_login_")
    args = [
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        "--remote-allow-origins=*",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1100,800",
        f"--user-agent={DESKTOP_UA}",
    ]
    if not headed:
        args.append("--headless=new")
        args.append("--disable-gpu")
    args.append("https://yuanbao.tencent.com/")
    proc = subprocess_popen(args)
    time.sleep(3)
    try:
        tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json").read().decode())
        tab = next((t for t in tabs if t.get("type") == "page"), None)
        if not tab or not tab.get("webSocketDebuggerUrl"):
            print("无法连接 Chrome 调试端口。")
            sys.exit(1)
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        ws.recv()
        start = time.time()
        header = ""
        while time.time() - start < 300:
            cookies = _chrome_cookies(ws, "https://yuanbao.tencent.com/")
            parts = {c.get("name"): c.get("value") for c in cookies if c.get("name")}
            if parts.get("hy_user") and parts.get("hy_token"):
                # keep all yuanbao/tencent cookies from this jar
                header = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
                break
            time.sleep(2)
            print(f"\r等待登录…… {int(time.time()-start)} 秒", end="", flush=True)
        print()
        ws.close()
        if not cookie_has_login(header):
            print("超时还没有检测到登录，没有保存凭据。请再运行 login 重试。")
            sys.exit(1)
        save_auth(header, source="login")
        print(f"登录已保存到 {AUTH_FILE}")
    finally:
        proc.kill()
        proc.wait()
        shutil.rmtree(profile, ignore_errors=True)


def subprocess_popen(args):
    import subprocess

    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


def download_file(url, filepath, referer="https://channels.weixin.qq.com/"):
    headers = {
        "User-Agent": DESKTOP_UA,
        "Referer": referer,
        "Origin": "https://channels.weixin.qq.com",
    }
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=120)
    total = int(resp.headers.get("Content-Length", 0))
    ctype = resp.headers.get("Content-Type", "")
    if total:
        print(f"  大小：{total / 1024 / 1024:.1f} MB")
    with open(filepath, "wb") as f:
        downloaded = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                print(f"\r  进度：{downloaded * 100 / total:.0f}%", end="", flush=True)
            else:
                print(f"\r  已下载：{downloaded / 1024 / 1024:.1f} MB", end="", flush=True)
    print(" 完成")
    return ctype


def cmd_download(raw):
    share = extract_share_url(raw)
    print(f"分享链接：{share}")
    resolved = resolve_short_url(share)
    short_id = short_id_from_url(resolved) or short_id_from_url(share)
    if resolved != share:
        print(f"解析后：{resolved}")
    if not short_id:
        print("无法从链接里取出视频号短 ID。")
        sys.exit(1)
    print(f"短 ID：{short_id}")

    print("\n--- 读取官方预览信息 ---")
    status, data = get_feed_info(short_id=short_id)
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
            print(
                "\n官方预览页不提供播放地址（页面只会提示去微信里观看）。\n"
                "要下载视频，需要先登录一次腾讯元宝：\n"
                f"  python3 {os.path.abspath(sys.argv[0])} login\n"
                "也可以把元宝 Cookie 放到 --cookie-file 或环境变量 YUANBAO_COOKIE。"
            )
            sys.exit(2)
        print("\n--- 通过腾讯元宝解析播放地址 ---")
        status, pdata, _ = yuanbao_parse(share if share.startswith("http") else f"https://weixin.qq.com/sph/{short_id}", cookie)
        err_code = None
        if isinstance(pdata, dict):
            err_code = pdata.get("code") or pdata.get("errorCode") or pdata.get("errCode")
        if status in (401, 403) or err_code in (20000, 20001, 401, 403):
            print("元宝登录已失效，请重新运行 login。")
            sys.exit(3)
        if status >= 400:
            msg = ""
            if isinstance(pdata, dict):
                msg = str(pdata.get("msg") or pdata.get("message") or pdata.get("errMsg") or "")
            print(f"元宝解析失败（HTTP {status}{('：'+msg) if msg else ''}）。")
            sys.exit(1)
        token, export_id, _playable = parse_playable(pdata)
        if not token and not export_id:
            print("元宝返回里没有找到播放凭证，可能是接口改了，或这条内容无法解析。")
            sys.exit(1)
        status, data = get_feed_info(
            short_id=None if export_id else short_id,
            export_id=export_id or None,
            token=token or "",
        )
        meta2 = feed_meta(data)
        # keep title/author from whichever response is richer
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
                print("这是图文作品，不是视频，当前脚本只下载视频。")
                sys.exit(2)
            extra = f"（{meta['err_title']}）" if meta["err_title"] else ""
            print(f"解析成功但没有拿到视频地址{extra}。付费、私密或已失效的内容通常会这样。")
            sys.exit(1)
        print(f"已拿到播放地址（{kind or 'unknown'}）")

    if not video_url and meta.get("pics"):
        print("这是图文作品，不是视频，当前脚本只下载视频。")
        sys.exit(2)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    base = safe_filename(meta.get("title") or "", f"wx_{short_id}")
    output_file = os.path.join(DOWNLOAD_DIR, f"wxchannels_{short_id}_{base}.mp4")
    # avoid overly long duplicate if title already has id
    if not meta.get("title"):
        output_file = os.path.join(DOWNLOAD_DIR, f"wxchannels_{short_id}.mp4")

    print("\n--- 下载视频 ---")
    download_file(video_url, output_file)
    size = os.path.getsize(output_file)
    if size < 64 * 1024:
        # probably an error page
        print(f"下载的文件只有 {size} 字节，可能不是有效视频，请检查后重试。")
        sys.exit(1)
    print("\n=== 下载完成 ===")
    print(f"文件：{output_file}")
    print(f"大小：{size / 1024 / 1024:.1f} MB")
    if meta.get("author"):
        print(f"作者：{meta['author']}")
    if meta.get("title"):
        print(f"标题：{meta['title']}")


def main():
    leftover = parse_args()
    cmd = leftover[0] if leftover else ""
    if cmd == "login":
        cmd_login()
        return
    if cmd == "status":
        cmd_status()
        return
    if cmd == "logout":
        cmd_logout()
        return
    raw = " ".join(leftover).strip() if leftover else "-"
    cmd_download(raw)


if __name__ == "__main__":
    main()
