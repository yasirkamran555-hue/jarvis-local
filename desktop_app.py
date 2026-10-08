"""Native Windows voice-first Jarvis application."""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time
import wave
from pathlib import Path
from tkinter import Canvas, Tk
from tkinter import messagebox

import numpy as np

from main import _voice_turn

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_024
VOICE_MODEL = os.getenv("JARVIS_VOICE_MODEL", "tiny")


def _frame_level(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))


def _write_recording(frames: list[np.ndarray], path: Path, sample_rate: int) -> None:
    audio = np.concatenate(frames)
    pcm = np.clip(audio * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


class JarvisDesktop:
    def __init__(self):
        self.root = Tk()
        self.root.title("JARVIS")
        self.root.configure(bg="#050b14")
        self.root.geometry("560x620")
        self.root.minsize(420, 480)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.status_text = "Starting microphone..."
        self.mode = "starting"
        self.level = 0.0
        self.sample_rate = SAMPLE_RATE
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.audio_frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=96)
        self.stop_event = threading.Event()
        self.busy_event = threading.Event()
        self.paused_event = threading.Event()
        self.transcriber_started = False
        self.audio_thread: threading.Thread | None = None
        self.state: dict = {"pending": None, "conversation": []}
        self.audio_stream = None
        self._build_window()
        self.root.after(40, self._animate)
        self.root.after(60, self._drain_events)
        self.root.after(150, self._start_microphone)

    def _build_window(self) -> None:
        self.canvas = Canvas(self.root, bg="#050b14", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._draw_orb())
        self.canvas.bind("<Button-1>", self._toggle_microphone)
        self.status = self.canvas.create_text(
            0, 0, text="Starting microphone...", fill="#a9dceb",
            font=("Segoe UI", 11), anchor="center",
        )
        self.last_reply = self.canvas.create_text(
            0, 0, text="", fill="#d5eaf3", font=("Segoe UI", 12),
            anchor="n", width=440, justify="center",
        )
        self.rings: list[int] = []
        self.core = 0
        self.microphone_icon = 0
        self._draw_orb()

    def _orb_color(self) -> str:
        return {
            "listening": "#38f5ae",
            "recording": "#45ffd0",
            "thinking": "#ffc45e",
            "speaking": "#d783ff",
            "error": "#ff6969",
            "paused": "#41657c",
            "starting": "#68dffb",
        }.get(self.mode, "#68dffb")

    def _draw_orb(self) -> None:
        width = max(self.canvas.winfo_width(), 320)
        height = max(self.canvas.winfo_height(), 360)
        self.canvas.delete("orb")
        center_x, center_y = width / 2, height * 0.43
        radius = min(width * 0.31, height * 0.28, 150)
        color = self._orb_color()
        self.rings = []
        for scale in (1.0, 0.74, 0.56):
            self.rings.append(self.canvas.create_oval(
                center_x - radius * scale,
                center_y - radius * scale,
                center_x + radius * scale,
                center_y + radius * scale,
                outline=color,
                width=2 if scale == 1 else 1,
                tags=("orb",),
            ))
        self.core = self.canvas.create_oval(
            center_x - radius * 0.42,
            center_y - radius * 0.42,
            center_x + radius * 0.42,
            center_y + radius * 0.42,
            fill="#0d3550",
            outline="#b1f2ff",
            width=2,
            tags=("orb",),
        )
        self.canvas.create_text(
            center_x, center_y, text="J", fill="#d9fbff",
            font=("Segoe UI Light", int(radius * 0.62)), tags=("orb",),
        )
        mic_y = center_y + radius * 0.65
        self.microphone_icon = self.canvas.create_text(
            center_x + radius * 0.63, mic_y, text="●",
            fill=color, font=("Segoe UI", 23), tags=("orb",),
        )
        self.canvas.tag_raise(self.status)
        self.canvas.tag_raise(self.last_reply)
        self.canvas.coords(self.status, center_x, center_y + radius + 42)
        self.canvas.coords(self.last_reply, center_x, center_y + radius + 80)
        self._set_status(self.status_text, self.mode)

    def _set_status(self, text: str, mode: str | None = None) -> None:
        self.status_text = text
        if mode:
            self.mode = mode
        if hasattr(self, "status"):
            self.canvas.itemconfigure(self.status, text=text, fill=self._orb_color())
        for ring in getattr(self, "rings", []):
            self.canvas.itemconfigure(ring, outline=self._orb_color())
        if getattr(self, "microphone_icon", 0):
            self.canvas.itemconfigure(self.microphone_icon, fill=self._orb_color())

    def _animate(self) -> None:
        if self.stop_event.is_set():
            return
        if self.mode in {"listening", "recording", "thinking", "speaking"} and self.rings:
            pulse = 1 + 0.035 * np.sin(time.monotonic() * (8 if self.mode == "recording" else 2.8))
            width = max(self.canvas.winfo_width(), 320)
            height = max(self.canvas.winfo_height(), 360)
            center_x, center_y = width / 2, height * 0.43
            radius = min(width * 0.31, height * 0.28, 150)
            outer = radius * pulse
            self.canvas.coords(self.rings[0], center_x - outer, center_y - outer, center_x + outer, center_y + outer)
            core_radius = radius * (0.42 + min(self.level, 0.3) * 0.2)
            self.canvas.coords(
                self.core,
                center_x - core_radius, center_y - core_radius,
                center_x + core_radius, center_y + core_radius,
            )
        self.root.after(40, self._animate)

    def _start_microphone(self) -> None:
        if self.stop_event.is_set():
            return
        if not self.transcriber_started:
            self.transcriber_started = True
            threading.Thread(target=self._transcription_worker, daemon=True, name="jarvis-transcriber").start()
        if self.audio_thread and self.audio_thread.is_alive():
            return
        self.audio_thread = threading.Thread(target=self._audio_worker, daemon=True, name="jarvis-audio")
        self.audio_thread.start()

    def _audio_worker(self) -> None:
        try:
            import sounddevice as sd

            input_device = sd.query_devices(kind="input")
            self.sample_rate = int(input_device["default_samplerate"])
            self.audio_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=FRAME_SAMPLES,
                callback=self._on_audio,
            )
            self.audio_stream.start()
            self.events.put(("status", ("Listening — speak naturally.", "listening")))
            while not self.stop_event.wait(0.2):
                if self.audio_stream and self.audio_stream.closed:
                    break
        except Exception as exc:
            if self.audio_stream:
                try:
                    self.audio_stream.close()
                except Exception:
                    pass
                self.audio_stream = None
            self.events.put(("status", (f"Microphone unavailable: {exc}", "error")))

    def _on_audio(self, audio, _frames, _timing, status) -> None:
        if status:
            self.events.put(("audio-warning", str(status)))
        if self.stop_event.is_set() or self.busy_event.is_set() or self.paused_event.is_set():
            return
        frame = audio[:, 0].copy()
        self.level = _frame_level(frame)
        try:
            self.audio_frames.put_nowait(frame)
        except queue.Full:
            self.events.put(("audio-warning", "Microphone buffer full; audio frames were dropped."))

    def _transcription_worker(self) -> None:
        try:
            from hands import transcribe_audio
        except Exception as exc:
            self.events.put(("status", (f"Local speech recognition unavailable: {exc}", "error")))
            return
        speech_frames: list[np.ndarray] = []
        pre_roll: list[np.ndarray] = []
        speech_started = False
        loud_count = 0
        quiet_seconds = 0.0
        while not self.stop_event.is_set():
            try:
                frame = self.audio_frames.get(timeout=0.15)
            except queue.Empty:
                continue
            level = _frame_level(frame)
            if not speech_started:
                pre_roll.append(frame)
                pre_roll = pre_roll[-5:]
                loud_count = loud_count + 1 if level > 0.008 else 0
                if loud_count >= 3:
                    speech_started = True
                    speech_frames = list(pre_roll)
                    quiet_seconds = 0.0
                    self.events.put(("status", ("I hear you…", "recording")))
                continue

            speech_frames.append(frame)
            frame_seconds = len(frame) / self.sample_rate
            quiet_seconds = quiet_seconds + frame_seconds if level < 0.006 else 0.0
            duration = sum(len(part) for part in speech_frames) / self.sample_rate
            if quiet_seconds >= 0.85 or duration >= 15:
                speech_started = False
                pre_roll.clear()
                loud_count = 0
                quiet_seconds = 0.0
                self._process_speech(speech_frames, transcribe_audio)
                speech_frames = []

    def _process_speech(self, frames: list[np.ndarray], transcribe_audio) -> None:
        self.busy_event.set()
        succeeded = False
        self.events.put(("status", ("Understanding your voice…", "thinking")))
        try:
            with tempfile.NamedTemporaryFile(prefix="jarvis-voice-", suffix=".wav", delete=False) as stream:
                audio_path = Path(stream.name)
            try:
                _write_recording(frames, audio_path, self.sample_rate)
                transcript = transcribe_audio(str(audio_path), model_size=VOICE_MODEL)["text"].strip()
            finally:
                audio_path.unlink(missing_ok=True)
            if not transcript:
                self.events.put(("status", ("I didn't catch that. Please say it again.", "listening")))
                return
            self.events.put(("transcript", transcript))
            self.events.put(("status", ("Thinking…", "thinking")))
            response, self.state = _voice_turn(transcript, self.state)
            reply = json.loads(response)["text"]
            self.events.put(("reply", reply))
            self.events.put(("status", ("Speaking…", "speaking")))
            self._speak(reply)
            succeeded = True
        except Exception as exc:
            self.events.put(("status", (f"Voice request failed: {exc}", "error")))
        finally:
            self.busy_event.clear()
            while True:
                try:
                    self.audio_frames.get_nowait()
                except queue.Empty:
                    break
            if succeeded and not self.stop_event.is_set() and not self.paused_event.is_set():
                self.events.put(("status", ("Listening — speak naturally.", "listening")))

    def _speak(self, text: str) -> None:
        if not text:
            return
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            raise RuntimeError(f"Windows speech output failed: {exc}") from exc

    def _drain_events(self) -> None:
        if self.stop_event.is_set():
            return
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                text, mode = value
                self._set_status(text, mode)
            elif kind == "transcript":
                self.canvas.itemconfigure(self.last_reply, text=f"You: {value}")
            elif kind == "reply":
                self.canvas.itemconfigure(self.last_reply, text=str(value)[:350])
            elif kind == "audio-warning":
                self._set_status(f"Audio warning: {value}", "error")
        self.root.after(60, self._drain_events)

    def _toggle_microphone(self, _event=None) -> None:
        if self.stop_event.is_set():
            return
        if self.mode == "paused":
            self.paused_event.clear()
            self.busy_event.clear()
            self._set_status("Listening — speak naturally.", "listening")
        elif self.mode == "error":
            self.paused_event.clear()
            self.busy_event.clear()
            self._set_status("Reconnecting to the microphone…", "starting")
            self._start_microphone()
        else:
            self.paused_event.set()
            self.busy_event.set()
            self._set_status("Microphone paused — click the orb to resume.", "paused")

    def close(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        if self.audio_stream:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    try:
        app = JarvisDesktop()
        app.run()
    except Exception as exc:
        try:
            messagebox.showerror("JARVIS could not start", str(exc))
        except Exception:
            print(f"JARVIS could not start: {exc}")
        raise


if __name__ == "__main__":
    main()
