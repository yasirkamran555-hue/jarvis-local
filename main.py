"""JARVIS-LOCAL: private, local-first Gradio desktop assistant."""

from __future__ import annotations

import json
import os
import re
import uuid
import base64
import binascii
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env", override=False)

import gradio as gr

from agent import Agent
from connections_ui import build_connections_tab
from memory import MemoryStore, list_skills

APP_CSS = """
html, body, .gradio-container {
    min-height: 100% !important;
    background: #050b14 !important;
}
.gradio-container {
    max-width: none !important;
    padding: 0 !important;
}
#jarvis-voice-input, #jarvis-voice-reply, #jarvis-audio-payload,
#jarvis-transcription-result, #jarvis-transcribe-button, #jarvis-voice-submit,
.built-with, button.settings, footer {
    display: none !important;
}
#jarvis-voice-ui {
    display: grid;
    min-height: 100vh;
    place-items: center;
    overflow: hidden;
}
#jarvis-orb {
    position: relative;
    display: grid;
    width: min(62vw, 340px);
    aspect-ratio: 1;
    place-items: center;
    border: 0;
    border-radius: 50%;
    background: transparent;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
}
#jarvis-orb:focus-visible {
    outline: 2px solid #8cecff;
    outline-offset: 10px;
}
.jarvis-ring, .jarvis-core {
    position: absolute;
    border-radius: 50%;
    pointer-events: none;
}
.jarvis-ring {
    inset: 8%;
    border: 1px solid rgba(72, 207, 255, .32);
    box-shadow: 0 0 32px rgba(0, 174, 255, .14), inset 0 0 32px rgba(0, 174, 255, .1);
    animation: jarvis-orbit 8s linear infinite, jarvis-pulse 3.2s ease-in-out infinite;
}
.jarvis-ring:nth-child(2) {
    inset: 19%;
    border-color: rgba(105, 232, 255, .62);
    border-style: dashed;
    animation-duration: 14s, 2.4s;
    animation-direction: reverse, normal;
}
.jarvis-core {
    inset: 29%;
    display: grid;
    place-items: center;
    border: 1px solid rgba(156, 241, 255, .8);
    background: radial-gradient(circle at 35% 30%, #287ba7, #0a2342 62%, #071222);
    box-shadow: 0 0 55px rgba(0, 195, 255, .42), inset 0 0 35px rgba(109, 228, 255, .25);
    color: #d9fbff;
    font: 500 clamp(48px, 12vw, 96px)/1 sans-serif;
    text-shadow: 0 0 22px #52dbff;
    animation: jarvis-core 3s ease-in-out infinite;
}
#jarvis-orb[data-state="listening"] .jarvis-ring {
    border-color: rgba(55, 255, 174, .75);
    box-shadow: 0 0 45px rgba(55, 255, 174, .3), inset 0 0 35px rgba(55, 255, 174, .16);
    animation-duration: 2s, .8s;
}
#jarvis-orb[data-state="thinking"] .jarvis-ring {
    border-color: rgba(255, 188, 74, .8);
    animation-duration: 1.2s, 1s;
}
#jarvis-orb[data-state="speaking"] .jarvis-ring {
    border-color: rgba(215, 120, 255, .85);
    box-shadow: 0 0 55px rgba(200, 90, 255, .36), inset 0 0 40px rgba(200, 90, 255, .18);
    animation-duration: .65s, .55s;
}
#jarvis-orb[data-state="error"] .jarvis-ring {
    border-color: rgba(255, 90, 90, .9);
    animation: none;
}
.jarvis-mic {
    position: absolute;
    right: 12%;
    bottom: 15%;
    z-index: 2;
    width: 36px;
    height: 36px;
    color: #a9efff;
    filter: drop-shadow(0 0 8px #25bfea);
}
@keyframes jarvis-orbit { to { transform: rotate(360deg); } }
@keyframes jarvis-pulse { 50% { opacity: .45; transform: scale(.96); } }
@keyframes jarvis-core { 50% { box-shadow: 0 0 75px rgba(0, 195, 255, .65), inset 0 0 45px rgba(109, 228, 255, .38); } }
@media (prefers-reduced-motion: reduce) {
    .jarvis-ring, .jarvis-core { animation-duration: 12s !important; }
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


def _voice_turn(message: str, state: dict | None):
    state = dict(state or {})
    text = (message or "").strip()
    if not text:
        reply = "I didn't catch that. Please say it again."
        return json.dumps({"id": uuid.uuid4().hex, "text": reply}), state

    pending = state.get("pending")
    normalized = text.casefold().strip(" .,!?:;")
    if pending:
        if normalized in {"jarvis approve", "approve", "do it", "go ahead", "confirm"}:
            try:
                result = Agent().execute(pending)
                reply = f"Done. {len(result['results'])} action steps completed."
            except Exception as exc:
                reply = f"I couldn't complete that task: {exc}"
            state["pending"] = None
            return json.dumps({"id": uuid.uuid4().hex, "text": reply}), state
        if normalized in {"cancel", "cancel it", "never mind", "nevermind"}:
            state["pending"] = None
            reply = "Cancelled. I did not run the actions."
            return json.dumps({"id": uuid.uuid4().hex, "text": reply}), state
        reply = "I have an action waiting for approval. Say approve to run it, or cancel."
        return json.dumps({"id": uuid.uuid4().hex, "text": reply}), state

    from brain import OllamaBrain

    brain = OllamaBrain()
    conversation = list(state.get("conversation", []))

    def save_turn(answer: str) -> None:
        conversation.extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": answer},
        ])
        state["conversation"] = conversation[-12:]

    search_match = re.search(
        r"\b(search (?:the )?(?:web|internet)|look up|find online|on the internet|"
        r"latest|current|breaking news|news today)\b",
        text,
        re.IGNORECASE,
    )
    if search_match:
        query = re.sub(
            r"^\s*(?:jarvis[, ]*)?(?:search (?:the )?(?:web|internet)(?: for)?|"
            r"look up|find online)\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip(" .?!")
        query = query or text
        try:
            from web_search import search_web
            results = search_web(query, limit=5)["results"]
        except Exception as exc:
            reply = f"I couldn't complete that web search: {exc}"
            return json.dumps({"id": uuid.uuid4().hex, "text": reply}), state
        sources = "\n".join(
            f"[{index}] {item['title']}: {item['snippet']} Source: {item['url']}"
            for index, item in enumerate(results, start=1)
        )
        try:
            reply = brain.chat([
                {"role": "system", "content": "Answer briefly using the provided current web results. Cite source numbers and do not invent facts."},
                *conversation[-8:],
                {"role": "user", "content": f"Question: {text}\n\nWeb results:\n{sources}"},
            ])
        except Exception as exc:
            reply = f"I found and saved these web sources, but the local AI could not summarize them: {exc}. {sources[:1200]}"
        save_turn(reply)
        return json.dumps({"id": uuid.uuid4().hex, "text": reply}), state

    action_request = re.search(
        r"^\s*(?:(?:hey|okay|ok)\s+)?(?:jarvis[, ]*)?(?:please\s+)?"
        r"(?:can you\s+|could you\s+|would you\s+)?"
        r"(open|click|press|switch|type|dictate|write|create|delete|remove|send|email|upload|publish|"
        r"install|run|launch|build|maximize|minimize|restore|search|change|update|download|move|copy|save|"
        r"navigate|browse|inspect|show|fill|preview|demonstrate)\b",
        text,
        re.IGNORECASE,
    )
    if action_request:
        try:
            plan = Agent().plan(text)
            if plan.get("steps"):
                state["pending"] = plan
                actions = ", ".join(step["tool"].replace("_", " ") for step in plan["steps"])
                reply = f"{plan['summary']} I propose: {actions}. Say approve to run these actions, or cancel."
            else:
                reply = plan.get("summary", "I couldn't identify a safe action.")
        except Exception as exc:
            reply = f"I couldn't prepare that action: {exc}"
        return json.dumps({"id": uuid.uuid4().hex, "text": reply}), state

    try:
        memories = MemoryStore().recall(text)
    except Exception:
        memories = []
    context = "\n".join(memories) or "(none)"
    try:
        reply = brain.chat([
            {"role": "system", "content": "You are Jarvis, a helpful local voice assistant. Answer briefly and naturally. Do not claim to perform actions."},
            {"role": "system", "content": f"Relevant saved local research:\n{context}"},
            *conversation[-10:],
            {"role": "user", "content": text},
        ])
    except Exception as exc:
        reply = f"I can't answer right now because the local AI is unavailable: {exc}"
    save_turn(reply)
    return json.dumps({"id": uuid.uuid4().hex, "text": reply}), state


def _transcribe_voice_clip(encoded_audio: str) -> str:
    request_id = uuid.uuid4().hex
    output_dir = APP_DIR / "workspace" / "audio"
    audio_path = None
    try:
        audio = base64.b64decode(encoded_audio, validate=True)
        if not audio or len(audio) > 25 * 1024 * 1024:
            raise ValueError("Audio recording is empty or exceeds 25 MB.")
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / f"voice-{request_id}.webm"
        audio_path.write_bytes(audio)
        from hands import transcribe_audio

        model_size = os.getenv("JARVIS_VOICE_MODEL", "tiny")
        transcript = transcribe_audio(str(audio_path), model_size=model_size)["text"].strip()
        return json.dumps({"id": request_id, "text": transcript})
    except (binascii.Error, OSError, RuntimeError, ValueError) as exc:
        return json.dumps({"id": request_id, "error": f"Local speech transcription failed: {exc}"})
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)


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
    voice_js = r"""() => {
        const button = document.querySelector("#jarvis-orb");
        const input = document.querySelector("#jarvis-voice-input textarea, #jarvis-voice-input input");
        const audioPayload = document.querySelector("#jarvis-audio-payload textarea, #jarvis-audio-payload input");
        const transcriptionResult = document.querySelector("#jarvis-transcription-result textarea, #jarvis-transcription-result input");
        const transcribeButton = document.querySelector("#jarvis-transcribe-button");
        const submit = document.querySelector("#jarvis-voice-submit");
        const reply = document.querySelector("#jarvis-voice-reply textarea, #jarvis-voice-reply input");
        if (!button || !input || !audioPayload || !transcriptionResult || !transcribeButton || !submit || !reply || button.dataset.initialized) return;
        button.dataset.initialized = "true";
        let active = false;
        let busy = false;
        let lastReply = "";
        let stream = null;
        let audioContext = null;
        let analyser = null;
        let recorder = null;
        let chunks = [];
        let lastSoundAt = 0;
        let recordStartedAt = 0;
        let loudFrames = 0;
        let awaitingTranscription = false;
        let lastTranscriptionResult = "";
        const setState = state => { button.dataset.state = state; };
        const setInput = value => {
            const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set;
            setter.call(input, value);
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
        };
        const monitor = () => {
            if (!active || (busy && !recorder) || !analyser) return;
            const samples = new Uint8Array(analyser.fftSize);
            analyser.getByteTimeDomainData(samples);
            let energy = 0;
            for (const sample of samples) {
                const value = (sample - 128) / 128;
                energy += value * value;
            }
            const level = Math.sqrt(energy / samples.length);
            const now = Date.now();
            if (level > 0.024) {
                loudFrames += 1;
                lastSoundAt = now;
                if (!recorder && loudFrames >= 3) beginRecording();
            } else {
                loudFrames = 0;
                if (recorder && level > 0.012) lastSoundAt = now;
            }
            if (recorder && now - lastSoundAt > 900) recorder.stop();
            else if (recorder && now - recordStartedAt > 18000) recorder.stop();
            requestAnimationFrame(monitor);
        };
        const transcribeRecording = async blob => {
            if (!active || !blob.size) {
                busy = false;
                if (active) requestAnimationFrame(monitor);
                return;
            }
            setState("thinking");
            button.title = "Transcribing your recording on this computer…";
            const reader = new FileReader();
            reader.onload = () => {
                if (!active) {
                    busy = false;
                    return;
                }
                const encoded = String(reader.result).split(",", 2)[1];
                if (!encoded) {
                    active = false;
                    busy = false;
                    setState("error");
                    button.title = "Could not read the microphone recording. Click the orb to retry.";
                    return;
                }
                lastTranscriptionResult = transcriptionResult.value;
                setInput(audioPayload, encoded);
                awaitingTranscription = true;
                window.setTimeout(() => transcribeButton.click(), 100);
            };
            reader.onerror = () => {
                active = false;
                busy = false;
                setState("error");
                button.title = "Could not read the microphone recording. Click the orb to retry.";
            };
            reader.readAsDataURL(blob);
        };
        const checkTranscription = () => {
            if (!awaitingTranscription || !transcriptionResult.value || transcriptionResult.value === lastTranscriptionResult) return;
            awaitingTranscription = false;
            try {
                const data = JSON.parse(transcriptionResult.value);
                if (data.error) throw new Error(data.error);
                if (!active) {
                    busy = false;
                    return;
                }
                if (!data.text) {
                    busy = false;
                    setState("listening");
                    button.title = "I didn't catch that. Speak clearly; click the orb to pause.";
                    requestAnimationFrame(monitor);
                    return;
                }
                setInput(input, data.text);
                window.setTimeout(() => submit.click(), 100);
            } catch (error) {
                active = false;
                busy = false;
                stream?.getTracks().forEach(track => track.stop());
                setState("error");
                button.title = `${error.message}. Click the orb to retry.`;
            }
        };
        const beginRecording = () => {
            if (!active || busy || recorder) return;
            const mimeType = ["audio/webm;codecs=opus", "audio/webm"].find(type => MediaRecorder.isTypeSupported(type));
            if (!mimeType) {
                active = false;
                setState("error");
                button.title = "This browser cannot record WebM audio. Open the app in current Chrome or Edge.";
                return;
            }
            try {
                chunks = [];
                recorder = new MediaRecorder(stream, { mimeType });
                recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
                recorder.onstop = () => {
                    const recording = new Blob(chunks, { type: mimeType });
                    recorder = null;
                    chunks = [];
                    busy = true;
                    transcribeRecording(recording);
                };
                recorder.start();
                busy = true;
                recordStartedAt = Date.now();
                lastSoundAt = recordStartedAt;
                button.title = "Recording your voice…";
            } catch (error) {
                recorder = null;
                active = false;
                setState("error");
                button.title = `Could not record microphone audio: ${error.message}. Click to retry.`;
            }
        };
        const startListening = async () => {
            if (!navigator.mediaDevices?.getUserMedia) {
                setState("error");
                button.title = "This browser cannot access a microphone. Open the app in current Chrome or Edge.";
                return;
            }
            button.title = "Waiting for microphone permission…";
            try {
                stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                audioContext = new (window.AudioContext || window.webkitAudioContext)();
                await audioContext.resume();
                analyser = audioContext.createAnalyser();
                analyser.fftSize = 2048;
                audioContext.createMediaStreamSource(stream).connect(analyser);
                active = true;
                busy = false;
                setState("listening");
                button.title = "Listening. Speak clearly; click the orb to pause.";
                requestAnimationFrame(monitor);
            } catch (error) {
                active = false;
                stream?.getTracks().forEach(track => track.stop());
                setState("error");
                button.title = error.name === "NotAllowedError"
                    ? "Microphone permission denied. Allow microphone access in browser site settings, then click the orb."
                    : `Could not open the microphone: ${error.message}. Check that a microphone is connected.`;
            }
        };
        button.addEventListener("click", () => {
            if (active) {
                active = false;
                busy = false;
                if (recorder && recorder.state !== "inactive") recorder.stop();
                stream?.getTracks().forEach(track => track.stop());
                audioContext?.close();
                stream = null;
                analyser = null;
                recorder = null;
                window.speechSynthesis?.cancel();
                setState("idle");
                button.title = "Click the orb to resume listening.";
                return;
            }
            startListening();
        });
        const speakReply = () => {
            if (!reply.value || reply.value === lastReply) return;
            lastReply = reply.value;
            try {
                const payload = JSON.parse(reply.value);
                if (!payload.text) return;
                busy = true;
                setState("speaking");
                const utterance = new SpeechSynthesisUtterance(payload.text);
                utterance.onend = utterance.onerror = () => {
                    busy = false;
                    if (active) {
                        setState("listening");
                        button.title = "Listening. Speak clearly; click the orb to pause.";
                        requestAnimationFrame(monitor);
                    }
                    else setState("idle");
                };
                window.speechSynthesis.cancel();
                window.speechSynthesis.speak(utterance);
            } catch (_) {}
        };
        window.setInterval(speakReply, 250);
        window.setInterval(checkTranscription, 200);
        setState("idle");
        button.title = "Click to allow microphone access and start local listening.";
    }"""
    logo = """
    <main id="jarvis-voice-ui">
      <button id="jarvis-orb" type="button" data-state="idle"
              aria-label="Jarvis microphone. Click to grant permission and start listening."
              title="Click the orb to allow microphone access and start listening.">
        <span class="jarvis-ring"></span><span class="jarvis-ring"></span>
        <span class="jarvis-core" aria-hidden="true">J</span>
        <svg class="jarvis-mic" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="9" y="2" width="6" height="13" rx="3" fill="currentColor"/>
          <path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3m-4 0h8"
                stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
        </svg>
      </button>
    </main>
    """
    with gr.Blocks(title="JARVIS", css=APP_CSS, fill_height=True) as demo:
        gr.HTML(logo)
        voice_input = gr.Textbox(elem_id="jarvis-voice-input")
        voice_reply = gr.Textbox(elem_id="jarvis-voice-reply")
        audio_payload = gr.Textbox(elem_id="jarvis-audio-payload")
        transcription_result = gr.Textbox(elem_id="jarvis-transcription-result")
        voice_state = gr.State({"pending": None})
        transcribe = gr.Button("Transcribe audio", elem_id="jarvis-transcribe-button")
        submit = gr.Button("Submit voice", elem_id="jarvis-voice-submit")
        transcribe.click(
            _transcribe_voice_clip,
            inputs=[audio_payload],
            outputs=[transcription_result],
            show_progress="hidden",
        )
        submit.click(
            _voice_turn,
            inputs=[voice_input, voice_state],
            outputs=[voice_reply, voice_state],
            show_progress="hidden",
        )
        demo.load(None, js=voice_js, show_progress="hidden")
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
