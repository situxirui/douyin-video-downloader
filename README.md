# 抖音视频下载脚本

一组用浏览器开发者协议（CDP）拦截播放流的抖音视频下载脚本，覆盖 Windows 和 Linux。不依赖第三方签名接口，也无需登录抖音账号。

抖音经常调整 `a_bogus`、`msToken` 等反爬签名，基于接口的工具容易突然失效。这组脚本换了一种做法：由真正的浏览器完成页面加载和鉴权，再通过 Chrome DevTools Protocol 拦截媒体 CDN 地址，下载视频流和音频流后合并为 MP4。

## 版本一览

| 版本 | 脚本 | 平台 | 输入 | 说明 |
| --- | --- | --- | --- | --- |
| v1.0 | `douyin_dl.py` | Windows + Microsoft Edge | 视频 ID、完整链接，或从 Edge 浏览记录自动检测 | 原始版本 |
| v1.1 | `douyin_dl_linux.py` | Linux + Google Chrome / Chromium | 视频 ID 或 douyin.com 完整链接 | 在 v1.0 基础上适配 Linux 的分支 |
| **v1.2（抖音推荐）** | `douyin_download.py` | Linux + Google Chrome / Chromium | 视频 ID、链接，或从抖音 App 复制的分享口令（含 `v.douyin.com` 短链接）；也支持从标准输入读取 | 功能最完整，日常使用建议选这个 |
| **v1.4（视频号）** | `wx_channels_download.py` | Windows / macOS / Linux；登录时需要 Chrome 或 Edge | 微信视频号分享链接（`weixin.qq.com/sph/...`）、包含链接的分享文字、多条链接或文本文件 | 支持批量下载和双击启动；需要登录一次腾讯元宝 |

## 环境要求

- Python 3.8 或更高版本
- **Windows 版**：本机已安装 Microsoft Edge（系统一般自带）
- **Linux 版**：已安装 Google Chrome 或 Chromium
- Python 依赖：`websocket-client`、`imageio-ffmpeg`（ffmpeg 会随后者自动提供，不必单独安装）

## 安装

```bash
git clone https://github.com/situxirui/douyin-video-downloader.git
cd douyin-video-downloader
pip install -r requirements.txt
```

如果是系统自带的 Python，可能需要加 `--break-system-packages`，或在虚拟环境中安装。

## 用法

输出文件默认保存为 `~/Downloads/douyin_<视频ID>.mp4`；若没有 Downloads 目录，则保存到当前目录。Linux 两个脚本都支持 `--out-dir` 指定目录，以及 `--duration` 手动指定目标时长（用来排除自动播放的广告）。

### v1.0：Windows + Edge（原始版本）

```bash
# 用视频 ID 下载
python douyin_dl.py 7529381510349180223

# 用完整链接下载
python douyin_dl.py https://www.douyin.com/video/7529381510349180223

# 不带参数时，从 Edge 浏览记录里查找最近打开过的抖音视频
python douyin_dl.py
```

### v1.1：Linux，按 ID 或链接下载

```bash
python3 douyin_dl_linux.py 7701340688763342115
python3 douyin_dl_linux.py https://www.douyin.com/video/7701340688763342115
python3 douyin_dl_linux.py --out-dir ./videos 7701340688763342115
```

本脚本不解析 `v.douyin.com` 短链接或 App 分享口令。如需这类输入，请改用 v1.2。

### v1.2：Linux，支持分享口令（推荐）

```bash
# 直接粘贴从抖音 App 复制的整段分享文字
python3 douyin_download.py "3.87 复制打开抖音，看看【作者的作品】…… https://v.douyin.com/xxxx/"

# 也可以只给短链接、完整链接或视频 ID
python3 douyin_download.py https://v.douyin.com/xxxx/
python3 douyin_download.py 7705047265420922121

# 从标准输入读取
echo "分享文字" | python3 douyin_download.py -
python3 douyin_download.py --out-dir ./videos -
```

## 微信视频号下载（v1.4）

视频号和抖音不一样：微信官方分享页只会跳到预览页，给出封面、标题和“可扫码前往微信观看”，不会给出播放地址，浏览器拦截也拦不到。所以 `wx_channels_download.py` 借助腾讯元宝（yuanbao.tencent.com）的解析接口换取播放地址，需要先登录一次元宝。

### 环境要求

- Python 3.8 或更高版本（安装时请加入 PATH）
- 登录时需要本机已安装 **Google Chrome** 或 **Microsoft Edge**（Chromium 也可以），并且要在有图形界面的桌面上运行，方便用微信扫码
- 下载本身不需要再打开浏览器；`websocket-client` 只在运行 `login` 时需要（可执行 `pip install websocket-client`，脚本不会自动安装任何软件包）

### 双击使用

把仓库下载或克隆到本机后：

1. **Windows**：双击 `wx_channels_download.bat`
2. **macOS**：双击 `wx_channels_download.command`（若系统提示无法打开，可先在终端执行 `chmod +x wx_channels_download.command`，或在访达中右键选择“打开”）

窗口打开后会出现菜单。第一次请先选「登录腾讯元宝」，用微信扫码；登录成功后凭据会保存在本机。之后选「下载视频」，把一条或多条分享链接（或微信分享文字）粘贴进去，单独一行输入 `END` 再回车即可开始下载。也可以在菜单里改保存目录，默认是 `~/Downloads`。

出错时窗口不会立刻关掉，方便查看提示。

### 命令行用法

```bash
# 第一次使用：打开 Chrome 或 Edge，用微信扫码登录腾讯元宝（只需一次）
python3 wx_channels_download.py login

# 下载一条视频
python3 wx_channels_download.py --out-dir ~/Downloads "https://weixin.qq.com/sph/xxxx"

# 一次下载多条（多个参数、一段分享文字里的多条链接都可以）
python3 wx_channels_download.py "https://weixin.qq.com/sph/xxxx" "https://weixin.qq.com/sph/yyyy"
python3 wx_channels_download.py --input-file links.txt
python3 wx_channels_download.py --out-dir ~/Downloads "分享文字里可以有多条 https://weixin.qq.com/sph/xxxx"

# 从标准输入读取
echo "分享文字 https://weixin.qq.com/sph/xxxx" | python3 wx_channels_download.py -

# 打开交互菜单（和双击启动是同一套界面）
python3 wx_channels_download.py menu

# 查看或清除已保存的登录
python3 wx_channels_download.py status
python3 wx_channels_download.py logout
```

`links.txt` 需为 UTF-8 编码，每行可以是链接、分享文字或 `#` 开头的注释。重复链接会按出现顺序去重。批量下载时，一条失败不会中断其余条目，最后会打印汇总；若有失败，进程会返回非 0 退出码。

可选参数：`--input-file FILE` 从文本文件读取链接，`--cookie-file FILE` 从文件读取元宝 Cookie，`--quality h264|h265` 选择优先的编码（默认 h264）。也可以用环境变量 `YUANBAO_COOKIE` 传入。

注意事项：

- 元宝登录信息只保存在本机，脚本不会把它打印出来。不要把这个文件提交到 Git 或发给别人。
  - Windows：`%APPDATA%\wx-channels\auth.json`
  - macOS / Linux：`~/.config/wx-channels/auth.json`（权限 600）
- 登录过期后重新运行一次 `login` 即可。
- 付费、私密或已删除的视频拿不到播放地址。
- 当前脚本只下载视频，不下载图文作品。

## 工作原理（Linux 版）

1. **单独启动无头 Chrome**：使用临时用户目录，CDP 调试端口 `9227`，并加上 `--remote-allow-origins=*` 和桌面版 User-Agent。这条浏览器进程与本机日常使用的浏览器互不影响。
2. **打开未登录可用的页面**：访问 `https://www.douyin.com/jingxuan?modal_id=<视频ID>`。目前未登录状态下，只有这个地址能稳定打开目标视频；直接访问 `/video/<id>` 或 `user/self?modal_id=` 往往加载不出来。
3. **拦截媒体流**：监听 `Network.requestWillBeSent`，捕获来自 `douyinvod.com`、`zjcdn.com`、`bytecdn`、`douyinstatic` 或路径含 `/video/tos/` 的 `media-video` / `media-audio` 地址。
4. **按时长排除广告**：从页面上正在播放的 `<video>` 元素读取目标时长，在多组流里选择最接近该时长的一对，避免把自动插播的广告当成目标视频。
5. **下载并合并**：分别下载视频轨和音频轨，再用 `imageio-ffmpeg` 合并成单个 MP4。

Windows 原版思路相同，只是启动的是无头 Microsoft Edge，并额外支持从 Edge 历史记录里自动发现最近观看的视频。

## 限制

- 不支持图文帖（地址中含 `/note/` 或 `/slides/`），脚本会提示后退出，不会保存为 MP4。
- **下载无需登录抖音账号**。
- 抖音脚本一次只下载一条视频；视频号脚本支持一次下载多条。
- 页面结构或媒体域名若再次调整，可能需要更新拦截规则。
- 本工具仅供个人学习与备份自己有权访问的内容使用，请遵守抖音用户协议及当地法律法规，不得用于侵权或商业用途。

## 许可证

MIT License（见 [LICENSE](LICENSE)）。
