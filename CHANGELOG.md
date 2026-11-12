# 更新日志

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
