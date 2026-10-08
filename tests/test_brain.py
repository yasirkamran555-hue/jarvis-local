import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain import OllamaBrain


class OllamaBrainTests(unittest.TestCase):
    def test_selects_installed_memory_friendly_model_first(self):
        brain = OllamaBrain()
        with patch.object(brain, "models", return_value=[
            {"name": "qwen2.5-coder:14b"},
            {"name": "llama3.1:8b"},
        ]):
            self.assertEqual(brain.choose_model(), "llama3.1:8b")

    def test_prefers_small_installed_model_over_14b_fallback(self):
        brain = OllamaBrain()
        with patch.object(brain, "models", return_value=[
            {"name": "qwen2.5-coder:14b"},
            {"name": "llama3.2:3b"},
        ]):
            self.assertEqual(brain.choose_model(), "llama3.2:3b")

    def test_chat_uses_local_server_and_bounded_context(self):
        brain = OllamaBrain()
        response = Mock()
        response.json.return_value = {"message": {"content": "Jarvis is online."}}
        with patch.object(brain, "models", return_value=[{"name": "llama3.1:8b"}]), \
                patch("brain.requests.post", return_value=response) as post:
            answer = brain.chat([{"role": "user", "content": "Are you online?"}])
        self.assertEqual(answer, "Jarvis is online.")
        post.assert_called_once_with(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": "llama3.1:8b",
                "messages": [{"role": "user", "content": "Are you online?"}],
                "options": {"num_ctx": 1024, "num_predict": 384, "num_thread": 2},
                "keep_alive": "1m",
                "stream": False,
            },
            timeout=180,
        )

    def test_rejects_invalid_context_configuration(self):
        with patch.dict("os.environ", {"OLLAMA_NUM_CTX": "128"}):
            with self.assertRaises(ValueError):
                OllamaBrain()

    def test_rejects_invalid_generation_limits(self):
        for setting, value in (("OLLAMA_NUM_PREDICT", "10"), ("OLLAMA_NUM_THREAD", "0")):
            with self.subTest(setting=setting), patch.dict("os.environ", {setting: value}):
                with self.assertRaises(ValueError):
                    OllamaBrain()

    def test_resource_limits_can_be_configured(self):
        with patch.dict("os.environ", {
            "OLLAMA_NUM_CTX": "2048",
            "OLLAMA_NUM_PREDICT": "512",
            "OLLAMA_NUM_THREAD": "3",
            "OLLAMA_KEEP_ALIVE": "30s",
        }):
            brain = OllamaBrain()
        self.assertEqual((brain.num_ctx, brain.num_predict, brain.num_thread, brain.keep_alive),
                         (2048, 512, 3, "30s"))


if __name__ == "__main__":
    unittest.main()
