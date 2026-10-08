"""OAuth-authenticated client for Replit's documented MCP server."""

from __future__ import annotations

import asyncio
import os
import urllib.parse
import webbrowser
from typing import Any, Awaitable, Callable

MCP_URL = "https://mcp.replit.com/server/mcp"
INTEGRATION_KEY = "replit_mcp"
DEFAULT_CALLBACK_PORT = 8765
AUTH_TIMEOUT_SECONDS = 600


class _VaultTokenStorage:
    """Persist MCP OAuth registration and tokens inside the Fernet vault."""

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        from security import get_integration_state

        raw = get_integration_state(INTEGRATION_KEY).get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        from security import get_integration_state, set_integration_state

        state = get_integration_state(INTEGRATION_KEY)
        state["tokens"] = tokens.model_dump(mode="json")
        set_integration_state(INTEGRATION_KEY, state)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        from security import get_integration_state

        raw = get_integration_state(INTEGRATION_KEY).get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        from security import get_integration_state, set_integration_state

        state = get_integration_state(INTEGRATION_KEY)
        state["client_info"] = client_info.model_dump(mode="json")
        set_integration_state(INTEGRATION_KEY, state)


class _OAuthCallback:
    def __init__(self):
        self._result = None
        self._server = None

    async def start(self):
        try:
            port = int(os.getenv("JARVIS_REPLIT_OAUTH_PORT", str(DEFAULT_CALLBACK_PORT)))
        except ValueError as exc:
            raise ValueError("JARVIS_REPLIT_OAUTH_PORT must be a valid port number.") from exc
        if not 1024 <= port <= 65535:
            raise ValueError("JARVIS_REPLIT_OAUTH_PORT must be between 1024 and 65535.")
        self._result = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        return f"http://127.0.0.1:{port}/oauth/callback"

    async def close(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for_code(self):
        if self._result is None:
            raise RuntimeError("OAuth callback listener was not started.")
        return await asyncio.wait_for(self._result, timeout=AUTH_TIMEOUT_SECONDS)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        from mcp.client.auth import AuthorizationCodeResult

        response_code = "200 OK"
        body = "<h1>Replit connected</h1><p>You can close this window and return to JARVIS-LOCAL.</p>"
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            parts = request_line.decode("ascii", errors="replace").strip().split(" ")
            if len(parts) < 2 or parts[0] != "GET":
                raise ValueError("OAuth callback must use GET.")
            parsed = urllib.parse.urlsplit(parts[1])
            if parsed.path != "/oauth/callback":
                response_code, body = "404 Not Found", "<h1>Not found</h1>"
            else:
                query = urllib.parse.parse_qs(parsed.query)
                if "error" in query:
                    raise RuntimeError(f"Replit authorization was not completed: {query['error'][0]}")
                code = query.get("code", [None])[0]
                state = query.get("state", [None])[0]
                if not code or not state:
                    raise ValueError("OAuth callback did not include a code and state.")
                if self._result is not None and not self._result.done():
                    self._result.set_result(AuthorizationCodeResult(
                        code=code,
                        state=state,
                        iss=query.get("iss", [None])[0],
                    ))
        except Exception as exc:
            body = f"<h1>Replit sign-in failed</h1><p>{_html_escape(str(exc))}</p>"
            if self._result is not None and not self._result.done():
                self._result.set_exception(exc)
        encoded = body.encode("utf-8")
        headers = (
            f"HTTP/1.1 {response_code}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "Connection: close\r\n"
            f"Content-Length: {len(encoded)}\r\n\r\n"
        ).encode("ascii")
        writer.write(headers + encoded)
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


def _html_escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#x27;"))


async def _with_replit_client(operation: Callable[[Any], Awaitable[Any]]) -> Any:
    import httpx2
    from mcp import Client
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.auth import OAuthClientMetadata
    from pydantic import AnyUrl

    callback = _OAuthCallback()
    redirect_uri = await callback.start()
    metadata = OAuthClientMetadata(
        client_name="JARVIS-LOCAL",
        redirect_uris=[AnyUrl(redirect_uri)],
        scope=os.getenv("JARVIS_REPLIT_OAUTH_SCOPE", "user"),
        application_type="native",
    )
    oauth = OAuthClientProvider(
        server_url=MCP_URL,
        client_metadata=metadata,
        storage=_VaultTokenStorage(),
        redirect_handler=_open_authorization_page,
        callback_handler=callback.wait_for_code,
    )
    try:
        timeout = httpx2.Timeout(30.0, read=300.0)
        async with httpx2.AsyncClient(auth=oauth, timeout=timeout) as http_client:
            transport = streamable_http_client(MCP_URL, http_client=http_client)
            async with Client(transport) as client:
                return await operation(client)
    finally:
        await callback.close()


async def _open_authorization_page(url: str) -> None:
    if not webbrowser.open(url, new=2, autoraise=True):
        raise RuntimeError(
            "Could not open the Replit sign-in page. Set a default browser and try again."
        )


def _content_text(result: Any) -> str:
    pieces = []
    for item in getattr(result, "content", []) or []:
        if getattr(item, "type", None) == "text":
            pieces.append(str(getattr(item, "text", "")))
    return "\n".join(pieces).strip()


def _result_payload(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise RuntimeError(_content_text(result) or "Replit MCP tool returned an error.")
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        payload = structured
    else:
        payload = [
            {"type": getattr(item, "type", "unknown"), "text": getattr(item, "text", "")}
            for item in getattr(result, "content", []) or []
            if getattr(item, "type", None) in {"text", "image", "audio", "resource"}
        ]
    return {"result": payload}


async def _list_tools(client: Any) -> list[str]:
    result = await client.list_tools()
    return [item.name for item in result.tools]


async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def invoke(client: Any):
        names = await _list_tools(client)
        if name not in names:
            raise RuntimeError(f"Replit MCP does not expose the `{name}` tool for this account.")
        result = await client.call_tool(name, arguments=arguments)
        return _result_payload(result)

    try:
        return await _with_replit_client(invoke)
    except Exception as exc:
        message = str(exc)
        if "8765" in message or "address already in use" in message.casefold():
            message += " If another app uses the callback port, set JARVIS_REPLIT_OAUTH_PORT in .env."
        raise RuntimeError(f"Replit MCP request failed: {message}") from exc


def replit_connection_status() -> dict[str, Any]:
    from security import get_integration_state

    state = get_integration_state(INTEGRATION_KEY)
    tokens = state.get("tokens")
    info = state.get("client_info")
    connected = bool(tokens and isinstance(info, dict))
    return {
        "connected": connected,
        "message": "OAuth credentials are saved in the encrypted local vault."
        if connected else "Replit is not connected. Sign in to authorize Replit MCP.",
    }


def test_replit_connection() -> dict[str, Any]:
    async def inspect(client: Any):
        names = await _list_tools(client)
        return {"connected": True, "tool_count": len(names), "tools": names}

    return asyncio.run(_with_replit_client(inspect))


def forget_replit_connection() -> dict[str, str]:
    from security import delete_integration_state

    delete_integration_state(INTEGRATION_KEY)
    return {
        "message": (
            "Saved OAuth credentials were removed from this device. To revoke JARVIS-LOCAL's "
            "authorization at Replit, also remove it from your Replit account's connected apps."
        )
    }


APP_STACKS = {
    "react_website", "mobile_app", "design", "slides", "animation",
    "data_visualization", "3d_game", "document", "spreadsheet",
}


def create_repl(
    app_description: str,
    app_stack: str = "react_website",
    app_name: str | None = None,
    source_repl_id: str | None = None,
) -> dict[str, Any]:
    """Create a hosted Replit app via Replit MCP after OAuth authorization."""
    description = str(app_description).strip()
    if not description or len(description) > 6000:
        raise ValueError("App description must contain 1–6,000 characters.")
    if app_stack not in APP_STACKS:
        raise ValueError(f"app_stack must be one of: {', '.join(sorted(APP_STACKS))}.")
    arguments: dict[str, Any] = {"appDescription": description, "app_stack": app_stack}
    if app_name:
        name = str(app_name).strip()
        if not name or len(name) > 80:
            raise ValueError("App name must contain 1–80 characters.")
        arguments["userSpecifiedAppName"] = name
    if source_repl_id:
        arguments["sourceReplId"] = _required_text(source_repl_id, "source_repl_id", 200)
    return _call_tool_sync("create_app_from_prompt", arguments)


def list_repls(query: str | None = None, limit: int = 25) -> dict[str, Any]:
    if not 1 <= int(limit) <= 50:
        raise ValueError("limit must be between 1 and 50.")
    args: dict[str, Any] = {"limit": int(limit)}
    if query:
        args["query"] = str(query).strip()[:250]
    return _call_tool_sync("list_apps", args)


def search_repls(query: str | None = None, url: str | None = None, limit: int = 10) -> dict[str, Any]:
    if not 1 <= int(limit) <= 50:
        raise ValueError("limit must be between 1 and 50.")
    args: dict[str, Any] = {"limit": int(limit)}
    if query:
        args["query"] = str(query).strip()[:250]
    if url:
        args["url"] = str(url).strip()[:1000]
    return _call_tool_sync("search_apps", args)


def ask_repl(repl_id: str, question: str) -> dict[str, Any]:
    return _call_tool_sync("ask_question", {
        "replId": _required_text(repl_id, "repl_id", 200),
        "question": _required_text(question, "question", 2000),
    })


def update_repl(repl_id: str, change_description: str) -> dict[str, Any]:
    return _call_tool_sync("update_app_using_prompt", {
        "replId": _required_text(repl_id, "repl_id", 200),
        "changeDescription": _required_text(change_description, "change_description", 6000),
    })


def publish_repl(repl_id: str) -> dict[str, Any]:
    return _call_tool_sync("publish_app", {"replId": _required_text(repl_id, "repl_id", 200)})


def repl_publish_status(repl_id: str) -> dict[str, Any]:
    return _call_tool_sync("get_publish_status", {"replId": _required_text(repl_id, "repl_id", 200)})


def _required_text(value: str, name: str, limit: int) -> str:
    text = str(value).strip()
    if not text or len(text) > limit:
        raise ValueError(f"{name} must contain 1–{limit} characters.")
    return text


def _call_tool_sync(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call_tool(name, arguments))
    raise RuntimeError("Replit MCP tools must be run from a worker thread, not an active asyncio event loop.")
