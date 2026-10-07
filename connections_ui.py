"""Gradio interface for creating and maintaining the encrypted vault."""

from __future__ import annotations

import gradio as gr

from security import delete_connection, list_connections, save_connection, set_connection_enabled
from tools_connections import SUPPORTED_TYPES, test_connection

TYPES = ["zimbra", "gmail", "mysql", "ftps", "ftp", "whatsapp", "github"]


def _summary_rows():
    rows = list_connections()
    choices = [f"{item['name']} · {item['type']} · {item['id']}" for item in rows]
    display = [
        {
            "name": item["name"],
            "type": item["type"],
            "host": item["host"],
            "user": item["username"],
            "enabled": "Yes" if item.get("enabled", True) else "No",
            "id": item["id"],
        }
        for item in rows
    ]
    return display, gr.Dropdown(choices=choices, value=None)


def _selected_id(selection: str | None) -> str:
    if not selection:
        raise ValueError("Select a saved connection first.")
    return selection.rsplit(" · ", 1)[-1]


def build_connections_tab():
    gr.Markdown(
        "Credentials are encrypted with Fernet before they are written to `connections.json`. "
        "The encryption key is stored in the local `.env` file and is excluded from Git."
    )
    with gr.Row():
        with gr.Column():
            name = gr.Textbox(label="Name", placeholder="Work Gmail")
            kind = gr.Dropdown(label="Type", choices=TYPES, value="gmail")
            host = gr.Textbox(label="Host / API phone ID / repository", placeholder="imap.gmail.com or owner/repo")
            username = gr.Textbox(label="User")
            password = gr.Textbox(label="Pass / token", type="password")
            with gr.Row():
                test_form = gr.Button("Test")
                save = gr.Button("Save Encrypted", variant="primary")
            form_result = gr.Markdown()
        with gr.Column():
            connections = gr.Dropdown(label="Saved connections", choices=[], interactive=True)
            table = gr.JSON(label="Connections (secrets hidden)", value=[])
            with gr.Row():
                test_saved = gr.Button("Test selected")
                toggle = gr.Button("Enable / Disable")
                delete = gr.Button("Delete", variant="stop")
            confirm_delete = gr.Checkbox(
                label="Confirm permanent removal of the selected connection",
                value=False,
            )
            selected_result = gr.Markdown()
    refresh = gr.Button("Refresh list")

    def test_form_connection(conn_name, conn_type, conn_host, conn_user, conn_pass):
        try:
            result = test_connection({
                "name": conn_name, "type": conn_type, "host": conn_host,
                "username": conn_user, "password": conn_pass,
            })
            return f"**Connection test passed:** {result['message']}"
        except Exception as exc:
            return f"**Connection test failed:** {exc}"

    def save_form(conn_name, conn_type, conn_host, conn_user, conn_pass):
        try:
            saved = save_connection({
                "name": conn_name, "type": conn_type, "host": conn_host,
                "username": conn_user, "password": conn_pass,
            })
            rows = list_connections()
            choices = [f"{item['name']} · {item['type']} · {item['id']}" for item in rows]
            display = [
                {"name": item["name"], "type": item["type"], "host": item["host"],
                 "user": item["username"], "enabled": item["enabled"], "id": item["id"]}
                for item in rows
            ]
            selected = next(value for value in choices if value.endswith(saved["id"]))
            return f"Saved **{saved['name']}** in the encrypted vault.", display, gr.Dropdown(choices=choices, value=selected)
        except Exception as exc:
            return f"**Save failed:** {exc}", list_connections(), gr.Dropdown(choices=[], value=None)

    def test_saved_connection(selection):
        try:
            result = test_connection(_selected_id(selection))
            return f"**Connection test passed:** {result['message']}"
        except Exception as exc:
            return f"**Connection test failed:** {exc}"

    def toggle_connection(selection):
        try:
            connection_id = _selected_id(selection)
            rows = list_connections()
            current = next(row for row in rows if row["id"] == connection_id)
            updated = set_connection_enabled(connection_id, not current["enabled"])
            display, dropdown = _summary_rows()
            dropdown = gr.Dropdown(choices=dropdown.choices, value=selection)
            state = "enabled" if updated["enabled"] else "disabled"
            return f"**{updated['name']} is now {state}.**", display, dropdown
        except Exception as exc:
            return f"**Update failed:** {exc}", list_connections(), gr.Dropdown(choices=[], value=None)

    def delete_saved(selection, confirmed):
        try:
            if not confirmed:
                raise ValueError("Tick the confirmation box before deleting a saved connection.")
            connection_id = _selected_id(selection)
            delete_connection(connection_id)
            display, dropdown = _summary_rows()
            return "Connection deleted from the encrypted vault.", display, dropdown, False
        except Exception as exc:
            rows = list_connections()
            choices = [f"{row['name']} · {row['type']} · {row['id']}" for row in rows]
            return f"**Delete failed:** {exc}", rows, gr.Dropdown(choices=choices, value=selection), confirmed

    test_form.click(test_form_connection, [name, kind, host, username, password], form_result)
    save.click(save_form, [name, kind, host, username, password], [form_result, table, connections])
    test_saved.click(test_saved_connection, [connections], selected_result)
    toggle.click(toggle_connection, [connections], [selected_result, table, connections])
    delete.click(delete_saved, [connections, confirm_delete], [selected_result, table, connections, confirm_delete])
    refresh.click(
        lambda: (
            [{"name": row["name"], "type": row["type"], "host": row["host"], "user": row["username"],
              "enabled": row["enabled"], "id": row["id"]} for row in list_connections()],
            gr.Dropdown(choices=[f"{row['name']} · {row['type']} · {row['id']}" for row in list_connections()], value=None),
        ),
        outputs=[table, connections],
    )
