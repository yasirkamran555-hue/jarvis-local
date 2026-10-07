"""Persistent ChromaDB memory and automatic reusable skill recipes."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
MEMORY_DIR = APP_DIR / "workspace" / "memory"
SKILLS_DIR = APP_DIR / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

_SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|token|secret|api[_ -]?key)\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
)


def redact_secrets(text: str) -> str:
    safe = text
    for pattern in _SECRET_PATTERNS:
        safe = pattern.sub("[redacted]", safe)
    return safe


def _embedding(text: str, size: int = 384) -> list[float]:
    """Deterministic local hashed-token embedding; no cloud/model download."""
    vector = [0.0] * size
    tokens = re.findall(r"[\w'-]+", text.casefold())
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % size
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class MemoryStore:
    def __init__(self):
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError("ChromaDB is not installed. Run `pip install -r requirements.txt`.") from exc
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(MEMORY_DIR))
        self.collection = client.get_or_create_collection(
            "jarvis_local_memory",
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return int(self.collection.count())

    def remember(self, text: str, category: str = "conversation") -> str:
        safe = redact_secrets(text).strip()
        if not safe:
            raise ValueError("Memory text is empty after secret filtering.")
        item_id = hashlib.sha256(f"{category}\n{safe}".encode("utf-8")).hexdigest()
        self.collection.upsert(
            ids=[item_id],
            documents=[safe],
            metadatas=[{"category": category[:40], "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}],
            embeddings=[_embedding(safe)],
        )
        return item_id

    def recall(self, query: str, limit: int = 4) -> list[str]:
        if not self.collection.count():
            return []
        result = self.collection.query(
            query_embeddings=[_embedding(redact_secrets(query))],
            n_results=min(limit, self.collection.count()),
            include=["documents"],
        )
        return list((result.get("documents") or [[]])[0])


def maybe_create_skill(request: str, plan: dict, successful: bool) -> Path | None:
    """Save a reusable tool sequence without storing prompts, arguments, or results."""
    steps = plan.get("steps", [])
    if not successful or len(steps) < 2:
        return None
    tool_names = [str(step.get("tool", "unknown")) for step in steps]
    sequence = " → ".join(tool_names)
    signature = hashlib.sha256(sequence.encode()).hexdigest()[:8]
    title = f"Workflow: {tool_names[0]} → {tool_names[-1]}"
    slug = re.sub(r"[^a-z0-9]+", "-", " ".join(tool_names).casefold()).strip("-")[:60] or "reusable-workflow"
    path = SKILLS_DIR / f"{slug}-{signature}.md"
    if path.exists():
        return path
    recipe = [
        f"# {title}",
        "",
        "Auto-created from a successful local workflow. Review it before reuse.",
        "",
        "## Workflow",
    ]
    for index, tool_name in enumerate(tool_names, start=1):
        recipe.append(f"{index}. Use `{tool_name}`.")
    recipe.extend([
        "",
        f"Tool sequence: `{sequence}`",
        "User prompts, tool arguments, and results are not stored in this skill.",
        "",
    ])
    path.write_text("\n".join(recipe), encoding="utf-8")
    return path


def list_skills() -> list[dict]:
    result = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        content = path.read_text(encoding="utf-8")
        title = next((line[2:].strip() for line in content.splitlines() if line.startswith("# ")), path.stem)
        summary = next((line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")), "")
        result.append({
            "title": title,
            "summary": summary,
            "filename": path.name,
            "updated": datetime.fromtimestamp(path.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M"),
        })
    return result
