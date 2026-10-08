"""On-demand web search with local persistence of public result summaries."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import requests

from memory import MemoryStore


class _SearchResultsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._active: str | None = None
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and ("result__a" in classes or "result__snippet" in classes):
            self._active = "title" if "result__a" in classes else "snippet"
            self._href = attributes.get("href") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._active:
            return
        text = " ".join(" ".join(self._text).split())
        if self._active == "title" and text and self._href:
            parsed = urlparse(self._href)
            target = parse_qs(parsed.query).get("uddg", [self._href])[0]
            self.results.append({"title": text, "url": unquote(target), "snippet": ""})
        elif self._active == "snippet" and text and self.results:
            self.results[-1]["snippet"] = text
        self._active = None
        self._href = ""
        self._text = []


def search_web(query: str, limit: int = 5) -> dict:
    """Search public web pages only when called, and save summaries in local memory."""
    query = str(query).strip()
    if not query or len(query) > 300:
        raise ValueError("Web search query must contain 1–300 characters.")
    if not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ValueError("Web search result limit must be between 1 and 10.")

    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "JARVIS-LOCAL/1.0"},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Web search failed: {exc}") from exc

    parser = _SearchResultsParser()
    parser.feed(response.text)
    results = parser.results[:limit]
    if not results:
        raise RuntimeError("Web search returned no readable results.")

    memory = MemoryStore()
    for item in results:
        item["title"] = item["title"][:300]
        item["snippet"] = item["snippet"][:1000]
        item["url"] = item["url"][:2000]
        memory.remember(
            f"Web result for {query}: {item['title']}. {item['snippet']} Source: {item['url']}",
            category="web-research",
        )
    return {"query": query, "results": results}
