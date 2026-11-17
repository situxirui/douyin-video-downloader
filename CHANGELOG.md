# 更新日志

## v1.4 - 2026-11-17

- 微信视频号脚本支持一次下载多条：可传入多个链接、粘贴含多条链接的分享文字、或用 `--input-file` 读取 UTF-8 文本文件；重复链接会按出现顺序去重。
- 批量下载逐条进行，一条失败不会中断其余条目，结束时打印汇总，并给出有意义的退出码。
- 新增交互菜单（`python3 wx_channels_download.py menu`）：可粘贴多行链接（单独一行输入 `END` 结束）、选择保存目录，以及登录 / 查看状态 / 退出登录。
- 新增双击启动工具：`wx_channels_download.bat`（Windows）和 `wx_channels_download.command`（macOS），会自动查找 Python 3.8+，出错后窗口保持打开。
- 登录时会在 Windows / macOS / Linux 上查找 Google Chrome 或 Microsoft Edge（也可以是 Chromium）；需要图形界面以便扫码。凭据仍只保存在本机，Windows 下位于 `%APPDATA%\wx-channels\auth.json`。
- 单条链接以及 `login` / `status` / `logout` 的原有用法保持兼容。`status` 不会打印 Cookie 或登录凭证。

## v1.3 - 2026-11-17

- 新增 `wx_channels_download.py`，支持下载微信视频号视频（`weixin.qq.com/sph/...` 分享链接或包含链接的分享文字）。
- 先通过官方预览接口读取作者和标题，再借助腾讯元宝的解析接口取得播放地址并下载 MP4。
- 新增 `login` / `status` / `logout` 子命令；元宝 Cookie 只保存在本机 `~/.config/wx-channels/auth.json`（权限 600）。
- 支持 `--out-dir`、`--cookie-file`、`--quality h264|h265`，以及环境变量 `YUANBAO_COOKIE`。

## v1.2 - 2026-11-13

- 新增 `douyin_download.py`（推荐日常使用）。
- 支持直接粘贴抖音 App 的分享口令，自动从中提取 `v.douyin.com` 短链接。
- 使用移动端 User-Agent 跟随短链接跳转，解析出真实视频 ID。
- 支持从标准输入读取分享文字（无参数或传入 `-`）。
- 图文帖（`/note/`、`/slides/`）会明确提示，不会误当成视频下载。
- Linux 版仍然通过 `jingxuan?modal_id=` 打开页面，并按时长匹配排除广告。

## v1.1 - 2026-11-12

- 新增 `douyin_dl_linux.py`，在 v1.0 基础上适配 Linux（v1.0 的分支）。
- 使用独立的无头 Google Chrome / Chromium（临时配置目录，CDP 端口 9227，`--remote-allow-origins=*`）。
- 未登录时打开 `https://www.douyin.com/jingxuan?modal_id=<id>`，这是目前无需登录就能播放目标视频的地址。
- 扩大媒体域名过滤：`douyinvod.com`、`zjcdn.com`、`bytecdn`、`douyinstatic`、`/video/tos/`。
- 根据页面 `<video>` 元素自动识别目标时长，排除自动播放的广告。
- 增加 `--out-dir`、`--duration` 参数。
- 输入为视频 ID 或 `douyin.com` 完整链接。

## v1.0 - 2026-11-05

- 首次发布 `douyin_dl.py`。
- 在 Windows 上启动无头 Microsoft Edge，通过 CDP 拦截 `douyinvod.com` 播放流。
- 支持视频 ID、完整链接；不带参数时，从 Edge 浏览记录中查找最近打开过的抖音视频。
- 使用 `imageio-ffmpeg` 合并 DASH 音视频流，输出 `~/Downloads/douyin_<视频ID>.mp4`。
