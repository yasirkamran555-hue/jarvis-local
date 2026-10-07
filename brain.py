"""Offline Ollama client used by the planner and local chat."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests


class OllamaError(RuntimeError):
    """Raised when Ollama is unavailable or returns an invalid response."""


class OllamaBrain:
    PREFERRED_MODELS = ("qwen2.5-coder:14b", "llama3.2:3b")
    DEFAULT_URL = "http://127.0.0.1:11434"

    def __init__(self, base_url: str | None = None, timeout: int = 180):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or self.DEFAULT_URL).rstrip("/")
        self.timeout = timeout

    def models(self) -> list[dict[str, Any]]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            return response.json().get("models", [])
        except requests.RequestException as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url}. Start Ollama and try again ({exc})."
            ) from exc

    def choose_model(self, requested: str | None = None) -> str:
        installed = {str(model.get("name", "")) for model in self.models()}
        if requested and requested in installed:
            return requested
        for preferred in self.PREFERRED_MODELS:
            if preferred in installed:
                return preferred
        found = ", ".join(sorted(installed)) or "none"
        raise OllamaError(
            "Neither requested model is installed. Run `ollama pull qwen2.5-coder:14b` "
            f"or `ollama pull llama3.2:3b`. Installed models: {found}."
        )

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        selected = self.choose_model(model)
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={"model": selected, "messages": messages, "stream": False},
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            if not isinstance(content, str) or not content.strip():
                raise OllamaError("Ollama returned an empty response.")
            return content.strip()
        except requests.RequestException as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

    def plan(self, request: str, tool_catalog: list[dict], memories: list[str] | None = None) -> dict:
        context = "\n".join(f"- {item}" for item in memories or []) or "(no relevant saved memories)"
        prompt = f"""Create a short, safe execution plan for this user request.

Only choose tools that appear in the catalog. Never invent tools or arguments.
Do not request raw secrets, run arbitrary shell commands, access unrelated files,
or send messages / upload files / modify the computer unless the request clearly asks.
Use at most 6 steps. If the request is ambiguous, produce zero steps and ask a question
in the summary. Return JSON only, with this shape:
{{"summary":"one sentence","steps":[{{"tool":"tool_name","args":{{}},"reason":"why this step is needed"}}]}}

Allowed tools:
{json.dumps(tool_catalog, ensure_ascii=False)}

Relevant local memory:
{context}

User request:
{request}
"""
        raw = self.chat(
            [
                {"role": "system", "content": "You are a cautious local desktop assistant. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            model="qwen2.5-coder:14b",
        )
        cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw, flags=re.IGNORECASE)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Planner returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("steps", []), list):
            raise OllamaError("Planner response must contain an object with a steps list.")
        return data
