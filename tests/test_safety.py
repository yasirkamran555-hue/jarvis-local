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
import replit_mcp
from replit_mcp import create_repl
from tools_replit import create_local_project


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
            "create_repl", "create_local_project", "list_repls", "update_repl",
            "publish_repl", "live_preview", "make_exe", "install_package",
            "audio_transcribe", "speak_text", "docker_build", "docker_run",
        ):
            with self.subTest(tool=name):
                self.assertTrue(callable(tools[name]))

    def test_create_local_project_builds_files_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("tools_replit.PROJECTS_DIR", Path(directory)):
                created = create_local_project("test-project", "python")
                project = Path(created["path"])
                self.assertTrue((project / "main.py").is_file())
                self.assertTrue((project / "README.md").is_file())
                with self.assertRaises(FileExistsError):
                    create_local_project("test-project", "python")

    def test_replit_create_rejects_invalid_stack_before_network(self):
        with self.assertRaises(ValueError):
            create_repl("Build a website", "unsupported")

    def test_replit_create_maps_arguments_to_documented_mcp_schema(self):
        with patch("replit_mcp._call_tool_sync", return_value={"result": "queued"}) as call:
            result = create_repl(
                "Build a task tracker",
                "react_website",
                "Task Tracker",
                "source-app-id",
            )
        self.assertEqual(result, {"result": "queued"})
        call.assert_called_once_with("create_app_from_prompt", {
            "appDescription": "Build a task tracker",
            "app_stack": "react_website",
            "userSpecifiedAppName": "Task Tracker",
            "sourceReplId": "source-app-id",
        })

    def test_replit_oauth_callback_server_returns_validated_code_parameters(self):
        import asyncio
        import socket
        from urllib.parse import parse_qs, urlsplit

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()

        async def exercise_callback():
            with patch.dict("os.environ", {"JARVIS_REPLIT_OAUTH_PORT": str(port)}):
                callback = replit_mcp._OAuthCallback()
                redirect_uri = await callback.start()
                parsed = urlsplit(redirect_uri)
                reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
                writer.write(
                    b"GET /oauth/callback?code=test-code&state=test-state&iss=https%3A%2F%2Freplit.com HTTP/1.1\r\n"
                    b"Host: 127.0.0.1\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                response = await reader.read()
                result = await callback.wait_for_code()
                writer.close()
                await writer.wait_closed()
                await callback.close()
                return response, result

        response, result = asyncio.run(exercise_callback())
        self.assertIn(b"200 OK", response)
        self.assertEqual(result.code, "test-code")
        self.assertEqual(result.state, "test-state")
        self.assertEqual(result.iss, "https://replit.com")


if __name__ == "__main__":
    unittest.main()
