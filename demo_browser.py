"""Visible Chromium session isolated from the user's everyday browser."""

from __future__ import annotations

import atexit
import queue
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus, urlparse

APP_DIR = Path(__file__).resolve().parent
PROFILE_DIR = APP_DIR / "workspace" / "browser-profile"
SEARCH_ENGINES = {
    "bing": "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "google": "https://www.google.com/search?q=",
}
CLICK_ROLES = {"button", "link", "checkbox", "radio", "tab", "menuitem"}


class _BrowserSession:
    def __init__(self) -> None:
        self._jobs: queue.Queue[tuple[Callable, Future] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._lock = threading.Lock()

    def _run(self) -> None:
        playwright = None
        context = None
        try:
            from playwright.sync_api import sync_playwright

            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 800},
                args=["--disable-background-networking", "--no-first-run"],
            )
            page = context.pages[0] if context.pages else context.new_page()
        except BaseException as exc:
            self._start_error = exc
            self._ready.set()
            if context is not None:
                context.close()
            if playwright is not None:
                playwright.stop()
            return

        self._ready.set()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                operation, future = job
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(operation(page))
                    except BaseException as exc:
                        future.set_exception(exc)
        finally:
            context.close()
            playwright.stop()

    def call(self, operation: Callable) -> dict:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._start_error = None
                self._thread = threading.Thread(
                    target=self._run,
                    name="jarvis-demo-browser",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(timeout=30):
            raise TimeoutError("Jarvis's isolated browser did not start within 30 seconds.")
        if self._start_error is not None:
            raise RuntimeError(f"Could not start Jarvis's isolated browser: {self._start_error}") from self._start_error
        future = Future()
        self._jobs.put((operation, future))
        return future.result(timeout=45)

    def close(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return
            self._jobs.put(None)
        thread.join(timeout=5)


_BROWSER = _BrowserSession()
atexit.register(_BROWSER.close)


def _validate_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or any(char in url for char in "\r\n\x00")
    ):
        raise ValueError("Provide an http or https URL without embedded credentials.")
    return url.strip()


def open_demo_url(url: str) -> dict:
    """Open a page in Jarvis's own visible Chromium window and profile."""
    target = _validate_url(url)

    def navigate(page):
        page.goto(target, wait_until="domcontentloaded", timeout=30000)
        return {"opened_url": page.url, "title": page.title(), "browser": "Jarvis isolated Chromium"}

    return _BROWSER.call(navigate)


def search_demo_web(query: str, engine: str = "duckduckgo") -> dict:
    query = query.strip()
    if not query or len(query) > 300:
        raise ValueError("Browser search query must contain 1–300 characters.")
    provider = engine.casefold().strip()
    if provider not in SEARCH_ENGINES:
        raise ValueError("Browser search engine must be Google, Bing, or DuckDuckGo.")
    url = SEARCH_ENGINES[provider] + quote_plus(query)
    result = open_demo_url(url)
    result["search_engine"] = provider
    return result


def inspect_demo_page() -> dict:
    """Summarize the current page in the visible Jarvis browser."""

    def inspect(page):
        body = page.locator("body").inner_text(timeout=10000)[:4000]
        buttons = page.get_by_role("button").all_inner_texts()[:30]
        links = [
            {"text": item.inner_text()[:160], "href": item.get_attribute("href")}
            for item in page.get_by_role("link").all()[:30]
        ]
        return {
            "url": page.url,
            "title": page.title(),
            "text": body,
            "buttons": buttons,
            "links": links,
        }

    return _BROWSER.call(inspect)


def click_demo_element(text: str, role: str = "button") -> dict:
    label = text.strip()
    role = role.casefold().strip()
    if not label or len(label) > 200:
        raise ValueError("Provide an exact visible element name up to 200 characters.")
    if role not in CLICK_ROLES:
        raise ValueError(f"Supported browser element roles: {', '.join(sorted(CLICK_ROLES))}.")

    def click(page):
        matches = page.get_by_role(role, name=label, exact=True)
        count = matches.count()
        if count != 1:
            raise ValueError(f"Expected one {role} named {label!r}; found {count}.")
        matches.click(timeout=10000)
        return {"clicked": label, "role": role, "url": page.url}

    return _BROWSER.call(click)


def fill_demo_field(label: str, text: str) -> dict:
    field_name = label.strip()
    if not field_name or len(field_name) > 200:
        raise ValueError("Provide a field label up to 200 characters.")
    if len(text) > 10000:
        raise ValueError("Browser field text must be 10,000 characters or fewer.")

    def fill(page):
        field = page.get_by_label(field_name, exact=True)
        count = field.count()
        if count != 1:
            field = page.get_by_placeholder(field_name, exact=True)
            count = field.count()
        if count != 1:
            raise ValueError(f"Expected one field labelled or placeholder {field_name!r}; found {count}.")
        field.fill(text, timeout=10000)
        return {"filled": field_name, "characters": len(text), "submitted": False}

    return _BROWSER.call(fill)
