import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
import eyes
import hands
import memory
from tools_connections import mysql_query
from tools_replit import create_repl


class SafetyBoundaryTests(unittest.TestCase):
    def test_workspace_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(hands, "WORKSPACE_ROOT", Path(directory)):
                with self.assertRaises(ValueError):
                    hands._workspace_path("../outside.txt")

    def test_mysql_rejects_writes_before_connecting(self):
        record = {"type": "mysql", "host": "localhost", "username": "u", "password": "p"}
        for query in ("DROP TABLE users", "SELECT * FROM users; DELETE FROM users", "SELECT secret INTO OUTFILE '/tmp/x'"):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    mysql_query(record, query)

    def test_screen_ocr_rejects_arbitrary_files(self):
        with self.assertRaises(ValueError):
            eyes.ocr("/etc/passwd")

    def test_skill_recipe_omits_prompt_and_action_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(memory, "SKILLS_DIR", Path(directory)):
                path = memory.maybe_create_skill(
                    "send private@example.com a secret message",
                    {"steps": [
                        {"tool": "gmail", "args": {"to": "private@example.com", "body": "secret"}},
                        {"tool": "github", "args": {"token": "not-for-disk"}},
                    ]},
                    successful=True,
                )
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("private@example.com", content)
                self.assertNotIn("not-for-disk", content)
                self.assertIn("gmail", content)
                self.assertIn("github", content)

    def test_agent_exposes_required_real_tool_implementations(self):
        tools = agent._load_tools()
        for name in (
            "desktop_click", "desktop_type", "file_list", "file_read", "file_write",
            "zimbra_email", "gmail", "mysql_query", "ftp_upload", "whatsapp", "github",
            "create_repl", "live_preview", "make_exe", "install_package",
            "audio_transcribe", "speak_text", "docker_build", "docker_run",
        ):
            with self.subTest(tool=name):
                self.assertTrue(callable(tools[name]))

    def test_create_repl_builds_real_local_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("tools_replit.PROJECTS_DIR", Path(directory)):
                created = create_repl("test-project", "python")
                project = Path(created["path"])
                self.assertTrue((project / "main.py").is_file())
                self.assertTrue((project / "README.md").is_file())
                with self.assertRaises(FileExistsError):
                    create_repl("test-project", "python")


if __name__ == "__main__":
    unittest.main()
