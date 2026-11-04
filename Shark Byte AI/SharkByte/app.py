import asyncio
import json
import html
import webbrowser
import urllib.parse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
import random
import signal
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import secrets
import zipfile
from datetime import datetime

# Keep Unicode punctuation and emoji intact in Windows terminals.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
try:
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
except (AttributeError, OSError):
    pass
from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

# prompt_toolkit requires a real Windows console screen buffer. A process launched
# with pythonw.exe, by a detached launcher, or without an attached console has none.
# Agent workers are deliberately detached and do not need a terminal at all.
def ensure_windows_console():
    if os.name != "nt" or "--agent-worker" in sys.argv:
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        if not kernel32.GetConsoleWindow():
            if not kernel32.AllocConsole():
                raise OSError("Windows could not allocate a console for SharkByte.")

        # A console window can exist while Python's standard handles are still
        # redirected or detached. Rebind any non-console handles explicitly.
        for handle_id, stream_name in ((-10, "stdin"), (-11, "stdout"), (-12, "stderr")):
            handle = kernel32.GetStdHandle(ctypes.c_uint32(handle_id).value)
            mode = wintypes.DWORD()
            is_console = bool(handle) and kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            if not is_console:
                if stream_name == "stdin":
                    sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
                elif stream_name == "stdout":
                    sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
                else:
                    sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        # Application construction below will record the precise failure.
        raise


from sharkbyte_browser import search_and_read as browser_search_and_read

import requests
from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window, WindowAlign
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

# ----------------------------- Configuration -----------------------------
OLLAMA_URL = "http://localhost:11434"
CHAT_MODEL = "qwen3:0.6b"
CODE_MODEL = "qwen2.5-coder:1.5b"
CASUAL_MODEL = "gemma3:1b"
EMBED_MODEL = "embeddinggemma-2:270m"  # retained for the standalone embedding helper
BLUE = "#168bff"
MENU_VISIBLE = 8
MENU_PAGE_STEP = 4
AGENT_INTERVAL_SECONDS = 45  # let interactive chat use Ollama between background research cycles
MAX_ACTIVE_AGENTS = 2
WORK_MODEL = "phi3:mini"
AGENT_COORDINATOR_MODEL = "qwen3:0.6b"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_SYSTEM_PROMPT = (
    "You are SharkByte, a friendly and intelligent AI assistant. "
    "Have natural, engaging conversations. Match the user's casual tone. "
    "Don't give unnecessarily short or robotic replies. "
    "When the user makes casual conversation, respond like a real conversational partner. "
    "Show interest, ask natural follow-up questions when appropriate, "
    "and avoid repetitive responses. "
    "Be accurate, useful, and direct. Never invent sources."
)
DEEP_THINKING_SYSTEM_PROMPT = (
    "You are SharkByte in Deep Thinking mode. Carefully analyze the exact task before answering. "
    "Identify the user's intent, relevant context, assumptions, edge cases, and competing explanations. "
    "For research, synthesize evidence rather than listing definitions. For problem-solving, check the result. "
    "Do not expose private chain-of-thought; provide a clear conclusion and a concise explanation of the key reasoning."
)

MENU_ITEMS = [
    ("chat", "CHAT", "mode"),
    ("work", "WORK — PHI-3", "mode"),
    ("download-models", "DOWNLOAD MODELS", "mode"),
    ("upload", "UPLOAD FILE", "action"),
    ("edit-file", "EDIT FILE (ASK BEFORE WRITING)", "action"),
    ("api-keys", "API KEYS — LOCALHOST", "action"),
    ("download", "DOWNLOAD SOFTWARE / FILE", "action"),
    ("open", "OPEN WEB INTERFACE", "action"),
    ("coder", "CODER", "mode"),
    ("deep-thinking", "DEEP THINKING", "mode"),
    ("casual", "CASUAL — GEMMA", "mode"),
        ("help", "HELP", "mode"),
    ("temperature", "SET TEMPERATURE", "action"),
    ("system-prompt", "SYSTEM PROMPT", "action"),
    ("search", "WEB SEARCH", "action"),
    ("agents", "AGENTS", "mode"),
    ("agent-create", "DEPLOY AGENT", "action"),
    ("agent-stop", "STOP SELECTED AGENT", "action"),
    ("save-chat", "SAVE CHAT", "action"),
    ("chats", "SAVED CHATS", "action"),
    ("new-chat", "NEW CHAT", "action"),
    ("delete-current-chat", "DELETE CURRENT CHAT", "action"),
    ("delete-all-chats", "DELETE ALL CHATS", "action"),
    ("reset-temperature", "RESET TEMPERATURE", "action"),
    ("reset-system-prompt", "RESET SYSTEM PROMPT", "action"),
    ("toggle-slash-menu", "SLASH OPENS MENU: ON", "toggle"),
    ("toggle-reset-temperature", "RESET TEMPERATURE BUTTON: ON", "toggle"),
    ("toggle-reset-system-prompt", "RESET SYSTEM PROMPT BUTTON: ON", "toggle"),
    ("clear", "CLEAR HISTORY", "action"),
    ("quit", "QUIT SHARKBYTE", "action"),
]

MODE_LABELS = {
    "chat": "/chat", "work": "/work", "models": "/models", "download-models": "/download models",
    "coder": "/coder", "deep-thinking": "/deep-thinking", "casual": "/casual", "help": "/help", "agents": "/agents",
}

STYLE = Style.from_dict({
    "title": f"bold {BLUE}",
    "title.border": BLUE,
    "subtitle": "bold #ffffff",
    "page": "bg:#168bff #ffffff bold",
    "menu": "bg:#102633 #eeeeee",
    "menu.selected": f"bg:{BLUE} #ffffff bold",
    "input": "#ffffff",
    "thinking": f"bold {BLUE}",
    "status": "#aaaaaa",
    "response": "#eeeeee",
    "help.text": "#eeeeee",
    "md.bold": "bold #ffffff",
    "md.italic": "italic #eeeeee",
    "md.code": "bg:#183746 #7fe7ff",
    "md.heading": f"bold {BLUE}",
    "md.link": "underline #7fc8ff",
    "md.quote": "italic #aab7c4",
    "md.hr": "#168bff",
    "command": "bold #d58cff",
    "command.open": "bold #55d6be",
    "command.model": "bold #ffd166",
    "md.box": "#ff9f1c",
    "copy.button": "bg:#168bff #ffffff bold",
    "agent.selected": f"bg:{BLUE} #ffffff bold",
    "agent.status": "#7fe7ff",
})

history = []
system_prompt = DEFAULT_SYSTEM_PROMPT
temperature = 0.7
selected_mode = "chat"
header_visible = True  # Show the SharkByte title until the first chat request starts.
last_conversation_mode = "chat"
selected_model = CHAT_MODEL
last_command = ""
model_choices = []
model_index = 0
installed_models_cache = []
download_choices = []
download_index = 0
model_pull_busy = False
download_status = ""
catalog_loading = False
web_server = None
web_server_port = None
web_commands = deque()
web_commands_lock = threading.Lock()
menu_open = False
menu_index = 0
menu_scroll = 0
busy = False
response_text = ""
pending_action = None
help_open = False
thinking_inline = False
thinking_detected = False
thinking_text = ""
thinking_expanded = False
thinking_anchor = None
animation_index = 0
request_id = 0
active_cancel_event = threading.Event()
request_watchdog = None
# Track the active streaming response and the exact cross-process lock owner so a
# deleted chat can interrupt the socket and release only its own generation lock.
active_ollama_response = None
active_ollama_response_lock = threading.RLock()
active_ollama_lock_token = None
active_ollama_lock_state_lock = threading.RLock()
# Associate a held Ollama lock with the chat request that acquired it. This lets
# deleting that chat release only its own lock, even before an HTTP stream exists.
active_chat_lock_token = None
active_chat_lock_request_id = None
ollama_resetting = False
ui_events = deque()
events_lock = threading.Lock()
state_lock = threading.RLock()
chat_store_lock = threading.RLock()
app = None
agents = []
selected_agent_id = None
slash_menu_enabled = True
show_reset_temperature_button = True
show_reset_system_prompt_button = True
agent_view_scroll = 0
agent_detail_open = False
# Transcript scrolling: follow new output only while the reader is at/near the bottom.
response_scroll = 0
response_auto_follow = True
active_chat_id = None
pending_uploads = []
file_download_busy = False
last_install_command = None
last_download_path = None
pending_download_candidate = None
pending_edit_path = None
pending_edit_content = None
pending_edit_instruction = None
ANIMATION_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

MODEL_CATALOG = [
    # Recommended lightweight models first
    "qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b", "qwen3:14b", "qwen3:30b-a3b",
    "qwen2.5-coder:0.5b", "qwen2.5-coder:1.5b", "qwen2.5-coder:3b", "qwen2.5-coder:7b",
    "gemma3:1b", "gemma3:4b", "gemma3:12b", "gemma3:27b", "gemma2:2b", "gemma2:9b", "gemma2:27b",
    "smollm2:135m", "smollm2:360m", "smollm2:1.7b", "tinyllama:1.1b", "phi3:mini", "phi4:14b",
    "llama3.2:1b", "llama3.2:3b", "llama3.1:8b", "llama3.1:70b", "llama3:8b", "llama3:70b",
    "deepseek-r1:1.5b", "deepseek-r1:7b", "deepseek-r1:8b", "deepseek-r1:14b", "deepseek-r1:32b",
    "deepseek-coder:1.3b", "deepseek-coder:6.7b", "deepseek-coder:33b",
    "codegemma:2b", "codegemma:7b", "starcoder2:3b", "starcoder2:7b", "starcoder2:15b",
    "mistral:7b", "mistral-nemo:12b", "mixtral:8x7b", "granite3.3:2b", "granite3.3:8b",
    "nomic-embed-text", "mxbai-embed-large", "embeddinggemma:300m", "embeddinggemma-2:270m",
    "qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:3b", "qwen2.5:7b", "qwen2.5:14b", "qwen2.5:32b", "qwen2.5:72b",
    "qwen2.5vl:3b", "qwen2.5vl:7b", "qwen2.5vl:32b", "qwen2.5-math:1.5b", "qwen2.5-math:7b", "qwen2.5-math:72b",
    "gemma3n:e2b", "gemma3n:e4b", "llama3.2-vision:11b", "llama3.2-vision:90b",
    "llava:7b", "llava:13b", "llava:34b", "llava-llama3:8b", "bakllava:7b", "moondream:1.8b", "minicpm-v:8b",
    "command-r:latest", "command-r-plus:latest", "dolphin3:8b", "wizardlm2:7b", "wizardlm2:8x22b",
    "orca-mini:3b", "orca-mini:7b", "orca-mini:13b", "openchat:7b", "solar:10.7b",
    "yi:6b", "yi:9b", "yi:34b", "falcon:7b", "falcon:40b", "stable-code:3b", "mathstral:7b",
    "nous-hermes2:10.7b", "nemotron-mini:4b", "granite-code:3b", "granite-code:8b",
]


def post_event(kind, value=None, event_id=None):
    with events_lock:
        ui_events.append((kind, value, event_id))


def ensure_ollama():
    """Connect to Ollama or try to start it; never terminate the SharkByte UI just because it is offline."""
    try:
        response = requests.get(OLLAMA_URL, timeout=2)
        response.raise_for_status()
        return True
    except requests.RequestException:
        pass

    executable = shutil.which("ollama")
    if not executable:
        # Keep the TUI and localhost web interface available even when Ollama
        # is stopped or its executable is not on PATH. Chat requests can show
        # a useful connection error instead of closing the entire application.
        return False

    ollama_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt":
        ollama_flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        ollama_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    try:
        subprocess.Popen([executable, "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=ollama_flags, close_fds=(os.name != "nt"))
    except OSError:
        return False

    for _ in range(25):
        time.sleep(1)
        try:
            response = requests.get(OLLAMA_URL, timeout=2)
            response.raise_for_status()
            return True
        except requests.RequestException:
            continue
    return False


# ------------------------ Persistent agent storage ------------------------
def data_root():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "SharkByte"
    path.mkdir(parents=True, exist_ok=True)
    return path


def chat_store_path():
    return data_root() / "chats.json"


def read_chat_store():
    with chat_store_lock:
        try:
            data = json.loads(chat_store_path().read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError):
            return []


def persist_active_chat():
    """Atomically save the active conversation and its latest 50 messages."""
    global active_chat_id
    has_user_turn = any(m.get("role") == "user" for m in history) or bool(
        re.search(r"(?m)^\*\*You(?: — [^*]+)?:\*\*", response_text)
    )
    # Do not turn command output, notices, or a delete-all confirmation into a new saved chat.
    if not has_user_turn:
        return False
    if not active_chat_id:
        active_chat_id = uuid.uuid4().hex[:12]
    try:
        with chat_store_lock:
            chats = read_chat_store()
            first_user = next((str(m.get("content", "")) for m in history if m.get("role") == "user"), "")
            if not first_user:
                match = re.search(r"(?m)^\*\*You(?: — [^*]+)?:\*\*\s*(.+)$", response_text)
                first_user = match.group(1).strip() if match else ""
            title = (first_user.splitlines()[0][:72] if first_user else "Untitled chat")
            record = {
                "id": active_chat_id,
                "title": title,
                "updated": time.time(),
                "mode": selected_mode if selected_mode in ("chat", "work", "coder", "deep-thinking", "casual") else last_conversation_mode,
                "transcript": response_text,
                "history": list(history[-50:]),
            }
            chats = [c for c in chats if c.get("id") != active_chat_id]
            chats.append(record)
            chats.sort(key=lambda c: float(c.get("updated") or 0), reverse=True)
            atomic_json(chat_store_path(), chats[:100])
        return True
    except Exception as exc:
        add_notice(f"Couldn't save chat history to `{chat_store_path()}`: {exc}")
        return False


def load_most_recent_chat():
    global active_chat_id, response_text, selected_mode, last_conversation_mode
    global response_scroll, response_auto_follow
    chats = read_chat_store()
    if not chats:
        active_chat_id = uuid.uuid4().hex[:12]
        return False
    chats.sort(key=lambda c: float(c.get("updated") or 0), reverse=True)
    record = chats[0]
    active_chat_id = record.get("id") or uuid.uuid4().hex[:12]
    response_text = str(record.get("transcript", ""))
    with state_lock:
        history.clear()
        history.extend(record.get("history", [])[-50:])
    mode = record.get("mode", "chat")
    if mode in ("chat", "work", "coder", "deep-thinking", "casual"):
        selected_mode = last_conversation_mode = mode
    response_scroll = 0
    response_auto_follow = True
    return True


def list_saved_chats():
    chats = sorted(read_chat_store(), key=lambda c: float(c.get("updated") or 0), reverse=True)
    if not chats:
        add_notice(f"No saved chats yet. Chats are saved automatically after each response. Storage: `{chat_store_path()}`")
        return
    lines = [f"**Saved chats — {len(chats)} stored, newest first**", f"Storage: `{chat_store_path()}`", ""]
    for i, item in enumerate(chats[:50], 1):
        try:
            stamp = datetime.fromtimestamp(float(item.get("updated") or 0)).strftime("%Y-%m-%d %I:%M %p")
        except (ValueError, TypeError, OSError):
            stamp = "Unknown date"
        marker = " · CURRENT" if item.get("id") == active_chat_id else ""
        lines.append(f"{i}. **{item.get('title') or 'Untitled chat'}** — {stamp} — `/chats {i}`{marker}")
    add_notice("\n".join(lines))


def load_saved_chat(target):
    global active_chat_id, response_text, response_scroll, response_auto_follow, thinking_detected, thinking_text, thinking_expanded, thinking_anchor
    global request_id, busy, thinking_inline, active_cancel_event, request_watchdog
    chats = sorted(read_chat_store(), key=lambda c: float(c.get("updated") or 0), reverse=True)
    record = None
    if str(target).isdigit():
        index = int(target) - 1
        if 0 <= index < len(chats):
            record = chats[index]
    else:
        record = next((c for c in chats if c.get("id") == target), None)
    if not record:
        add_notice(f"Couldn't find saved chat `{target}`. Run `/chats` to see the list.")
        return
    if record.get("id") != active_chat_id and (response_text.strip() or history):
        persist_active_chat()
    active_cancel_event.set()
    abort_active_ollama_request()
    request_id += 1
    busy = False
    thinking_inline = False; thinking_detected = False
    thinking_text = ""; thinking_expanded = False; thinking_anchor = None
    active_cancel_event = threading.Event()
    if request_watchdog:
        request_watchdog.cancel()
    active_chat_id = record.get("id") or uuid.uuid4().hex[:12]
    response_text = str(record.get("transcript", ""))
    with state_lock:
        history.clear()
        history.extend(record.get("history", [])[-50:])
    response_scroll = 0
    response_auto_follow = True
    pending_uploads.clear()
    mode = record.get("mode", "chat")
    if mode in ("chat", "work", "coder", "deep-thinking", "casual"):
        set_mode(mode)
    add_notice(f"Loaded chat: **{record.get('title', 'Untitled chat')}**")
    if app:
        app.invalidate()


def new_chat():
    global active_chat_id, header_visible
    if response_text.strip() or history:
        persist_active_chat()
    clear_conversation(persist=False)
    active_chat_id = uuid.uuid4().hex[:12]
    header_visible = True
    add_notice("New chat started. Your previous chat is saved.")
    if app:
        app.invalidate()


def active_ollama_model():
    """Return the Ollama model currently selected for the active terminal mode."""
    return {
        "chat": selected_model,
        "work": WORK_MODEL,
        "coder": CODE_MODEL,
        "deep-thinking": CHAT_MODEL,
        "casual": CASUAL_MODEL,
    }.get(selected_mode, selected_model)


def reset_model_after_chat_deletion(model, force_release=False, request_owner_id=None):
    """Abort and unload the deleted chat's model without disturbing agent locks."""
    global ollama_resetting
    ollama_resetting = True
    abort_active_ollama_request()
    if request_owner_id is not None:
        release_chat_generation_lock(request_owner_id)

    def unload():
        global ollama_resetting
        try:
            reset_ollama_model(model, force_release=force_release)
        finally:
            ollama_resetting = False
            if app:
                app.invalidate()
    threading.Thread(target=unload, name="SharkByte-chat-delete-model-reset", daemon=True).start()


def delete_current_chat():
    global active_chat_id, header_visible
    old_id = active_chat_id
    old_request_id = request_id if busy else None
    model_to_reset = active_ollama_model()
    try:
        with chat_store_lock:
            chats = [c for c in read_chat_store() if c.get("id") != old_id]
            atomic_json(chat_store_path(), chats)
        clear_conversation(persist=False)
        reset_model_after_chat_deletion(model_to_reset, request_owner_id=old_request_id)
        active_chat_id = uuid.uuid4().hex[:12]
        header_visible = True
        add_notice("Current chat deleted. The active Ollama model is being reset; other saved chats were kept.")
        if app:
            app.invalidate()
    except Exception as exc:
        add_notice(f"Couldn't delete the current chat: {exc}")


def delete_all_chats(confirmed=False):
    global active_chat_id, header_visible
    old_request_id = request_id if busy else None
    model_to_reset = active_ollama_model()
    if not confirmed:
        add_notice("This permanently deletes every saved chat and Markdown chat export. Confirm with `/delete-all-chats yes`.")
        return
    try:
        with chat_store_lock:
            atomic_json(chat_store_path(), [])
            export_dir = data_root() / "saved_chats"
            if export_dir.exists():
                for path in export_dir.glob("*.md"):
                    try:
                        path.unlink()
                    except OSError:
                        pass
        clear_conversation(persist=False)
        reset_model_after_chat_deletion(model_to_reset, request_owner_id=old_request_id)
        active_chat_id = uuid.uuid4().hex[:12]
        header_visible = True
        add_notice("All chats deleted. The active Ollama model is being reset.")
        if app:
            app.invalidate()
    except Exception as exc:
        add_notice(f"Couldn't delete all chats: {exc}")

def extract_uploaded_text(filename, raw):
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            import io
            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
        except Exception as exc:
            raise ValueError(f"Could not read this PDF (install pypdf): {exc}")
    if suffix == ".docx":
        try:
            from docx import Document
            import io
            doc = Document(io.BytesIO(raw))
            return "\n".join(par.text for par in doc.paragraphs)
        except Exception as exc:
            raise ValueError(f"Could not read this DOCX (install python-docx): {exc}")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            raise ValueError("This file type isn't text-readable yet. Try TXT, MD, CSV, JSON, PY, HTML, PDF, or DOCX.")


def attach_uploaded_file(filename, raw):
    global pending_uploads
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("Files must be 10 MB or smaller.")
    safe_name = Path(filename).name or "upload.txt"
    text_content = extract_uploaded_text(safe_name, raw)
    if not text_content.strip():
        raise ValueError("No readable text was found in that file.")
    folder = data_root() / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / f"{datetime.now():%Y%m%d_%H%M%S}_{safe_name}"
    stored.write_bytes(raw)
    pending_uploads.append({"name": safe_name, "text": text_content[:60000], "path": str(stored)})
    pending_uploads = pending_uploads[-4:]
    add_notice(f"Attached `{safe_name}` ({len(text_content):,} readable characters). Ask SharkByte about it in your next message.")


def upload_file_dialog(path=""):
    """Attach a file from /upload PATH or open the native file picker."""
    selected = path.strip().strip('"')
    if not selected:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            selected = filedialog.askopenfilename(title="Upload a file to SharkByte")
            root.destroy()
        except Exception as exc:
            add_notice(f"Couldn't open the file picker: {exc}. Use `/upload C:\\path\\to\\file.txt`.")
            return
    if not selected:
        add_notice("Upload cancelled."); return
    try:
        file_path = Path(selected).expanduser()
        attach_uploaded_file(file_path.name, file_path.read_bytes())
    except Exception as exc:
        add_notice(f"Upload failed: {exc}")


@contextmanager
def ollama_generation_lock(timeout=900, cancel_event=None, request_owner_id=None, priority=0):
    """Serialize generations and record which chat request owns the lock."""
    global active_ollama_lock_token, active_chat_lock_token, active_chat_lock_request_id
    lock_path = data_root() / "ollama-generation.lock"
    deadline = time.monotonic() + timeout
    fd = None
    owner_token = uuid.uuid4().hex
    while fd is None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Cancelled while waiting for another Ollama request to finish.")
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            lock_data = {"pid": os.getpid(), "thread_id": threading.get_ident(),
                         "created": time.time(), "token": owner_token,
                         "request_owner_id": request_owner_id}
            os.write(fd, json.dumps(lock_data).encode("utf-8"))
            with active_ollama_lock_state_lock:
                active_ollama_lock_token = owner_token
                if request_owner_id is not None:
                    active_chat_lock_token = owner_token
                    active_chat_lock_request_id = request_owner_id
        except FileExistsError:
            stale = False
            try:
                info = json.loads(lock_path.read_text(encoding="utf-8"))
                pid = int(info.get("pid", 0))
                if pid > 0:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        stale = True
                    except PermissionError:
                        stale = False
                    # PID-only checks miss orphaned locks when the process is alive
                    # but the exact worker thread that created the lock has died.
                    if not stale and pid == os.getpid() and info.get("thread_id"):
                        live_ids = {thread.ident for thread in threading.enumerate() if thread.is_alive()}
                        stale = int(info.get("thread_id") or 0) not in live_ids
                elif time.time() - lock_path.stat().st_mtime > 1800:
                    stale = True
            except (OSError, ValueError, TypeError):
                try:
                    stale = time.time() - lock_path.stat().st_mtime > 1800
                except OSError:
                    stale = True
            if stale:
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for another SharkByte agent/model request to release Ollama.")
            time.sleep(0.25)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            info = json.loads(lock_path.read_text(encoding="utf-8"))
            if info.get("token") == owner_token:
                lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass
        with active_ollama_lock_state_lock:
            if active_ollama_lock_token == owner_token:
                active_ollama_lock_token = None
            if active_chat_lock_token == owner_token:
                active_chat_lock_token = None
                active_chat_lock_request_id = None


def agent_root():
    path = data_root() / "agents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_folder(agent_id):
    path = agent_root() / agent_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "inbox").mkdir(exist_ok=True)
    return path


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_agent_state(folder):
    try:
        value = json.loads((Path(folder) / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def refresh_agents_from_disk():
    global agents
    refreshed = []
    for folder in agent_root().iterdir():
        if not folder.is_dir():
            continue
        state = read_agent_state(folder)
        if state:
            state["folder"] = str(folder)
            refreshed.append(state)
    with state_lock:
        agents = sorted(refreshed, key=lambda a: a.get("created", 0))


def launch_agent_process(folder):
    """Launch this same app.py as a detached agent worker (single-file distribution)."""
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                 | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    log_path = Path(folder) / "worker.log"
    log_handle = open(log_path, "ab", buffering=0)
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--agent-worker", "--agent-dir", str(folder)],
            cwd=str(Path(__file__).resolve().parent), stdin=subprocess.DEVNULL,
            stdout=log_handle, stderr=subprocess.STDOUT,
            creationflags=flags, close_fds=(os.name != "nt"),
        )
    finally:
        log_handle.close()
    return process.pid


def terminate_agent_pid(pid):
    if not isinstance(pid, int) or pid <= 0:
        return
    # Never allow stale agent metadata or PID reuse to terminate SharkByte itself.
    if pid == os.getpid():
        return
    try:
        if os.name == "nt":
            # Do not use taskkill /T: a reused PID could otherwise terminate an
            # unrelated process tree. Agent workers are standalone processes.
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def resume_orphaned_agents():
    """Restart dead workers and upgrade older workers to the embedded mini-agent team."""
    refresh_agents_from_disk()
    for agent in list(agents):
        if not agent.get("running"):
            continue
        pid = agent.get("pid")
        alive = False
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0); alive = True
            except (OSError, ProcessLookupError):
                alive = False
        try:
            worker_version = int(agent.get("worker_version", 0) or 0)
        except (TypeError, ValueError):
            worker_version = 0
        needs_upgrade = worker_version < 3
        # A PID can be reused after a worker exits. Never kill the current app.
        if pid == os.getpid():
            alive = False
        folder = Path(agent.get("folder", ""))
        if alive and not needs_upgrade:
            continue
        if alive and needs_upgrade:
            # Ask a legacy worker to stop through its own stop flag. Do not use
            # taskkill during startup: a stale/reused PID could belong to cmd.exe
            # or another unrelated process, which can close the user's terminal.
            try:
                (folder / "stop").write_text("upgrade", encoding="utf-8")
                state = read_agent_state(folder) or agent
                state["status"] = "Stopping older worker before upgrade; restart SharkByte after it stops"
                atomic_json(folder / "state.json", state)
            except OSError:
                pass
            continue
        try:
            (folder / "stop").unlink(missing_ok=True)
            worker_pid = launch_agent_process(folder)
            latest_state = read_agent_state(folder) or agent
            latest_state.update({"pid": worker_pid, "worker_version": 3, "running": True,
                                 "status": "Starting upgraded mini-agent research team"})
            atomic_json(folder / "state.json", latest_state)
        except Exception as exc:
            state = read_agent_state(folder) or agent
            state["running"] = False
            state["status"] = f"Could not resume worker: {exc}"
            atomic_json(folder / "state.json", state)
    refresh_agents_from_disk()


# ------------------------------- UI panels -------------------------------
def terminal_width():
    try:
        return max(40, app.output.get_size().columns - 4) if app else 80
    except Exception:
        return 80


def boxed_fragments(lines, centered=False, min_width=34, padding=2, title=None, styles=None):
    """Render a centered, blue-outlined panel with real line breaks."""
    lines = [str(x) for x in lines]
    width = min(terminal_width(), max(min_width, max((len(x) for x in lines), default=0) + padding * 2 + 2,
                                      len(title or "") + 6))
    inner = width - 2
    top = "╭" + ("─ " + title + " " + "─" * max(0, inner - len(title) - 3) if title else "─" * inner) + "╮"
    out = [("class:title.border", top + "\n")]
    for i, line in enumerate(lines):
        clipped = line[:inner]
        left = max(0, (inner - len(clipped)) // 2) if centered else min(padding, max(0, inner - len(clipped)))
        right = max(0, inner - len(clipped) - left)
        style = styles[i] if styles and i < len(styles) else "class:help.text"
        out.extend([("class:title.border", "│"), (style, " " * left + clipped + " " * right),
                    ("class:title.border", "│\n")])
    out.append(("class:title.border", "╰" + "─" * inner + "╯"))
    return out


def header_fragments():
    # Keep the panel at a stable width; the Window itself centers it in the terminal.
    return boxed_fragments(["S H A R K B Y T E", "LOCAL TERMINAL UI"], centered=True,
                           min_width=42, padding=4, styles=["class:title", "class:subtitle"])


def help_fragments():
    lines = [
        "/chat MESSAGE              Fast chat using your permanently selected model",
        "/work MESSAGE              Fast Phi-3 work mode (no max-depth thinking)",
        "/models                    Show installed models and choose one",
        "/download models           Browse and download Ollama models (Qwen/Gemma first)",
        "/open                      Open the SharkByte web interface in a new browser tab",
        "/open terminal             Focus the SharkByte terminal from the web interface",
        "/coder MESSAGE             Coding-focused chat using Qwen2.5-Coder 1.5B",
        "/deep-thinking QUESTION    Deep Qwen3 thinking mode",
        "/casual MESSAGE            Casual conversation using Gemma 3:1b",
        "/temperature NUMBER        Set temperature from 0 to 2",
        "/system-prompt TEXT        Change the system prompt",
        "/search QUERY              Search the web with the background browser and read sources",
        "/agents                    View, create, and talk to persistent background agents",
        "/agent GOAL                Deploy a background research agent",
        "/agent-message ID TEXT     Tell an agent what to change or investigate",
        "/delete-agent [ID]         Delete an agent and its stored data",
        "/stop-agent [ID]           Stop a background agent",
        "/clear                     Clear visible conversation and AI history (keeps saved copy)",
        "/save-chat                 Save the current transcript as Markdown",
        "/chats [NUMBER]            List saved chats, or load one by its number",
        "/new chat                  Save the current chat and start a new one",
        "/delete-chat               Delete only the current chat",
        "/delete-all-chats yes      Permanently delete all saved chats and exports",
        "/upload [FILE PATH]        Attach a readable file to the next message",
        "/edit-file                 Ask SharkByte to edit a local text file (WRITE approval required)",
        "/api-keys                  Add, list, rename, or delete localhost API keys",
        "/download QUERY | URL      Search and download software or a file",
        "Menu: UPLOAD FILE and DOWNLOAD SOFTWARE / FILE are also available",
        "/download run              Confirm and launch the last suggested install command",
        "/reset-temperature         Restore default temperature",
        "/reset-system-prompt       Restore the default system prompt",
        "/help                      Show this help panel",
        "/quit                      Exit SharkByte",
        f"LAST COMMAND: {last_command or '/help'}",
        "",
        "Plain text sends a message using the selected mode. /embed remains an alias for /casual.",
        "F2 or / opens the menu · Up/Down scroll · PgUp/PgDn move 4 items · blue Jump to Bottom button returns to newest text.",
        "Enter selects an item · Esc closes the menu · Ctrl-C cancels generation but never exits SharkByte.",
        "Agents run continuous research cycles with three mini-researchers and a max-depth Qwen coordinator.",
        "In Agents: click or select an agent, Enter to open its activity/instruction prompt, Backspace to delete it.",
        "The model selected in /models remains selected; chat/work are not forced into max-depth mode.",
        "Agent sources are displayed with each research update.",
        "Menu toggles independently control slash-to-menu and each reset button.",
        "Web research runs one search, crawls up to 10 websites, and summarizes relevant passages.",
        "Keep ordinary answers brief and direct. Include full details only when the user asks or when needed to complete a task.",
        "Separate expandable thinking is available only when the selected Ollama model emits a native thinking field (currently enabled for Qwen3). Other models may show no separate thinking trace.",
        "Chat history is saved automatically; the latest chat opens on startup and up to 50 messages are sent as context.",
        "Web research excludes social-media sites except Reddit.",
        "Press Enter to close this help page.",
    ]
    return boxed_fragments(lines, centered=False, min_width=76, padding=2,
                           title="SharkByte Help")


# --------------------------- Markdown rendering --------------------------
def markdown_fragments(text):
    """Dependency-free Markdown renderer: headings, lists, quotes, fences and inline styles."""
    fragments = []
    in_fence = False
    inline_pattern = re.compile(r"(\*\*.+?\*\*|(?<!\*)\*[^*]+\*(?!\*)|`[^`]+`|\[[^\]]+\]\([^\)]+\))")
    for line in str(text).splitlines(keepends=True):
        content = line[:-1] if line.endswith("\n") else line
        ending = "\n" if line.endswith("\n") else ""
        stripped = content.lstrip()
        if stripped.startswith("COMMAND  "):
            lower = stripped.lower()
            command_style = "class:command.open" if "/open" in lower else ("class:command.model" if "/models" in lower or "/download" in lower else "class:command")
            fragments.append((command_style, content + ending)); continue
        if stripped.startswith("```"):
            if not in_fence:
                fragments.append(("class:md.box", "╭" + "─" * min(terminal_width() - 2, 72) + "╮\n"))
            else:
                fragments.append(("class:md.box", "╰" + "─" * min(terminal_width() - 2, 72) + "╯" + ending))
            in_fence = not in_fence
            continue
        if in_fence:
            fragments.append(("class:md.box", "│ "))
            fragments.append(("class:md.code", content + " │" + ending))
            continue
        if stripped.startswith("/") and len(stripped) < 180:
            fragments.append(("class:command", content + ending)); continue
        if stripped in ("---", "***", "___"):
            fragments.append(("class:md.hr", "─" * 36 + ending)); continue
        if stripped.startswith("> "):
            fragments.append(("class:md.quote", stripped[2:] + ending)); continue
        if stripped.startswith("### "):
            fragments.append(("class:md.heading", stripped[4:] + ending)); continue
        if stripped.startswith("## "):
            fragments.append(("class:md.heading", stripped[3:] + ending)); continue
        if stripped.startswith("# "):
            fragments.append(("class:md.heading", stripped[2:] + ending)); continue
        bullet = re.match(r"^(\s*)([-*+] |\d+[.)] )(.*)$", content)
        if bullet:
            prefix, marker, rest = bullet.groups()
            rendered_marker = "• " if marker[0].isdigit() or marker in ("- ", "* ", "+ ") else marker
            fragments.append(("", prefix + rendered_marker))
            content = rest
        pos = 0
        for match in inline_pattern.finditer(content):
            if match.start() > pos:
                fragments.append(("class:response", content[pos:match.start()]))
            token = match.group(0)
            if token.startswith("**"):
                fragments.append(("class:md.bold", token[2:-2]))
            elif token.startswith("*"):
                fragments.append(("class:md.italic", token[1:-1]))
            elif token.startswith("`"):
                fragments.append(("class:md.code", token[1:-1]))
            else:
                m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token)
                fragments.append(("class:md.link", f"{m.group(1)} ({m.group(2)})" if m else token))
            pos = match.end()
        if pos < len(content):
            fragments.append(("class:response", content[pos:]))
        if ending:
            fragments.append(("class:response", ending))
    return fragments


# ------------------------------- Web search ------------------------------
BLOCKED_SOCIAL_DOMAINS = (
    "tiktok.com", "instagram.com", "facebook.com", "fb.com", "x.com", "twitter.com",
    "threads.net", "snapchat.com", "pinterest.com", "youtube.com", "youtu.be",
    "youtube-nocookie.com", "youtube.googleapis.com", "youtubei.googleapis.com",
    "linkedin.com", "tumblr.com", "discord.com", "quora.com", "twitch.tv",
    "bsky.app", "bluesky.social", "mastodon.social", "vk.com", "weibo.com",
    "t.me", "telegram.org", "whatsapp.com", "line.me", "flickr.com",
)


def is_allowed_search_url(url):
    try:
        parsed = urllib.parse.urlparse(str(url))
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme.lower() not in ("http", "https"):
            return False
    except Exception:
        return False
    if not host:
        return False
    if host in ("reddit.com", "redd.it") or host.endswith(".reddit.com"):
        return True
    return not any(host == domain or host.endswith("." + domain) for domain in BLOCKED_SOCIAL_DOMAINS)


def filter_search_results(items):
    return [item for item in (items or []) if is_allowed_search_url(item.get("url", ""))]




def perform_web_research(query, cancel_token, progress_callback=None, model_name=None):
    """Run topic-aware searches and pass only page-verified evidence to the answer model."""
    search_query = " ".join(str(query or "").split()).strip()
    if not search_query:
        raise ValueError("Enter a question to research.")
    if progress_callback:
        progress_callback("Interpreting the topic, generating focused searches, and verifying source pages…")
    if cancel_token.is_set():
        raise RuntimeError("Research cancelled.")

    collected = filter_search_results(browser_search_and_read(
        search_query, progress_callback, cancel_token, max_results=10, total_timeout=210
    ))
    if cancel_token.is_set():
        raise RuntimeError("Research cancelled.")
    if not collected:
        raise RuntimeError(
            "Search engines returned results, but none passed the topic-relevance and page-content checks. "
            "Try a more specific query or check your browser connection."
        )

    # Browser-side relevance scores are useful for ranking, but the model is also
    # explicitly told to ignore any source that does not address the user's subject.
    source_blocks = []
    actual_sources = []
    seen_source_urls = set()
    for index, item in enumerate(collected, 1):
        page_text = str(item.get("excerpt", "") or item.get("snippet", "")).strip()
        if not page_text:
            continue
        source_url = str(item.get("url", "")).strip()
        if not source_url or source_url in seen_source_urls or not is_allowed_search_url(source_url):
            continue
        seen_source_urls.add(source_url)
        title = str(item.get("title") or "Untitled source")
        score = item.get("relevance", "not scored")
        source_blocks.append(
            f"SOURCE {len(source_blocks)+1}: {title}\nURL: {source_url}\n"
            f"TOPIC RELEVANCE SCORE: {score}\nPAGE TEXT:\n{page_text[:4200]}"
        )
        actual_sources.append((title, source_url))

    if not source_blocks:
        raise RuntimeError("The browser opened pages, but no verified readable source text was available.")

    reddit_count = sum(1 for _, url in actual_sources
                       if (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.").endswith("reddit.com"))
    evidence = (
        f"USER'S EXACT QUESTION: {search_query}\n"
        f"TOPIC-VERIFIED WEBSITES READ: {len(source_blocks)}\n"
        f"REDDIT PAGES FOUND: {reddit_count}\n\n"
        "SOURCE QUALITY RULE: Use only pages that substantively discuss the requested subject. "
        "A page matching an incidental word, number, or common verb is not relevant evidence. "
        "If sources disagree or do not establish an origin, say so. Do not invent facts or citations.\n\n"
        + "\n\n---\n\n".join(source_blocks)
    )
    return evidence, len(actual_sources), len(actual_sources), actual_sources


# ------------------------------- AI workers ------------------------------
def ollama_chat(messages, model, deep=False, cancel_token=None, token_callback=None,
                prompt_temperature=None, large_context=False, max_tokens=None, thinking_callback=None,
                request_owner_id=None):
    """Stream Ollama output and continue automatically if it ends at the token limit."""
    started = time.monotonic()
    # A request gets four minutes. A timeout is handled by the worker, which unloads
    # the model and makes one clean retry with the same conversation context.
    hard_limit = 240
    token_budget = int(max_tokens or (3072 if deep else (6144 if "coder" in str(model).lower() else 768)))
    request_messages = [dict(item) for item in messages]
    answer = ""
    with ollama_generation_lock(timeout=240, cancel_event=cancel_token,
                                 request_owner_id=request_owner_id):
        for continuation in range(3):
            body = {
                "model": model,
                "messages": request_messages,
                "stream": True,
                "options": {
                    "temperature": temperature if prompt_temperature is None else prompt_temperature,
                    "num_predict": token_budget,
                    "num_ctx": 8192 if (deep or large_context or "coder" in str(model).lower()) else 4096,
                },
            }
            # Match the browser client exactly: ask Ollama for a separate native
            # thinking stream only in Deep Thinking mode, and only on Qwen3.
            if deep and str(model).lower().startswith("qwen3"):
                body["think"] = True
            done_reason = ""
            response = requests.post(f"{OLLAMA_URL}/api/chat", json=body, stream=True,
                                     timeout=(10, 240))
            global active_ollama_response
            with active_ollama_response_lock:
                active_ollama_response = response
            try:
                with response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if cancel_token and cancel_token.is_set():
                            break
                        if time.monotonic() - started > hard_limit:
                            raise TimeoutError(f"The model exceeded SharkByte's {hard_limit}-second response limit.")
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except (ValueError, TypeError):
                            continue
                        msg = data.get("message", {})
                        # Ollama may stream a separate reasoning field. Keep it out of the
                        # normal transcript; the user can explicitly expand or hide it.
                        # Ollama versions/models may expose the separate trace as
                        # `thinking`; accept `reasoning` as a compatibility alias.
                        thinking_piece = (msg.get("thinking") or msg.get("reasoning")
                                          or data.get("thinking") or "")
                        if thinking_piece and thinking_callback:
                            thinking_callback(str(thinking_piece))
                        piece = msg.get("content", "")
                        if piece:
                            answer += piece
                            if token_callback:
                                token_callback(piece)
                        if data.get("done"):
                            done_reason = str(data.get("done_reason", "")).lower()
                            break
            finally:
                with active_ollama_response_lock:
                    if active_ollama_response is response:
                        active_ollama_response = None
                try:
                    response.close()
                except Exception:
                    pass
            if cancel_token and cancel_token.is_set():
                return answer
            if done_reason not in ("length", "max_tokens", "token_limit") or continuation >= 2:
                break
            request_messages.extend([
                {"role": "assistant", "content": answer},
                {"role": "user", "content": (
                    "Continue exactly where your previous response ended. Do not repeat any earlier text. "
                    "Finish the requested task, including any remaining code, explanation, and verification."
                )},
            ])
    if cancel_token and cancel_token.is_set():
        return answer
    if not answer.strip():
        raise RuntimeError("Ollama finished without returning answer text. Check that the selected model supports /api/chat and is fully downloaded.")
    return answer


def answer_is_repeating(text):
    """Flag a genuinely stuck answer only after the same sentence/line occurs 3+ times."""
    normalized = []
    for line in str(text).splitlines():
        line = re.sub(r"\s+", " ", line).strip().lower()
        if len(line) >= 28:
            normalized.append(line)
    counts = {}
    for line in normalized:
        counts[line] = counts.get(line, 0) + 1
        if counts[line] >= 3:
            return True
    sentences = re.split(r"(?<=[.!?])\s+", str(text))
    counts.clear()
    for sentence in sentences:
        sentence = re.sub(r"\s+", " ", sentence).strip().lower()
        if len(sentence) >= 35:
            counts[sentence] = counts.get(sentence, 0) + 1
            if counts[sentence] >= 3:
                return True
    return False


def abort_active_ollama_request():
    """Close the current streaming response so a cancelled chat releases its worker."""
    with active_ollama_response_lock:
        response = active_ollama_response
    if response is not None:
        try:
            response.close()
        except Exception:
            pass


def release_current_process_generation_lock():
    """Release only the exact lock token owned by this process's active request."""
    with active_ollama_lock_state_lock:
        token = active_ollama_lock_token
    if not token:
        return False
    lock_path = data_root() / "ollama-generation.lock"
    try:
        info = json.loads(lock_path.read_text(encoding="utf-8"))
        if int(info.get("pid", 0)) == os.getpid() and info.get("token") == token:
            lock_path.unlink(missing_ok=True)
            return True
    except (OSError, ValueError, TypeError):
        pass
    return False


def release_chat_generation_lock(request_owner_id):
    """Release a lock only when its recorded owner is this deleted chat request."""
    global active_ollama_lock_token, active_chat_lock_token, active_chat_lock_request_id
    with active_ollama_lock_state_lock:
        if active_chat_lock_request_id != request_owner_id or not active_chat_lock_token:
            return False
        token = active_chat_lock_token
        lock_path = data_root() / "ollama-generation.lock"
        try:
            info = json.loads(lock_path.read_text(encoding="utf-8"))
            if (int(info.get("pid", 0)) != os.getpid()
                    or info.get("token") != token
                    or info.get("request_owner_id") != request_owner_id):
                return False
            lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            return False
        if active_ollama_lock_token == token:
            active_ollama_lock_token = None
        if active_chat_lock_token == token:
            active_chat_lock_token = None
            active_chat_lock_request_id = None
        return True


def reset_ollama_model(model, force_release=False):
    """Stop/unload a model without waiting on the generation lock it may be stuck holding."""
    stopped_or_unloaded = False
    try:
        executable = shutil.which("ollama")
        if executable:
            try:
                stopped = subprocess.run([executable, "stop", str(model)], capture_output=True,
                                          text=True, timeout=15, check=False,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
                stopped_or_unloaded = stopped.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        if not stopped_or_unloaded:
            try:
                response = requests.post(f"{OLLAMA_URL}/api/generate",
                                         json={"model": model, "prompt": "", "keep_alive": 0}, timeout=(4, 12))
                response.raise_for_status()
                stopped_or_unloaded = True
            except Exception:
                pass
    finally:
        # On chat deletion the stream has already been closed and cancellation signalled.
        # Do not leave a dead UI request's lock blocking every later chat.
        if force_release:
            release_current_process_generation_lock()


def start_chat_worker(prompt, model, instruction, event_id, cancel_token,
                      deep=False, use_history=True, system_snapshot=None, temperature_snapshot=None):
    with state_lock:
        active_history = list(history[-50:]) if use_history else []
        # Keep as many as 50 messages while bounding the combined context for small local models.
        history_chars = sum(len(str(item.get("content", ""))) for item in active_history)
        while active_history and history_chars > 24000:
            removed = active_history.pop(0)
            history_chars -= len(str(removed.get("content", "")))
        active_system = system_snapshot if system_snapshot is not None else system_prompt
        active_temperature = temperature if temperature_snapshot is None else temperature_snapshot
    messages = [{"role": "system", "content": active_system}]
    messages.extend(active_history)
    actual_prompt = f"{instruction}\n\n{prompt}" if instruction else prompt
    messages.append({"role": "user", "content": actual_prompt})
    answer = ""

    def on_token(piece):
        nonlocal answer
        answer += piece
        post_event("token", piece, event_id)

    def on_thinking(piece):
        post_event("thinking_piece", piece, event_id)

    def run_model(request_messages):
        return ollama_chat(
            request_messages, model, deep, cancel_token, on_token, active_temperature,
            large_context=(len(active_history) >= 20 or history_chars > 10000),
            max_tokens=6144 if "coder" in str(model).lower() else None,
            thinking_callback=on_thinking,
            request_owner_id=event_id,
        )

    try:
        try:
            result = run_model(messages)
        except (TimeoutError, requests.Timeout) as timeout_error:
            if cancel_token.is_set():
                post_event("cancelled", None, event_id)
                return
            post_event("retrying_timeout", len(answer), event_id)
            reset_ollama_model(model)
            if cancel_token.is_set():
                post_event("cancelled", None, event_id)
                return
            answer = ""
            result = run_model(messages)

        if result and answer_is_repeating(result) and not cancel_token.is_set():
            post_event("retrying", len(answer), event_id)
            reset_ollama_model(model)
            answer = ""
            retry_messages = messages + [
                {"role": "assistant", "content": result},
                {"role": "user", "content": (
                    "RESET AND RETRY REQUIRED: Your previous answer repeated the same content at least three times. "
                    "Use the conversation history above, discard the repetitive approach, and complete the original request. "
                    "Do not repeat your previous wording. Give one coherent, complete answer and verify it before stopping."
                )},
            ]
            result = run_model(retry_messages)
        if cancel_token.is_set():
            post_event("cancelled", None, event_id)
            return
        if result and use_history:
            with state_lock:
                history.extend([{"role": "user", "content": prompt},
                                {"role": "assistant", "content": result}])
        post_event("done", None, event_id)
    except (TimeoutError, requests.Timeout) as e:
        reset_ollama_model(model)
        post_event("error", f"The model still did not respond after its one automatic reset and retry: {e}", event_id)
    except requests.RequestException as e:
        detail = str(e)
        if e.response is not None:
            detail += "\n" + e.response.text[:1500]
        post_event("error", detail, event_id)
    except Exception as e:
        post_event("cancelled" if cancel_token.is_set() else "error", None if cancel_token.is_set() else str(e), event_id)


def start_embedding_worker(text, event_id, cancel_token):
    try:
        with ollama_generation_lock(timeout=300, cancel_event=cancel_token, priority=5):
            r = requests.post(f"{OLLAMA_URL}/api/embed",
                              json={"model": EMBED_MODEL, "input": text}, timeout=120)
            r.raise_for_status()
        if not cancel_token.is_set():
            post_event("embedding", r.json()["embeddings"][0], event_id)
        else:
            post_event("done", None, event_id)
    except Exception as e:
        if cancel_token.is_set(): post_event("cancelled", None, event_id)
        else: post_event("error", f"Embedding failed: {e}", event_id)


def start_search_worker(query, event_id, cancel_token, temperature_snapshot, model_snapshot):
    try:
        def progress(message):
            post_event("search_progress", message, event_id)
        sources, found, read, actual_sources = perform_web_research(query, cancel_token, progress, model_snapshot)
        if cancel_token.is_set():
            post_event("done", None, event_id); return
        post_event("search_sources", (model_snapshot, read, sources, actual_sources), event_id)
        with state_lock:
            active_system = system_prompt
        active_temp = temperature_snapshot
        instruction = (
            "Answer the user's exact question using only the crawled webpages and relevant passages below. "
            "For memes, slang, and internet trends, use the included Reddit discussions as important evidence of "
            "how people actually use and interpret the term. Distinguish Reddit users' opinions or jokes from "
            "verified origin/history facts; do not treat one comment as universal consensus. If Reddit was searched "
            "but no relevant Reddit page was found, say that rather than pretending Reddit supports the answer. "
            "Never invent a meaning, origin, source, quote, or URL. If the evidence does not establish an answer, "
            "say what remains uncertain. Give a brief, direct summary (normally 3-6 sentences). Compare sources, "
            "mention important disagreements or uncertainty, and cite claims inline as [1], [2], etc., then list "
            "only the sources actually used with title and URL. Do not dump the research notes.\n\n"
            "SEARCH PAGE CONTENT (readable text extracted from source pages):\n" + sources
        )
        messages = [{"role": "system", "content": active_system},
                    {"role": "user", "content": instruction + "\n\nQUESTION: " + query}]
        answer = ollama_chat(messages, model_snapshot, False, cancel_token,
                             lambda piece: post_event("token", piece, event_id), active_temp,
                             large_context=True,
                             thinking_callback=lambda piece: post_event("thinking_piece", piece, event_id))
        post_event("done", None, event_id)
    except Exception as e:
        if cancel_token.is_set(): post_event("cancelled", None, event_id)
        else: post_event("error", f"Web research failed: {e}", event_id)


# ----------------------- Models, downloads, clipboard ----------------------
def load_selected_model():
    global selected_model
    settings = data_root() / "settings.json"
    try:
        value = json.loads(settings.read_text(encoding="utf-8"))
        selected_model = str(value.get("selected_model") or CHAT_MODEL)
    except (OSError, ValueError, TypeError):
        selected_model = CHAT_MODEL


def save_selected_model():
    try:
        atomic_json(data_root() / "settings.json", {"selected_model": selected_model})
    except OSError:
        pass


def refresh_model_choices():
    global model_choices, model_index, installed_models_cache
    installed = installed_ollama_models()
    installed_models_cache = list(installed)
    model_choices = sorted(set(installed), key=lambda x: (x != selected_model, x.lower()))
    model_index = model_choices.index(selected_model) if selected_model in model_choices else 0


def refresh_model_catalog_async():
    """Fetch the public Ollama library when online; keep the built-in catalog as a fallback."""
    global catalog_loading, download_status
    if catalog_loading: return
    catalog_loading = True
    download_status = "Refreshing the official Ollama model library…"
    def worker():
        global catalog_loading
        try:
            response = requests.get("https://ollama.com/library",
                                    headers={"User-Agent": "SharkByte-local-model-catalog/1.0"},
                                    timeout=(8, 14))
            response.raise_for_status()
            page = response.text
            slugs = re.findall(r"href=[\"']/?library/([A-Za-z0-9][A-Za-z0-9._-]{0,63})(?:[/?#\"'])", page)
            slugs = sorted(set(x.lower() for x in slugs))
            if len(slugs) < 12:
                raise RuntimeError("The public library page did not return enough model entries.")
            recommended = list(MODEL_CATALOG[:23])
            catalog = list(recommended)
            for slug in slugs:
                tag = f"{slug}:latest"
                if tag not in catalog and slug not in ("library", "search", "login", "signup", "blog", "about"):
                    catalog.append(tag)
            for name in MODEL_CATALOG:
                if name not in catalog: catalog.append(name)
            post_event("catalog_ready", catalog)
        except Exception as exc:
            post_event("catalog_ready", None)
        finally:
            catalog_loading = False
    threading.Thread(target=worker, daemon=True).start()


def choose_model(name):
    global selected_model
    name = str(name).strip()
    if not name:
        add_notice("Choose a model name first."); return
    installed = installed_ollama_models()
    if installed and name not in installed:
        add_notice(f"`{name}` is not installed. Download it with `/download models {name}` first."); return
    selected_model = name
    save_selected_model()
    if selected_mode in ("models", "download-models"):
        set_mode(last_conversation_mode)
    add_notice(f"**MODEL SELECTED:** `{selected_model}`. This selection is saved and will not be auto-switched. Current mode remains {MODE_LABELS.get(selected_mode, selected_mode)}.")


def start_model_pull(name):
    global model_pull_busy, download_status
    name = str(name).strip()
    if not name:
        add_notice("Choose a model from /download models or type `/download models MODEL:TAG`."); return
    if model_pull_busy:
        add_notice("A model download is already running. Let it finish first."); return
    model_pull_busy = True
    download_status = f"Starting download: {name}"
    add_notice(f"**DOWNLOAD STARTED:** `{name}` — Ollama is pulling this model. Progress will appear below.")
    threading.Thread(target=model_pull_worker, args=(name,), daemon=True).start()


def model_pull_worker(name):
    global model_pull_busy, download_status
    try:
        with requests.post(f"{OLLAMA_URL}/api/pull", json={"name": name, "stream": True},
                           stream=True, timeout=(15, 180)) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line: continue
                try: data = json.loads(line)
                except ValueError: continue
                status = data.get("status", "Downloading")
                total = data.get("total") or 0
                completed = data.get("completed") or 0
                if total:
                    pct = min(100, int(completed * 100 / total))
                    post_event("download_progress", f"{name}: {status} {pct}% ({completed // 1048576}/{total // 1048576} MB)")
                else:
                    post_event("download_progress", f"{name}: {status}")
        post_event("download_done", f"Downloaded `{name}` successfully.")
    except Exception as exc:
        post_event("download_done", f"Download failed for `{name}`: {exc}")
    finally:
        model_pull_busy = False


def copy_to_clipboard(value):
    value = str(value)
    try:
        if os.name == "nt":
            subprocess.run("clip", input=value, text=True, shell=True, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        command = shutil.which("wl-copy") or shutil.which("xclip")
        if command:
            args = [command] if command.endswith("wl-copy") else [command, "-selection", "clipboard"]
            subprocess.run(args, input=value, text=True, check=True)
            return True
    except Exception:
        pass
    return False


def extract_transcript_messages():
    pattern = re.compile(r"(?m)^\*\*(?:You(?: — [^*]+)?|AUTO selected [^*]+|[^*]+ — [^*]+):\*\*")
    matches = list(pattern.finditer(response_text))
    result = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(response_text)
        body = response_text[match.start():end].strip()
        if body: result.append(body)
    return result


def message_control_mouse(content, action, event):
    """Copy/download the exact immutable message text captured when its button was rendered."""
    if event.event_type.name != "MOUSE_UP": return None
    content = str(content).strip()
    if not content: return None
    if action == "copy":
        ok = copy_to_clipboard(content)
        add_notice("Message copied to clipboard." if ok else "Clipboard copy failed. Use /save-chat to export the transcript.")
    else:
        folder = data_root() / "downloads"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"SharkByte_message_{datetime.now():%Y%m%d_%H%M%S_%f}.md"
        path.write_text(content + "\n", encoding="utf-8")
        add_notice(f"Message downloaded to: {path}")
    return None


def code_file_details(language):
    extensions = {"python":"py", "py":"py", "javascript":"js", "js":"js", "typescript":"ts",
                  "ts":"ts", "html":"html", "css":"css", "json":"json", "bash":"sh",
                  "sh":"sh", "powershell":"ps1", "ps1":"ps1", "bat":"bat", "sql":"sql",
                  "yaml":"yml", "yml":"yml", "toml":"toml", "java":"java", "c":"c",
                  "cpp":"cpp", "c++":"cpp", "csharp":"cs", "cs":"cs", "go":"go", "rust":"rs",
                  "ruby":"rb", "php":"php", "swift":"swift", "kotlin":"kt", "markdown":"md"}
    ext = extensions.get((language or "").strip().lower(), "txt")
    return f"SharkByte_Generated.{ext}", ext


def code_block_mouse(code, language, action, event):
    if event.event_type.name != "MOUSE_UP":
        return None

    language = (language or "").strip().lower()
    folder = data_root() / "downloads"
    folder.mkdir(parents=True, exist_ok=True)

    filename, ext = code_file_details(language)

    if action == "copy":
        add_notice(
            "Code copied to clipboard."
            if copy_to_clipboard(code)
            else "Clipboard copy failed."
        )
        return None

    # HTML downloads the exact code from the AI response.
    if language in ("html", "htm"):
        path = folder / filename
        path.write_text(code.rstrip() + "\n", encoding="utf-8")
        add_notice(f"HTML downloaded: {path}")
        return None

    is_large = len(code) >= 3500 or code.count("\n") >= 70
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    if is_large:
        bundle_name = f"SharkByte_Project_{stamp}"
        project_dir = folder / bundle_name
        project_dir.mkdir(parents=True, exist_ok=True)

        (project_dir / filename).write_text(
            code.rstrip() + "\n", encoding="utf-8"
        )
        (project_dir / "README.txt").write_text(
            f"Generated by SharkByte\nMain file: {filename}\nLanguage: {language or 'text'}\n",
            encoding="utf-8"
        )

        archive = folder / f"{bundle_name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in project_dir.rglob("*"):
                if item.is_file():
                    zf.write(item, item.relative_to(folder))

        add_notice(f"Large code saved as a project folder: {project_dir}\nDownloadable ZIP: {archive}")
    else:
        path = folder / f"SharkByte_code_{stamp}.{ext}"
        path.write_text(code.rstrip() + "\n", encoding="utf-8")
        add_notice(f"Code block downloaded to: {path}")

    return None


def markdown_with_code_controls(value, show_controls=True):
    if not show_controls:
        return markdown_fragments(value)

    pattern = re.compile(r"```([^\n`]*)\n([\s\S]*?)```")
    out = []
    cursor = 0

    for match in pattern.finditer(value):
        if match.start() > cursor:
            out.extend(markdown_fragments(value[cursor:match.start()]))

        language = match.group(1).strip()
        code = match.group(2)
        normalized_language = language.lower()
        filename, ext = code_file_details(language)

        # HTML: replace the ENTIRE code block with one compact download box.
        if normalized_language in ("html", "htm"):
            out.append(("class:md.box", f"╭─ {filename}  "))
            out.append((
                "class:md.box",
                "[ DOWNLOAD ]",
                lambda event, c=code, lang=language: code_block_mouse(
                    c, lang, "download", event
                )
            ))
            out.append(("class:md.box", " ─╮"))
            cursor = match.end()
            continue

        # Normal controls for every other language.
        is_large = len(code) >= 3500 or code.count("\n") >= 70
        label = f"\n╭─ {filename} · .{ext} " + (
            "· PROJECT BUNDLE ─╮\n" if is_large else "─╮\n"
        )

        out.append(("class:md.box", label))
        out.append((
            "class:md.box",
            "[ COPY CODE ]",
            lambda event, c=code, lang=language: code_block_mouse(
                c, lang, "copy", event
            )
        ))
        out.append(("class:help.text", " "))

        download_label = (
            "[ DOWNLOAD FOLDER ]" if is_large else "[ DOWNLOAD CODE ]"
        )
        out.append((
            "class:md.box",
            download_label,
            lambda event, c=code, lang=language: code_block_mouse(
                c, lang, "download", event
            )
        ))

        out.extend(markdown_fragments(match.group(0)))
        cursor = match.end()

    if cursor < len(value):
        out.extend(markdown_fragments(value[cursor:]))

    return out

def latest_assistant_message():
    """Return the latest completed assistant message, excluding user prompts and metadata headings."""
    if busy or file_download_busy:
        return ""
    pattern = re.compile(r"(?m)^\*\*((?!You(?:\s|:))[^*\n]+? — [^*\n]+):\*\*")
    matches = list(pattern.finditer(response_text))
    if not matches:
        return ""
    return response_text[matches[-1].end():].strip()


def markdown_with_message_controls(value):
    """Keep message actions in the fixed action row, never inside the scrolling transcript."""
    return markdown_with_code_controls(value, show_controls=not (busy or file_download_busy))


# ----------------------------- Browser interface -------------------------
LEGACY_HTML_MARKERS = (
    "LOCAL RESEARCH TERMINAL · WEB COMPANION",
    "font:15px Segoe UI,Arial,sans-serif",
)


def resolve_web_html_path():
    """Find an existing user page or the bundled polished fallback; never create/overwrite index.html."""
    app_dir = Path(__file__).resolve().parent
    user_page = app_dir / "index.html"
    if user_page.is_file():
        try:
            preview = user_page.read_text(encoding="utf-8", errors="replace")[:50000]
            is_old_generated_page = all(marker in preview for marker in LEGACY_HTML_MARKERS)
            if not is_old_generated_page:
                return user_page
        except OSError:
            pass
    bundled_page = app_dir / "web" / "sharkbyte-polished.html"
    if bundled_page.is_file():
        return bundled_page
    return None


def missing_web_page_html():
    return ("<!doctype html><html><head><meta charset='utf-8'><title>SharkByte page missing</title>"
            "<style>body{font:16px system-ui;background:#0b1220;color:#eef;padding:40px;max-width:760px;margin:auto}"
            "code{color:#7cf}</style></head><body><h1>SharkByte web page not found</h1>"
            "<p>SharkByte did not create or replace an index.html. Put your polished index.html beside app.py, "
            "or restore the bundled web/sharkbyte-polished.html file.</p></body></html>").encode("utf-8")


def api_keys_path():
    return data_root() / "api_keys.json"


def load_api_keys():
    try:
        value = json.loads(api_keys_path().read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError, TypeError):
        return []


def save_api_keys(keys):
    target = api_keys_path()
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(keys, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(target)
    try:
        if os.name != "nt": target.chmod(0o600)
    except OSError:
        pass


def api_key_manager(value):
    """Manage bearer keys for the loopback-only SharkByte API."""
    parts = value.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else "list"
    argument = parts[1].strip() if len(parts) > 1 else ""
    keys = load_api_keys()
    if action in ("add", "new", "create"):
        start_action("api-key-add-name")
    elif action in ("list", "show"):
        if not keys:
            add_notice("No API keys yet. Choose API KEYS in the menu and enter: add")
        else:
            api_base = f"http://127.0.0.1:{web_server_port}/api/v1" if web_server_port else "Local API server is not running."
            lines = [f"LOCALHOST API KEYS · API BASE: {api_base}", "Full values are shown only inside this local terminal:"]
            for item in keys:
                lines.append(f"{item.get('name','Unnamed')} | {item.get('key','')} | created {item.get('created','unknown')}")
            add_notice("\n".join(lines))
    elif action in ("delete", "remove"):
        if not argument:
            add_notice("Usage: delete KEY_NAME")
        else:
            kept = [k for k in keys if k.get("name", "").casefold() != argument.casefold()]
            if len(kept) == len(keys): add_notice(f"No key named {argument!r} was found.")
            else:
                save_api_keys(kept); add_notice(f"Deleted API key {argument!r}.")
    elif action == "rename":
        pair = [x.strip() for x in argument.split("|", 1)]
        if len(pair) != 2 or not all(pair):
            add_notice("Usage: rename OLD_NAME | NEW_NAME")
        else:
            found = next((k for k in keys if k.get("name", "").casefold() == pair[0].casefold()), None)
            if not found: add_notice(f"No key named {pair[0]!r} was found.")
            elif any(k.get("name", "").casefold() == pair[1].casefold() for k in keys): add_notice("That key name is already in use.")
            else:
                found["name"] = pair[1]; save_api_keys(keys); add_notice(f"Renamed API key to {pair[1]!r}.")
    else:
        add_notice("API key commands: add · list · delete NAME · rename OLD | NEW")


def create_named_api_key(name):
    name = name.strip()
    if not name:
        add_notice("A key name is required. No key was created."); return
    keys = load_api_keys()
    if any(k.get("name", "").casefold() == name.casefold() for k in keys):
        add_notice("That key name already exists. Choose a different name."); return
    key = "sb_local_" + secrets.token_urlsafe(32)
    keys.append({"name": name, "key": key, "created": datetime.now().astimezone().isoformat(timespec="seconds")})
    save_api_keys(keys)
    base = f"http://127.0.0.1:{web_server_port}/api/v1" if web_server_port else "Start SharkByte's local server by opening the web interface once."
    add_notice(f"API key created for localhost: {name}\n\n{key}\n\nAPI base: {base}\nKeep this key private. It is stored in your local SharkByte data folder and works only while SharkByte is running.")


def edit_file_begin(path_text):
    global pending_edit_path
    raw = path_text.strip().strip('"')
    if not raw:
        add_notice("Enter the full path of the file you want SharkByte to edit."); return
    path = Path(raw).expanduser()
    try:
        path = path.resolve(strict=True)
        if not path.is_file(): raise ValueError("That path is not a regular file.")
        if path.stat().st_size > 1_000_000: raise ValueError("For safety, Edit File currently accepts files up to 1 MB.")
        path.read_text(encoding="utf-8")
    except Exception as exc:
        add_notice(f"Cannot open that file as editable UTF-8 text: {exc}"); return
    pending_edit_path = path
    start_action("edit-file-instruction")


def edit_file_generate(instruction):
    global pending_edit_path, pending_edit_content, pending_edit_instruction
    if not pending_edit_path or not instruction.strip():
        add_notice("No file or edit instruction was provided. Nothing was changed."); return
    try:
        original = pending_edit_path.read_text(encoding="utf-8")
        prompt = (
            "You are editing a user-selected local source file. Apply the requested changes while preserving "
            "unrelated behavior. Return the COMPLETE updated file contents only, with no Markdown fences or commentary.\n\n"
            f"FILE: {pending_edit_path.name}\nREQUEST: {instruction.strip()}\n\nCURRENT FILE CONTENTS:\n{original}"
        )
        response = requests.post(f"{OLLAMA_URL}/api/chat", json={
            "model": CODE_MODEL, "stream": False, "messages": [
                {"role":"system","content":"You are a careful code editor. Return only the complete replacement file text."},
                {"role":"user","content":prompt}], "options":{"temperature":0.2}}, timeout=(15, 300))
        response.raise_for_status()
        proposed = str(response.json().get("message", {}).get("content", ""))
        proposed = re.sub(r"^```[^\n]*\n|\n```\s*$", "", proposed.strip())
        if not proposed:
            raise RuntimeError("The model returned an empty edit.")
        pending_edit_content = proposed
        pending_edit_instruction = instruction.strip()
        preview = proposed[:1800] + ("\n… preview shortened …" if len(proposed) > 1800 else "")
        add_notice(f"EDIT PROPOSAL — {pending_edit_path}\nRequested: {instruction.strip()}\n\n{preview}\n\n No file has been changed. Type WRITE to approve saving this edit, or CANCEL")
        start_action("edit-file-confirm")
    except Exception as exc:
        pending_edit_path = None; pending_edit_content = None
        add_notice(f"Could not prepare the edit: {exc}. Your original file was not changed.")


def edit_file_confirm(value):
    global pending_edit_path, pending_edit_content, pending_edit_instruction
    if value.strip().upper() != "WRITE":
        add_notice("Edit cancelled. The original file was not changed.")
    elif not pending_edit_path or pending_edit_content is None:
        add_notice("There is no pending file edit to approve.")
    else:
        try:
            backup = pending_edit_path.with_name(pending_edit_path.name + ".bak")
            if not backup.exists(): shutil.copy2(pending_edit_path, backup)
            temp = pending_edit_path.with_name(pending_edit_path.name + ".sharkbyte.tmp")
            temp.write_text(pending_edit_content, encoding="utf-8")
            temp.replace(pending_edit_path)
            add_notice(f"Approved and saved: {pending_edit_path}\nBackup: {backup}")
        except Exception as exc:
            add_notice(f"Could not write the approved edit: {exc}")
    pending_edit_path = None; pending_edit_content = None; pending_edit_instruction = None


def ensure_web_server():
    global web_server, web_server_port
    if web_server:
        return f"http://127.0.0.1:{web_server_port}/"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def _json(self, obj, status=200):
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw))); self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers(); self.wfile.write(raw)
        def _authorized_api(self):
            if not self.client_address or self.client_address[0] not in ("127.0.0.1", "::1"):
                return False
            supplied = self.headers.get("Authorization", "")
            if not supplied.startswith("Bearer "): return False
            token = supplied[7:].strip()
            return any(secrets.compare_digest(token, str(item.get("key", ""))) for item in load_api_keys())
        def do_GET(self):
            if self.path.startswith("/api/v1/"):
                if not self._authorized_api(): self._json({"error":"unauthorized"}, 401); return
                if self.path == "/api/v1/models":
                    self._json({"models": installed_ollama_models(), "host":"127.0.0.1"})
                else: self._json({"error":"not found"}, 404)
            elif self.path.startswith("/api/state"):
                refresh_agents_from_disk()
                self._json({"transcript": response_text, "selected_model": selected_model,
                            "selected_mode": selected_mode, "busy": busy,
                            "download_busy": model_pull_busy, "download_status": download_status,
                            "models": installed_models_cache, "catalog": (download_choices or MODEL_CATALOG),
                            "catalog_loading": catalog_loading,
                            "agents": [{**{k:a.get(k) for k in ("id","name","goal","status","running")},
                                        "latest_update": (a.get("updates") or [""])[-1],
                                        "next_focus": a.get("next_focus", ""),
                                        "activity": (a.get("activity") or [])[-8:],
                                        "messages": (a.get("messages") or [])[-6:],
                                        "sources": (a.get("sources") or [])[:8]} for a in agents]})
            elif self.path in ("/api/ollama/tags", "/api/ollama/ps"):
                endpoint = self.path.rsplit("/", 1)[-1]
                try:
                    upstream = requests.get(f"{OLLAMA_URL}/api/{endpoint}", timeout=(5, 20))
                    upstream.raise_for_status()
                    self._json(upstream.json())
                except Exception as exc:
                    self._json({"error": f"Could not reach local Ollama: {exc}"}, 503)
            elif self.path in ("/", "/index.html"):
                page_path = resolve_web_html_path()
                if page_path is not None:
                    raw = page_path.read_bytes()
                    status = 200
                else:
                    raw = missing_web_page_html()
                    status = 404
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            else:
                self._json({"error": "not found"}, 404)
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0")); payload = {}
            try: payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception: pass
            if self.path.startswith("/api/v1/"):
                if not self._authorized_api(): self._json({"error":"unauthorized"}, 401); return
                if self.path == "/api/v1/chat":
                    try:
                        model = str(payload.get("model") or selected_model)
                        messages = payload.get("messages")
                        if not isinstance(messages, list) or not messages:
                            raise ValueError("messages must be a non-empty array")
                        upstream = requests.post(f"{OLLAMA_URL}/api/chat", json={"model":model,"messages":messages,"stream":False}, timeout=(10,300))
                        upstream.raise_for_status(); self._json(upstream.json())
                    except Exception as exc: self._json({"error":str(exc)}, 502)
                else: self._json({"error":"not found"}, 404)
            elif self.path == "/api/ollama/chat":
                upstream = None
                headers_sent = False
                try:
                    upstream = requests.post(f"{OLLAMA_URL}/api/chat", json=payload,
                                             stream=True, timeout=(10, 180))
                    if not upstream.ok:
                        raw = upstream.content
                        self.send_response(upstream.status_code)
                        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    headers_sent = True
                    self.close_connection = True
                    for line in upstream.iter_lines():
                        if line:
                            self.wfile.write(line + b"\n")
                            self.wfile.flush()
                except (requests.RequestException, OSError) as exc:
                    if not headers_sent:
                        self._json({"error": f"Local Ollama request failed: {exc}"}, 502)
                finally:
                    if upstream is not None:
                        upstream.close()
            elif self.path == "/api/command":
                with web_commands_lock: web_commands.append(str(payload.get("text", "")))
                self._json({"queued": True})
            elif self.path == "/api/upload":
                try:
                    filename = Path(str(payload.get("filename", "upload.txt"))).name
                    encoded = str(payload.get("content_base64", ""))
                    if len(encoded) > 14 * 1024 * 1024:
                        raise ValueError("Files must be 10 MB or smaller.")
                    import base64
                    raw = base64.b64decode(encoded, validate=True)
                    attach_uploaded_file(filename, raw)
                    self._json({"uploaded": True, "message": f"Attached {filename}. Ask SharkByte about it in your next message."})
                except Exception as exc:
                    self._json({"error": str(exc)}, 400)
            elif self.path == "/api/focus-terminal":
                focus_terminal_window(); self._json({"requested": True})
            else: self._json({"error":"not found"}, 404)
    web_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    web_server_port = web_server.server_address[1]
    threading.Thread(target=web_server.serve_forever, daemon=True).start()
    refresh_model_catalog_async()
    return f"http://127.0.0.1:{web_server_port}/"


def focus_terminal_window():
    if os.name == "nt":
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd: ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception: pass


def open_web_interface():
    url = ensure_web_server().rstrip("/") + "/index.html"
    opened = webbrowser.open_new_tab(url)
    page_path = resolve_web_html_path()
    page_label = str(page_path) if page_path else "No index.html found (nothing was created)."
    add_notice(f"**WEB INTERFACE:** {url} · Serving: `{page_label}`" + ("" if opened else " · If the tab did not open, copy the URL into your browser."))


# --------------------------- Embedded agent worker -------------------------
def agent_worker_ollama(messages, deep=False, max_tokens=500):
    body = {"model": AGENT_COORDINATOR_MODEL if deep else CHAT_MODEL, "messages": messages,
            "stream": False, "think": bool(deep),
            "options": {"temperature": 0.25, "num_predict": 1800 if deep else max_tokens,
                        "num_ctx": 8192 if deep else 4096}}
    with ollama_generation_lock(timeout=900, priority=10):
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=(10, 240 if deep else 150))
        r.raise_for_status(); data = r.json().get("message", {})
    answer = data.get("content", "")
    if not answer and deep:
        body["think"] = False
        with ollama_generation_lock(timeout=900, priority=10):
            r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=(10, 180)); r.raise_for_status()
            answer = r.json().get("message", {}).get("content", "")
    return answer or "The model returned no visible summary."


def run_agent_worker(agent_dir):
    folder = Path(agent_dir); config_path = folder / "config.json"; state_path = folder / "state.json"
    try: config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception: return
    try: state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception: state = {}
    state.update({"running": True, "pid": os.getpid(), "worker_version": 3, "status": "Starting research team"})
    state.setdefault("updates", []); state.setdefault("messages", []); state.setdefault("sources", []); state.setdefault("activity", [])
    save_lock = threading.RLock()
    try: ensure_ollama()
    except Exception as exc:
        state.update({"running": False, "status": f"Ollama unavailable: {exc}"})
        atomic_json(state_path, state); return
    def save(status=None, activity=None):
        with save_lock:
            if status: state["status"] = status
            if activity:
                state["activity"].append(f"{datetime.now():%H:%M:%S} · {activity}")
                state["activity"] = state["activity"][-80:]
            atomic_json(state_path, state)
    def stopped(): return (folder / "stop").exists()
    def read_instructions():
        inbox = folder / "inbox"; inbox.mkdir(exist_ok=True)
        notes = []
        for item in sorted(inbox.glob("*.json")):
            try:
                msg = json.loads(item.read_text(encoding="utf-8")).get("text", "").strip()
                if msg:
                    notes.append(msg); state["messages"].append({"role":"user","content":msg})
            except Exception: pass
            try: item.unlink(missing_ok=True)
            except Exception: pass
        return notes
    while not stopped():
        try:
            instructions = read_instructions()
            if instructions:
                config["goal"] = config.get("goal", "") + "\nUSER INSTRUCTIONS / CHANGES: " + "\n".join(instructions)
                atomic_json(config_path, config)
                state["next_focus"] = ""
                save("Applying new instructions", "Received new user direction: " + " | ".join(instructions))
            goal = config.get("goal", "")
            focus = state.get("next_focus") or goal
            subgoals = [
                f"{focus} — related reporting, origin, timeline, and context (preserve the exact topic)",
                f"{focus} — articles, primary posts, credible coverage, and real-world examples",
                f"{focus} — related trends, community reactions, competing interpretations, and recent developments",
            ]
            reports = []; all_sources = []
            # Let all three browser researchers work at once. Local Ollama generations remain
            # serialized by ollama_generation_lock to avoid exhausting the user's RAM.
            search_results = {}
            cycle_cancel = threading.Event()
            def run_mini_search(index, subgoal):
                if stopped():
                    return index, subgoal, [], "Stopped before search started."
                save(f"Mini-agent {index}/3 researching", f"Mini-agent {index} started: {subgoal[:140]}")
                def search_progress(message):
                    inbox_path = folder / "inbox"
                    if stopped() or any(inbox_path.glob("*.json")):
                        cycle_cancel.set()
                    save(activity=message)
                try:
                    found = browser_search_and_read(
                        subgoal, search_progress, cycle_cancel,
                        max_results=8, total_timeout=90
                    )
                    return index, subgoal, found or [], ""
                except Exception as exc:
                    return index, subgoal, [], f"This mini-agent's web search failed: {str(exc)[:400]}"
            with ThreadPoolExecutor(max_workers=3) as search_pool:
                futures = [search_pool.submit(run_mini_search, i, subgoal)
                           for i, subgoal in enumerate(subgoals, 1)]
                for future in as_completed(futures):
                    if stopped():
                        break
                    index, subgoal, found, search_error = future.result()
                    search_results[index] = (subgoal, found, search_error)
            if stopped():
                break
            # If the user sent a new direction while Chromium was researching, cancel the
            # current search batch and restart promptly with the updated goal.
            pending_directions = read_instructions()
            if pending_directions:
                config["goal"] = config.get("goal", "") + "\nUSER INSTRUCTIONS / CHANGES: " + "\n".join(pending_directions)
                atomic_json(config_path, config)
                state["next_focus"] = ""
                save("Restarting with your new instructions", "Cancelled the previous search batch to apply new direction")
                continue
            restart_cycle = False
            for i in range(1, 4):
                if stopped(): break
                # Pull instructions between mini-agents instead of making users wait for
                # an entire three-agent/coordinator cycle before their direction is noticed.
                new_instructions = read_instructions()
                if new_instructions:
                    config["goal"] = config.get("goal", "") + "\nUSER INSTRUCTIONS / CHANGES: " + "\n".join(new_instructions)
                    atomic_json(config_path, config)
                    state["next_focus"] = ""
                    save("Restarting with your new instructions", "Received new user direction; restarting this research cycle")
                    restart_cycle = True
                    break
                subgoal, found, search_error = search_results.get(i, (subgoals[i-1], [], "Search did not finish."))
                found = filter_search_results(found or [])
                all_sources.extend(found or [])
                source_text = "\n\n".join(f"{j+1}. {x.get('title','Source')}\nURL: {x.get('url','')}\n{x.get('excerpt','')[:1500]}" for j,x in enumerate(found or []))
                if not source_text: source_text = search_error or "No readable web pages were returned. Do not invent evidence; report this limitation."
                save(f"Mini-agent {i}/3 analyzing", f"Mini-agent {i} is analyzing {len(found or [])} pages; waiting only for its Ollama turn")
                report = agent_worker_ollama([
                    {"role":"system","content":"You are a specialist research agent. Analyze your assigned slice of the user's actual topic, not isolated keywords. Prefer articles, reporting, examples, primary posts, and related context. Avoid generic definitions unless directly requested. Cite supplied URLs; never invent evidence."},
                    {"role":"user","content":f"Assigned research slice: {subgoal}\nOverall goal: {goal}\n\nWeb pages read:\n{source_text}\n\nReturn concise findings, supporting evidence, related context, uncertainty, and the most useful remaining gap."}
                ], deep=False, max_tokens=560)
                reports.append({"mini_agent": f"Mini-agent {i}", "task": subgoal, "report": report,
                                "sources": [{"title":x.get("title",""),"url":x.get("url","")} for x in (found or [])]})
                save(f"Mini-agent {i}/3 reported to coordinator", f"Mini-agent {i} sent its report to the max-depth coordinator")
            if restart_cycle:
                continue
            if stopped(): break
            pending_directions = read_instructions()
            if pending_directions:
                config["goal"] = config.get("goal", "") + "\nUSER INSTRUCTIONS / CHANGES: " + "\n".join(pending_directions)
                atomic_json(config_path, config)
                state["next_focus"] = ""
                save("Restarting with your new instructions", "Applied new direction before coordinator synthesis")
                continue
            save("Max-depth coordinator analyzing all mini-agent reports", "Coordinator received all three specialist reports")
            combined = "\n\n".join(f"{r['mini_agent']} TASK: {r['task']}\nREPORT:\n{r['report']}\nSOURCES: {r['sources']}" for r in reports)
            previous = "\n\n".join(state.get("updates", [])[-2:])
            final = agent_worker_ollama([
                {"role":"system","content":f"You are {config.get('name','SharkByte Agent')}, the MAX-DEPTH research coordinator. Synthesize the mini-agents' reports, check for contradictions, identify unsupported claims, and decide the most useful next research direction. Never invent a source. Use Markdown and cite exact URLs supplied by the mini-agents."},
                {"role":"user","content":f"Overall research goal: {goal}\n\nCurrent research focus: {focus}\n\nPrevious coordinator findings:\n{previous}\n\nThree mini-agent reports:\n{combined}\n\nProduce a detailed, structured research update with findings, disagreements, confidence, remaining gaps, and the next research focus. At the very end include exactly one machine-readable line: NEXT_RESEARCH_FOCUS: <one specific unresolved question or new direction for the next cycle>. Do not repeat the same focus if it has already been covered."}
            ], deep=True)
            if stopped(): break
            state["updates"].append(final); state["updates"] = state["updates"][-30:]
            next_match = re.search(r"(?im)^NEXT_RESEARCH_FOCUS:\s*(.+)$", final)
            state["next_focus"] = next_match.group(1).strip()[:500] if next_match else ""
            state["sources"] = all_sources[:30]
            state["messages"].append({"role":"assistant","content":final})
            state["last_run"] = time.time()
            save("Research cycle complete · pausing before next cycle", "Coordinator completed a full synthesis")
            # Yield Ollama to interactive requests and avoid continuous background crawling.
            for _ in range(AGENT_INTERVAL_SECONDS * 2):
                if stopped() or any((folder / "inbox").glob("*.json")):
                    break
                time.sleep(0.5)
        except Exception as exc:
            save(f"Research error: {str(exc)[:160]}", f"Cycle failed: {exc}")
            # Brief backoff only after an actual error; normal cycles are never delayed.
            for _ in range(5):
                if stopped(): break
                time.sleep(0.1)
    state["running"] = False; state["status"] = "Stopped"
    atomic_json(state_path, state)
    try: (folder / "stop").unlink(missing_ok=True)
    except Exception: pass


# --------------------------------- Agents ---------------------------------
def get_agent(agent_id):
    with state_lock:
        return next((a for a in agents if a.get("id") == agent_id), None)


def create_agent(goal):
    global selected_agent_id
    goal = goal.strip()
    if not goal:
        add_notice("Give the agent a goal, e.g. /agent research lightweight local coding models.")
        return
    refresh_agents_from_disk()
    with state_lock:
        active = sum(1 for a in agents if a.get("running"))
        if active >= MAX_ACTIVE_AGENTS:
            add_notice(f"You already have {MAX_ACTIVE_AGENTS} active agents. Stop one before creating another.")
            return
        agent_id = uuid.uuid4().hex[:8]
        folder = agent_folder(agent_id)
        existing_names = [a.get("name", "") for a in agents]
        name = f"Agent-{len(existing_names) + 1}"
        config = {"id": agent_id, "name": name, "goal": goal, "temperature": float(temperature)}
        now = time.time()
        state = {"id": agent_id, "name": name, "goal": goal, "status": "Launching hidden worker",
                 "running": True, "updates": [], "messages": [], "sources": [], "activity": [],
                 "last_run": None, "created": now, "pid": None, "worker_version": 3}
        atomic_json(folder / "config.json", config)
        atomic_json(folder / "state.json", state)
        selected_agent_id = agent_id
    try:
        worker_pid = launch_agent_process(folder)
        latest_state = read_agent_state(folder) or state
        latest_state["pid"] = worker_pid
        atomic_json(folder / "state.json", latest_state)
    except Exception as exc:
        state["running"] = False; state["status"] = f"Launch failed: {exc}"
        atomic_json(folder / "state.json", state)
        refresh_agents_from_disk()
        add_notice(f"Couldn't launch {name}: {exc}")
        return
    set_mode("agents")
    refresh_agents_from_disk()
    add_notice(f"Deployed {name}. Its detached hidden worker process continues after the SharkByte UI closes.")


def stop_agent(agent_id):
    agent = get_agent(agent_id)
    if not agent:
        add_notice("Agent not found."); return
    folder = Path(agent.get("folder") or agent_folder(agent_id))
    try:
        (folder / "stop").write_text("stop", encoding="utf-8")
        agent["status"] = "Stopping"; agent["running"] = False
    except OSError as exc:
        add_notice(f"Couldn't stop agent: {exc}")
    if app: app.invalidate()


def delete_agent(agent_id):
    """Stop and permanently remove the selected agent's local state and history."""
    global selected_agent_id
    agent = get_agent(agent_id)
    if not agent:
        add_notice("Agent not found."); return
    folder = Path(agent.get("folder") or agent_folder(agent_id))
    try: (folder / "stop").write_text("stop", encoding="utf-8")
    except OSError: pass
    terminate_agent_pid(agent.get("pid"))
    try:
        shutil.rmtree(folder, ignore_errors=False)
        if selected_agent_id == agent_id: selected_agent_id = None
        refresh_agents_from_disk()
        add_notice(f"Deleted **{agent.get('name', agent_id)}** and its saved agent data.")
    except OSError as exc:
        add_notice(f"Couldn't fully delete agent data: {exc}")
    if app: app.invalidate()


def agent_chat(agent_id, message):
    agent = get_agent(agent_id)
    if not agent:
        add_notice("Select an agent first."); return
    folder = Path(agent.get("folder") or agent_folder(agent_id))
    inbox = folder / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {"text": message, "created": time.time()}
    try:
        atomic_json(inbox / f"{time.time_ns()}.json", payload)
        add_notice(f"**You:** [Instruction to {agent.get('name', agent_id)}] {message}")
        add_notice(f"Sent instruction to **{agent.get('name', agent_id)}**. It will apply the change in its next research cycle.")
    except OSError as exc:
        add_notice(f"Couldn't send message to agent: {exc}")


def stop_all_agents():
    # Deliberately do not stop detached agents when the UI exits. Users stop them
    # explicitly from the Agents page or with /stop-agent.
    return


def save_chat():
    content = response_text.strip()
    if not content:
        add_notice("There is no conversation to save yet."); return
    persist_active_chat()
    folder = data_root() / "saved_chats"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = folder / f"SharkByte_chat_{stamp}.md"
    try:
        path.write_text(content + "\n", encoding="utf-8")
        add_notice(f"Chat saved to: {path}")
    except OSError as exc:
        add_notice(f"Couldn't save chat: {exc}")


# ------------------------- File / software downloads ----------------------
DOWNLOAD_EXTENSIONS = (
    ".msi", ".exe", ".msix", ".appx", ".zip", ".7z", ".rar", ".tar.gz",
    ".tgz", ".dmg", ".pkg", ".deb", ".rpm", ".appimage", ".whl", ".jar",
)


def _safe_download_name(name):
    name = Path(str(name or "download.bin").replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._()+ -]+", "_", name).strip(" .")
    return name[:180] or "download.bin"


def _download_from_url(url, progress=None):
    """Download one explicitly selected HTTPS file URL into SharkByte's downloads folder."""
    parsed = urllib.parse.urlparse(str(url).strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Downloads require a direct HTTPS URL.")
    if not is_allowed_search_url(url):
        raise ValueError("This URL belongs to a blocked social-media domain.")
    folder = data_root() / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, stream=True, allow_redirects=True,
                           headers={"User-Agent": "SharkByte-Downloader/1.0"}, timeout=(15, 90))
    response.raise_for_status()
    final_url = response.url
    final_parsed = urllib.parse.urlparse(final_url)
    if final_parsed.scheme.lower() != "https" or not final_parsed.hostname or not is_allowed_search_url(final_url):
        response.close()
        raise ValueError("The download redirected to a non-HTTPS or blocked social-media address; it was blocked.")
    disposition = response.headers.get("Content-Disposition", "")
    filename = ""
    match = re.search(r"filename\*=UTF-8''([^;]+)|filename=[\"']?([^;\"']+)", disposition, re.I)
    if match:
        filename = urllib.parse.unquote(match.group(1) or match.group(2) or "")
    if not filename:
        filename = urllib.parse.unquote(Path(final_parsed.path).name)
    filename = _safe_download_name(filename)
    if filename.lower() in ("", "index.html", "", "."):
        response.close()
        raise ValueError("The URL returned a web page rather than a downloadable file.")
    total = int(response.headers.get("Content-Length", "0") or 0)
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" in content_type and Path(filename).suffix.lower() not in (".html", ".htm"):
        response.close()
        raise ValueError("The server returned a web page, not a file download.")
    if total > 1024 * 1024 * 1024:
        response.close()
        raise ValueError("The file is larger than SharkByte's 1 GB download limit.")
    destination = folder / filename
    if destination.exists():
        destination = folder / f"{destination.stem}_{datetime.now():%Y%m%d_%H%M%S}{destination.suffix}"
    temporary = destination.with_name(destination.name + ".part")
    downloaded = 0
    last_report = -1
    try:
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > 1024 * 1024 * 1024:
                    raise ValueError("The file exceeded SharkByte's 1 GB download limit.")
                handle.write(chunk)
                if progress and total:
                    pct = min(100, int(downloaded * 100 / total))
                    if pct >= last_report + 10:
                        progress(f"Downloading {filename}: {pct}%")
                        last_report = pct
        if downloaded <= 0:
            raise ValueError("The server returned an empty file.")
        temporary.replace(destination)
    except Exception:
        try: temporary.unlink(missing_ok=True)
        except OSError: pass
        raise
    finally:
        response.close()
    return destination


def _winget_search_package(query):
    winget = shutil.which("winget")
    if not winget or os.name != "nt":
        return None
    result = subprocess.run(
        [winget, "search", "--query", query, "--source", "winget",
         "--accept-source-agreements", "--disable-interactivity"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "winget search failed")[-1200:])
    rows = []
    for line in result.stdout.splitlines():
        columns = re.split(r"\s{2,}", line.strip())
        if len(columns) < 3:
            continue
        name, package_id = columns[0], columns[1]
        if name.lower() == "name" or package_id.lower() == "id":
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{1,127}", package_id):
            continue
        low_name = name.lower()
        score = sum(3 if token == low_name else 1 for token in re.findall(r"[a-z0-9]+", query.lower()) if token in low_name)
        if query.lower().replace(" ", "") in low_name.replace(" ", ""):
            score += 10
        rows.append((score, name, package_id))
    if not rows:
        return None
    rows.sort(key=lambda item: (-item[0], item[1].lower()))
    # Prefer the Node.js LTS package for a generic Node.js request.
    if "node" in query.lower():
        lts = next((row for row in rows if row[2].lower() == "openjs.nodejs.lts"), None)
        if lts:
            return lts[1], lts[2]
    return rows[0][1], rows[0][2]


def _download_with_winget(query, progress):
    found = _winget_search_package(query)
    if not found:
        return None
    name, package_id = found
    if progress:
        progress(f"Found {name} ({package_id}) in the official winget source. Downloading installer…")
    winget = shutil.which("winget")
    folder = data_root() / "downloads" / re.sub(r"[^A-Za-z0-9._-]+", "_", package_id)
    folder.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [winget, "download", "--id", package_id, "--exact", "--source", "winget",
         "--download-directory", str(folder), "--accept-source-agreements",
         "--accept-package-agreements", "--disable-interactivity"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "winget download failed")[-1600:])
    files = [item for item in folder.rglob("*") if item.is_file() and not item.name.endswith(".part")]
    if not files:
        raise RuntimeError("winget reported success, but no installer file was found in the download folder.")
    installer_files = [item for item in files if item.suffix.lower() in
                       (".exe", ".msi", ".msix", ".appx", ".zip", ".7z", ".dmg", ".pkg", ".deb", ".rpm")]
    newest = max(installer_files or files, key=lambda item: item.stat().st_mtime)
    install_command = [winget, "install", "--id", package_id, "--exact", "--source", "winget",
                       "--accept-source-agreements", "--accept-package-agreements"]
    return {"path": str(newest), "package": name, "package_id": package_id,
            "install_command": install_command}


def _find_download_candidate(query, progress=None):
    """Find a candidate and return its identity/URL without downloading anything."""
    query = str(query).strip()
    if query.lower().startswith("http://"):
        raise ValueError("Only HTTPS download links are accepted. Use the vendor's HTTPS link.")
    if query.lower().startswith("https://"):
        parsed = urllib.parse.urlparse(query)
        if not parsed.hostname or not is_allowed_search_url(query):
            raise ValueError("That URL is invalid or belongs to a blocked domain.")
        return {"kind": "url", "url": query, "package": Path(parsed.path).name or query,
                "source": query}
    if progress: progress(f"Searching the web for an official download: {query}")
    results = filter_search_results(browser_search_and_read(
        f"{query} official download installer", (lambda msg: progress(f"Search: {msg}")) if progress else (lambda _msg: None),
        threading.Event(), max_results=12))
    if not results:
        raise RuntimeError("No allowed search results were found. Try a more specific product name or direct HTTPS file URL.")
    tokens = [x for x in re.findall(r"[a-z0-9]+", query.lower()) if len(x) > 1]
    def rank(item):
        title = str(item.get("title", "")).lower(); url = str(item.get("url", "")).lower()
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        score = sum(2 for token in tokens if token in title or token in host)
        if "official" in title: score += 8
        if "download" in title: score += 5
        if any(word in title for word in ("advertisement", "sponsored", "forum")): score -= 10
        return score
    results.sort(key=rank, reverse=True)
    for item in results[:8]:
        page_url = str(item.get("url", "")); parsed = urllib.parse.urlparse(page_url)
        if parsed.scheme != "https" or not parsed.hostname or not is_allowed_search_url(page_url): continue
        if any(parsed.path.lower().endswith(ext) for ext in DOWNLOAD_EXTENSIONS):
            return {"kind": "url", "url": page_url, "package": item.get("title", query), "source": page_url}
        try:
            page = requests.get(page_url, headers={"User-Agent": "Mozilla/5.0 SharkByte Downloader"},
                                timeout=(10, 25), allow_redirects=True)
            page.raise_for_status()
            if len(page.content) > 8 * 1024 * 1024: continue
            host = (urllib.parse.urlparse(page.url).hostname or "").lower()
            anchors = re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", page.text, re.I | re.S)
            candidates = []
            for href, label in anchors:
                candidate = urllib.parse.urljoin(page.url, html.unescape(href)); cp = urllib.parse.urlparse(candidate)
                chost = (cp.hostname or "").lower()
                if cp.scheme != "https" or not chost or not (chost == host or chost.endswith("." + host)): continue
                if any(cp.path.lower().endswith(ext) for ext in DOWNLOAD_EXTENSIONS):
                    label_text = re.sub(r"<[^>]+>", " ", html.unescape(label)).lower()
                    score = sum(2 for token in tokens if token in candidate.lower() or token in label_text)
                    if any(word in label_text for word in ("windows", "x64", "installer", "download", "lts")): score += 4
                    candidates.append((score, candidate))
            if candidates:
                candidates.sort(reverse=True)
                return {"kind": "url", "url": candidates[0][1], "package": item.get("title", query),
                        "source": page.url}
        except Exception:
            continue
    # Web search is always attempted first. If no direct vendor link can be resolved,
    # offer the official Windows package catalog as a fallback candidate (still no download).
    if os.name == "nt" and shutil.which("winget"):
        try:
            found = _winget_search_package(query)
            if found:
                name, package_id = found
                return {"kind": "winget", "package": name, "package_id": package_id,
                        "source": "Windows Package Manager (winget official source; web search found no direct file link)"}
        except Exception as exc:
            if progress: progress(f"winget fallback unavailable: {str(exc)[:300]}")
    if re.sub(r"[\s._-]+", "", query.lower()) in ("node", "nodejs") and os.name == "nt":
        response = requests.get("https://nodejs.org/dist/index.json", timeout=(10, 30),
                                headers={"User-Agent": "SharkByte-Downloader/1.0"})
        response.raise_for_status()
        release = next((item for item in response.json() if item.get("lts") and
                        re.fullmatch(r"v\d+\.\d+\.\d+", str(item.get("version", "")))), None)
        if release:
            version = release["version"]
            url = f"https://nodejs.org/dist/{version}/node-{version}-x64.msi"
            return {"kind": "url", "url": url, "package": f"Node.js {version} LTS (Windows x64)",
                    "source": "https://nodejs.org (official Node.js site)"}
    raise RuntimeError("Search found pages but no safe direct installer link. Open the official vendor page and provide its direct HTTPS file URL.")


def file_download_worker(query):
    global file_download_busy
    try:
        candidate = _find_download_candidate(query, lambda message: post_event("file_download_progress", message))
        post_event("file_download_found", candidate)
    except Exception as exc:
        post_event("file_download_error", str(exc))
    finally:
        file_download_busy = False


def confirmed_download_worker(candidate):
    global file_download_busy
    try:
        if candidate.get("kind") == "winget":
            # Search is repeated at confirmation time so a changed package identity is not trusted.
            found = _winget_search_package(candidate.get("package_id", ""))
            if not found or found[1].lower() != candidate.get("package_id", "").lower():
                raise RuntimeError("The winget package identity could not be re-verified; download cancelled.")
            result = _download_with_winget(candidate["package_id"], lambda msg: post_event("file_download_progress", msg))
        elif candidate.get("kind") == "url":
            path = _download_from_url(candidate["url"], lambda msg: post_event("file_download_progress", msg))
            suffix = Path(path).suffix.lower()
            # Provide a launch/install action for Windows installers, but never execute it
            # until the user separately confirms with /download run -> RUN.
            launch_command = ["__installer__", str(path)] if os.name == "nt" and suffix in (".exe", ".msi") else None
            result = {"path": str(path), "package": candidate.get("package") or Path(path).name,
                      "package_id": None, "install_command": launch_command}
        else:
            raise ValueError("Unknown download candidate type.")
        post_event("file_download_done", result)
    except Exception as exc:
        post_event("file_download_error", str(exc))
    finally:
        file_download_busy = False


def start_file_download(query):
    global file_download_busy
    query = str(query or "").strip()
    if not query:
        add_notice("Usage: `/download SOFTWARE NAME`, `/download https://.../file.msi`, or `/download run`.")
        return
    if query.lower() == "run":
        start_action("download-run"); return
    if query.lower() == "download":
        start_action("download-confirm"); return
    if file_download_busy:
        add_notice("A download search or transfer is already running. Wait for it to finish."); return
    file_download_busy = True
    add_notice(f"**DOWNLOAD SEARCH — {query}:** Searching first. SharkByte will ask before downloading anything…")
    threading.Thread(target=file_download_worker, args=(query,), daemon=True).start()


# ---------------------------- Request / actions ---------------------------
def clear_conversation(persist=True):
    global response_text, busy, thinking_inline, thinking_detected, thinking_text, thinking_expanded, thinking_anchor
    global request_id, active_cancel_event, response_scroll, response_auto_follow, active_chat_id, request_watchdog
    if persist and (response_text.strip() or history):
        persist_active_chat()
    active_cancel_event.set(); abort_active_ollama_request(); request_id += 1
    if request_watchdog:
        request_watchdog.cancel()
        request_watchdog = None
    busy = False; thinking_inline = False; thinking_detected = False
    thinking_text = ""; thinking_expanded = False; thinking_anchor = None
    response_text = ""
    response_scroll = 0; response_auto_follow = True
    with state_lock:
        history.clear()
    pending_uploads.clear()
    active_chat_id = uuid.uuid4().hex[:12]
    pending_action_reset()
    with events_lock:
        ui_events.clear()
    if app:
        app.invalidate()


def pending_action_reset():
    global pending_action
    pending_action = None
    input_area.prompt = "You ❯  "
    input_area.read_only = False
    input_area.text = ""


def set_mode(mode):
    global selected_mode, last_conversation_mode, menu_open, help_open, pending_action
    if mode in ("chat", "work", "coder", "deep-thinking", "casual"):
        last_conversation_mode = mode
    selected_mode = mode; menu_open = False; help_open = False; pending_action = None
    input_area.text = ""
    if mode == "help":
        input_area.prompt = "CLICK ENTER  "; input_area.read_only = True
    elif mode == "agents":
        input_area.prompt = "Agent instruction ❯  "; input_area.read_only = False
    elif mode == "models":
        refresh_model_choices()
        input_area.prompt = "Use ↑/↓ then Enter to select a model ❯  "; input_area.read_only = True
    elif mode == "download-models":
        global download_choices, download_index
        download_choices = list(MODEL_CATALOG); download_index = 0
        refresh_model_catalog_async()
        input_area.prompt = "Use ↑/↓ then Enter to download · or /download models MODEL ❯  "; input_area.read_only = True
    else:
        input_area.prompt = "You ❯  "; input_area.read_only = False
    if app:
        app.layout.focus(input_window); app.invalidate()


def add_notice(message):
    global response_text
    prepare_response_append()
    if response_text:
        response_text += "\n\n"
    response_text += message
    if app: app.invalidate()


def installed_ollama_models():
    """Return model names currently available in the local Ollama server."""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        response.raise_for_status()
        return [str(item.get("name", "")) for item in response.json().get("models", []) if item.get("name")]
    except Exception:
        return []




def arm_request_watchdog(event_id, cancel_token, seconds=255, model=None):
    """Stop a worker that outlives its recovery window and unload its model."""
    global request_watchdog
    if request_watchdog:
        request_watchdog.cancel()
    def expire():
        if busy and request_id == event_id:
            cancel_token.set()
            if model:
                threading.Thread(target=reset_ollama_model, args=(model,),
                                 name="SharkByte-watchdog-model-reset", daemon=True).start()
            post_event("timeout", f"This request exceeded {seconds} seconds, so SharkByte stopped it and reset the model. You can try again.", event_id)
    request_watchdog = threading.Timer(seconds, expire)
    request_watchdog.daemon = True
    request_watchdog.start()


def begin_request(text):
    global busy, response_text, thinking_inline, thinking_detected, thinking_text, thinking_expanded, thinking_anchor
    global request_id, active_cancel_event, selected_mode
    if ollama_resetting:
        add_notice("SharkByte is resetting the previous Ollama request. Try again in a moment.")
        return
    if file_download_busy:
        add_notice("A file download is running. Wait for it to finish before starting another AI response.")
        return
    if busy:
        add_notice("A request is already running. Press Ctrl-C to cancel it, or wait for it to finish.")
        return
    request_id += 1; this_id = request_id
    active_cancel_event = threading.Event(); token = active_cancel_event

    # Every mode has a fixed, explicit routing rule. Only Chat follows /models selection.
    mode_models = {
        "chat": selected_model,
        "work": WORK_MODEL,
        "coder": CODE_MODEL,
        "deep-thinking": CHAT_MODEL,
        "casual": CASUAL_MODEL,
    }
    if selected_mode not in mode_models:
        selected_mode = "chat"
    model = mode_models[selected_mode]
    if model not in installed_ollama_models():
        command = f"/download models {model}"
        add_notice(f"The {selected_mode.replace('-', ' ').title()} mode needs `{model}`, which is not installed. Run `{command}` first.")
        return

    global header_visible
    header_visible = False
    busy = True; thinking_inline = True; thinking_detected = False
    thinking_text = ""; thinking_expanded = False; thinking_anchor = None
    prepare_response_append()
    if response_text: response_text += "\n\n"
    response_text += f"**You:** {text}\n\n"
    if app:
        app.invalidate()

    deep_requested = selected_mode == "deep-thinking"
    request_prompt = text
    if pending_uploads:
        attached = list(pending_uploads)
        pending_uploads.clear()
        remaining_chars = 20000
        file_parts = []
        for item in attached:
            if remaining_chars <= 0: break
            snippet = item["text"][:remaining_chars]
            remaining_chars -= len(snippet)
            file_parts.append(f"ATTACHED FILE: {item['name']}\nLOCAL COPY: {item['path']}\nCONTENT:\n{snippet}")
        file_context = "\n\n".join(file_parts)
        request_prompt = text + "\n\nUse the following uploaded file content as source material when relevant.\n\n" + file_context
    instruction = None
    sys_snapshot = system_prompt
    if deep_requested:
        sys_snapshot = DEEP_THINKING_SYSTEM_PROMPT + "\n\nUser-configured system prompt: " + system_prompt
        instruction = (
            "Solve the actual question carefully. Break the task into relevant subproblems internally, "
            "check assumptions and edge cases, compare plausible explanations, and verify the final result. "
            "Do not output hidden chain-of-thought. Give the answer plus a concise, useful rationale."
        )
    elif selected_mode == "coder":
        instruction = (
            "You are in CODER mode and must use the Qwen Coder model. Prioritize correct, runnable code. "
            "Inspect the user's existing architecture and constraints, preserve working features, explain exact edits, "
            "and include complete code when requested. Check imports, syntax, edge cases, and compatibility. "
            "Do not stop immediately after presenting code: finish with how to run it, inspect the solution for mistakes, "
            "and include any remaining required files or setup steps. If the response hits its output limit, continue without repeating."
        )

    elif selected_mode == "chat":
        instruction = (
            "CHAT MODE ACCURACY RULES: Answer the user's actual question, not an imagined situation. "
            "Do not invent a meme meaning, origin, person, event, statistic, explanation, or source. "
            "For slang, memes, trends, and internet references, distinguish the established origin from "
            "the meaning people commonly give it; if the meaning is intentionally vague or disputed, say so. "
            "If the reference is ambiguous or you do not know it, say that plainly and ask for the missing context "
            "instead of filling the gap with a confident guess. Never create a 'Sources Used' list or cite websites "
            "unless those sources were actually retrieved during this request. Do not claim you searched or verified "
            "anything unless you actually did. Keep answers focused; do not pad them with fabricated breakdowns. "
            "Before sending factual claims, check that each claim follows from the conversation or reliable knowledge. "
            "When uncertain, be transparent rather than sounding certain."
        )

    elif selected_mode == "casual" or str(model).lower().startswith("gemma"):
        instruction = (
            "You are a friendly, natural conversational AI assistant. "
            "Respond directly to the user's actual message. "
            "For greetings, greet the user naturally. "
            "Never respond with status updates like 'Understood', 'Ready', "
            "'I am waiting for your request', or 'I am processing information'. "
            "Do not describe your internal state. Just have a normal conversation."
        )

    elif selected_mode == "work":
        instruction = "Work mode: be practical and concise. Focus on completing the task without extended reasoning."

    with state_lock:
        temp_snapshot = float(temperature)

    mode_label = {
        "chat": "Chat",
        "work": "Work",
        "coder": "Coder",
        "deep-thinking": "Deep Thinking",
        "casual": "Casual"
    }.get(selected_mode, selected_mode.title())

    response_text += f"**{mode_label} — {model} (temperature {temp_snapshot:g}):** "
    thinking_anchor = len(response_text)
    persist_active_chat()

    worker = threading.Thread(
        target=start_chat_worker,
        args=(request_prompt, model, instruction, this_id, token, deep_requested, True, sys_snapshot, temp_snapshot),
        daemon=True,
    )

    # Each Ollama attempt has a 240-second deadline and the worker retries once.
    # 510s is the outer guard for both attempts plus model unload/reload overhead.
    arm_request_watchdog(this_id, token, 510, model=model)
    worker.start(); app.invalidate()


def begin_search(query):
    global busy, response_text, thinking_inline, thinking_detected, thinking_text, thinking_expanded, thinking_anchor
    global request_id, active_cancel_event
    if file_download_busy:
        add_notice("A file download is running. Wait for it to finish before starting web research.")
        return
    if busy:
        add_notice("Wait for the current request, or press Ctrl-C to cancel it."); return
    request_id += 1; this_id = request_id
    active_cancel_event = threading.Event(); token = active_cancel_event
    busy = True; thinking_inline = True; thinking_detected = False
    thinking_text = ""; thinking_expanded = False; thinking_anchor = None
    prepare_response_append()
    if response_text: response_text += "\n\n"
    response_text += f"**You — Web research:** {query}\n\n"
    with state_lock:
        temp_snapshot = float(temperature)
        model_snapshot = selected_model
    response_text += f"**Model snapshot: {model_snapshot} · Temperature: {temp_snapshot:g}**\n\n"
    thinking_anchor = None
    arm_request_watchdog(this_id, token, 420, model=model_snapshot)
    threading.Thread(target=start_search_worker, args=(query, this_id, token, temp_snapshot, model_snapshot), daemon=True).start()
    app.invalidate()


def start_action(action):
    global pending_action
    pending_action = action; input_area.text = ""; input_area.read_only = False
    input_area.prompt = {"temperature": f"Temperature now {temperature:g} (enter 0–2) ❯  ",
                         "system-prompt": "New system prompt ❯  ",
                         "search": "Web search query ❯  ",
                         "agent-create": "New agent goal ❯  ",
                         "delete-all-chats": "Type DELETE ALL to confirm ❯  ",
                         "download-run": "Type RUN to launch the last install command ❯  ",
                         "download-confirm": "Type DOWNLOAD to confirm this exact file/package ❯  ",
                         "download-query": "Software name or direct HTTPS file URL ❯  ",
                         "api-keys": "API keys: add | list | delete NAME | rename OLD | NEW ❯  ",
                         "api-key-add-name": "Name for the new API key ❯  ",
                         "edit-file-path": "Full path of the file ❯  ",
                         "edit-file-instruction": "Describe your changes ❯  ",
                         "edit-file-confirm": "Type WRITE to approve ❯  "}.get(action, "Input ❯  ")
    app.layout.focus(input_window); app.invalidate()


def process_action(action, value=""):
    global temperature, system_prompt, slash_menu_enabled, show_reset_temperature_button, show_reset_system_prompt_button
    global last_install_command, pending_download_candidate, file_download_busy
    value = value.strip()
    if action == "api-keys": api_key_manager(value)
    elif action == "api-key-add-name": create_named_api_key(value)
    elif action == "edit-file-path": edit_file_begin(value)
    elif action == "edit-file-instruction": edit_file_generate(value)
    elif action == "edit-file-confirm": edit_file_confirm(value)
    elif action == "temperature":
        try:
            new = float(value)
            if not 0 <= new <= 2: raise ValueError
            with state_lock:
                temperature = new
            add_notice(f"Temperature set to {new:g}. New requests will use this value; active requests keep their original setting.")
        except ValueError:
            add_notice("Invalid temperature. Enter a number from 0 to 2.")
    elif action == "system-prompt":
        if not value: add_notice(f"Current system prompt: {system_prompt}")
        else:
            with state_lock:
                system_prompt = value; history.clear()
            add_notice("System prompt updated. New requests will use it; existing transcript is preserved.")
    elif action == "search":
        if value: begin_search(value)
        else: add_notice("Enter a Google search query.")
    elif action == "agent-create":
        create_agent(value)
    elif action == "agent-stop":
        if selected_agent_id:
            stop_agent(selected_agent_id)
        else:
            add_notice("Select an agent on the AGENTS page before stopping it.")
    elif action == "save-chat":
        save_chat()
    elif action == "delete-current-chat":
        delete_current_chat()
    elif action == "delete-all-chats":
        delete_all_chats(value.upper() == "DELETE ALL")
        if value.upper() != "DELETE ALL":
            add_notice("Confirmation did not match. No chats were deleted.")
    elif action == "download-query":
        if value: start_file_download(value)
        else: add_notice("Enter a software name or a direct HTTPS download URL.")
    elif action == "download-confirm":
        if value.upper() != "DOWNLOAD":
            add_notice("Confirmation did not match. Nothing was downloaded.")
        elif not pending_download_candidate:
            add_notice("There is no searched download candidate waiting for confirmation.")
        elif file_download_busy:
            add_notice("A download is already running.")
        else:
            candidate = dict(pending_download_candidate)
            pending_download_candidate = None
            file_download_busy = True
            add_notice(f"Confirmed. Downloading {candidate.get('package') or 'the selected file'}…")
            threading.Thread(target=confirmed_download_worker, args=(candidate,), daemon=True).start()
    elif action == "download-run":
        if value.upper() != "RUN":
            add_notice("Confirmation did not match. Nothing was executed.")
        elif not last_install_command or not isinstance(last_install_command, list) or not last_install_command:
            add_notice("There is no verified installer command waiting. Download an installer with `/download NAME` first.")
        elif last_install_command[0] == "__installer__" and len(last_install_command) == 2 and Path(last_install_command[1]).suffix.lower() in (".exe", ".msi") and Path(last_install_command[1]).is_file():
            try:
                path = last_install_command[1]
                command = ["msiexec.exe", "/i", path] if Path(path).suffix.lower() == ".msi" else [path]
                flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
                subprocess.Popen(command, shell=False, creationflags=flags)
                add_notice("Started the confirmed installer in a separate process. Follow its prompts; SharkByte did not run it before your confirmation.")
            except Exception as exc:
                add_notice(f"Could not start the installer: {exc}")
        elif Path(last_install_command[0]).name.lower() in ("winget.exe", "winget") and len(last_install_command) >= 4 and last_install_command[1] == "install":
            try:
                flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
                subprocess.Popen(last_install_command, shell=False, creationflags=flags,
                                 start_new_session=(os.name != "nt"))
                add_notice("Started the confirmed winget install command in a separate process. Follow any installer prompts that appear.")
            except Exception as exc:
                add_notice(f"Could not start the installer command: {exc}")
        else:
            add_notice("The saved install command failed SharkByte's safety check and was not executed.")
    elif action == "reset-temperature":
        with state_lock:
            temperature = DEFAULT_TEMPERATURE
        add_notice(f"Temperature reset to {DEFAULT_TEMPERATURE}. New requests will use the default; active requests are unchanged.")
    elif action == "reset-system-prompt":
        with state_lock:
            system_prompt = DEFAULT_SYSTEM_PROMPT
            history.clear()
        add_notice("System prompt reset to the SharkByte default. Existing transcript is preserved.")
    elif action == "toggle-slash-menu":
        slash_menu_enabled = not slash_menu_enabled
        add_notice(f"Typing / opens the menu: {'ON' if slash_menu_enabled else 'OFF'}.")
    elif action == "toggle-reset-temperature":
        show_reset_temperature_button = not show_reset_temperature_button
        add_notice(f"Reset Temperature button: {'ON' if show_reset_temperature_button else 'OFF'}.")
    elif action == "toggle-reset-system-prompt":
        show_reset_system_prompt_button = not show_reset_system_prompt_button
        add_notice(f"Reset System Prompt button: {'ON' if show_reset_system_prompt_button else 'OFF'}.")


def add_command_echo(command):
    global response_text, last_command
    label = command.strip()
    if not label: return
    last_command = label
    prepare_response_append()
    if response_text: response_text += "\n\n"
    response_text += f"COMMAND  {label}"
    if app: app.invalidate()


def model_page_fragments():
    lines = [f"LAST COMMAND: {last_command or '/models'}", "INSTALLED OLLAMA MODELS", f"CURRENT SELECTION: {selected_model}",
             "Use ↑/↓ to browse · Enter to select · /download models to install more", ""]
    if not model_choices:
        lines.append("No installed models found. Use /download models to install Qwen or Gemma.")
    first = max(0, min(model_index - 6, max(0, len(model_choices) - 13)))
    for i in range(first, min(len(model_choices), first + 13)):
        name = model_choices[i]
        prefix = "▶ " if i == model_index else "  "
        suffix = "  ← SELECTED" if name == selected_model else ""
        lines.append(prefix + name + suffix)
    if first: lines.append("↑ more models above")
    if first + 13 < len(model_choices): lines.append("↓ more models below")
    return boxed_fragments(lines, centered=False, min_width=76, padding=2, title="Model Selector")


def download_page_fragments():
    installed = set(installed_models_cache)
    lines = [f"LAST COMMAND: {last_command or '/download models'}", "OLLAMA MODEL CATALOG · QWEN AND GEMMA RECOMMENDED FIRST",
             "Use ↑/↓ then Enter to download. Large models may need 16–64+ GB RAM; check size before downloading.",
             "This is a broad curated catalog; /download models MODEL:TAG accepts any valid Ollama model tag.",
             ("DOWNLOAD: " + download_status) if download_status else "", ""]
    first = max(0, min(download_index - 6, max(0, len(download_choices) - 13)))
    for i in range(first, min(len(download_choices), first + 13)):
        name = download_choices[i]
        prefix = "▶ " if i == download_index else "  "
        suffix = "  [INSTALLED]" if name in installed else ""
        lines.append(prefix + name + suffix)
    if first: lines.append("↑ more models above")
    if first + 13 < len(download_choices): lines.append("↓ more models below")
    return boxed_fragments(lines, centered=False, min_width=76, padding=2, title="Download Models")


def current_menu_items():
    items = []
    for key, label, kind in MENU_ITEMS:
        if key == "reset-temperature" and not show_reset_temperature_button:
            continue
        if key == "reset-system-prompt" and not show_reset_system_prompt_button:
            continue
        if key == "toggle-slash-menu": label = f"SLASH OPENS MENU: {'ON' if slash_menu_enabled else 'OFF'}"
        elif key == "toggle-reset-temperature": label = f"RESET TEMPERATURE BUTTON: {'ON' if show_reset_temperature_button else 'OFF'}"
        elif key == "toggle-reset-system-prompt": label = f"RESET SYSTEM PROMPT BUTTON: {'ON' if show_reset_system_prompt_button else 'OFF'}"
        items.append((key, label, kind))
    items.insert(0, ("models", f"SELECTED MODEL: {selected_model}", "mode"))
    return items


def run_menu_item(item):
    key, _label, kind = item
    global menu_open
    menu_open = False
    command_names = {"chat":"/chat", "work":"/work", "models":"/models", "download-models":"/download models",
                     "coder":"/coder", "deep-thinking":"/deep-thinking", "casual":"/casual", "help":"/help",
                     "agents":"/agents", "open":"/open", "agent-create":"/agent", "agent-stop":"/stop-agent",
                     "save-chat":"/save-chat", "chats":"/chats", "new-chat":"/new chat",
                     "delete-current-chat":"/delete-chat", "delete-all-chats":"/delete-all-chats",
                     "upload":"/upload", "download":"/download",
                     "clear":"/clear", "quit":"/quit"}
    if key != "selected-model": add_command_echo(command_names.get(key, "/" + key))
    if kind == "mode":
        set_mode(key)
    elif key == "open":
        open_web_interface()
    elif key == "upload":
        upload_file_dialog()
    elif key == "edit-file":
        start_action("edit-file-path")
    elif key == "api-keys":
        start_action("api-keys")
    elif key == "download":
        start_action("download-query")
    elif key == "clear":
        clear_conversation()
        add_command_echo("/clear")
    elif key == "chats":
        list_saved_chats()
    elif key == "new-chat":
        new_chat()
    elif key == "quit":
        app.exit()
    elif key in ("temperature", "system-prompt", "search", "agent-create"):
        if selected_mode == "help": set_mode("chat")
        start_action(key)
    elif key == "agent-stop":
        process_action(key)
    elif kind == "toggle":
        process_action(key)
    elif key in ("save-chat", "reset-temperature", "reset-system-prompt", "delete-current-chat"):
        process_action(key)
    elif key == "delete-all-chats":
        start_action("delete-all-chats")
    if app: app.invalidate()


def handle_command(text):
    command, _, argument = text.partition(" "); argument = argument.strip()
    modes = {"/chat": "chat", "/work": "work", "/coder": "coder", "/deep-thinking": "deep-thinking",
             "/casual": "casual", "/embed": "casual", "/help": "help", "/agents": "agents"}
    if command in modes:
        set_mode(modes[command])
        if argument and modes[command] not in ("help", "agents"):
            begin_request(argument)
    elif command == "/quit":
        app.exit()
    elif command == "/temperature": process_action("temperature", argument) if argument else start_action("temperature")
    elif command == "/system-prompt": process_action("system-prompt", argument) if argument else add_notice(f"Current system prompt: {system_prompt}")
    elif command == "/search": begin_search(argument) if argument else start_action("search")
    elif command == "/clear":
        clear_conversation(); add_command_echo("/clear")
    elif command == "/save-chat": save_chat()
    elif command == "/new" and argument.lower() in ("", "chat"): new_chat()
    elif command == "/chats":
        if not argument:
            list_saved_chats()
        elif argument.lower() in ("delete", "delete current", "delete-current"):
            delete_current_chat()
        elif argument.lower() in ("delete all", "clear all"):
            delete_all_chats(False)
        else:
            load_saved_chat(argument)
    elif command in ("/delete-chat", "/delete-current-chat"):
        delete_current_chat()
    elif command == "/delete-all-chats":
        delete_all_chats(argument.lower() in ("yes", "confirm", "delete all"))
    elif command == "/upload": upload_file_dialog(argument)
    elif command == "/edit-file": edit_file_begin(argument) if argument else start_action("edit-file-path")
    elif command == "/api-keys": api_key_manager(argument) if argument else start_action("api-keys")
    elif command == "/reset-temperature": process_action("reset-temperature")
    elif command == "/reset-system-prompt": process_action("reset-system-prompt")
    elif command == "/agent": create_agent(argument)
    elif command == "/stop-agent":
        target = argument or selected_agent_id
        if target: stop_agent(target)
        else: add_notice("Select an agent or provide its ID: /stop-agent ID")
    elif command == "/delete-agent":
        target = argument or selected_agent_id
        if target: delete_agent(target)
        else: add_notice("Select an agent first.")
    elif command == "/agent-message":
        target, _, message = argument.partition(" ")
        if target and message: agent_chat(target, message)
        else: add_notice("Usage: /agent-message AGENT_ID instruction")
    elif command == "/models":
        if argument: choose_model(argument)
        else:
            refresh_model_choices(); set_mode("models")
            add_notice(f"Installed models loaded. Current selection: `{selected_model}`. Use ↑/↓ and Enter to choose.")
    elif command in ("/download", "/download-models"):
        if command == "/download-models" or argument.lower().startswith("models"):
            model_name = argument.strip() if command == "/download-models" else argument[6:].strip()
            if model_name: start_model_pull(model_name)
            else:
                global download_choices, download_index
                download_choices = list(MODEL_CATALOG); download_index = 0
                set_mode("download-models")
        else:
            start_file_download(argument)
    elif command == "/open":
        if argument.lower() in ("terminal", "in terminal", "open in terminal"):
            focus_terminal_window(); add_notice("Requested focus for the SharkByte terminal.")
        else: open_web_interface()
    else: add_notice("Unknown command. Type /help or press F2 for the menu.")


def submit_input(_buffer):
    global pending_action
    text = input_area.text.strip()
    if selected_mode == "help": return True
    if pending_action:
        action = pending_action; pending_action = None
        input_area.prompt = "Agent instruction ❯  " if selected_mode == "agents" else "You ❯  "
        input_area.text = ""; process_action(action, text); return True
    input_area.text = ""
    if not text: return True
    if selected_mode == "models":
        choose_model(text)
    elif selected_mode == "download-models":
        start_model_pull(text)
    elif selected_mode == "agents":
        if text.startswith("/agent "): add_command_echo(text); create_agent(text[7:])
        elif text.startswith("/stop-agent"):
            add_command_echo(text); parts = text.split(maxsplit=1)
            target = parts[1] if len(parts) > 1 else selected_agent_id
            if target: stop_agent(target)
            else: add_notice("Select an agent first.")
        elif selected_agent_id: agent_chat(selected_agent_id, text)
        else: add_notice("Select an agent first, or use /agent YOUR GOAL to deploy one.")
    elif text.startswith("/"):
        add_command_echo(text)
        handle_command(text)
    else:
        begin_request(text)
    return True


# -------------------------------- Menu UI ---------------------------------
def menu_max_scroll():
    return max(0, len(current_menu_items()) - MENU_VISIBLE)


def ensure_menu_visible():
    global menu_scroll, menu_index
    count = len(current_menu_items())
    if count == 0:
        menu_index = menu_scroll = 0
        return
    menu_index = max(0, min(menu_index, count - 1))
    if menu_index < menu_scroll: menu_scroll = menu_index
    elif menu_index >= menu_scroll + MENU_VISIBLE: menu_scroll = menu_index - MENU_VISIBLE + 1
    menu_scroll = max(0, min(menu_scroll, menu_max_scroll()))


def move_menu(delta):
    global menu_index
    menu_index = max(0, min(len(current_menu_items()) - 1, menu_index + delta))
    ensure_menu_visible(); app.invalidate()


def page_menu(delta):
    global menu_index
    menu_index = max(0, min(len(current_menu_items()) - 1, menu_index + delta * MENU_PAGE_STEP))
    ensure_menu_visible(); app.invalidate()


def page_fragments():
    # Keep the current mode visible and show the model that mode will actually use.
    label = MODE_LABELS.get(selected_mode, "/chat")
    active_model = {"chat": selected_model, "work": WORK_MODEL, "coder": CODE_MODEL,
                    "deep-thinking": CHAT_MODEL, "casual": CASUAL_MODEL}.get(selected_mode, selected_model)
    return [("class:page", f" {label.upper()} · {active_model}  {'▲' if menu_open else '▼'} ", dropdown_mouse_handler)]


def dropdown_mouse_handler(mouse_event):
    global menu_open, menu_index, menu_scroll
    if mouse_event.event_type.name == "MOUSE_UP":
        menu_open = not menu_open
        if menu_open:
            menu_index = min(menu_index, len(current_menu_items())-1); ensure_menu_visible()
        app.invalidate()
    return None


def menu_item_mouse(index, event):
    if event.event_type.name == "MOUSE_UP":
        items = current_menu_items()
        if 0 <= index < len(items):
            run_menu_item(items[index])
    elif event.event_type.name == "SCROLL_UP":
        move_menu(-1)
    elif event.event_type.name == "SCROLL_DOWN":
        move_menu(1)
    return None


def dropdown_fragments():
    if not menu_open: return []
    fragments = []
    if menu_scroll > 0: fragments.append(("class:status", "  ↑ more options above  \n", lambda event: menu_item_mouse(menu_index, event)))
    items = current_menu_items()
    end = min(len(items), menu_scroll + MENU_VISIBLE)
    for idx in range(menu_scroll, end):
        key, label, _kind = items[idx]
        style = "class:menu.selected" if idx == menu_index else "class:menu"
        marker = " ❯ " if idx == menu_index else "   "
        fragments.append((style, marker + label, lambda event, i=idx: menu_item_mouse(i, event)))
        if idx < end - 1: fragments.append(("", "\n"))
    if end < len(items): fragments.append(("class:status", "\n  ↓ more options below  ", lambda event: menu_item_mouse(menu_index, event)))
    return fragments


def agent_summary():
    with state_lock:
        if not agents: return "No agents deployed yet. Choose Agents → Deploy New Agent, or type /agent followed by a goal."
        return "\n".join(f"{a['name']} [{a['id']}] — {a['status']}\nGoal: {a['goal']}" for a in agents)


def agent_name_mouse(agent_id, event):
    global selected_agent_id
    if event.event_type.name == "MOUSE_UP":
        selected_agent_id = agent_id
        chosen = get_agent(agent_id)
        if chosen: input_area.prompt = f"Instruction for {chosen.get('name','Agent')} ❯  "
        app.invalidate()
    return None


def agent_message_control(content, action, filename, event):
    if event.event_type.name != "MOUSE_UP": return None
    if action == "copy":
        add_notice("Agent message copied." if copy_to_clipboard(content) else "Clipboard copy failed.")
    else:
        folder = data_root() / "downloads"; folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename or "agent-message.md")
        stem, suffix = os.path.splitext(safe)
        path = folder / f"{stem}_{datetime.now():%Y%m%d_%H%M%S_%f}{suffix or '.md'}"
        path.write_text(content + "\n", encoding="utf-8")
        add_notice(f"Agent message downloaded to: {path}")
    return None


def agent_fragments():
    refresh_agents_from_disk()
    with state_lock:
        snapshot = list(agents)
    lines = [f"LAST COMMAND: {last_command or '/agents'}", "BACKGROUND AGENTS · MINI-AGENT RESEARCH TEAM", "Click an agent or use ↑/↓ to select one.",
             "Enter opens activity / instruction prompt · type below to direct the selected agent.",
             "Backspace permanently deletes the selected agent and its saved data.",
             "Three mini-agents research separate slices; a max-depth Qwen coordinator synthesizes them.",
             "Agents pause 45 seconds between cycles to leave Ollama available for chat.", ""]
    heading_map = {}
    action_map = {}
    if not snapshot:
        lines.append("No agents deployed. Choose DEPLOY AGENT from the menu.")
    else:
        for a in snapshot:
            heading_map[len(lines)] = a
            status = "RUNNING" if a.get("running") else "STOPPED"
            lines.append(("▶ " if a["id"] == selected_agent_id else "  ") +
                         f"{a.get('name', 'Agent')} [{status}] — {a.get('status', 'Unknown')}")
            lines.append("   Goal: " + a.get("goal", "")[:150])
            if a["id"] == selected_agent_id and agent_detail_open:
                lines.append("   LIVE ACTIVITY (newest last):")
                activity = a.get("activity", [])
                lines.extend("   • " + str(x)[:160] for x in activity[-15:])
                if not activity:
                    lines.append("   • No activity has been recorded yet.")
                if a.get("updates"):
                    lines.append("   LATEST AGENT UPDATE:")
                    lines.append("   [COPY UPDATE] [DOWNLOAD UPDATE]")
                    action_map[len(lines) - 1] = (a["updates"][-1], f"{a.get('name','agent')}-update.md")
                    lines.extend("   " + x for x in a["updates"][-1].splitlines()[:12])
                if a.get("next_focus"):
                    lines.append("   NEXT MINI-AGENT FOCUS: " + str(a.get("next_focus"))[:140])
                if a.get("sources"):
                    lines.append("   WEBPAGES OPENED / READ:")
                    for source in a.get("sources", [])[:12]:
                        lines.append("   • " + source.get("title", "Untitled")[:110])
                        lines.append("     " + source.get("url", "")[:140])
                if a.get("messages"):
                    lines.append("   RECENT CHECK-INS:")
                    for msg in a.get("messages", [])[-4:]:
                        who = "You" if msg.get("role") == "user" else a.get("name", "Agent")
                        content = msg.get("content", "")
                        snippet = content.replace("\n", " ")[:150]
                        lines.append(f"   {who}: {snippet} [COPY] [DOWNLOAD]")
                        action_map[len(lines) - 1] = (content, f"{who.lower()}-agent-message.md")
    width = min(terminal_width(), max(80, max((len(x) for x in lines), default=0) + 6))
    inner = width - 2
    out = [("class:title.border", "╭" + "─ Agent Control Center " + "─" * max(0, inner - 22) + "╮\n")]
    for i, line in enumerate(lines):
        clipped = line[:inner]
        left = 2
        right = max(0, inner - len(clipped) - left)
        out.append(("class:title.border", "│"))
        if i in heading_map:
            a = heading_map[i]
            style = "class:agent.selected" if a["id"] == selected_agent_id else "class:menu"
            out.append((style, " " * left + clipped + " " * right,
                        lambda event, aid=a["id"]: agent_name_mouse(aid, event)))
        elif i in action_map:
            content, filename = action_map[i]
            out.append(("class:help.text", " " * left))
            out.append(("class:copy.button", "[COPY]", lambda event, value=content: agent_message_control(value, "copy", None, event)))
            out.append(("class:help.text", " "))
            out.append(("class:page", "[DOWNLOAD]", lambda event, value=content, name=filename: agent_message_control(value, "download", name, event)))
            out.append(("class:help.text", " " * max(0, right - 20)))
        else:
            out.append(("class:help.text", " " * left + clipped + " " * right))
        out.append(("class:title.border", "│\n"))
    out.append(("class:title.border", "╰" + "─" * inner + "╯"))
    return out


def response_line_count():
    """Count the exact logical lines emitted by the current response fragments.

    Estimating from response_text alone misses extra control rows. More importantly,
    phantom thinking lines can put the cursor beyond FormattedTextControl.fragment_lines
    and crash prompt_toolkit during redraw.
    """
    if help_open or selected_mode in ("agents", "models", "download-models"):
        return 1
    fragments = response_fragments()
    rendered = "".join(str(fragment[1]) for fragment in fragments)
    return max(1, rendered.count("\n") + 1)


def response_cursor_position():
    # The hidden cursor must never point past the actual formatted logical lines.
    last_line = max(0, response_line_count() - 1)
    line = last_line if response_auto_follow else min(last_line, max(0, response_scroll))
    return Point(x=0, y=min(line, last_line))


def response_vertical_scroll(window):
    return 0 if response_auto_follow else max(0, response_scroll)


def response_near_bottom():
    """True when the visible transcript ends within two logical lines of its end."""
    try:
        info = response_window.render_info
        if info is None:
            return True
        visible = info.visible_line_to_input_line
        last_visible = max(visible.values()) if visible else info.vertical_scroll
        last_line = max(0, info.ui_content.line_count - 1)
        return last_line - last_visible <= 2
    except Exception:
        return True


def prepare_response_append():
    global response_auto_follow, response_scroll
    if response_auto_follow or response_near_bottom():
        response_auto_follow = True
        response_scroll = 0


def scroll_response(delta):
    global response_scroll, response_auto_follow
    # ui_content.line_count is the logical line count used by Window.vertical_scroll.
    # Keep this calculation active during generation; new tokens can extend the range.
    try:
        info = response_window.render_info
        last_line = max(0, info.ui_content.line_count - 1) if info else response_line_count() - 1
        viewport = max(1, int(info.window_height)) if info else 12
    except Exception:
        last_line = max(0, response_line_count() - 1)
        viewport = 12
    max_top = max(0, last_line - viewport + 1)
    current = max_top if response_auto_follow else min(max_top, max(0, response_scroll))
    new_top = max(0, min(max_top, current + int(delta)))
    response_scroll = new_top
    response_auto_follow = new_top >= max_top
    if response_auto_follow:
        response_scroll = 0
    if app:
        app.invalidate()


def jump_to_bottom():
    """Immediately return the transcript to its newest line and resume auto-follow."""
    global response_scroll, response_auto_follow
    response_scroll = 0
    response_auto_follow = True
    app.invalidate()


def jump_to_bottom_mouse(event):
    if event.event_type.name == "MOUSE_UP":
        jump_to_bottom()
    return None


def response_action_fragments():
    # Thinking is rendered directly beneath the model's `:` header in response_fragments.
    return []


def toggle_thinking_mouse(event):
    global thinking_expanded
    if event.event_type.name == "MOUSE_UP":
        thinking_expanded = not thinking_expanded
        if app:
            app.invalidate()
    return None


def hide_thinking_mouse(event):
    global thinking_expanded
    if event.event_type.name == "MOUSE_UP":
        thinking_expanded = False
        if app:
            app.invalidate()
    return None


def response_toolbar_fragments():
    # The jump control remains a fixed row below the transcript.
    if response_auto_follow:
        return [("class:status", "  ● LIVE · "),
                ("class:page", "[ ↓ JUMP TO BOTTOM ]", jump_to_bottom_mouse)]
    return [("class:status", "  MANUAL SCROLL · "),
            ("class:page", "[ ↓ JUMP TO BOTTOM ]", jump_to_bottom_mouse)]


def response_mouse_handler(event):
    if event.event_type.name == "SCROLL_UP":
        scroll_response(-1)
    elif event.event_type.name == "SCROLL_DOWN":
        scroll_response(1)
    return None


def response_fragments():
    if help_open: return help_fragments()
    if selected_mode == "agents": return agent_fragments()
    if selected_mode == "models": return model_page_fragments()
    if selected_mode == "download-models": return download_page_fragments()
    result = []
    if response_text:
        anchor = thinking_anchor
        # Keep the thinking control visible for every completed assistant response.
        # Some Ollama models do not emit a separate `thinking` field; the expanded
        # panel explains that rather than silently removing the control.
        has_anchor = (anchor is not None and 0 <= anchor <= len(response_text)
                      and selected_mode not in ("agents", "models", "download-models"))
        if has_anchor:
            before = response_text[:anchor].rstrip()
            after = response_text[anchor:]
            if before:
                result.extend(markdown_with_message_controls(before))
            result.append(("", "\n"))
            if busy:
                label = f"{ANIMATION_FRAMES[animation_index]} Thinking…"
            else:
                label = "[ HIDE THINKING ]" if thinking_expanded else "[ SHOW THINKING ]"

            result.append((
                "class:thinking",
                f" {label} ",
                toggle_thinking_mouse
            ))

            if thinking_expanded:
                details = thinking_text.strip() or (
                    "This model did not provide a separate thinking trace. "
                    "Ollama exposes native thinking separately only for models that support it. "
                    "The answer remains available above."
                )
                result.append(("", "\n"))
                result.extend(markdown_fragments(details))
                result.append(("", "\n"))
                if not busy:
                    result.append(("class:thinking", " [ HIDE THINKING ] ", hide_thinking_mouse))
                    result.append(("", "\n"))
            else:
                result.append(("", "\n"))
            result.extend(markdown_with_message_controls(after))
        else:
            result.extend(markdown_with_message_controls(response_text))
        message = latest_assistant_message()
        if message:
            result.extend([
                ("", "\n"),
                ("class:copy.button", "[ COPY TEXT ]",
                 lambda event, captured=message: message_control_mouse(captured, "copy", event)),
                ("", "  "),
                ("class:page", "[ DOWNLOAD TEXT ]",
                 lambda event, captured=message: message_control_mouse(captured, "download", event)),
            ])
    elif not thinking_inline:
        result = [("class:status", "Replies and help appear here.")]
    return [(fragment[0], fragment[1], fragment[2] if len(fragment) > 2 else response_mouse_handler) for fragment in result]


def status_fragments():
    # Thinking is embedded under the model header; keep this row informational.
    if busy: return [("class:status", "Ctrl-C cancels the current request · scrolling remains available")]
    if selected_mode == "help": return [("class:status", "Press ENTER to open help" if not help_open else "Press ENTER to close help")]
    if selected_mode == "agents": return [("class:status", f"Agents · ↑/↓ select · Enter instruct/view · Backspace delete · 45-second cycle pause")]
    if selected_mode == "models": return [("class:status", f"Selected model: {selected_model} · ↑/↓ browse · Enter select")]
    if selected_mode == "download-models": return [("class:status", "Qwen and Gemma are recommended first · ↑/↓ browse · Enter download")]
    return [("class:status", f"Temperature: {temperature:g} · Enter sends · F2 or / opens menu · Ctrl-S saves chat · ↑/↓ scroll · Esc closes")]


# -------------------------------- Widgets ---------------------------------
def input_prompt_fragments():
    """Render the prompt in its own fixed window so long input cannot scroll it away."""
    prompt = str(getattr(input_area, "prompt", "You ❯  ") or "")
    return [("class:text-area.prompt", prompt)]


def input_prompt_width():
    prompt = str(getattr(input_area, "prompt", "You ❯  ") or "")
    return min(32, max(7, len(prompt)))


input_area = TextArea(height=1, prompt="", multiline=False,
                      wrap_lines=False, accept_handler=submit_input)
input_prompt_window = Window(FormattedTextControl(input_prompt_fragments, focusable=False),
                             width=input_prompt_width, height=1, dont_extend_width=True,
                             always_hide_cursor=True)
header_window = Window(FormattedTextControl(header_fragments, focusable=False),
                       height=lambda: 4 if header_visible else 0,
                       align=WindowAlign.CENTER, always_hide_cursor=True)
page_window = Window(FormattedTextControl(page_fragments, focusable=True, show_cursor=False),
                     width=42, height=1, dont_extend_width=True)
dropdown_window = Window(FormattedTextControl(dropdown_fragments, focusable=True, show_cursor=False),
                         height=lambda: min(MENU_VISIBLE, len(current_menu_items())) + (1 if menu_scroll > 0 else 0) + (1 if menu_scroll + MENU_VISIBLE < len(current_menu_items()) else 0) if menu_open else 0,
                         wrap_lines=False)
input_window = Window(input_area.control, height=1, wrap_lines=False)

def separator_fragments():
    return [("class:title.border", "─" * max(1, terminal_width()))]

response_window = Window(
    FormattedTextControl(response_fragments, focusable=False,
                         get_cursor_position=response_cursor_position),
    wrap_lines=True, always_hide_cursor=True,
    get_vertical_scroll=response_vertical_scroll,
)
status_window = Window(FormattedTextControl(status_fragments, focusable=False),
                       height=1, always_hide_cursor=True)
response_action_window = Window(
    FormattedTextControl(response_action_fragments, focusable=False),
    height=0,
    align=WindowAlign.LEFT, always_hide_cursor=True,
)
response_toolbar_window = Window(
    FormattedTextControl(response_toolbar_fragments, focusable=False),
    height=1, align=WindowAlign.RIGHT, always_hide_cursor=True,
)
response_separator = Window(FormattedTextControl(separator_fragments, focusable=False),
                            height=1, always_hide_cursor=True)
input_separator = Window(FormattedTextControl(separator_fragments, focusable=False),
                         height=1, always_hide_cursor=True)
bottom_row = VSplit([page_window, input_prompt_window, input_window], padding=1)
root = HSplit([Window(height=lambda: 1 if header_visible else 0), header_window,
               Window(height=lambda: 1 if header_visible else 0), response_window,
               response_action_window, response_toolbar_window, response_separator, dropdown_window,
               Window(height=1), input_separator, bottom_row, status_window])
keys = KeyBindings()


@keys.add("c-s")
def _(event):
    save_chat()


@keys.add("c-c", eager=True)
def _(event):
    global busy, request_id
    log_startup(f"Ctrl-C key binding invoked; request_active={busy}")
    if busy:
        active_cancel_event.set()
        if request_watchdog:
            request_watchdog.cancel()
        # Finish the UI cancellation immediately; late worker tokens are ignored by request_id.
        busy = False
        request_id += 1
        post_event("cancelled", None, None)
    else:
        # Ctrl+C is a cancel/clear-input shortcut, never an application exit shortcut.
        input_area.text = ""
    if app:
        app.invalidate()


@keys.add("enter", filter=Condition(lambda: menu_open))
def _(event):
    items = current_menu_items()
    if items and 0 <= menu_index < len(items): run_menu_item(items[menu_index])


@keys.add("enter", filter=Condition(lambda: selected_mode == "help" and not menu_open))
def _(event):
    if not help_open: show_help_inside_app()
    else: set_mode("chat")


@keys.add("enter", filter=Condition(lambda: selected_mode == "agents" and not menu_open and not pending_action and not input_area.text.strip()))
def _(event):
    global agent_detail_open
    if selected_agent_id:
        agent_detail_open = not agent_detail_open
        chosen = get_agent(selected_agent_id)
        if chosen:
            input_area.prompt = f"Instruction for {chosen.get('name','Agent')} ❯  "
            add_notice(f"Selected {chosen.get('name','agent')}. Type what you want changed or researched below, then press Enter to send it.")
        app.invalidate()
    else:
        add_notice("Select an agent with ↑/↓ first, then press Enter to instruct it.")


@keys.add("f3", filter=Condition(lambda: selected_mode == "agents" and not menu_open))
def _(event):
    if selected_agent_id:
        chosen = get_agent(selected_agent_id)
        if chosen:
            add_notice(f"Checked in with {chosen['name']}: {chosen['status']}.")
            if chosen["updates"]:
                add_notice(chosen["updates"][-1])
    else:
        add_notice("No agent selected yet.")


@keys.add("/", filter=Condition(lambda: slash_menu_enabled and not menu_open and not pending_action and selected_mode != "help" and not input_area.text.strip()), eager=True)
def _(event):
    global menu_open, menu_index
    menu_open = True
    mode_index = next((i for i, item in enumerate(current_menu_items()) if item[0] == selected_mode), None)
    if mode_index is not None:
        menu_index = mode_index
    ensure_menu_visible()
    app.invalidate()


@keys.add("f2")
def _(event):
    global menu_open, menu_index
    menu_open = not menu_open
    if menu_open:
        menu_index = next((i for i, item in enumerate(current_menu_items()) if item[0] == selected_mode), menu_index)
        ensure_menu_visible()
    app.invalidate()


@keys.add("escape")
def _(event):
    global menu_open
    menu_open = False; app.invalidate()


@keys.add("up", filter=Condition(lambda: menu_open))
def _(event): move_menu(-1)
@keys.add("down", filter=Condition(lambda: menu_open))
def _(event): move_menu(1)
@keys.add("pageup", filter=Condition(lambda: menu_open))
def _(event): page_menu(-1)
@keys.add("pagedown", filter=Condition(lambda: menu_open))
def _(event): page_menu(1)


# Transcript scrolling (Up/Down and PageUp/PageDown) when the dropdown is closed.
@keys.add("up", filter=Condition(lambda: not menu_open and not input_area.text and selected_mode not in ("agents", "help", "models", "download-models")), eager=True)
def _(event): scroll_response(-1)


@keys.add("down", filter=Condition(lambda: not menu_open and not input_area.text and selected_mode not in ("agents", "help", "models", "download-models")), eager=True)
def _(event): scroll_response(1)


@keys.add("pageup", filter=Condition(lambda: not menu_open and not input_area.text and selected_mode not in ("agents", "help", "models", "download-models")), eager=True)
def _(event): scroll_response(-MENU_PAGE_STEP)


@keys.add("pagedown", filter=Condition(lambda: not menu_open and not input_area.text and selected_mode not in ("agents", "help", "models", "download-models")), eager=True)
def _(event): scroll_response(MENU_PAGE_STEP)


@keys.add("end", filter=Condition(lambda: not menu_open and selected_mode not in ("agents", "help", "models", "download-models")))
def _(event): jump_to_bottom()


@keys.add("up", filter=Condition(lambda: selected_mode == "models" and not menu_open))
def _(event):
    global model_index
    model_index = max(0, model_index - 1); app.invalidate()


@keys.add("down", filter=Condition(lambda: selected_mode == "models" and not menu_open))
def _(event):
    global model_index
    model_index = min(len(model_choices) - 1, model_index + 1); app.invalidate()


@keys.add("enter", filter=Condition(lambda: selected_mode == "models" and not menu_open))
def _(event):
    if model_choices:
        add_command_echo(f"/models {model_choices[model_index]}")
        choose_model(model_choices[model_index])


@keys.add("up", filter=Condition(lambda: selected_mode == "download-models" and not menu_open))
def _(event):
    global download_index
    download_index = max(0, download_index - 1); app.invalidate()


@keys.add("down", filter=Condition(lambda: selected_mode == "download-models" and not menu_open))
def _(event):
    global download_index
    download_index = min(len(download_choices) - 1, download_index + 1); app.invalidate()


@keys.add("enter", filter=Condition(lambda: selected_mode == "download-models" and not menu_open))
def _(event):
    if download_choices:
        add_command_echo(f"/download models {download_choices[download_index]}")
        start_model_pull(download_choices[download_index])


@keys.add("backspace", filter=Condition(lambda: selected_mode == "agents" and not menu_open and not input_area.text.strip()), eager=True)
def _(event):
    if selected_agent_id:
        add_command_echo(f"/delete-agent {selected_agent_id}")
        delete_agent(selected_agent_id)
    else: add_notice("Select an agent first, then press Backspace to delete it.")


@keys.add("up", filter=Condition(lambda: selected_mode == "agents" and not menu_open))
def _(event):
    global selected_agent_id
    with state_lock:
        ids = [a["id"] for a in agents]
        if ids:
            i = ids.index(selected_agent_id) if selected_agent_id in ids else len(ids)
            selected_agent_id = ids[max(0, i - 1)]
            chosen = get_agent(selected_agent_id)
            if chosen: input_area.prompt = f"Instruction for {chosen.get('name','Agent')} ❯  "
    app.invalidate()


@keys.add("down", filter=Condition(lambda: selected_mode == "agents" and not menu_open))
def _(event):
    global selected_agent_id
    with state_lock:
        ids = [a["id"] for a in agents]
        if ids:
            i = ids.index(selected_agent_id) if selected_agent_id in ids else -1
            selected_agent_id = ids[min(len(ids) - 1, i + 1)]
            chosen = get_agent(selected_agent_id)
            if chosen: input_area.prompt = f"Instruction for {chosen.get('name','Agent')} ❯  "
    app.invalidate()


def show_help_inside_app():
    global help_open
    help_open = True; app.invalidate()


def tick():
    global busy, thinking_inline, thinking_detected, thinking_text, thinking_expanded, response_text, request_id, download_status, download_choices, download_index
    global response_scroll, response_auto_follow, last_install_command, last_download_path, file_download_busy, pending_download_candidate, thinking_anchor
    refresh_agents_from_disk()
    with web_commands_lock:
        incoming_web = list(web_commands); web_commands.clear()
    for web_command in incoming_web:
        if web_command.startswith("/agent-message "):
            add_command_echo(web_command)
            parts = web_command.split(" ", 2)
            if len(parts) == 3: agent_chat(parts[1], parts[2])
        elif web_command.startswith("/delete-agent "):
            add_command_echo(web_command)
            delete_agent(web_command.split(" ", 1)[1].strip())
        elif web_command.startswith("/"):
            add_command_echo(web_command); handle_command(web_command)
        elif selected_mode == "agents" and selected_agent_id:
            agent_chat(selected_agent_id, web_command)
        else:
            begin_request(web_command)
    changed = False
    with events_lock:
        pending = list(ui_events); ui_events.clear()
    for kind, value, event_id in pending:
        if event_id is not None and event_id != request_id:
            # Agent events use no request id and remain valid independently.
            continue
        changed = True
        if kind in ("token", "search_progress", "search_sources", "embedding", "error", "cancelled", "timeout", "agent_chat_start", "agent_chat_token", "agent_chat_error"):
            prepare_response_append()
        if kind == "token": response_text += value
        elif kind == "thinking_detected": thinking_detected = True
        elif kind == "thinking_piece":
            thinking_detected = True
            thinking_text += str(value or "")
        elif kind == "search_progress": response_text += f"\n[{value}]"
        elif kind == "search_sources":
            model_name, read_count, source_text, actual_sources = value
            response_text += f"\n\n**Actual webpages opened and read · {read_count}:**\n"
            if actual_sources:
                for source_title, source_url in actual_sources:
                    safe_title = source_title.replace("[", "(").replace("]", ")")
                    response_text += f"- [{safe_title}]({source_url})\n"
            else:
                response_text += "- No verifiable page URLs were returned by the browser.\n"
            response_text += f"\n**{model_name} — Brief summary:** "
            thinking_anchor = len(response_text)
        elif kind == "done":
            busy = False; thinking_inline = False; thinking_detected = False
            response_scroll = 0; response_auto_follow = True
            if request_watchdog: request_watchdog.cancel()
            persist_active_chat()
        elif kind in ("retrying", "retrying_timeout"):
            remove_count = max(0, min(len(response_text), int(value or 0)))
            if remove_count: response_text = response_text[:-remove_count]
            thinking_text = ""; thinking_expanded = False
            if kind == "retrying_timeout":
                response_text += "\n\n[The model waited 240 seconds. Resetting it and retrying once with the same chat…]\n\n"
            else:
                response_text += "\n\n[Repeated output detected. Resetting the model and retrying with the previous 50 messages…]\n\n"
        elif kind == "embedding":
            busy = False; thinking_inline = False; thinking_detected = False
            if request_watchdog: request_watchdog.cancel()
            response_text += f"\n\n**Embedding generated**\nDimensions: {len(value)}\nFirst 8 values: `{value[:8]}`"
        elif kind == "error":
            busy = False; thinking_inline = False; thinking_detected = False
            response_scroll = 0; response_auto_follow = True
            if request_watchdog: request_watchdog.cancel()
            response_text += f"\n\n**Error:** {value}"
            persist_active_chat()
        elif kind == "cancelled":
            busy = False; thinking_inline = False; thinking_detected = False
            response_scroll = 0; response_auto_follow = True
            if request_watchdog: request_watchdog.cancel()
            response_text += "\n\n[Cancelled.]"
            persist_active_chat()
        elif kind == "timeout":
            busy = False; thinking_inline = False; thinking_detected = False
            response_scroll = 0; response_auto_follow = True
            if request_watchdog: request_watchdog.cancel()
            response_text += f"\n\n**Stopped:** {value}"
            persist_active_chat()
            request_id += 1  # ignore any late events from the abandoned worker
        elif kind == "file_download_progress":
            add_notice(f"[DOWNLOAD] {value}")
        elif kind == "file_download_found":
            pending_download_candidate = dict(value)
            if value.get("kind") == "winget":
                details = f"{value.get('package')} · {value.get('package_id')} · {value.get('source')}"
            else:
                details = f"{value.get('package')} · {value.get('url')} · Source: {value.get('source')}"
            add_notice(f"**Download found — review before proceeding:**\n{details}\nType `/download download`, then type `DOWNLOAD` to approve the transfer. Nothing has been downloaded yet.")
        elif kind == "file_download_done":
            last_download_path = value.get("path")
            last_install_command = value.get("install_command")
            add_notice(f"**Download complete:** `{value.get('package') or 'File'}`\nSaved to: `{last_download_path}`")
            if last_install_command:
                if last_install_command[0] == "__installer__":
                    installer_path = last_install_command[1]
                    command_text = (f'msiexec.exe /i "{installer_path}"' if Path(installer_path).suffix.lower() == ".msi"
                                    else f'Start-Process -FilePath "{installer_path}"')
                else:
                    command_text = subprocess.list2cmdline(last_install_command)
                add_notice(f"Install command: `{command_text}`")
                add_notice("To install/launch it, use `/download run`, then type `RUN` to confirm. SharkByte will not execute it before that confirmation.")
            else:
                add_notice("The file is downloaded but has not been executed. Open it yourself if you trust the source.")
            file_download_busy = False
        elif kind == "file_download_error":
            file_download_busy = False
            add_notice(f"**Download failed:** {value}")
        elif kind == "catalog_ready":
            if value:
                download_choices = list(value); download_index = 0
                download_status = f"Official Ollama library catalog loaded · {len(download_choices)} entries"
            else:
                download_status = f"Using built-in model catalog · {len(MODEL_CATALOG)} entries (library refresh unavailable)"
        elif kind == "download_progress":
            download_status = str(value)
            response_text += f"\n[{value}]"
        elif kind == "download_done":
            download_status = str(value)
            response_text += f"\n\n**MODEL DOWNLOAD:** {value}"
            refresh_model_choices()
        elif kind in ("agent_status", "agent_update"):
            if kind == "agent_status":
                agent_id, status = value; a = get_agent(agent_id)
                if a: a["status"] = status
        elif kind == "agent_chat_start": add_notice("\n[Agent is replying…]")
        elif kind == "agent_chat_token":
            agent_id, piece = value
            if selected_agent_id == agent_id:
                response_text += piece
        elif kind == "agent_chat_done": pass
        elif kind == "agent_chat_error": add_notice(f"Agent chat error: {value[1]}")
    if busy or changed: app.invalidate()


# The terminal Application is created inside main(), after the console has been
# checked. Detached agent workers never create a prompt_toolkit Application.
app = None
def handle_console_sigint(_signum, _frame):
    """Ignore process-level SIGINT; prompt_toolkit handles an actual Ctrl+C key."""
    return


async def ui_poller():
    global animation_index
    while True:
        if busy:
            animation_index = (animation_index + 1) % len(ANIMATION_FRAMES)
        tick()
        await asyncio.sleep(0.1)


def log_startup(message):
    """Append a diagnostic message to the startup log from any UI callback."""
    startup_log = Path(__file__).resolve().parent / "sharkbyte-startup.log"
    stamp = datetime.now().isoformat(timespec="seconds")
    try:
        with startup_log.open("a", encoding="utf-8") as log:
            log.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def main():
    global app, busy, request_id
    if "--agent-worker" in sys.argv:
        try:
            folder = sys.argv[sys.argv.index("--agent-dir") + 1]
        except (ValueError, IndexError):
            raise SystemExit("Missing --agent-dir for agent worker")
        run_agent_worker(folder)
        return

    log_startup("Startup began")
    log_startup(f"Python executable: {sys.executable} | Version: {sys.version.split()[0]}")
    try:
        log_startup("Checking/allocating Windows console")
        ensure_windows_console()
        print("Running...", flush=True)

        log_startup("Loading selected model")
        load_selected_model()

        log_startup("Checking Ollama")
        ollama_ready = ensure_ollama()
        if not ollama_ready:
            warning = "WARNING: Ollama is not responding. SharkByte will still open, but AI requests need Ollama running."
            print(warning, flush=True)
            log_startup(warning)

        log_startup("Starting localhost web server")
        ensure_web_server()

        log_startup("Refreshing installed models")
        refresh_model_choices()

        log_startup("Loading saved chat")
        load_most_recent_chat()

        log_startup("Resuming background agents")
        resume_orphaned_agents()

        log_startup("Creating terminal interface")
        app = Application(layout=Layout(root, focused_element=input_window), style=STYLE,
                          key_bindings=keys, full_screen=True, mouse_support=True,
                          refresh_interval=0.1)
        log_startup("Starting terminal interface")
        # prompt_toolkit must not replace this handler with one that raises
        # KeyboardInterrupt and tears down/recreates its asyncio event loop.
        previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, handle_console_sigint)
        try:
            app.run(pre_run=lambda: app.create_background_task(ui_poller()), handle_sigint=False)
            log_startup("Terminal interface returned normally")
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)
    except KeyboardInterrupt:
        log_startup("KeyboardInterrupt received during startup")
        print("\nSharkByte startup was interrupted.", flush=True)
    except Exception as exc:
        log_startup(f"FATAL {type(exc).__name__}: {exc!r}")
        try:
            with startup_log.open("a", encoding="utf-8") as log:
                traceback.print_exc(file=log)
        except OSError:
            pass
        print(f"\nSharkByte startup failed: {type(exc).__name__}: {exc}", flush=True)
        print(f"Details saved to: {startup_log}", flush=True)
        try:
            input("Press Enter to close this window...")
        except (EOFError, OSError, RuntimeError):
            pass
    finally:
        try:
            persist_active_chat()
        except Exception as exc:
            log_startup(f"Chat save during shutdown failed: {type(exc).__name__}: {exc!r}")


if __name__ == "__main__":
    main()
