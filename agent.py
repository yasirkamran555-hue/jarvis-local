"""Planner → user-approved executor → screenshot checker orchestration."""

from __future__ import annotations

import json
from typing import Any, Callable

import eyes
from brain import OllamaBrain
from memory import MemoryStore, maybe_create_skill

MAX_STEPS = 6
TOOL_CATALOG = [
    {"name": "screen_screenshot", "args": {}, "description": "Capture the local screen."},
    {"name": "screen_ocr", "args": {"image_path": "optional path"}, "description": "OCR a local screenshot."},
    {"name": "screen_parse", "args": {"image_path": "optional path"}, "description": "Parse a screenshot with configured local OmniParser."},
    {"name": "desktop_click", "args": {"x": "int", "y": "int", "button": "optional"}, "description": "Click a screen coordinate."},
    {"name": "desktop_type", "args": {"text": "string"}, "description": "Type text into the focused application."},
    {"name": "desktop_hotkey", "args": {"keys": ["key names"]}, "description": "Press a supported hotkey."},
    {"name": "ui_controls", "args": {"max_depth": "optional int"}, "description": "Inspect Windows UI Automation controls."},
    {"name": "ui_click", "args": {"name": "exact control name"}, "description": "Click one uniquely named Windows control."},
    {"name": "file_list", "args": {"relative_path": "workspace-relative path"}, "description": "List files inside the JARVIS workspace."},
    {"name": "file_read", "args": {"relative_path": "workspace-relative path"}, "description": "Read a text file in the JARVIS workspace."},
    {"name": "file_write", "args": {"relative_path": "workspace-relative path", "content": "text"}, "description": "Write a text file inside the JARVIS workspace."},
    {"name": "file_delete", "args": {"relative_path": "workspace-relative path"}, "description": "Move a workspace file to the recycle bin."},
    {"name": "browser_visit", "args": {"url": "http or https URL"}, "description": "Fetch a web page using local headless Playwright."},
    {"name": "speak_text", "args": {"text": "short text"}, "description": "Create a local Piper speech file."},
    {"name": "audio_transcribe", "args": {"audio_path": "local audio file"}, "description": "Transcribe local audio using faster-whisper."},
    {"name": "create_repl", "args": {"app_description": "app specification", "app_stack": "react_website/mobile_app/design/slides/animation/data_visualization/3d_game/document/spreadsheet", "app_name": "optional title"}, "description": "Create a hosted Replit app through Replit MCP. Requires OAuth and sends the approved plan to Replit."},
    {"name": "create_local_project", "args": {"name": "project name", "template": "python or static"}, "description": "Create a local Python or static project workspace."},
    {"name": "list_repls", "args": {"query": "optional title query", "limit": "optional 1-50"}, "description": "List Replit apps authorized for this account."},
    {"name": "search_repls", "args": {"query": "optional title", "url": "optional app URL", "limit": "optional 1-50"}, "description": "Search authorized Replit apps."},
    {"name": "ask_repl", "args": {"repl_id": "app ID", "question": "question for the app agent"}, "description": "Ask Replit Agent about an app without changing it."},
    {"name": "update_repl", "args": {"repl_id": "app ID", "change_description": "requested app change"}, "description": "Start a Replit Agent update for an existing app."},
    {"name": "publish_repl", "args": {"repl_id": "app ID"}, "description": "Publish or republish a Replit app. Requires explicit plan approval."},
    {"name": "repl_publish_status", "args": {"repl_id": "app ID"}, "description": "Check the Replit app's publish status and URL."},
    {"name": "live_preview", "args": {"project": "project name"}, "description": "Start a loopback-only local project preview."},
    {"name": "stop_preview", "args": {"project": "project name"}, "description": "Stop a local preview."},
    {"name": "make_exe", "args": {"project": "project name", "entry_file": "python file"}, "description": "Build a project executable with PyInstaller."},
    {"name": "install_package", "args": {"package": "PyPI package name", "project": "optional project"}, "description": "Install one named PyPI package."},
    {"name": "docker_status", "args": {}, "description": "Check local Docker availability."},
    {"name": "docker_build", "args": {"project": "project with Dockerfile", "image_tag": "local image tag"}, "description": "Build a Docker image."},
    {"name": "docker_run", "args": {"image": "local image", "command": "optional argument list"}, "description": "Run an isolated, network-disabled local Docker container."},
    {"name": "connection_test", "args": {"connection_id": "saved vault connection ID"}, "description": "Test one enabled saved connection."},
    {"name": "zimbra_email", "args": {"connection_id": "ID", "to": "email", "subject": "text", "body": "text"}, "description": "Send email using a Zimbra connection."},
    {"name": "gmail", "args": {"connection_id": "ID", "action": "inbox or send", "to": "optional email", "subject": "optional text", "body": "optional text", "limit": "optional int"}, "description": "List recent Gmail inbox headers or send email."},
    {"name": "mysql_query", "args": {"connection_id": "ID", "query": "read-only SELECT"}, "description": "Run a capped, read-only SELECT query."},
    {"name": "ftp_upload", "args": {"connection_id": "ID", "local_path": "workspace file", "remote_path": "absolute remote path"}, "description": "Upload a workspace file using FTP or FTPS."},
    {"name": "whatsapp_send", "args": {"connection_id": "ID", "to": "phone number", "message": "text"}, "description": "Send a WhatsApp Cloud API message."},
    {"name": "whatsapp", "args": {"connection_id": "ID", "to": "phone number", "message": "text"}, "description": "Send a WhatsApp Cloud API message."},
    {"name": "github_issues", "args": {"connection_id": "ID", "repository": "owner/name", "state": "open/closed/all"}, "description": "List GitHub issues using a saved connection."},
    {"name": "github", "args": {"connection_id": "ID", "repository": "owner/name", "state": "open/closed/all"}, "description": "List GitHub issues using a saved connection."},
]


def _load_tools() -> dict[str, Callable]:
    import hands
    from tools_connections import (
        github, github_issues, gmail, mysql_query, send_email, test_connection, ftp_upload,
        whatsapp, whatsapp_send, zimbra_email,
    )
    from tools_replit import (
        create_local_project, docker_build, docker_run, docker_status, install_package, live_preview,
        make_exe, stop_preview,
    )
    from replit_mcp import (
        ask_repl, create_repl, list_repls, publish_repl, repl_publish_status, search_repls, update_repl,
    )

    def selected_test(connection_id):
        return test_connection(connection_id)

    return {
        "screen_screenshot": eyes.screenshot,
        "screen_ocr": eyes.ocr,
        "screen_parse": eyes.omniparser,
        "desktop_click": hands.click_screen,
        "desktop_type": hands.type_text,
        "desktop_hotkey": hands.hotkey,
        "ui_controls": hands.get_ui_controls,
        "ui_click": hands.click_ui_control,
        "file_list": hands.list_files,
        "file_read": hands.read_file,
        "file_write": hands.write_file,
        "file_delete": hands.delete_file,
        "browser_visit": hands.open_browser_url,
        "speak_text": hands.speak_text,
        "audio_transcribe": hands.transcribe_audio,
        "create_repl": create_repl,
        "create_local_project": create_local_project,
        "list_repls": list_repls,
        "search_repls": search_repls,
        "ask_repl": ask_repl,
        "update_repl": update_repl,
        "publish_repl": publish_repl,
        "repl_publish_status": repl_publish_status,
        "live_preview": live_preview,
        "stop_preview": stop_preview,
        "make_exe": make_exe,
        "install_package": install_package,
        "docker_status": docker_status,
        "docker_build": docker_build,
        "docker_run": docker_run,
        "connection_test": selected_test,
        "zimbra_email": zimbra_email,
        "gmail": gmail,
        "mysql_query": mysql_query,
        "ftp_upload": ftp_upload,
        "whatsapp_send": whatsapp_send,
        "whatsapp": whatsapp,
        "github_issues": github_issues,
        "github": github,
    }


class Agent:
    def __init__(self):
        self.brain = OllamaBrain()

    def plan(self, request: str) -> dict[str, Any]:
        memories = []
        catalog = list(TOOL_CATALOG)
        try:
            memories = MemoryStore().recall(request)
        except Exception:
            # Planning does not depend on optional persistent memory.
            pass
        try:
            from security import list_connections
            saved = [
                {"id": item["id"], "name": item["name"], "type": item["type"]}
                for item in list_connections(include_disabled=False)
            ]
            if saved:
                catalog.append({"saved_connections": saved})
        except Exception:
            # The vault may not have been initialized until its first save.
            pass
        result = self.brain.plan(request, catalog, memories)
        if len(result.get("steps", [])) > MAX_STEPS:
            raise ValueError(f"Planner exceeded the {MAX_STEPS}-step execution limit.")
        names = {tool["name"] for tool in TOOL_CATALOG}
        for step in result.get("steps", []):
            if not isinstance(step, dict) or step.get("tool") not in names:
                raise ValueError(f"Planner proposed an unsupported tool: {step.get('tool') if isinstance(step, dict) else step}")
            if not isinstance(step.get("args", {}), dict):
                raise ValueError("Each plan step must have an argument object.")
            step["args"] = step.get("args", {})
        return {
            "summary": str(result.get("summary", "Plan prepared."))[:1000],
            "request": request[:2000],
            "steps": result.get("steps", []),
        }

    def execute(self, plan: dict[str, Any]) -> dict[str, Any]:
        steps = plan.get("steps", [])
        if not isinstance(steps, list) or len(steps) > MAX_STEPS:
            raise ValueError("Invalid or oversized plan.")
        tools = _load_tools()
        results = []
        desktop_tools = {"desktop_click", "desktop_type", "desktop_hotkey", "ui_click"}
        try:
            for index, step in enumerate(steps, start=1):
                name = step["tool"]
                if name not in tools:
                    raise ValueError(f"Tool is not available: {name}")
                args = step.get("args", {})
                if not isinstance(args, dict):
                    raise ValueError(f"Step {index} arguments must be an object.")
                result = tools[name](**args)
                checked = None
                if name in desktop_tools:
                    checked = eyes.validate_screen_after_action(name)
                results.append({"step": index, "tool": name, "result": result, "screen_check": checked})
        except Exception as exc:
            raise RuntimeError(
                f"Step {len(results) + 1} failed after {len(results)} successful step(s): {exc}"
            ) from exc

        try:
            memory = MemoryStore()
            memory.remember(
                f"Completed task: {plan.get('summary', 'local workflow')}. "
                f"Tools used: {', '.join(item['tool'] for item in results)}.",
                category="successful-run",
            )
        except Exception:
            pass
        skill = maybe_create_skill(
            str(plan.get("request") or plan.get("summary", "")),
            plan,
            successful=bool(results),
        )
        output = []
        for item in results:
            output.append(f"**{item['step']}. {item['tool']}**\n\n```json\n{json.dumps(item['result'], ensure_ascii=False, indent=2, default=str)[:3500]}\n```")
            if item.get("screen_check"):
                output.append(f"Screen check: `{json.dumps(item['screen_check'], ensure_ascii=False, default=str)[:700]}`")
        if skill:
            output.append(f"Reusable local skill saved: `{skill.name}`")
        return {"results": results, "message": "\n\n".join(output) if output else "Plan completed with no actions."}
