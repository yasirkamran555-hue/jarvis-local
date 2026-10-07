"""JARVIS-LOCAL: private, local-first Gradio desktop assistant."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env", override=False)

import gradio as gr

from agent import Agent
from connections_ui import build_connections_tab
from memory import MemoryStore, list_skills

APP_CSS = """
.jarvis-floating-widget {
    position: fixed !important;
    right: 18px;
    bottom: 16px;
    z-index: 1000;
    width: min(360px, calc(100vw - 36px));
    border: 1px solid var(--border-color-primary);
    border-radius: 14px;
    box-shadow: 0 12px 36px rgba(0, 0, 0, .22);
    background: var(--background-fill-primary);
}
"""


def _memory_status() -> str:
    try:
        store = MemoryStore()
        return f"Local memory ready · {store.count()} saved items"
    except Exception as exc:
        return f"Local memory unavailable · {exc}"


def _ollama_status() -> str:
    from brain import OllamaBrain

    brain = OllamaBrain()
    try:
        models = brain.models()
        names = {item.get("name", "") for item in models}
        ready = [model for model in brain.PREFERRED_MODELS if model in names]
        return "Ollama connected" + (f" · {', '.join(ready)}" if ready else " · models not pulled yet")
    except Exception as exc:
        return f"Ollama offline · {exc}"


def _render_plan(plan: dict) -> str:
    summary = plan.get("summary", "No plan summary provided.")
    steps = plan.get("steps", [])
    if not steps:
        return f"**Plan**\n\n{summary}\n\nNo actions are queued."
    lines = [f"**Proposed plan**\n\n{summary}", ""]
    for index, step in enumerate(steps, start=1):
        args = json.dumps(step.get("args", {}), ensure_ascii=False)
        lines.append(f"{index}. **{step['tool']}** — {step.get('reason', 'No explanation provided.')}")
        lines.append(f"   `{args}`")
    lines.extend(["", "Nothing runs until you select **Do it**. Review each action first."])
    return "\n".join(lines)


def _chat_submit(message: str, history: list | None, state: dict | None):
    history = list(history or [])
    state = state or {}
    if not message or not message.strip():
        return history, "", "Enter a request to create a plan.", state
    agent = Agent()
    try:
        plan = agent.plan(message.strip())
        state = {"pending": plan, "request": message.strip()}
        history.extend([
            {"role": "user", "content": message.strip()},
            {"role": "assistant", "content": _render_plan(plan)},
        ])
        return history, "", _render_plan(plan), state
    except Exception as exc:
        history.extend([
            {"role": "user", "content": message.strip()},
            {"role": "assistant", "content": f"Planning failed: {exc}"},
        ])
        return history, "", f"Planning failed: {exc}", {"pending": None}


def _do_it(history: list | None, state: dict | None):
    history = list(history or [])
    state = state or {}
    plan = state.get("pending")
    if not plan:
        return history, "There is no pending plan. Send a request first.", {"pending": None}
    try:
        result = Agent().execute(plan)
        output = result["message"]
        history.append({"role": "assistant", "content": f"**Run result**\n\n{output}"})
        state = {"pending": None, "last_result": output}
        return history, f"**Run result**\n\n{output}", state
    except Exception as exc:
        output = f"Execution stopped: {exc}"
        history.append({"role": "assistant", "content": output})
        return history, output, {"pending": None, "last_error": str(exc)}


def _refresh_skills():
    skills = list_skills()
    if not skills:
        return "No reusable skills saved yet. Multi-step plans that complete successfully are turned into reusable local recipes."
    rendered = []
    for skill in skills:
        rendered.append(
            f"### {skill['title']}\n\n"
            f"{skill['summary']}\n\n"
            f"`{skill['filename']}` · updated {skill['updated']}"
        )
    return "\n\n---\n\n".join(rendered)


def create_app() -> gr.Blocks:
    with gr.Blocks(title="JARVIS-LOCAL", theme=gr.themes.Soft(), css=APP_CSS) as demo:
        gr.Markdown(
            "# JARVIS-LOCAL\n"
            "A local-first assistant for Ollama, desktop workflows, and encrypted connections. "
            "Requests are planned before any tool runs."
        )
        with gr.Row():
            ollama_status = gr.Markdown(_ollama_status())
            memory_status = gr.Markdown(_memory_status())

        with gr.Tabs():
            with gr.Tab("Chat"):
                chat = gr.Chatbot(type="messages", height=470, label="Conversation")
                voice_input = gr.Audio(
                    sources=["microphone", "upload"],
                    type="filepath",
                    label="Voice request (local transcription)",
                )
                composer = gr.Textbox(
                    label="Request",
                    placeholder="Describe the task. JARVIS will show a plan before acting.",
                    lines=2,
                )
                with gr.Row():
                    send = gr.Button("Plan", variant="secondary")
                    do_it = gr.Button("Do it", variant="primary")
                    clear = gr.Button("Clear")
                plan_view = gr.Markdown("Send a request to see the proposed plan.")
                pending_state = gr.State({"pending": None})

                def transcribe_to_composer(audio_path):
                    if not audio_path:
                        return ""
                    from hands import transcribe_audio
                    try:
                        return transcribe_audio(audio_path)["text"]
                    except Exception as exc:
                        return f"[Transcription failed: {exc}]"

                send.click(_chat_submit, [composer, chat, pending_state], [chat, composer, plan_view, pending_state])
                composer.submit(_chat_submit, [composer, chat, pending_state], [chat, composer, plan_view, pending_state])
                voice_input.change(transcribe_to_composer, [voice_input], [composer])
                do_it.click(_do_it, [chat, pending_state], [chat, plan_view, pending_state])
                clear.click(
                    lambda: ([], "Send a request to see the proposed plan.", {"pending": None}),
                    outputs=[chat, plan_view, pending_state],
                )

            with gr.Tab("Connections Hub"):
                build_connections_tab()

            with gr.Tab("Skills"):
                gr.Markdown("Reusable local workflow recipes created from successful multi-step runs.")
                skills_view = gr.Markdown(_refresh_skills())
                refresh_skills = gr.Button("Refresh skills")
                refresh_skills.click(_refresh_skills, outputs=skills_view)

        with gr.Accordion("JARVIS quick action", open=False, elem_classes=["jarvis-floating-widget"]):
            quick_request = gr.Textbox(
                label="Quick request",
                placeholder="Describe an action to plan",
                lines=2,
            )
            with gr.Row():
                quick_plan = gr.Button("Plan", variant="secondary")
                quick_do = gr.Button("Do it", variant="primary")
            quick_plan.click(
                _chat_submit,
                [quick_request, chat, pending_state],
                [chat, quick_request, plan_view, pending_state],
            )
            quick_request.submit(
                _chat_submit,
                [quick_request, chat, pending_state],
                [chat, quick_request, plan_view, pending_state],
            )
            quick_do.click(_do_it, [chat, pending_state], [chat, plan_view, pending_state])

        gr.Markdown(
            "Local-only by default. Review plans before running them; credentials are encrypted on this device."
        )
    return demo


if __name__ == "__main__":
    # Loopback binding prevents an unauthenticated vault/desktop-control UI from
    # being exposed to a public network. Set JARVIS_HOST only for a trusted LAN
    # deployment and configure Gradio authentication in the environment first.
    host = os.getenv("JARVIS_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("JARVIS_UI_PASSWORD"):
        raise SystemExit("Refusing to expose JARVIS on a network without JARVIS_UI_PASSWORD.")
    auth = None
    if os.getenv("JARVIS_UI_PASSWORD"):
        auth = (os.getenv("JARVIS_UI_USERNAME", "jarvis"), os.environ["JARVIS_UI_PASSWORD"])
    create_app().launch(
        server_name=host,
        server_port=int(os.getenv("PORT", "7860")),
        share=False,
        auth=auth,
        show_error=False,
        inbrowser=os.getenv("JARVIS_OPEN_BROWSER", "1") == "1",
    )
