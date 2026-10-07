"""Screen capture, OCR, and optional local OmniParser integration."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = APP_DIR / "workspace" / "screenshots"


def _screen_capture(output_path: str | Path | None = None) -> Path:
    try:
        import mss
        import mss.tools
    except ImportError as exc:
        raise RuntimeError("Screen capture requires mss. Install the project requirements.") from exc
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    target = (SCREENSHOT_DIR / Path(output_path).name) if output_path else SCREENSHOT_DIR / f"screen-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
    target = target.resolve()
    try:
        target.relative_to(SCREENSHOT_DIR.resolve())
    except ValueError as exc:
        raise ValueError("Screenshots must be saved inside workspace/screenshots.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    with mss.mss() as capture:
        monitor = capture.monitors[1] if len(capture.monitors) > 1 else capture.monitors[0]
        if monitor["width"] <= 0 or monitor["height"] <= 0:
            raise RuntimeError("No capturable display is available in this session.")
        shot = capture.grab(monitor)
        mss.tools.to_png(shot.rgb, shot.size, output=str(target))
    return target.resolve()


def screenshot(output_path: str | None = None) -> dict[str, Any]:
    path = _screen_capture(output_path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": _hash_file(path)}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ocr(image_path: str | None = None) -> dict[str, Any]:
    path = Path(image_path).expanduser().resolve() if image_path else _screen_capture()
    if image_path:
        try:
            path.relative_to(SCREENSHOT_DIR.resolve())
        except ValueError as exc:
            raise ValueError("OCR may read only screenshots inside workspace/screenshots.") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Screenshot does not exist: {path}")
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("OCR requires pytesseract and Pillow. Install requirements and Tesseract OCR.") from exc
    try:
        tesseract_cmd = os.getenv("TESSERACT_CMD")
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        text = pytesseract.image_to_string(Image.open(path))
    except Exception as exc:
        raise RuntimeError(f"Tesseract OCR failed. Install Tesseract OCR and configure TESSERACT_CMD if needed: {exc}") from exc
    return {"path": str(path), "text": text.strip(), "characters": len(text.strip())}


def omniparser(image_path: str | None = None) -> dict[str, Any]:
    endpoint = os.getenv("OMNIPARSER_URL", "").strip()
    if not endpoint:
        raise RuntimeError(
            "OmniParser is not configured. Start a local OmniParser-compatible HTTP service "
            "and set OMNIPARSER_URL to its /parse endpoint."
        )
    path = Path(image_path).expanduser().resolve() if image_path else _screen_capture()
    if image_path:
        try:
            path.relative_to(SCREENSHOT_DIR.resolve())
        except ValueError as exc:
            raise ValueError("OmniParser may read only screenshots inside workspace/screenshots.") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Screenshot does not exist: {path}")
    try:
        with path.open("rb") as image:
            response = requests.post(
                endpoint,
                files={"image": (path.name, image, "image/png")},
                timeout=120,
            )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"OmniParser request failed: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("OmniParser endpoint returned non-JSON data.") from exc
    return {"path": str(path), "result": result}


def validate_screen_after_action(action: str) -> dict[str, Any]:
    """Capture evidence after a desktop action; report observability honestly."""
    try:
        captured = screenshot()
    except Exception as exc:
        return {"checked": False, "action": action, "reason": str(exc)}
    try:
        text_result = ocr(captured["path"])
        return {
            "checked": True,
            "action": action,
            "screenshot": captured["path"],
            "sha256": captured["sha256"],
            "ocr_characters": text_result["characters"],
            "ocr_excerpt": text_result["text"][:500],
        }
    except Exception as exc:
        return {
            "checked": True,
            "action": action,
            "screenshot": captured["path"],
            "sha256": captured["sha256"],
            "ocr_available": False,
            "reason": str(exc),
        }
