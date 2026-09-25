"""KAVACH v1: wake word -> STT -> LLM tool-calling -> TTS, with a safe action layer. Windows."""
import asyncio
import json
import msvcrt
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
import winsound
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import chromadb
import edge_tts
import numpy as np
import openwakeword
import pyaudio
import pyautogui
import pygame
import pyttsx3
import speech_recognition as sr
from openwakeword.model import Model
from groq import Groq
from sarvamai import SarvamAI
from safety import Guard

MODEL = "openai/gpt-oss-120b"
SYSTEM = (
    "You are Jarvis, a personal voice assistant on a Windows PC. Replies are "
    "spoken aloud: keep them short (1-3 sentences), plain text, no markdown. "
    "Use tools for actions; confirm briefly after."
)

APPS = {
    "notepad": "notepad",
    "calculator": "calc",
    "chrome": "chrome",
    "vscode": "code",
    "explorer": "explorer",
    "terminal": "wt",
    "paint": "mspaint",
}

TOOLS = [
    {
        "name": "open_app",
        "description": "Open an app on the PC.",
        "input_schema": {
            "type": "object",
            "properties": {"app": {"type": "string", "enum": list(APPS)}},
            "required": ["app"],
        },
    },
    {
        "name": "web_search",
        "description": "Open a Google search in the browser.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "set_reminder",
        "description": "Speak a reminder after a delay.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "minutes": {"type": "number"},
            },
            "required": ["message", "minutes"],
        },
    },
    {
        "name": "remember",
        "description": "Save a fact or preference about the user for future sessions.",
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": ["fact"],
        },
    },
    {
        "name": "recall",
        "description": "Search saved memories about the user.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "set_volume",
        "description": "Change system volume.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["up", "down", "mute"]},
                "steps": {"type": "number", "description": "Each step is about 2%. Default 5."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "screenshot",
        "description": "Take a screenshot and save it to Pictures.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_file",
        "description": "Find files by name in Desktop, Documents and Downloads. Set open=true ONLY if the user explicitly asks to open it (open, kholo); for find, search or dhundo leave open false.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "open": {"type": "boolean"}},
            "required": ["name"],
        },
    },
    {
        "name": "play_music",
        "description": "Search YouTube for a song or artist in the browser.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "undo_last",
        "description": "Undo the last reversible action (volume, screenshot, reminder, remembered fact). Use when the user says undo.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_time",
        "description": "Get the current date and time.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

client = Groq()  # reads GROQ_API_KEY (free)
try:
    sarvam_client = SarvamAI()  # reads SARVAM_API_KEY
except Exception as e:
    print(f"Sarvam unavailable, will use Google STT only: {e}")
    sarvam_client = None
GROQ_TOOLS = [
    {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
    for t in TOOLS
]
recognizer = sr.Recognizer()
recognizer.pause_threshold = 1.2  # tolerate short mid-sentence pauses
recognizer.non_speaking_duration = 0.6
memory = chromadb.PersistentClient(path="jarvis_memory").get_or_create_collection("facts")
speak_lock = threading.Lock()
history = []
TIMERS = []
LAST_MEMORY_ID = []


VOICE = "en-GB-RyanNeural"
HINDI_VOICE = "hi-IN-MadhurNeural"


def speak(text):
    print(f"Jarvis: {text}")
    voice = HINDI_VOICE if any("\u0900" <= ch <= "\u097f" for ch in text) else VOICE
    with speak_lock:
        try:
            path = os.path.join(tempfile.gettempdir(), f"jarvis_{uuid.uuid4().hex}.mp3")
            asyncio.run(edge_tts.Communicate(text, voice).save(path))
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.wait(50)
            pygame.mixer.music.unload()
            os.remove(path)
        except Exception as e:
            print(f"TTS fallback: {e}")
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()


SKIP_DIRS = {"venv", ".venv", "env", "node_modules", "site-packages",
             "__pycache__", ".git", "dist", "build", ".mypy_cache", ".pytest_cache",
             "Windows", "Program Files", "Program Files (x86)", "$Recycle.Bin",
             "System Volume Information", "AppData", "ProgramData", "Windows.old"}


def list_drives():
    if os.name != "nt":
        return [Path("/")]
    import string
    return [Path(f"{letter}:\\") for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]


def find_file(name, open_it=False, limit=12, max_seconds=8):
    """Search every fixed drive for a matching filename, skipping system/dependency
    folders. Time-capped so a large drive can't hang the assistant; results are
    sorted most-recently-modified first as a proxy for relevance."""
    name = name.lower()
    start = time.time()
    found = []
    for root_dir in list_drives():
        for root, dirs, files in os.walk(root_dir, topdown=True):
            if time.time() - start > max_seconds:
                break
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
            found += [os.path.join(root, f) for f in files if name in f.lower()]
        if time.time() - start > max_seconds:
            break
    try:
        found.sort(key=os.path.getmtime, reverse=True)
    except OSError:
        pass
    total = len(found)
    found = found[:limit]
    if not found:
        return "No matching files."
    if open_it:
        os.startfile(found[0])
        return found[0]  # opening is a committed action - report only the file that was opened
    result = "\n".join(found)
    if total > limit:
        result += f"\n({total - limit} additional matches exist)"
    return result


def search_memory(query, n=3):
    count = memory.count()
    if not count:
        return []
    res = memory.query(query_texts=[query], n_results=min(n, count))
    return res["documents"][0]


def run_tool(name, args):
    try:
        if name == "remember":
            mid = str(uuid.uuid4())
            memory.add(documents=[args["fact"]], ids=[mid])
            LAST_MEMORY_ID.append(mid)
            return "Saved."
        if name == "recall":
            return "\n".join(search_memory(args["query"], 5)) or "Nothing saved."
        if name == "open_app":
            subprocess.Popen(APPS[args["app"]], shell=True)
            return f"Opened {args['app']}."
        if name == "web_search":
            webbrowser.open("https://www.google.com/search?q=" + quote_plus(args["query"]))
            return "Search opened."
        if name == "set_reminder":
            msg, mins = args["message"], float(args["minutes"])
            t = threading.Timer(mins * 60, lambda: speak(f"Reminder: {msg}"))
            t.daemon = True
            t.start()
            TIMERS.append(t)
            return f"Reminder set for {mins} minutes."
        if name == "set_volume":
            action = args["action"]
            key = {"up": "volumeup", "down": "volumedown", "mute": "volumemute"}[action]
            pyautogui.press(key, presses=1 if action == "mute" else int(args.get("steps", 5)))
            return "Done."
        if name == "screenshot":
            folder = Path.home() / "Pictures"
            folder.mkdir(exist_ok=True)
            path = folder / f"jarvis_{datetime.now():%Y%m%d_%H%M%S}.png"
            pyautogui.screenshot(str(path))
            return f"Saved to {path}."
        if name == "find_file":
            return find_file(args["name"], args.get("open", False))
        if name == "play_music":
            webbrowser.open("https://www.youtube.com/results?search_query=" + quote_plus(args["query"]))
            return "Opened YouTube results."
        if name == "get_time":
            return datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
    except Exception as e:
        return f"Error: {e}"
    return "Unknown tool."


TRASH = Path.home() / ".jarvis_trash"


def confirm(desc, timeout=10):
    speak_text, print_text = desc if isinstance(desc, tuple) else (desc, desc)
    while msvcrt.kbhit():
        msvcrt.getwch()  # flush stray keys
    speak(f"{speak_text} Press Y in the terminal to confirm.")
    print(f"[CONFIRM] {print_text} (press y within {timeout}s)")
    end = time.time() + timeout
    while time.time() < end:
        if msvcrt.kbhit():
            return msvcrt.getwch().lower() == "y"
        time.sleep(0.05)
    return False  # no answer = denied


def _friendly_name(path):
    return Path(path).stem.replace("_", " ").replace("-", " ")


def _describe_find(args):
    hits = find_file(args["name"], False)
    if hits.startswith("No matching"):
        return None
    path = hits.splitlines()[0]
    speak_text = f"Open {_friendly_name(path)} from your {Path(path).parent.name} folder?"
    print_text = f"Open {path}?"
    return (speak_text, print_text), f"find_file:{path}"  # key by the resolved file, not the search phrase


def _undo_volume(args, result):
    inverse = {"up": "volumedown", "down": "volumeup", "mute": "volumemute"}[args["action"]]
    n = 1 if args["action"] == "mute" else int(args.get("steps", 5))
    return lambda: pyautogui.press(inverse, presses=n)


def _undo_screenshot(args, result):
    path = Path(result.removeprefix("Saved to ").rstrip("."))

    def undo():
        TRASH.mkdir(exist_ok=True)
        shutil.move(str(path), str(TRASH / path.name))

    return undo


def _undo_reminder(args, result):
    return TIMERS[-1].cancel


def _undo_remember(args, result):
    mid = LAST_MEMORY_ID[-1]
    return lambda: memory.delete(ids=[mid])


guard = Guard(
    run_tool,
    confirm,
    undo_makers={
        "set_volume": _undo_volume,
        "screenshot": _undo_screenshot,
        "set_reminder": _undo_reminder,
        "remember": _undo_remember,
    },
    describers={"find_file": _describe_find},
)


def trim_history(limit=20):
    """Cut old turns, always starting at a plain user message so tool pairs stay intact."""
    global history
    if len(history) <= limit:
        return
    history = history[-limit:]
    while history and not (
        history[0]["role"] == "user" and isinstance(history[0]["content"], str)
    ):
        history.pop(0)


def ask(text):
    history.append({"role": "user", "content": text})
    guard.transcript = text
    trim_history()
    known = search_memory(text)
    system = SYSTEM + ("\nKnown about the user:\n- " + "\n- ".join(known) if known else "")
    while True:
        resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=400,
            tools=GROQ_TOOLS,
            messages=[{"role": "system", "content": system}] + history,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            history.append({"role": "assistant", "content": msg.content or ""})
            return msg.content or ""
        history.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments},
                    }
                    for c in msg.tool_calls
                ],
            }
        )
        for c in msg.tool_calls:
            result = guard.call(c.function.name, json.loads(c.function.arguments or "{}"))
            history.append({"role": "tool", "tool_call_id": c.id, "content": result})


def transcribe_sarvam(audio):
    path = os.path.join(tempfile.gettempdir(), f"kavach_stt_{uuid.uuid4().hex}.wav")
    with open(path, "wb") as f:
        f.write(audio.get_wav_data(convert_rate=16000, convert_width=2))
    try:
        with open(path, "rb") as f:
            r = sarvam_client.speech_to_text.transcribe(file=f, model="saaras:v3", mode="codemix")
        return (r.transcript or "").lower()
    finally:
        os.remove(path)


def listen(mic, timeout=8, limit=15):
    try:
        audio = recognizer.listen(mic, timeout=timeout, phrase_time_limit=limit)
    except sr.WaitTimeoutError:
        return ""
    if sarvam_client:
        try:
            return transcribe_sarvam(audio)
        except Exception as e:
            print(f"Sarvam STT error, falling back to Google: {e}")
    try:
        return recognizer.recognize_google(audio).lower()
    except (sr.UnknownValueError, sr.RequestError) as e:
        print(f"STT error: {e}")
        return ""


def wait_for_wake_word(model):
    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1280)
    try:
        while True:
            chunk = np.frombuffer(stream.read(1280, exception_on_overflow=False), dtype=np.int16)
            if max(model.predict(chunk).values()) > 0.5:
                model.reset()
                return
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def main():
    pygame.mixer.init()
    try:
        openwakeword.utils.download_models(["hey_jarvis"])
    except TypeError:
        openwakeword.utils.download_models()
    wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    speak("Online. Say hey Jarvis.")
    while True:
        wait_for_wake_word(wake)
        with sr.Microphone() as mic:
            recognizer.adjust_for_ambient_noise(mic, duration=0.3)
            winsound.Beep(900, 150)
            command = listen(mic, timeout=6)
        if not command:
            continue
        if command in ("stop", "exit", "quit", "goodbye"):
            speak("Goodbye.")
            break
        try:
            speak(ask(command))
        except Exception as e:
            print(f"Error: {e}")
            speak("Something went wrong.")


if __name__ == "__main__":
    main()