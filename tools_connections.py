"""Real, direct connection tests and provider operations for the encrypted vault."""

from __future__ import annotations

import ftplib
import imaplib
import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.header import decode_header, make_header
from typing import Any

import requests

from security import get_connection

SUPPORTED_TYPES = {"zimbra", "gmail", "mysql", "ftp", "ftps", "whatsapp", "github"}
DEFAULT_HOSTS = {
    "gmail": "imap.gmail.com",
    "zimbra": "",
}


def _connection(value: str | dict, required: bool = True) -> dict[str, Any]:
    record = get_connection(value, require_enabled=required) if isinstance(value, str) else dict(value)
    kind = str(record.get("type", "")).lower()
    if kind not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported connection type: {kind}")
    if not all(record.get(key) for key in ("host", "username", "password")):
        raise ValueError("Connection is missing host, user, or password.")
    return record


def _host_port(host_field: str, default_port: int) -> tuple[str, int]:
    raw = host_field.strip()
    if raw.startswith("[") and "]:" in raw:
        host, port = raw[1:].split("]:", 1)
        return host, int(port)
    if raw.count(":") == 1:
        host, port = raw.rsplit(":", 1)
        if port.isdigit():
            return host, int(port)
    return raw, default_port


def _mail_test(record: dict) -> dict:
    kind = record["type"]
    host_fallback = "imap.gmail.com" if kind == "gmail" else ""
    host, port = _host_port(record.get("host") or host_fallback, 993)
    try:
        with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=10) as client:
            client.login(record["username"], record["password"])
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise RuntimeError(f"IMAP login succeeded but INBOX selection returned {status}.")
            client.logout()
        return {"ok": True, "message": f"IMAP login succeeded for {record['username']}."}
    except Exception as exc:
        raise RuntimeError(f"Mail connection failed: {exc}") from exc


def test_connection(connection: str | dict) -> dict:
    record = _connection(connection, required=False)
    kind = record["type"]
    try:
        if kind in {"zimbra", "gmail"}:
            return _mail_test(record)
        if kind == "mysql":
            try:
                import mysql.connector
            except ImportError as exc:
                raise RuntimeError("Install mysql-connector-python to test MySQL.") from exc
            host, port = _host_port(record["host"], 3306)
            db = mysql.connector.connect(
                host=host, port=port, user=record["username"], password=record["password"],
                connection_timeout=6, autocommit=False,
            )
            try:
                cursor = db.cursor()
                cursor.execute("SELECT 1")
                value = cursor.fetchone()
                cursor.close()
            finally:
                db.close()
            return {"ok": value == (1,), "message": "MySQL authenticated and SELECT 1 succeeded."}
        if kind in {"ftp", "ftps"}:
            host, port = _host_port(record["host"], 21)
            if kind == "ftps":
                client = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=10)
            else:
                client = ftplib.FTP(timeout=10)
            client.connect(host, port)
            client.login(record["username"], record["password"])
            if kind == "ftps":
                client.prot_p()
            welcome = client.getwelcome()
            client.quit()
            return {"ok": True, "message": f"{kind.upper()} login succeeded. {welcome[:150]}"}
        if kind == "whatsapp":
            phone_id = record["host"].strip().rstrip("/").split("/")[-1]
            response = requests.get(
                f"https://graph.facebook.com/v20.0/{phone_id}",
                params={"fields": "id,display_phone_number,verified_name"},
                headers={"Authorization": f"Bearer {record['password']}"},
                timeout=12,
            )
            response.raise_for_status()
            data = response.json()
            return {"ok": True, "message": f"WhatsApp phone number verified: {data.get('display_phone_number', data.get('id', phone_id))}."}
        if kind == "github":
            response = requests.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {record['password']}", "Accept": "application/vnd.github+json"},
                timeout=12,
            )
            response.raise_for_status()
            user = response.json()
            return {"ok": True, "message": f"GitHub token authenticated as {user.get('login', 'user')}."}
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        if isinstance(exc, requests.HTTPError):
            raise RuntimeError(f"{kind} connection returned HTTP {exc.response.status_code}.") from exc
        raise RuntimeError(f"{kind} connection test failed: {exc}") from exc
    raise ValueError(f"Unsupported connection type: {kind}")


def send_email(connection: str | dict, to: str, subject: str, body: str) -> dict:
    record = _connection(connection)
    if record["type"] not in {"gmail", "zimbra"}:
        raise ValueError("Email sending supports Gmail IMAP/SMTP and Zimbra connections.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to.strip()):
        raise ValueError("Recipient must be a valid email address.")
    if len(subject) > 200 or len(body) > 20_000:
        raise ValueError("Email subject/body exceeds the size limit.")
    host_field = record.get("host") or ("imap.gmail.com" if record["type"] == "gmail" else "")
    if not host_field:
        raise ValueError("Enter the mail server hostname in the connection form.")
    host, _ = _host_port(host_field, 993)
    smtp_host = "smtp.gmail.com" if record["type"] == "gmail" else host
    smtp_port = 465
    message = EmailMessage()
    message["From"] = record["username"]
    message["To"] = to.strip()
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ssl.create_default_context(), timeout=15) as smtp:
        smtp.login(record["username"], record["password"])
        smtp.send_message(message)
    return {"sent": True, "to": to.strip(), "subject": subject}


def zimbra_email(connection: str | dict, to: str, subject: str, body: str) -> dict:
    record = _connection(connection)
    if record["type"] != "zimbra":
        raise ValueError("zimbra_email requires a Zimbra connection.")
    return send_email(record, to, subject, body)


def gmail(
    connection: str | dict,
    action: str = "inbox",
    to: str | None = None,
    subject: str = "",
    body: str = "",
    limit: int = 10,
) -> dict:
    """List recent Gmail messages or send one through the Gmail SMTP service."""
    record = _connection(connection)
    if record["type"] != "gmail":
        raise ValueError("gmail requires a Gmail connection.")
    if action == "send":
        if not to:
            raise ValueError("Gmail send requires a recipient.")
        return send_email(record, to, subject, body)
    if action != "inbox":
        raise ValueError("Gmail action must be inbox or send.")
    if not 1 <= limit <= 50:
        raise ValueError("Inbox limit must be between 1 and 50.")
    host, port = _host_port(record["host"] or "imap.gmail.com", 993)
    try:
        with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=12) as client:
            client.login(record["username"], record["password"])
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise RuntimeError(f"Gmail inbox selection returned {status}.")
            status, data = client.search(None, "ALL")
            if status != "OK":
                raise RuntimeError("Could not search the Gmail inbox.")
            ids = (data[0].split() if data and data[0] else [])[-limit:]
            messages = []
            for message_id in reversed(ids):
                status, parts = client.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                from email.parser import BytesParser
                header = BytesParser().parsebytes(parts[0][1])
                decoded_subject = str(make_header(decode_header(header.get("Subject", ""))))
                messages.append({
                    "from": str(make_header(decode_header(header.get("From", "")))),
                    "subject": decoded_subject[:300],
                    "date": header.get("Date", ""),
                })
            client.logout()
        return {"messages": messages, "count": len(messages)}
    except Exception as exc:
        raise RuntimeError(f"Gmail inbox request failed: {exc}") from exc


def mysql_query(connection: str | dict, query: str) -> dict:
    record = _connection(connection)
    if record["type"] != "mysql":
        raise ValueError("mysql_query requires a MySQL connection.")
    normalized = re.sub(r"/\*.*?\*/|--[^\n]*|#[^\n]*", " ", query, flags=re.DOTALL).strip()
    normalized = normalized.rstrip(";").strip()
    if not re.match(r"^SELECT\b", normalized, flags=re.IGNORECASE):
        raise ValueError("Only read-only SELECT queries are allowed.")
    if ";" in normalized or re.search(
        r"\b(INTO\s+(?:OUTFILE|DUMPFILE)|FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE|"
        r"LOAD_FILE|SLEEP\s*\(|BENCHMARK\s*\(|GET_LOCK\s*\()", normalized, re.IGNORECASE
    ):
        raise ValueError("Query contains a blocked statement or side-effecting function.")
    try:
        import mysql.connector
    except ImportError as exc:
        raise RuntimeError("Install mysql-connector-python to query MySQL.") from exc
    host, port = _host_port(record["host"], 3306)
    db = mysql.connector.connect(
        host=host, port=port, user=record["username"], password=record["password"],
        connection_timeout=6, autocommit=False,
    )
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SET TRANSACTION READ ONLY")
        if not re.search(r"\bLIMIT\s+\d+\b", normalized, re.IGNORECASE):
            normalized += " LIMIT 200"
        cursor.execute(normalized)
        rows = cursor.fetchmany(200)
        columns = [column[0] for column in cursor.description or []]
        cursor.close()
        db.rollback()
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": len(rows) == 200}
    finally:
        db.close()


def ftp_upload(connection: str | dict, local_path: str, remote_path: str) -> dict:
    record = _connection(connection)
    if record["type"] not in {"ftp", "ftps"}:
        raise ValueError("FTP upload requires FTP or FTPS.")
    from hands import WORKSPACE_ROOT
    source = __import__("pathlib").Path(local_path).expanduser().resolve()
    root = WORKSPACE_ROOT.resolve()
    normalized_source = os.path.normcase(os.path.realpath(source))
    normalized_root = os.path.normcase(os.path.realpath(root))
    try:
        if os.path.commonpath([normalized_source, normalized_root]) != normalized_root:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Only files inside the JARVIS workspace may be uploaded.") from exc
    if not source.is_file() or source.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Upload a workspace file no larger than 25 MB.")
    destination = remote_path.replace("\\", "/")
    if not destination.startswith("/") or ".." in destination.split("/") or any(char in destination for char in ("\r", "\n", "\x00")):
        raise ValueError("Remote path must be absolute and may not contain parent traversal.")
    host, port = _host_port(record["host"], 21)
    client = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=20) if record["type"] == "ftps" else ftplib.FTP(timeout=20)
    try:
        client.connect(host, port)
        client.login(record["username"], record["password"])
        if record["type"] == "ftps":
            client.prot_p()
        with source.open("rb") as stream:
            client.storbinary(f"STOR {destination}", stream)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()
    return {"uploaded": True, "filename": source.name, "remote_path": destination, "bytes": source.stat().st_size}


def whatsapp_send(connection: str | dict, to: str, message: str) -> dict:
    record = _connection(connection)
    if record["type"] != "whatsapp":
        raise ValueError("whatsapp_send requires a WhatsApp Cloud API connection.")
    if not re.fullmatch(r"\+?\d{8,15}", to.strip()) or len(message) > 4096:
        raise ValueError("Use a phone number with 8–15 digits and a message up to 4,096 characters.")
    phone_id = record["host"].strip().rstrip("/").split("/")[-1]
    response = requests.post(
        f"https://graph.facebook.com/v20.0/{phone_id}/messages",
        headers={"Authorization": f"Bearer {record['password']}", "Content-Type": "application/json"},
        json={
            "messaging_product": "whatsapp",
            "to": to.strip().lstrip("+"),
            "type": "text",
            "text": {"body": message},
        },
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(f"WhatsApp API returned HTTP {response.status_code}: {response.text[:500]}")
    return {"sent": True, "to": to.strip(), "result": response.json()}


def whatsapp(connection: str | dict, to: str, message: str) -> dict:
    return whatsapp_send(connection, to, message)


def github_issues(connection: str | dict, repository: str | None = None, state: str = "open") -> dict:
    record = _connection(connection)
    if record["type"] != "github":
        raise ValueError("github_issues requires a GitHub connection.")
    repo = (repository or record["host"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Repository must be written as owner/name.")
    if state not in {"open", "closed", "all"}:
        raise ValueError("state must be open, closed, or all.")
    response = requests.get(
        f"https://api.github.com/repos/{repo}/issues",
        params={"state": state, "per_page": 50},
        headers={"Authorization": f"Bearer {record['password']}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    response.raise_for_status()
    issues = [
        {"number": item["number"], "title": item["title"], "state": item["state"], "url": item["html_url"]}
        for item in response.json() if "pull_request" not in item
    ]
    return {"repository": repo, "issues": issues}


def github(connection: str | dict, repository: str | None = None, state: str = "open") -> dict:
    return github_issues(connection, repository, state)
