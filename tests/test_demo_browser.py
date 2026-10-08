import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_browser
import tools_replit


class DemoBrowserTests(unittest.TestCase):
    def test_open_url_uses_the_dedicated_visible_session(self):
        page = Mock()
        page.url = "https://example.com/"
        page.title.return_value = "Example"
        with patch.object(demo_browser._BROWSER, "call", side_effect=lambda operation: operation(page)) as call:
            result = demo_browser.open_demo_url("https://example.com")
        page.goto.assert_called_once_with("https://example.com", wait_until="domcontentloaded", timeout=30000)
        self.assertEqual(result["browser"], "Jarvis isolated Chromium")
        self.assertEqual(call.call_count, 1)

    def test_demo_browser_rejects_non_web_urls_and_embedded_credentials(self):
        for url in ("file:///C:/Windows/win.ini", "https://user:pass@example.com"):
            with self.subTest(url=url), patch.object(demo_browser._BROWSER, "call") as call:
                with self.assertRaises(ValueError):
                    demo_browser.open_demo_url(url)
                call.assert_not_called()

    def test_demo_search_uses_an_allowlisted_provider(self):
        with patch.object(demo_browser, "open_demo_url", return_value={"opened_url": "https://www.google.com/search?q=jarvis+voice"}) as open_url:
            result = demo_browser.search_demo_web("jarvis voice", "Google")
        open_url.assert_called_once_with("https://www.google.com/search?q=jarvis+voice")
        self.assertEqual(result["search_engine"], "google")
        with patch.object(demo_browser, "open_demo_url") as open_url:
            with self.assertRaises(ValueError):
                demo_browser.search_demo_web("query", "unknown")
            open_url.assert_not_called()

    def test_click_requires_one_exactly_named_supported_role(self):
        page = Mock()
        match = Mock()
        match.count.return_value = 1
        page.get_by_role.return_value = match
        with patch.object(demo_browser._BROWSER, "call", side_effect=lambda operation: operation(page)):
            result = demo_browser.click_demo_element("Continue")
        page.get_by_role.assert_called_once_with("button", name="Continue", exact=True)
        match.click.assert_called_once_with(timeout=10000)
        self.assertEqual(result["clicked"], "Continue")

    def test_project_writer_confines_files_to_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(tools_replit, "PROJECTS_DIR", Path(directory)):
                tools_replit.create_local_project("voice-demo", "static")
                result = tools_replit.write_project_file("voice-demo", "pages/home.html", "<h1>Hello</h1>")
                self.assertEqual(result["file"], str(Path("pages") / "home.html"))
                self.assertEqual(
                    (Path(directory) / "voice-demo" / "pages" / "home.html").read_text(encoding="utf-8"),
                    "<h1>Hello</h1>",
                )
                with self.assertRaises(ValueError):
                    tools_replit.write_project_file("voice-demo", "../escape.txt", "no")
                with self.assertRaises(ValueError):
                    tools_replit.write_project_file("voice-demo", "large.txt", "x" * 500_001)

    def test_show_project_hands_preview_to_the_isolated_browser(self):
        process = Mock()
        process.poll.return_value = None
        process.pid = 1234
        with patch.dict(tools_replit._PREVIEWS, {"demo": process}, clear=True), \
                patch.dict(tools_replit._PREVIEW_URLS, {"demo": "http://127.0.0.1:4321"}, clear=True), \
                patch("demo_browser.open_demo_url", return_value={"title": "Demo"}) as open_url:
            result = tools_replit.show_project("demo")
        open_url.assert_called_once_with("http://127.0.0.1:4321")
        self.assertEqual(result["browser"], {"title": "Demo"})
        self.assertEqual(result["url"], "http://127.0.0.1:4321")


if __name__ == "__main__":
    unittest.main()
