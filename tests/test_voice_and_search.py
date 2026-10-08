import base64
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import agent
import desktop_app
import hands
from web_search import search_web


class VoiceTurnTests(unittest.TestCase):
    def test_voice_pc_command_creates_approval_plan_before_execution(self):
        plan = {
            "summary": "Open Notepad.",
            "steps": [{"tool": "open_application", "args": {"application": "notepad"}}],
        }
        with patch("main.Agent") as agent_class:
            agent_class.return_value.plan.return_value = plan
            response, state = main._voice_turn("Hey Jarvis, open Notepad", {})
            agent_class.return_value.execute.assert_not_called()
        agent_class.return_value.plan.assert_called_once_with("Hey Jarvis, open Notepad")
        self.assertEqual(state["pending"], plan)
        self.assertIn("Say approve", json.loads(response)["text"])

    def test_open_application_allows_only_known_windows_apps(self):
        with patch("hands.sys.platform", "win32"), patch.object(hands.os, "startfile", create=True) as startfile:
            result = hands.open_application("File Explorer")
        startfile.assert_called_once_with("explorer.exe")
        self.assertEqual(result, {"opened_application": "file explorer"})
        with patch("hands.sys.platform", "win32"), patch.object(hands.os, "startfile", create=True) as startfile:
            with self.assertRaises(ValueError):
                hands.open_application("powershell")
        startfile.assert_not_called()

    def test_open_url_only_launches_validated_web_urls(self):
        with patch("hands.webbrowser.open", return_value=True) as open_browser:
            result = hands.open_url("https://example.com")
        open_browser.assert_called_once_with("https://example.com", new=2)
        self.assertEqual(result, {"opened_url": "https://example.com"})
        with patch("hands.webbrowser.open") as open_browser:
            with self.assertRaises(ValueError):
                hands.open_url("file:///C:/Windows/win.ini")
        open_browser.assert_not_called()

    def test_browser_search_opens_encoded_query_in_allowlisted_engine(self):
        with patch("hands.open_url", return_value={"opened_url": "https://www.google.com/search?q=jarvis+desktop+control"}) as open_url:
            result = hands.search_in_browser("jarvis desktop control", "Google")
        open_url.assert_called_once_with("https://www.google.com/search?q=jarvis+desktop+control")
        self.assertEqual(result["opened_url"], "https://www.google.com/search?q=jarvis+desktop+control")
        with patch("hands.open_url") as open_url:
            with self.assertRaises(ValueError):
                hands.search_in_browser("private query", "unknown")
        open_url.assert_not_called()

    def test_window_management_uses_only_supported_shortcuts(self):
        with patch("hands.sys.platform", "win32"), patch("hands._pyautogui") as pyautogui:
            result = hands.manage_windows("switch window")
        pyautogui.return_value.hotkey.assert_called_once_with("alt", "tab")
        self.assertEqual(result["window_action"], "switch_window")
        with patch("hands.sys.platform", "win32"), patch("hands._pyautogui") as pyautogui:
            with self.assertRaises(ValueError):
                hands.manage_windows("close_all")
        pyautogui.assert_not_called()

    def test_browser_and_window_voice_commands_require_approval(self):
        for phrase, tool in (
            ("Hey Jarvis, search Google for local voice control", "search_in_browser"),
            ("Jarvis, switch to the previous window", "manage_windows"),
            ("Jarvis, type hello there", "desktop_type"),
        ):
            with self.subTest(phrase=phrase):
                plan = {"summary": "Proposed local PC action.", "steps": [{"tool": tool, "args": {}}]}
                with patch("main.Agent") as agent_class:
                    agent_class.return_value.plan.return_value = plan
                    response, state = main._voice_turn(phrase, {})
                    agent_class.return_value.execute.assert_not_called()
                self.assertEqual(state["pending"], plan)
                self.assertIn("Say approve", json.loads(response)["text"])

    def test_demo_browser_voice_commands_require_approval(self):
        plan = {"summary": "Inspect the demo page.", "steps": [{"tool": "inspect_demo_page", "args": {}}]}
        with patch("main.Agent") as agent_class:
            agent_class.return_value.plan.return_value = plan
            response, state = main._voice_turn("Jarvis, inspect the demo page", {})
            agent_class.return_value.execute.assert_not_called()
        self.assertEqual(state["pending"], plan)
        self.assertIn("Say approve", json.loads(response)["text"])

    def test_desktop_voice_recording_uses_device_sample_rate_and_detects_audio(self):
        frames = [desktop_app.np.full(1024, 0.1, dtype="float32")]
        with tempfile.TemporaryDirectory() as directory:
            recording = Path(directory) / "speech.wav"
            desktop_app._write_recording(frames, recording, sample_rate=44100)
            with wave.open(str(recording), "rb") as audio:
                self.assertEqual(audio.getframerate(), 44100)
                self.assertEqual(audio.getnframes(), 1024)
        self.assertGreater(desktop_app._frame_level(frames[0]), 0.09)

    def test_local_voice_transcription_uses_whisper_and_removes_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            encoded_audio = base64.b64encode(b"webm-audio").decode("ascii")

            def transcribe(path, model_size):
                self.assertEqual(Path(path).read_bytes(), b"webm-audio")
                self.assertEqual(model_size, "tiny")
                return {"text": "hello jarvis"}

            with patch("main.APP_DIR", Path(directory)), \
                    patch("hands.transcribe_audio", side_effect=transcribe) as transcription, \
                    patch.dict("os.environ", {"JARVIS_VOICE_MODEL": "tiny"}):
                result = json.loads(main._transcribe_voice_clip(encoded_audio))
            self.assertEqual(result["text"], "hello jarvis")
            args, kwargs = transcription.call_args
            self.assertTrue(Path(args[0]).name.startswith("voice-"))
            self.assertEqual(kwargs, {"model_size": "tiny"})
            self.assertEqual(list((Path(directory) / "workspace" / "audio").iterdir()), [])

    def test_device_actions_wait_for_spoken_approval(self):
        plan = {"summary": "Create a project.", "steps": [{"tool": "create_local_project", "args": {}}]}
        with patch("main.Agent") as agent_class:
            agent_class.return_value.plan.return_value = plan
            response, state = main._voice_turn("create a project", {})
            agent_class.return_value.execute.assert_not_called()
            self.assertEqual(state["pending"], plan)
            self.assertIn("Say approve", json.loads(response)["text"])

            response, state = main._voice_turn("approve", state)
            agent_class.return_value.execute.assert_called_once_with(plan)
            self.assertIsNone(state.get("pending"))
            self.assertIn("completed", json.loads(response)["text"])

    def test_web_research_is_search_on_request_and_returns_answer(self):
        results = [{"title": "Result", "snippet": "Useful summary.", "url": "https://example.com"}]
        with patch("web_search.search_web", return_value={"results": results}) as search, \
                patch("brain.OllamaBrain.chat", return_value="Here is the latest answer.") as chat:
            response, state = main._voice_turn("Search the web for local AI news", {})
        search.assert_called_once_with("local AI news", limit=5)
        self.assertEqual(json.loads(response)["text"], "Here is the latest answer.")
        self.assertIsNone(state.get("pending"))
        self.assertIn("https://example.com", chat.call_args.args[0][1]["content"])

    def test_positive_acknowledgement_does_not_approve_pending_action(self):
        plan = {"summary": "Delete a file.", "steps": [{"tool": "file_delete", "args": {}}]}
        state = {"pending": plan}
        with patch("main.Agent") as agent_class:
            response, updated = main._voice_turn("yes", state)
        agent_class.return_value.execute.assert_not_called()
        self.assertEqual(updated["pending"], plan)
        self.assertIn("Say approve", json.loads(response)["text"])

    def test_voice_conversation_context_is_retained_between_turns(self):
        with patch("brain.OllamaBrain.chat", side_effect=["The first answer.", "The second answer."]) as chat, \
                patch("main.MemoryStore") as memory:
            memory.return_value.recall.return_value = []
            _, state = main._voice_turn("hello", {})
            _, state = main._voice_turn("tell me more", state)
        second_turn = chat.call_args_list[1].args[0]
        self.assertTrue(any(item.get("content") == "hello" for item in second_turn))
        self.assertTrue(any(item.get("content") == "The first answer." for item in second_turn))
        self.assertEqual(len(state["conversation"]), 4)


class WebSearchTests(unittest.TestCase):
    def test_search_parses_and_persists_only_bounded_public_result_summaries(self):
        html = """
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">Example result</a>
        <a class="result__snippet">A useful public summary.</a>
        """
        response = Mock(text=html)
        response.raise_for_status.return_value = None
        memory = Mock()
        with patch("web_search.requests.get", return_value=response) as get, \
                patch("web_search.MemoryStore", return_value=memory):
            result = search_web("example query", limit=1)
        get.assert_called_once()
        self.assertEqual(result["results"], [{
            "title": "Example result",
            "url": "https://example.com/page",
            "snippet": "A useful public summary.",
        }])
        memory.remember.assert_called_once()
        self.assertEqual(memory.remember.call_args.kwargs["category"], "web-research")

    def test_search_rejects_invalid_query_before_network(self):
        with patch("web_search.requests.get") as get:
            with self.assertRaises(ValueError):
                search_web("  ")
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
