"""User-approved local desktop, browser, and workspace tools."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus, urlparse

APP_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = Path(os.getenv("JARVIS_WORKSPACE", str(APP_DIR / "workspace"))).expanduser().resolve()
MAX_TEXT = 4000
MAX_READ_BYTES = 512_000


@lru_cache(maxsize=1)
def _load_whisper_model(model_name: str, cache_dir: str):
    from faster_whisper import WhisperModel

    return WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        download_root=cache_dir,
    )


def _workspace_path(relative_path: str) -> Path:
    candidate = (WORKSPACE_ROOT / relative_path).resolve()
    root = WORKSPACE_ROOT.resolve()
    normalized_candidate = os.path.normcase(os.path.realpath(candidate))
    normalized_root = os.path.normcase(os.path.realpath(root))
    try:
        if os.path.commonpath([normalized_candidate, normalized_root]) != normalized_root:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Path is outside the configured JARVIS workspace.") from exc
    return candidate


def _pyautogui():
    if sys.platform != "win32" and not os.getenv("DISPLAY") and not os.getenv("WAYLAND_DISPLAY"):
        raise RuntimeError("Desktop input is unavailable in this headless session. Run JARVIS on your Windows desktop.")
    try:
        import pyautogui
    except Exception as exc:
        raise RuntimeError(f"pyautogui is unavailable: {exc}") from exc
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.15
    return pyautogui


def click_screen(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    if button not in {"left", "right", "middle"} or clicks not in {1, 2}:
        raise ValueError("Use left/right/middle button and one or two clicks.")
    ui = _pyautogui()
    width, height = ui.size()
    if not (0 <= int(x) < width and 0 <= int(y) < height):
        raise ValueError(f"Coordinates must be inside the {width}×{height} screen.")
    ui.click(x=int(x), y=int(y), clicks=clicks, button=button)
    return {"clicked": True, "x": int(x), "y": int(y), "button": button, "clicks": clicks}


def type_text(text: str) -> dict:
    if len(text) > MAX_TEXT:
        raise ValueError(f"Text is limited to {MAX_TEXT} characters per action.")
    ui = _pyautogui()
    try:
        import pyperclip
        pyperclip.copy(text)
        ui.hotkey("ctrl", "v")
    except ImportError:
        if not text.isascii():
            raise RuntimeError("Unicode typing needs pyperclip. Install project requirements.")
        ui.write(text, interval=0.01)
    return {"typed_characters": len(text)}


def hotkey(keys: list[str]) -> dict:
    if not 1 <= len(keys) <= 4:
        raise ValueError("A hotkey must contain between one and four keys.")
    allowed = {
        "ctrl", "alt", "shift", "win", "enter", "esc", "tab", "space", "backspace", "delete",
        "up", "down", "left", "right", "home", "end", "pageup", "pagedown", "f1", "f2", "f3",
        "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    }
    normalized = [key.lower() for key in keys]
    if any(key not in allowed and not re.fullmatch(r"[a-z0-9]", key) for key in normalized):
        raise ValueError("Hotkey contains an unsupported key.")
    _pyautogui().hotkey(*normalized)
    return {"pressed": normalized}


_APPLICATIONS = {
    "calculator": "calc.exe",
    "edge": "msedge.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
    "settings": "ms-settings:",
    "task manager": "taskmgr.exe",
}


def open_application(application: str) -> dict:
    """Open one explicitly allowlisted Windows desktop application."""
    if sys.platform != "win32":
        raise RuntimeError("Opening desktop applications is supported only on Windows.")
    normalized = re.sub(r"\s+", " ", application.strip().casefold())
    target = _APPLICATIONS.get(normalized)
    if not target:
        supported = ", ".join(sorted(_APPLICATIONS))
        raise ValueError(f"Unsupported app. Choose one of: {supported}.")
    try:
        os.startfile(target)  # type: ignore[attr-defined]
    except OSError as exc:
        raise RuntimeError(f"Windows could not open {normalized}: {exc}") from exc
    return {"opened_application": normalized}


def open_url(url: str) -> dict:
    """Open a validated HTTP(S) URL in the user's default desktop browser."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(char in url for char in "\r\n\x00"):
        raise ValueError("Provide a complete http or https URL.")
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:
        raise RuntimeError(f"Could not open the URL in the desktop browser: {exc}") from exc
    if not opened:
        raise RuntimeError("The default desktop browser did not accept the URL.")
    return {"opened_url": url}


def search_in_browser(query: str, engine: str = "duckduckgo") -> dict:
    """Open a search results page in the desktop browser using a known search provider."""
    query = query.strip()
    if not query or len(query) > 300:
        raise ValueError("Browser search query must contain 1–300 characters.")
    search_urls = {
        "bing": "https://www.bing.com/search?q=",
        "duckduckgo": "https://duckduckgo.com/?q=",
        "google": "https://www.google.com/search?q=",
    }
    provider = engine.casefold().strip()
    if provider not in search_urls:
        raise ValueError("Browser search engine must be Google, Bing, or DuckDuckGo.")
    return open_url(search_urls[provider] + quote_plus(query))


def manage_windows(action: str) -> dict:
    """Perform a small, explicit set of standard Windows window-management shortcuts."""
    if sys.platform != "win32":
        raise RuntimeError("Window management is supported only on Windows.")
    shortcuts = {
        "maximize": ["win", "up"],
        "minimize": ["win", "down"],
        "restore": ["win", "shift", "m"],
        "show_desktop": ["win", "d"],
        "switch_window": ["alt", "tab"],
        "switch_application": ["alt", "tab"],
        "switch_back": ["alt", "shift", "tab"],
    }
    normalized = action.casefold().strip().replace(" ", "_")
    keys = shortcuts.get(normalized)
    if keys is None:
        supported = ", ".join(sorted(shortcuts))
        raise ValueError(f"Unsupported window action. Choose one of: {supported}.")
    _pyautogui().hotkey(*keys)
    return {"window_action": normalized, "keys": keys}


def get_ui_controls(max_depth: int = 3) -> list[dict]:
    if sys.platform != "win32":
        raise RuntimeError("Windows UI Automation (uiautomation) is available only on Windows.")
    if not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be between 1 and 6.")
    try:
        import uiautomation as auto
    except ImportError as exc:
        raise RuntimeError("Install the Windows uiautomation package.") from exc
    root = auto.GetRootControl()
    found: list[dict] = []

    def visit(control, depth: int):
        if len(found) >= 250:
            return
        try:
            found.append({
                "depth": depth,
                "name": str(control.Name or "")[:180],
                "type": str(control.ControlTypeName or ""),
                "automation_id": str(control.AutomationId or "")[:120],
                "enabled": bool(control.IsEnabled),
            })
            if depth < max_depth:
                for child in control.GetChildren():
                    visit(child, depth + 1)
        except Exception:
            return

    visit(root, 0)
    return found


def click_ui_control(name: str, control_type: str | None = None) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("Windows UI Automation (uiautomation) is available only on Windows.")
    if not name.strip() or len(name) > 180:
        raise ValueError("Provide a control name up to 180 characters.")
    try:
        import uiautomation as auto
    except ImportError as exc:
        raise RuntimeError("Install the Windows uiautomation package.") from exc
    root = auto.GetRootControl()
    matches = []
    pending = [(root, 0)]
    while pending and len(matches) < 2:
        control, depth = pending.pop()
        try:
            if control.Name == name and (not control_type or control.ControlTypeName == control_type):
                matches.append(control)
            if depth < 8:
                pending.extend((child, depth + 1) for child in control.GetChildren())
        except Exception:
            continue
    if len(matches) != 1:
        raise RuntimeError(f"Expected one matching top-level control, found {len(matches)}. Inspect controls first.")
    matches[0].Click()
    return {"clicked_control": name}


def list_files(relative_path: str = ".", limit: int = 100) -> list[dict]:
    root = _workspace_path(relative_path)
    if not root.exists():
        return []
    if not root.is_dir():
        raise ValueError("The requested workspace path is not a directory.")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500.")
    rows = []
    for item in sorted(root.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower()))[:limit]:
        rows.append({
            "name": item.name,
            "path": item.relative_to(WORKSPACE_ROOT).as_posix(),
            "directory": item.is_dir(),
            "size": item.stat().st_size if item.is_file() else None,
        })
    return rows


def read_file(relative_path: str) -> str:
    path = _workspace_path(relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"Workspace file not found: {relative_path}")
    if path.stat().st_size > MAX_READ_BYTES:
        raise ValueError(f"File exceeds the {MAX_READ_BYTES}-byte read limit.")
    return path.read_text(encoding="utf-8")


def write_file(relative_path: str, content: str) -> dict:
    path = _workspace_path(relative_path)
    if len(content.encode("utf-8")) > MAX_READ_BYTES:
        raise ValueError(f"File writes are limited to {MAX_READ_BYTES} bytes.")
    if path.suffix.lower() in {".exe", ".dll", ".bat", ".cmd", ".ps1", ".com", ".scr"}:
        raise ValueError("Writing executable or shell-script files through the agent is disabled.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"written": path.relative_to(WORKSPACE_ROOT).as_posix(), "bytes": path.stat().st_size}


def delete_file(relative_path: str) -> dict:
    path = _workspace_path(relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"Workspace file not found: {relative_path}")
    try:
        from send2trash import send2trash
        send2trash(str(path))
    except ImportError as exc:
        raise RuntimeError("Safe deletion requires send2trash. Install project requirements.") from exc
    return {"moved_to_recycle_bin": path.relative_to(WORKSPACE_ROOT).as_posix()}


def open_browser_url(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only complete http:// or https:// URLs are allowed.")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install Playwright and run `python -m playwright install chromium`.") from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        result = {
            "url": page.url,
            "title": page.title(),
            "text": page.locator("body").inner_text(timeout=10_000)[:5000],
        }
        browser.close()
    return result


def open_local_file(relative_path: str) -> dict:
    path = _workspace_path(relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"Workspace file not found: {relative_path}")
    if path.suffix.lower() in {".exe", ".com", ".bat", ".cmd", ".ps1", ".py", ".js", ".vbs", ".msi"}:
        raise ValueError("Opening executable or script files is disabled.")
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": path.relative_to(WORKSPACE_ROOT).as_posix()}


def speak_text(text: str) -> dict:
    if len(text) > 2000:
        raise ValueError("Speech output is limited to 2,000 characters.")
    try:
        from piper.voice import PiperVoice
    except ImportError as exc:
        raise RuntimeError("Install piper-tts. Download a Piper voice model and set PIPER_MODEL.") from exc
    model_path = Path(os.getenv("PIPER_MODEL", "")).expanduser()
    if not model_path.is_file():
        raise RuntimeError("Set PIPER_MODEL to a downloaded local Piper .onnx voice model.")
    from piper.config import SynthesisConfig
    voice = PiperVoice.load(str(model_path))
    output_dir = APP_DIR / "workspace" / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"speech-{int(time.time() * 1000)}.wav"
    with output.open("wb") as stream:
        voice.synthesize_wav(text, stream, SynthesisConfig())
    return {"audio_file": str(output)}


def transcribe_audio(audio_path: str, model_size: str | None = None) -> dict:
    """Transcribe a user-supplied local recording with faster-whisper."""
    if not audio_path:
        raise ValueError("Record or upload an audio file first.")
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Audio file must exist and be no larger than 25 MB.")
    if path.suffix.lower() not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".wma", ".webm"}:
        raise ValueError("Supported audio formats: WAV, MP3, M4A, FLAC, OGG, WMA, and WEBM.")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install faster-whisper from requirements.txt to transcribe audio.") from exc
    model_name = model_size or os.getenv("WHISPER_MODEL", "small")
    if model_name not in {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}:
        raise ValueError("Whisper model must be tiny, base, small, medium, large-v3, or large-v3-turbo.")
    cache_dir = APP_DIR / "workspace" / "models" / "whisper"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        model = _load_whisper_model(model_name, str(cache_dir))
        segments, info = model.transcribe(str(path), vad_filter=True)
        transcript = " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:
        raise RuntimeError(f"Speech transcription failed: {exc}") from exc
    return {"text": transcript, "language": info.language, "duration_seconds": round(info.duration, 2)}
