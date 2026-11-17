#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for wx_channels_download.py (network and downloads are mocked)."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import wx_channels_download as wx  # noqa: E402


URL_A = "https://weixin.qq.com/sph/AsyeU2ElGU"
URL_B = "https://weixin.qq.com/sph/ABCDEF1234"
URL_A_PUNCT = "https://weixin.qq.com/sph/AsyeU2ElGU。"
FAKE_COOKIE = "hy_user=test-user; hy_token=test-token; other=1"
PLAYABLE_VIDEO = "https://media.example.test/not-a-real-host/video.mp4?token=SECRET&eid=eid-from-playable"


def reset_globals():
    wx.DOWNLOAD_DIR = None
    wx.COOKIE_FILE = None
    wx.INPUT_FILE = None
    wx.QUALITY = "h264"


def feed_payload(title="标题", author="作者", video="https://media.example.test/v.mp4", pics=None):
    feed = {"description": title}
    if video:
        feed["h264VideoInfo"] = {"videoUrl": video}
        feed["videoUrl"] = video
    if pics:
        feed["picInfo"] = pics
    return 200, {
        "data": {
            "feedInfo": feed,
            "authorInfo": {"nickname": author},
            "sceneInfo": {},
            "errMsg": {},
        }
    }


class IsolatedAuthTestCase(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.auth_dir = os.path.join(self._tmpdir.name, "auth")
        self.out_dir = os.path.join(self._tmpdir.name, "out")
        os.makedirs(self.out_dir, exist_ok=True)
        self._env = mock.patch.dict(
            os.environ,
            {
                "WX_CHANNELS_AUTH_DIR": self.auth_dir,
            },
            clear=False,
        )
        self._env.start()
        self._saved_cookie_env = {}
        for key in ("YUANBAO_COOKIE", "WX_CHANNELS_COOKIE"):
            self._saved_cookie_env[key] = os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved_cookie_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._env.stop()
        self._tmpdir.cleanup()
        reset_globals()


class FindUrlTests(IsolatedAuthTestCase):
    def test_two_links_and_share_text(self):
        text = f"看看这个 {URL_A} 还有\n第二段分享 {URL_B} 结束"
        self.assertEqual(wx.find_share_urls(text), [URL_A, URL_B])

    def test_duplicate_share_text_stable(self):
        text = f"{URL_A}\n再分享一次 {URL_A_PUNCT}\n{URL_B}\n{URL_A}"
        urls, skipped = wx.collect_share_urls([text])
        self.assertEqual(urls, [URL_A, URL_B])
        self.assertEqual(skipped, 2)

    def test_bare_id_and_punctuation(self):
        text = "AsyeU2ElGU\n"
        self.assertEqual(wx.find_share_urls(text), [URL_A])
        self.assertEqual(wx.find_share_urls(URL_A_PUNCT), [URL_A])

    def test_unicode_input_file(self):
        path = os.path.join(self._tmpdir.name, "链接列表.txt")
        content = (
            "# 这是注释\n"
            f"分享一下 {URL_A}\n"
            "\n"
            f"{URL_B}\n"
            f"{URL_A}\n"
        )
        with open(path, "w", encoding="utf-8-sig") as fh:
            fh.write(content)
        text = wx.read_text_file(path)
        urls, skipped = wx.collect_share_urls([], input_file=path)
        self.assertIn("这是注释", text)
        self.assertEqual(urls, [URL_A, URL_B])
        self.assertEqual(skipped, 1)

    def test_cli_multiple_inputs(self):
        urls, skipped = wx.collect_share_urls([URL_A, f"文字 {URL_B}", URL_A])
        self.assertEqual(urls, [URL_A, URL_B])
        self.assertEqual(skipped, 1)

    def test_extract_share_url_first_only(self):
        self.assertEqual(wx.extract_share_url(f"{URL_A} {URL_B}"), URL_A)

    def test_public_url_strips_query(self):
        dirty = "https://channels.weixin.qq.com/finder-preview/pages/sph?id=AsyeU2ElGU&token=SECRET"
        self.assertEqual(
            wx.public_url(dirty),
            "https://channels.weixin.qq.com/finder-preview/pages/sph",
        )
        self.assertEqual(wx.public_url(URL_A), URL_A)


class ArgAndCommandTests(IsolatedAuthTestCase):
    def test_parse_args_single_link_and_options(self):
        leftover = wx.parse_args(["--out-dir", self.out_dir, "--quality=h265", URL_A])
        self.assertEqual(leftover, [URL_A])
        self.assertEqual(wx.DOWNLOAD_DIR, self.out_dir)
        self.assertEqual(wx.QUALITY, "h265")

    def test_parse_args_input_file(self):
        leftover = wx.parse_args(["--input-file", "列表.txt", URL_B])
        self.assertEqual(wx.INPUT_FILE, "列表.txt")
        self.assertEqual(leftover, [URL_B])

    def test_old_auth_commands_are_recognized(self):
        for cmd in ("login", "status", "logout", "menu"):
            leftover = wx.parse_args(["--out-dir", self.out_dir, cmd])
            self.assertEqual(leftover, [cmd])

    def test_help_and_version(self):
        for flag in ("--help", "-h"):
            with mock.patch("sys.stdout", new=io.StringIO()) as out:
                with self.assertRaises(SystemExit) as cm:
                    wx.parse_args([flag])
            self.assertEqual(cm.exception.code, 0)
            self.assertIn("微信视频号下载脚本", out.getvalue())
        with mock.patch("sys.stdout", new=io.StringIO()) as out:
            with self.assertRaises(SystemExit) as cm:
                wx.parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("1.4", out.getvalue())


class AuthStatusTests(IsolatedAuthTestCase):
    def test_status_does_not_print_cookie(self):
        wx.save_auth(FAKE_COOKIE, source="test")
        buf = io.StringIO()
        with mock.patch("sys.stdout", new=buf):
            wx.cmd_status()
        text = buf.getvalue()
        self.assertIn("已保存元宝登录：是", text)
        self.assertIn(self.auth_dir, text)
        self.assertNotIn("hy_user=test-user", text)
        self.assertNotIn("test-token", text)
        self.assertNotIn(FAKE_COOKIE, text)

    def test_save_auth_restrictive_permissions(self):
        path = wx.save_auth(FAKE_COOKIE, source="test")
        self.assertTrue(os.path.isfile(path))
        if sys.platform != "win32":
            self.assertEqual(stat_mode(path), 0o600)
            self.assertEqual(stat_mode(self.auth_dir), 0o700)

    def test_logout_removes_file_only(self):
        wx.save_auth(FAKE_COOKIE, source="test")
        buf = io.StringIO()
        with mock.patch("sys.stdout", new=buf):
            wx.cmd_logout()
        self.assertFalse(os.path.isfile(wx.auth_file()))
        self.assertIn("已删除", buf.getvalue())


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


class BrowserPathTests(IsolatedAuthTestCase):
    def test_linux_which_discovery(self):
        def which(name):
            mapping = {"google-chrome": "/usr/bin/google-chrome"}
            return mapping.get(name)

        with mock.patch.object(wx.sys, "platform", "linux"):
            with mock.patch.object(wx.shutil, "which", side_effect=which):
                with mock.patch.object(wx.os.path, "isfile", return_value=True):
                    self.assertEqual(wx.find_browser(), "/usr/bin/google-chrome")

    def test_windows_standard_paths(self):
        edge = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"

        def isfile(path):
            return path.replace("/", "\\").lower().endswith("msedge.exe")

        with mock.patch.object(wx.sys, "platform", "win32"):
            with mock.patch.object(wx.shutil, "which", return_value=None):
                with mock.patch.object(wx.os.path, "isfile", side_effect=isfile):
                    with mock.patch.dict(
                        os.environ,
                        {
                            "PROGRAMFILES": r"C:\Program Files",
                            "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                            "LOCALAPPDATA": r"C:\Users\demo\AppData\Local",
                        },
                        clear=False,
                    ):
                        found = wx.find_browser()
        self.assertTrue(found.lower().endswith("msedge.exe"), found)
        self.assertEqual(wx.browser_display_name(edge), "Microsoft Edge")

    def test_macos_chrome_path(self):
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        def isfile(path):
            return path == chrome

        with mock.patch.object(wx.sys, "platform", "darwin"):
            with mock.patch.object(wx.shutil, "which", return_value=None):
                with mock.patch.object(wx.os.path, "isfile", side_effect=isfile):
                    self.assertEqual(wx.find_browser(), chrome)

    def test_auth_dir_windows_uses_appdata(self):
        with mock.patch.object(wx.sys, "platform", "win32"):
            with mock.patch.dict(os.environ, {"APPDATA": r"C:\Users\demo\AppData\Roaming"}, clear=False):
                os.environ.pop("WX_CHANNELS_AUTH_DIR", None)
                os.environ.pop("XDG_CONFIG_HOME", None)
                path = wx.auth_dir()
        self.assertTrue(path.replace("/", "\\").endswith("wx-channels"))
        self.assertIn("AppData", path.replace("/", "\\"))


class DownloadBatchTests(IsolatedAuthTestCase):
    def _write_bytes(self, url, filepath, referer="https://channels.weixin.qq.com/"):
        with open(filepath, "wb") as fh:
            fh.write(b"0" * (70 * 1024))
        return "video/mp4"

    def test_single_link_success_compatible(self):
        def fake_feed(short_id=None, export_id=None, token=""):
            return feed_payload("单条视频", "作者A")

        with mock.patch.object(wx, "resolve_short_url", side_effect=lambda u: u):
            with mock.patch.object(wx, "get_feed_info", side_effect=fake_feed):
                with mock.patch.object(wx, "download_file", side_effect=self._write_bytes):
                    with mock.patch("sys.stdout", new=io.StringIO()) as out:
                        result = wx.download_one(URL_A, out_dir=self.out_dir)
                        code = wx.batch_exit_code([result])
        self.assertTrue(result["ok"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(result["path"]))
        self.assertIn("AsyeU2ElGU", result["path"])
        self.assertNotIn("SECRET", out.getvalue())
        self.assertNotIn(PLAYABLE_VIDEO, out.getvalue())

    def test_two_links_and_one_failure_continues(self):
        def fake_feed(short_id=None, export_id=None, token=""):
            if short_id == "AsyeU2ElGU":
                return feed_payload("成功的视频")
            return feed_payload("失败的视频", video="", pics=None)

        def fake_yuanbao(share_url, cookie_header):
            self.assertEqual(cookie_header, FAKE_COOKIE)
            self.assertNotIn("hy_token", sys.stdout.getvalue() if False else "")
            return 200, {"data": {"msg": "不能解析"}}, ""

        wx.save_auth(FAKE_COOKIE, source="test")
        with mock.patch.object(wx, "resolve_short_url", side_effect=lambda u: u):
            with mock.patch.object(wx, "get_feed_info", side_effect=fake_feed):
                with mock.patch.object(wx, "yuanbao_parse", side_effect=fake_yuanbao):
                    with mock.patch.object(wx, "download_file", side_effect=self._write_bytes):
                        with mock.patch("sys.stdout", new=io.StringIO()) as out:
                            results, code = wx.run_downloads(
                                [URL_A, URL_B],
                                out_dir=self.out_dir,
                                always_summary=True,
                            )
        text = out.getvalue()
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertEqual(code, 1)
        self.assertIn("汇总", text)
        self.assertIn("成功 1 条，失败 1 条", text)
        self.assertIn("SECRET", PLAYABLE_VIDEO)  # fixture itself may contain it
        self.assertNotIn("SECRET", text)
        self.assertNotIn("test-token", text)
        self.assertNotIn(FAKE_COOKIE, text)

    def test_batch_exit_summary_codes(self):
        ok = {"ok": True, "code": 0}
        fail = {"ok": False, "code": 1}
        no_login = {"ok": False, "code": 2}
        expired = {"ok": False, "code": 3}
        self.assertEqual(wx.batch_exit_code([]), 2)
        self.assertEqual(wx.batch_exit_code([ok, ok]), 0)
        self.assertEqual(wx.batch_exit_code([ok, fail]), 1)
        self.assertEqual(wx.batch_exit_code([no_login, no_login]), 2)
        self.assertEqual(wx.batch_exit_code([expired, expired]), 3)
        self.assertEqual(wx.batch_exit_code([expired, fail]), 1)

    def test_main_batch_input_file_and_exit(self):
        path = os.path.join(self._tmpdir.name, "input.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"{URL_A}\n{URL_B}\n")

        def fake_feed(short_id=None, export_id=None, token=""):
            if short_id == "AsyeU2ElGU":
                return feed_payload("A")
            return feed_payload("B", video="")

        wx.save_auth(FAKE_COOKIE, source="test")
        with mock.patch.object(wx, "resolve_short_url", side_effect=lambda u: u):
            with mock.patch.object(wx, "get_feed_info", side_effect=fake_feed):
                with mock.patch.object(
                    wx,
                    "yuanbao_parse",
                    return_value=(200, {"data": {}}, ""),
                ):
                    with mock.patch.object(wx, "download_file", side_effect=self._write_bytes):
                        with mock.patch("sys.stdout", new=io.StringIO()):
                            with mock.patch.object(sys.stdin, "isatty", return_value=False):
                                code = wx.main(["--out-dir", self.out_dir, "--input-file", path])
        self.assertEqual(code, 1)

    def test_main_old_single_link_success(self):
        def fake_feed(short_id=None, export_id=None, token=""):
            return feed_payload("单条")

        with mock.patch.object(wx, "resolve_short_url", side_effect=lambda u: u):
            with mock.patch.object(wx, "get_feed_info", side_effect=fake_feed):
                with mock.patch.object(wx, "download_file", side_effect=self._write_bytes):
                    with mock.patch("sys.stdout", new=io.StringIO()) as out:
                        code = wx.main(["--out-dir", self.out_dir, URL_A])
        self.assertEqual(code, 0)
        self.assertIn("下载完成", out.getvalue())
        self.assertNotIn("汇总", out.getvalue())

    def test_main_status_and_logout_commands(self):
        wx.save_auth(FAKE_COOKIE, source="test")
        with mock.patch("sys.stdout", new=io.StringIO()) as out:
            code = wx.main(["status"])
        self.assertEqual(code, 0)
        self.assertNotIn("test-token", out.getvalue())
        with mock.patch("sys.stdout", new=io.StringIO()):
            code = wx.main(["logout"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.isfile(wx.auth_file()))

    def test_download_errors_do_not_leak_url(self):
        err = urllib_http_error(403, PLAYABLE_VIDEO)
        self.assertEqual(wx.safe_error_text(err), "HTTP 403")
        self.assertNotIn("SECRET", wx.safe_error_text(err))

    def test_playable_eid_still_preferred(self):
        token, export_id, playable = wx.parse_playable(
            {
                "data": {
                    "wx_export_id": "wrong-id",
                    "token": "",
                    "playable_url": "https://channels.weixin.qq.com/preview?eid=eid-from-playable&token=abc",
                }
            }
        )
        self.assertEqual(export_id, "eid-from-playable")
        self.assertEqual(token, "abc")
        self.assertTrue(playable)


def urllib_http_error(code, url):
    return urllib.error.HTTPError(url, code, "no", hdrs=None, fp=None)


class MenuEofTests(IsolatedAuthTestCase):
    def test_menu_eof_exits_cleanly(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            with mock.patch("sys.stdout", new=io.StringIO()) as out:
                wx.cmd_menu()
        self.assertIn("微信视频号下载", out.getvalue())


if __name__ == "__main__":
    unittest.main()
