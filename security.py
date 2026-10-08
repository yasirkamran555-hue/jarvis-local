"""Fernet-encrypted local connection vault."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"
VAULT_FILE = APP_DIR / "connections.json"


def _read_or_create_key() -> bytes:
    load_dotenv(ENV_FILE, override=False)
    existing = os.getenv("JARVIS_FERNET_KEY")
    if existing:
        try:
            Fernet(existing.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("JARVIS_FERNET_KEY is invalid. Replace it only after backing up the vault.") from exc
        return existing.encode("ascii")

    key = Fernet.generate_key()
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    current = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    lines = [line for line in current.splitlines() if not line.startswith("JARVIS_FERNET_KEY=")]
    lines.append(f"JARVIS_FERNET_KEY={key.decode('ascii')}")
    fd, temp_name = tempfile.mkstemp(prefix=".env-", dir=str(ENV_FILE.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines).strip() + "\n")
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            pass
        os.replace(temp_name, ENV_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    os.environ["JARVIS_FERNET_KEY"] = key.decode("ascii")
    return key


def _read_vault() -> dict[str, dict[str, Any]]:
    if not VAULT_FILE.exists():
        return {}
    encrypted = VAULT_FILE.read_bytes()
    try:
        decoded = Fernet(_read_or_create_key()).decrypt(encrypted)
        data = json.loads(decoded.decode("utf-8"))
    except InvalidToken as exc:
        raise RuntimeError(
            "The vault cannot be decrypted with the current key. Restore the matching .env key; "
            "do not overwrite the existing vault."
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError("Encrypted vault has an invalid data format.")
    return data


def _write_vault(data: dict[str, dict[str, Any]]) -> None:
    VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    encrypted = Fernet(_read_or_create_key()).encrypt(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    fd, temp_name = tempfile.mkstemp(prefix=".connections-", dir=str(VAULT_FILE.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            pass
        os.replace(temp_name, VAULT_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def save_connection(record: dict[str, Any]) -> dict[str, Any]:
    required = ("name", "type", "host", "username", "password")
    if any(not str(record.get(key, "")).strip() for key in required):
        raise ValueError("Name, type, host, user, and password are required.")
    connection_id = str(record.get("id") or os.urandom(12).hex())
    data = _read_vault()
    data[connection_id] = {
        "id": connection_id,
        "name": str(record["name"]).strip()[:100],
        "type": str(record["type"]).strip().lower(),
        "host": str(record["host"]).strip()[:255],
        "username": str(record["username"]).strip()[:255],
        "password": str(record["password"]),
        "enabled": bool(record.get("enabled", True)),
        "created_at": str(record.get("created_at") or __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")),
    }
    _write_vault(data)
    return {key: value for key, value in data[connection_id].items() if key != "password"}


def list_connections(include_disabled: bool = True) -> list[dict[str, Any]]:
    data = _read_vault()
    rows = []
    for key, record in data.items():
        if key == "_integrations" or not isinstance(record, dict) or "name" not in record:
            continue
        if include_disabled or record.get("enabled", True):
            rows.append({key: value for key, value in record.items() if key != "password"})
    return sorted(rows, key=lambda item: item["name"].casefold())


def get_integration_state(name: str) -> dict[str, Any]:
    if not name or not name.replace("_", "").isalnum():
        raise ValueError("Invalid integration name.")
    data = _read_vault()
    state = data.get("_integrations", {}).get(name, {})
    if not isinstance(state, dict):
        raise RuntimeError("Encrypted integration state has an invalid format.")
    return state


def set_integration_state(name: str, state: dict[str, Any]) -> None:
    if not name or not name.replace("_", "").isalnum():
        raise ValueError("Invalid integration name.")
    if not isinstance(state, dict):
        raise ValueError("Integration state must be an object.")
    data = _read_vault()
    integrations = data.setdefault("_integrations", {})
    if not isinstance(integrations, dict):
        raise RuntimeError("Encrypted integration state has an invalid format.")
    integrations[name] = state
    _write_vault(data)


def delete_integration_state(name: str) -> None:
    if not name or not name.replace("_", "").isalnum():
        raise ValueError("Invalid integration name.")
    data = _read_vault()
    integrations = data.get("_integrations", {})
    if isinstance(integrations, dict):
        integrations.pop(name, None)
        if not integrations:
            data.pop("_integrations", None)
        _write_vault(data)


def get_connection(connection_id: str, require_enabled: bool = True) -> dict[str, Any]:
    record = _read_vault().get(connection_id)
    if not record:
        raise KeyError("Connection not found.")
    if require_enabled and not record.get("enabled", True):
        raise PermissionError("This connection is disabled.")
    return record


def set_connection_enabled(connection_id: str, enabled: bool) -> dict[str, Any]:
    data = _read_vault()
    if connection_id not in data:
        raise KeyError("Connection not found.")
    data[connection_id]["enabled"] = bool(enabled)
    _write_vault(data)
    return {key: value for key, value in data[connection_id].items() if key != "password"}


def delete_connection(connection_id: str) -> None:
    data = _read_vault()
    if connection_id not in data:
        raise KeyError("Connection not found.")
    del data[connection_id]
    _write_vault(data)
