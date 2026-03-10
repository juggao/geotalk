#!/usr/bin/env python3
"""
geotalk-gui.py — GeoTalk Desktop GUI  v2.3.0
A tkinter frontend for the GeoTalk radio-over-IP client.

Layout
  ┌─────────────────────────────────────────────┐
  │ HEADER — callsign · country · relay status  │
  ├──────────────┬──────────────────────────────┤
  │  CHANNELS    │  MESSAGES                    │
  │  (sidebar)   │  (scrolling log)             │
  │              │                              │
  │              ├──────────────────────────────┤
  │              │  REPL INPUT                  │
  ├──────────────┴──────────────────────────────┤
  │  PTT (large) │  MUTE │ STATUS BAR           │
  └─────────────────────────────────────────────┘
"""

import sys
import os
import queue
import threading
import time
import re
import wave
import tkinter as tk
from tkinter import font as tkfont
from tkinter import simpledialog, messagebox, filedialog

# ── locate geotalk.py next to this script ─────────────────────────────────────
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

# Silence pyaudio ALSA noise before import
import ctypes

# Suppress ALSA "no such file or directory" spam on stderr.
# IMPORTANT: the CFUNCTYPE wrapper MUST be stored in a module-level variable.
# If it is only a temporary expression, Python's GC frees it immediately and
# ALSA is left with a dangling function pointer — calling it from any ALSA/
# PortAudio background thread causes a segfault.
_ALSA_ERROR_HANDLER_T = ctypes.CFUNCTYPE(
    None,
    ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
    ctypes.c_int, ctypes.c_char_p,
)
_alsa_error_handler = _ALSA_ERROR_HANDLER_T(lambda *_: None)  # kept alive here

try:
    _asound = ctypes.cdll.LoadLibrary("libasound.so.2")
    _asound.snd_lib_error_set_handler(_alsa_error_handler)
except Exception:
    pass

import geotalk as gt_mod

# ══════════════════════════════════════════════════════════════════════════════
# ANSI STRIP
# ══════════════════════════════════════════════════════════════════════════════

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


# ══════════════════════════════════════════════════════════════════════════════
# STDOUT REDIRECT
# ══════════════════════════════════════════════════════════════════════════════

class _QueueWriter:
    """Replaces sys.stdout so GeoTalk's sys.stdout.write() posts to our queue."""
    def __init__(self, q: queue.Queue):
        self._q = q
        self._buf = ""

    def write(self, text: str):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = strip_ansi(line).strip("\r")
            if line:
                self._q.put(("msg", line))

    def flush(self):
        if self._buf.strip():
            self._q.put(("msg", strip_ansi(self._buf).strip("\r")))
            self._buf = ""

    def isatty(self):
        return False


# ══════════════════════════════════════════════════════════════════════════════
# COLOUR PALETTES  — dark (amber CRT) and light (clean white)
# ══════════════════════════════════════════════════════════════════════════════

PALETTE_DARK = {
    "bg":         "#0d0e0f",      # near-black chassis
    "bg2":        "#111314",      # slightly lighter panel
    "bg3":        "#161819",      # input fields
    "amber":      "#e8a030",      # primary amber glow
    "amber_dim":  "#7a521a",      # dimmed amber
    "amber_pale": "#ffd080",      # highlight / bright text
    "green":      "#3ecf6a",      # online / active indicator
    "red":        "#e04040",      # PTT active / error
    "red_dim":    "#6a1a1a",      # PTT button idle
    "blue":       "#4a9eff",      # info / relay
    "mute":       "#c0a020",      # muted state
    "border":     "#2a2e32",      # subtle borders
    "border2":    "#3a3f45",      # active borders
    "text":       "#c8b88a",      # body text (warm cream)
    "text_dim":   "#60584a",      # de-emphasised
    "text_hi":    "#f0e4c0",      # highlights
    "ptt_bg":     "#1a0a0a",      # PTT button background
    "ptt_active": "#ff2020",      # PTT on
    "ptt_idle":   "#3a1010",      # PTT off (dark red)
    "scan":       "#20b8d0",      # scan / info colour
    "play_idle":  "#0e2a18",      # PLAY button idle (dark green)
    "play_active":"#20c040",      # PLAY button transmitting (bright green)
    "play_dim":   "#1a6030",      # PLAY button border idle
    "rec_idle":   "#2a0a0a",      # REC button idle (dark red)
    "rec_active": "#e03030",      # REC button recording (bright red)
    "rec_dim":    "#6a1a1a",      # REC button border idle
}

PALETTE_LIGHT = {
    "bg":         "#f5f5f5",      # near-white chassis
    "bg2":        "#ececec",      # slightly darker panel (sidebar)
    "bg3":        "#ffffff",      # input fields
    "amber":      "#1a6fbd",      # primary accent (blue replaces amber)
    "amber_dim":  "#6b9ec7",      # dimmed accent
    "amber_pale": "#0d4a8a",      # highlight (dark blue)
    "green":      "#1a8c3e",      # online / active indicator
    "red":        "#cc2020",      # PTT active / error
    "red_dim":    "#c08080",      # PTT button idle
    "blue":       "#1a6fbd",      # info / relay
    "mute":       "#b08000",      # muted state
    "border":     "#d0d0d0",      # subtle borders
    "border2":    "#aaaaaa",      # active borders
    "text":       "#222222",      # body text (dark)
    "text_dim":   "#888888",      # de-emphasised
    "text_hi":    "#000000",      # highlights
    "ptt_bg":     "#f5f5f5",      # PTT button background
    "ptt_active": "#dd1010",      # PTT on
    "ptt_idle":   "#f0d8d8",      # PTT off (light red)
    "scan":       "#0088aa",      # scan / info colour
    "play_idle":  "#ddf0e4",      # PLAY button idle (light green)
    "play_active":"#1a8c3e",      # PLAY button transmitting
    "play_dim":   "#90c8a8",      # PLAY button border idle
    "rec_idle":   "#f5e0e0",      # REC button idle (light red)
    "rec_active": "#cc2020",      # REC button recording
    "rec_dim":    "#d08080",      # REC button border idle
}

# Active palette — mutable, widgets read from this dict
P = dict(PALETTE_DARK)

# ══════════════════════════════════════════════════════════════════════════════
# CONNECT DIALOG
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENT PREFERENCES
# ══════════════════════════════════════════════════════════════════════════════

import json as _json
import pathlib as _pathlib

_PREFS_PATH = _pathlib.Path.home() / ".config" / "geotalk" / "prefs.json"

_PREFS_DEFAULTS: dict = {
    "nick":       "",
    "relay":      "",
    "relay_port": 5073,
    "join":       "",
    "local_if":   "",
    "country":    "NL",
    "auto_channel": False,
    "join_active":  False,
    "window_geometry": "",
    "theme":        "dark",
}

def _load_prefs() -> dict:
    prefs = dict(_PREFS_DEFAULTS)
    try:
        if _PREFS_PATH.exists():
            data = _json.loads(_PREFS_PATH.read_text())
            for k in _PREFS_DEFAULTS:
                if k in data:
                    prefs[k] = data[k]
    except Exception:
        pass
    return prefs

def _save_prefs(prefs: dict) -> None:
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(_json.dumps(prefs, indent=2))
    except Exception:
        pass

class ConnectDialog(tk.Toplevel):
    def __init__(self, parent, prefs: dict | None = None):
        super().__init__(parent)
        self.title("GeoTalk — Connect")
        self.configure(bg=P["bg"])
        self.resizable(False, False)
        self.result = None
        self._prefs = prefs or dict(_PREFS_DEFAULTS)

        self._build()
        self.grab_set()
        self.focus_force()

        # Centre over parent
        self.update_idletasks()
        pw = parent.winfo_width(); ph = parent.winfo_height()
        px = parent.winfo_x();     py = parent.winfo_y()
        w = self.winfo_width();    h = self.winfo_height()
        self.geometry(f"+{px + (pw-w)//2}+{py + (ph-h)//2}")

    def _lbl(self, parent, text):
        return tk.Label(parent, text=text, bg=P["bg"], fg=P["amber_dim"],
                        font=("Courier", 9), anchor="w")

    def _entry(self, parent, default="", width=28):
        e = tk.Entry(parent, bg=P["bg3"], fg=P["amber_pale"], insertbackground=P["amber"],
                     relief="flat", font=("Courier", 11), width=width,
                     highlightthickness=1, highlightbackground=P["border2"],
                     highlightcolor=P["amber"])
        e.insert(0, default)
        return e

    def _build(self):
        pad = dict(padx=14, pady=5)

        # Title banner
        tk.Label(self, text="◈ GEOTALK", bg=P["bg"], fg=P["amber"],
                 font=("Courier", 16, "bold")).pack(pady=(18, 2))
        tk.Label(self, text="postal-code radio  v" + gt_mod.VERSION,
                 bg=P["bg"], fg=P["amber_dim"],
                 font=("Courier", 9)).pack(pady=(0, 16))

        frame = tk.Frame(self, bg=P["bg"])
        frame.pack(fill="x", **pad)

        def row(label, widget, r):
            self._lbl(frame, label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=r, column=1, sticky="ew", pady=3)

        p = self._prefs
        self._nick_e   = self._entry(frame, p.get("nick", ""))
        self._relay_e  = self._entry(frame, p.get("relay", "geotalk.net"))
        self._rport_e  = self._entry(frame, str(p.get("relay_port", 5073)), width=8)
        self._join_e   = self._entry(frame, p.get("join", ""))
        self._iface_e  = self._entry(frame, p.get("local_if", ""))
        self._cc_e     = self._entry(frame, p.get("country", "NL"), width=6)
        self._auto_var = tk.BooleanVar(value=bool(p.get("auto_channel", False)))
        self._join_active_var = tk.BooleanVar(value=bool(p.get("join_active", False)))

        row("Callsign / nick",  self._nick_e,  0)
        row("Relay host",       self._relay_e, 1)
        row("Relay port",       self._rport_e, 2)
        row("Join on start",    self._join_e,  3)
        row("Interface IP",     self._iface_e, 4)
        row("Country",          self._cc_e,    5)

        # Auto-channel checkbox (spans both columns)
        auto_chk = tk.Checkbutton(
            frame, text="Auto-channel (detect from public IP)",
            variable=self._auto_var,
            bg=P["bg"], fg=P["text"], selectcolor=P["bg3"],
            activebackground=P["bg"], activeforeground=P["amber_pale"],
            font=("Courier", 9))
        auto_chk.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Join-active checkbox (spans both columns)
        join_active_chk = tk.Checkbutton(
            frame, text="Join-active (join all live relay channels on start)",
            variable=self._join_active_var,
            bg=P["bg"], fg=P["text"], selectcolor=P["bg3"],
            activebackground=P["bg"], activeforeground=P["amber_pale"],
            font=("Courier", 9))
        join_active_chk.grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Hints
        tk.Label(self, text="Leave relay empty for LAN multicast mode",
                 bg=P["bg"], fg=P["text_dim"], font=("Courier", 8)).pack(pady=(0, 4))

        # Buttons
        btn_frame = tk.Frame(self, bg=P["bg"])
        btn_frame.pack(pady=(8, 18))

        tk.Button(btn_frame, text="CONNECT", command=self._ok,
                  bg=P["amber_dim"], fg=P["bg"], activebackground=P["amber"],
                  activeforeground=P["bg"], relief="flat", font=("Courier", 11, "bold"),
                  padx=20, pady=6, cursor="hand2").pack(side="left", padx=6)

        tk.Button(btn_frame, text="CANCEL", command=self.destroy,
                  bg=P["bg3"], fg=P["text_dim"], activebackground=P["border2"],
                  activeforeground=P["text"], relief="flat", font=("Courier", 10),
                  padx=16, pady=6, cursor="hand2").pack(side="left", padx=6)

        self._nick_e.focus_set()
        self.bind("<Return>", lambda _: self._ok())

    def _ok(self):
        nick = self._nick_e.get().strip()
        if not nick:
            self._nick_e.configure(highlightbackground=P["red"])
            return
        self.result = {
            "nick":         nick,
            "relay":        self._relay_e.get().strip(),
            "relay_port":   int(self._rport_e.get().strip() or "5073"),
            "join":         self._join_e.get().strip(),
            "local_if":     self._iface_e.get().strip() or "0.0.0.0",
            "country":      self._cc_e.get().strip().upper() or "NL",
            "auto_channel": bool(self._auto_var.get()),
            "join_active":  bool(self._join_active_var.get()),
        }
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class GeoTalkGUI:

    POLL_MS   = 80    # queue poll interval
    STATUS_MS = 1200  # status bar refresh
    CHAN_MS   = 2000  # channel list refresh

    def __init__(self, root: tk.Tk):
        self.root = root
        self.gt: gt_mod.GeoTalk | None = None
        self._q: queue.Queue = queue.Queue()
        self._ptt_pressed = False  # mouse/keyboard hold state
        self._ptt_release_id = None  # pending after() id for debounced key release
        self._play_busy = False       # True while WAV is being transmitted
        self._play_loop_var = tk.BooleanVar(value=False)  # Loop checkbox state
        self._rec_busy  = False       # True while recording incoming audio
        self._rec_path  = ""          # output WAV path
        self._rec_wave  = None        # wave.Wave_write object
        self._rec_lock  = threading.Lock()  # guards _rec_wave writes
        self._theme     = "dark"      # current theme: "dark" | "light"
        self._chan_keys: list[str] = []   # parallel to _chan_list rows
        self._chan_refreshing = False      # re-entrancy guard
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._prefs = _load_prefs()

        root.title("GeoTalk")
        root.configure(bg=P["bg"])
        root.minsize(820, 560)

        self._build_ui()
        self._bind_keys()
        self._schedule_polls()

        # Restore saved window geometry
        geom = self._prefs.get("window_geometry", "")
        if geom:
            try:
                root.geometry(geom)
            except Exception:
                pass

        # Restore saved theme (after UI is fully built)
        saved_theme = self._prefs.get("theme", "dark")
        if saved_theme == "light":
            self._theme = "light"
            P.update(PALETTE_LIGHT)
            self._theme_btn.configure(text="☾")
            self._apply_theme()

        # Show connect dialog after window appears
        root.after(120, self._show_connect)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(root, bg=P["bg"], height=40)
        hdr.pack(fill="x", padx=0, pady=0)
        hdr.pack_propagate(False)
        self._hdr = hdr

        self._hdr_left = tk.Label(hdr, text="◈ GEOTALK", bg=P["bg"],
                                  fg=P["amber"], font=("Courier", 13, "bold"))
        self._hdr_left.pack(side="left", padx=14)

        self._hdr_status = tk.Label(hdr, text="NOT CONNECTED", bg=P["bg"],
                                    fg=P["text_dim"], font=("Courier", 9))
        self._hdr_status.pack(side="left", padx=6)

        self._hdr_right = tk.Label(hdr, text="", bg=P["bg"],
                                   fg=P["amber_dim"], font=("Courier", 9))
        self._hdr_right.pack(side="right", padx=14)

        # Theme toggle button — right side of header
        self._theme_btn = tk.Button(
            hdr, text="☀", font=("Courier", 12),
            bg=P["bg"], fg=P["amber_dim"],
            activebackground=P["bg2"], activeforeground=P["amber"],
            relief="flat", cursor="hand2", bd=0,
            command=self._toggle_theme)
        self._theme_btn.pack(side="right", padx=(0, 4))

        # Separator
        tk.Frame(root, bg=P["border"], height=1).pack(fill="x")

        # ── Main body ─────────────────────────────────────────────────────────
        body = tk.Frame(root, bg=P["bg"])
        body.pack(fill="both", expand=True)
        self._body = body

        # Left sidebar — channels
        sidebar = tk.Frame(body, bg=P["bg2"], width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._sidebar = sidebar

        tk.Label(sidebar, text="CHANNELS", bg=P["bg2"], fg=P["amber_dim"],
                 font=("Courier", 8, "bold"), anchor="w").pack(
                     fill="x", padx=10, pady=(10, 4))

        tk.Frame(sidebar, bg=P["border"], height=1).pack(fill="x", padx=6)

        self._chan_list = tk.Listbox(
            sidebar, bg=P["bg2"], fg=P["text"], selectbackground=P["amber_dim"],
            selectforeground=P["amber_pale"], relief="flat", borderwidth=0,
            font=("Courier", 10), activestyle="none", highlightthickness=0,
            selectmode="single")
        self._chan_list.pack(fill="both", expand=True, padx=4, pady=4)
        self._chan_list.bind("<<ListboxSelect>>", self._on_chan_select)
        # Suppress the Listbox's built-in <space> selection behaviour.
        # Without this, pressing space while the sidebar has focus moves the
        # Listbox selection to the first entry.  We route the event to PTT
        # instead and return "break" to stop further propagation.
        self._chan_list.bind("<space>",            lambda e: (self._ptt_key_down(e), "break")[-1])
        self._chan_list.bind("<KeyRelease-space>", lambda e: (self._ptt_key_up(e),   "break")[-1])

        tk.Frame(sidebar, bg=P["border"], height=1).pack(fill="x", padx=6)

        chan_btn_f = tk.Frame(sidebar, bg=P["bg2"])
        chan_btn_f.pack(fill="x", padx=6, pady=6)

        self._join_entry = tk.Entry(
            chan_btn_f, bg=P["bg3"], fg=P["amber_pale"],
            insertbackground=P["amber"], relief="flat", font=("Courier", 10),
            highlightthickness=1, highlightbackground=P["border"],
            highlightcolor=P["amber"])
        self._join_entry.pack(fill="x", pady=(0, 4))
        self._join_entry.bind("<Return>", self._on_join_entry)

        tk.Label(chan_btn_f, text="#channel to join", bg=P["bg2"],
                 fg=P["text_dim"], font=("Courier", 7)).pack(anchor="w")

        # Vertical separator
        tk.Frame(body, bg=P["border"], width=1).pack(side="left", fill="y")

        # Right panel
        right = tk.Frame(body, bg=P["bg"])
        right.pack(side="left", fill="both", expand=True)

        # Messages area
        msg_frame = tk.Frame(right, bg=P["bg"])
        msg_frame.pack(fill="both", expand=True)

        self._msg_text = tk.Text(
            msg_frame, bg=P["bg"], fg=P["text"], state="disabled",
            relief="flat", borderwidth=0, font=("Courier", 10),
            wrap="word", padx=10, pady=8, cursor="arrow",
            highlightthickness=0)
        self._msg_text.pack(side="left", fill="both", expand=True)

        msg_scroll = tk.Scrollbar(msg_frame, command=self._msg_text.yview,
                                  bg=P["bg2"], troughcolor=P["bg"],
                                  activebackground=P["amber_dim"], relief="flat",
                                  width=8)
        msg_scroll.pack(side="right", fill="y")
        self._msg_text.configure(yscrollcommand=msg_scroll.set)

        # Configure text tags
        self._setup_tags()

        # Separator above input
        tk.Frame(right, bg=P["border2"], height=1).pack(fill="x")

        # REPL input row
        repl_f = tk.Frame(right, bg=P["bg3"], pady=0)
        repl_f.pack(fill="x")

        self._prompt_lbl = tk.Label(
            repl_f, text="➤", bg=P["bg3"], fg=P["amber"],
            font=("Courier", 11, "bold"), padx=8)
        self._prompt_lbl.pack(side="left")

        self._repl_entry = tk.Entry(
            repl_f, bg=P["bg3"], fg=P["amber_pale"],
            insertbackground=P["amber"], relief="flat",
            font=("Courier", 11), highlightthickness=0)
        self._repl_entry.pack(side="left", fill="x", expand=True, pady=6, padx=(0, 8))
        self._repl_entry.bind("<Return>", self._on_repl_enter)
        self._repl_entry.bind("<Up>",     self._hist_up)
        self._repl_entry.bind("<Down>",   self._hist_down)
        self._repl_history = []
        self._hist_pos = -1

        # ── Bottom bar ────────────────────────────────────────────────────────
        tk.Frame(root, bg=P["border"], height=1).pack(fill="x")

        bottom = tk.Frame(root, bg=P["bg"], height=72)
        bottom.pack(fill="x")
        bottom.pack_propagate(False)

        # PTT button — large, left side
        self._ptt_btn = tk.Button(
            bottom, text="● PTT", font=("Courier", 14, "bold"),
            bg=P["ptt_idle"], fg=P["red_dim"],
            activebackground=P["ptt_active"], activeforeground="white",
            relief="flat", width=10, cursor="hand2",
            highlightthickness=2, highlightbackground=P["red_dim"])
        self._ptt_btn.pack(side="left", fill="y", padx=(10, 6), pady=8)
        self._ptt_btn.bind("<ButtonPress-1>",   self._ptt_down)
        self._ptt_btn.bind("<ButtonRelease-1>", self._ptt_up)

        # MUTE button
        self._mute_btn = tk.Button(
            bottom, text="◉ MUTE", font=("Courier", 10, "bold"),
            bg=P["bg2"], fg=P["text_dim"],
            activebackground=P["mute"], activeforeground=P["bg"],
            relief="flat", cursor="hand2",
            highlightthickness=1, highlightbackground=P["border"])
        self._mute_btn.pack(side="left", fill="y", padx=(0, 8), pady=12)
        self._mute_btn.configure(command=self._toggle_mute)

        # PLAY button — browse for WAV file and transmit
        self._play_btn = tk.Button(
            bottom, text="▶ PLAY", font=("Courier", 10, "bold"),
            bg=P["play_idle"], fg=P["green"],
            activebackground=P["play_active"], activeforeground=P["bg"],
            relief="flat", cursor="hand2",
            highlightthickness=1, highlightbackground=P["play_dim"])
        self._play_btn.pack(side="left", fill="y", padx=(0, 4), pady=12)
        self._play_btn.configure(command=self._play_clicked)

        # Loop checkbox — sits immediately right of PLAY button
        self._loop_chk = tk.Checkbutton(
            bottom, text="Loop", font=("Courier", 9),
            variable=self._play_loop_var,
            bg=P["bg"], fg=P["green"], selectcolor=P["bg"],
            activebackground=P["bg"], activeforeground=P["amber"],
            cursor="hand2", relief="flat", bd=0)
        self._loop_chk.pack(side="left", fill="y", padx=(0, 6), pady=12)

        # REC button — record incoming audio to a WAV file
        self._rec_btn = tk.Button(
            bottom, text="⏺ REC", font=("Courier", 10, "bold"),
            bg=P["rec_idle"], fg=P["red"],
            activebackground=P["rec_active"], activeforeground="white",
            relief="flat", cursor="hand2",
            highlightthickness=1, highlightbackground=P["rec_dim"])
        self._rec_btn.pack(side="left", fill="y", padx=(0, 10), pady=12)
        self._rec_btn.configure(command=self._rec_clicked)

        # Divider
        tk.Frame(bottom, bg=P["border"], width=1).pack(side="left", fill="y", pady=8)

        # Status area (right side of bottom bar)
        status_f = tk.Frame(bottom, bg=P["bg"])
        status_f.pack(side="left", fill="both", expand=True, padx=12)

        self._status_line1 = tk.Label(
            status_f, text="", bg=P["bg"], fg=P["text"],
            font=("Courier", 9), anchor="w")
        self._status_line1.pack(fill="x", pady=(8, 2))

        self._status_line2 = tk.Label(
            status_f, text="", bg=P["bg"], fg=P["text_dim"],
            font=("Courier", 8), anchor="w")
        self._status_line2.pack(fill="x")

        # Transport indicator (far right)
        self._transport_lbl = tk.Label(
            bottom, text="", bg=P["bg"], fg=P["text_dim"],
            font=("Courier", 8), justify="right", anchor="e")
        self._transport_lbl.pack(side="right", padx=14)

    def _setup_tags(self):
        t = self._msg_text
        t.tag_configure("ts",       foreground=P["text_dim"],  font=("Courier", 9))
        t.tag_configure("nick",     foreground=P["amber_pale"], font=("Courier", 10, "bold"))
        t.tag_configure("region",   foreground=P["text_dim"],  font=("Courier", 9, "italic"))
        t.tag_configure("body",     foreground=P["text"],      font=("Courier", 10))
        t.tag_configure("voice",    foreground=P["green"],     font=("Courier", 10))
        t.tag_configure("system",   foreground=P["amber_dim"], font=("Courier", 9, "italic"))
        t.tag_configure("error",    foreground=P["red"],       font=("Courier", 10))
        t.tag_configure("success",  foreground=P["green"],     font=("Courier", 10))
        t.tag_configure("joined",   foreground=P["blue"],      font=("Courier", 10))
        t.tag_configure("scan",     foreground=P["scan"],      font=("Courier", 10))
        t.tag_configure("bbs",      foreground=P["mute"],      font=("Courier", 10, "italic"))
        t.tag_configure("chan_hdr", foreground=P["amber"],     font=("Courier", 11, "bold"))
        t.tag_configure("ping",     foreground=P["text_dim"],  font=("Courier", 9, "italic"))

    # ── Theme switching ───────────────────────────────────────────────────────

    def _toggle_theme(self):
        """Switch between dark and light themes."""
        if self._theme == "dark":
            self._theme = "light"
            P.update(PALETTE_LIGHT)
            self._theme_btn.configure(text="☾")
        else:
            self._theme = "dark"
            P.update(PALETTE_DARK)
            self._theme_btn.configure(text="☀")
        self._prefs["theme"] = self._theme
        _save_prefs(self._prefs)
        self._apply_theme()

    def _apply_theme(self):
        """Repaint every widget to reflect the current palette P."""
        # ── walk all widgets recursively ─────────────────────────────────────
        def _recolour(w):
            cls = w.winfo_class()
            try:
                if cls == "Frame":
                    # Separator frames (1-px wide or tall) → border colour
                    try:
                        ww = w.winfo_width()
                        wh = w.winfo_height()
                    except Exception:
                        ww = wh = 0
                    if ww == 1 or wh == 1:
                        w.configure(bg=P["border"])
                    else:
                        cur = w.cget("bg")
                        if cur in (PALETTE_DARK["bg2"], PALETTE_LIGHT["bg2"]):
                            w.configure(bg=P["bg2"])
                        elif cur in (PALETTE_DARK["bg3"], PALETTE_LIGHT["bg3"]):
                            w.configure(bg=P["bg3"])
                        else:
                            w.configure(bg=P["bg"])
                elif cls == "Label":
                    # Inherit bg from parent frame
                    try:
                        pbg = w.master.cget("bg")
                        if pbg in (PALETTE_DARK["bg2"], PALETTE_LIGHT["bg2"],
                                   P["bg2"]):
                            w.configure(bg=P["bg2"])
                        elif pbg in (PALETTE_DARK["bg3"], PALETTE_LIGHT["bg3"],
                                     P["bg3"]):
                            w.configure(bg=P["bg3"])
                        else:
                            w.configure(bg=P["bg"])
                    except Exception:
                        w.configure(bg=P["bg"])
                elif cls == "Scrollbar":
                    w.configure(bg=P["bg2"], troughcolor=P["bg"],
                                activebackground=P["amber_dim"])
            except Exception:
                pass
            for child in w.winfo_children():
                _recolour(child)

        _recolour(self.root)
        self.root.configure(bg=P["bg"])

        # ── named widgets that need precise colour treatment ──────────────────
        self._hdr.configure(bg=P["bg"])
        self._hdr_left.configure(bg=P["bg"], fg=P["amber"])
        self._hdr_status.configure(bg=P["bg"])
        self._hdr_right.configure(bg=P["bg"], fg=P["amber_dim"])
        self._theme_btn.configure(
            bg=P["bg"], fg=P["amber_dim"],
            activebackground=P["bg2"], activeforeground=P["amber"])

        self._sidebar.configure(bg=P["bg2"])
        self._chan_list.configure(
            bg=P["bg2"], fg=P["text"],
            selectbackground=P["amber_dim"],
            selectforeground=P["amber_pale"])

        self._join_entry.configure(
            bg=P["bg3"], fg=P["amber_pale"],
            insertbackground=P["amber"],
            highlightbackground=P["border"],
            highlightcolor=P["amber"])

        self._msg_text.configure(bg=P["bg"], fg=P["text"])
        self._setup_tags()   # repaint all text tags

        self._prompt_lbl.configure(bg=P["bg3"], fg=P["amber"])
        self._repl_entry.configure(
            bg=P["bg3"], fg=P["amber_pale"],
            insertbackground=P["amber"])

        # ── bottom bar buttons ────────────────────────────────────────────────
        if self.gt and self.gt._ptt_held:
            self._ptt_btn.configure(bg=P["ptt_active"], fg="white",
                                    activebackground=P["ptt_active"])
        else:
            self._ptt_btn.configure(
                bg=P["ptt_idle"], fg=P["red_dim"],
                activebackground=P["ptt_active"])

        self._mute_btn.configure(bg=P["bg2"], fg=P["text_dim"],
                                 activebackground=P["mute"],
                                 highlightbackground=P["border"])

        if self._play_busy:
            # keep active appearance — just update active colours
            pass
        else:
            self._play_btn.configure(
                bg=P["play_idle"], fg=P["green"],
                activebackground=P["play_active"],
                highlightbackground=P["play_dim"])

        self._loop_chk.configure(
            bg=P["bg"], fg=P["green"],
            selectcolor=P["bg"],
            activebackground=P["bg"], activeforeground=P["amber"])

        if self._rec_busy:
            pass   # keep active appearance
        else:
            self._rec_btn.configure(
                bg=P["rec_idle"], fg=P["red"],
                activebackground=P["rec_active"],
                highlightbackground=P["rec_dim"])

        self._status_line1.configure(bg=P["bg"], fg=P["text"])
        self._status_line2.configure(bg=P["bg"], fg=P["text_dim"])
        self._transport_lbl.configure(bg=P["bg"])



    def _bind_keys(self):
        self.root.bind("<space>",        self._ptt_key_down)
        self.root.bind("<KeyRelease-space>", self._ptt_key_up)
        self.root.bind("<Control-t>",    lambda _: self._ptt_toggle())
        self.root.bind("<Control-m>",    lambda _: self._toggle_mute())
        self.root.bind("<Escape>",       lambda _: self._repl_entry.focus_set())

    def _ptt_key_down(self, event):
        if str(event.widget) == str(self._repl_entry):
            return   # don't intercept space in input box
        # If a WAV loop is playing, space stops it instead of activating PTT.
        if self._play_busy:
            self._play_stop()
            return
        # Cancel any pending debounced release — this KeyPress is auto-repeat,
        # not a genuine new press after a release.
        if self._ptt_release_id is not None:
            self.root.after_cancel(self._ptt_release_id)
            self._ptt_release_id = None
        if not self._ptt_pressed:
            self._ptt_down(None)

    def _ptt_key_up(self, event):
        if str(event.widget) == str(self._repl_entry):
            return
        # On X11 key-repeat fires as KeyRelease+KeyPress pairs.
        # Defer the actual release by 30 ms; if a KeyPress arrives before the
        # timer fires we cancel it (handled in _ptt_key_down above).
        if self._ptt_release_id is not None:
            self.root.after_cancel(self._ptt_release_id)
        self._ptt_release_id = self.root.after(30, self._ptt_key_release_commit)

    def _ptt_key_release_commit(self):
        self._ptt_release_id = None
        if self._ptt_pressed:
            self._ptt_up(None)

    # ── Connect flow ──────────────────────────────────────────────────────────

    def _show_connect(self):
        dlg = ConnectDialog(self.root, prefs=self._prefs)
        self.root.wait_window(dlg)
        if dlg.result:
            self._connect(dlg.result)
        else:
            self._append_sys("Type /connect or restart to connect.")

    def _connect(self, cfg: dict):
        """
        Kick off connection on a background thread so that
        pyaudio.PyAudio() / PortAudio initialisation never runs on the
        tkinter main thread — that combination causes a segfault on Linux
        because PortAudio installs ALSA signal handlers that conflict with
        Xlib's internal locking.

        Flow:
          main thread  →  _connect()  →  spawns _connect_worker thread
          worker thread  →  GeoTalk.__init__ + gt.start()
                         →  posts ("connected", gt, cfg) onto self._q
          main thread  →  _poll_queue picks it up  →  _on_connected()
        """
        # Stop any previous instance (safe to do on main thread — it only
        # sets flags and closes sockets, no PortAudio calls).
        if self.gt:
            try:
                self.gt.stop()
            except Exception:
                pass
            self.gt = None
        # Stop any in-progress recording — the old audio object is gone
        if self._rec_busy:
            self._rec_stop()

        # Redirect stdout now (before the worker starts) so any early
        # GeoTalk prints are captured immediately.
        sys.stdout = _QueueWriter(self._q)
        sys.stderr = sys.stdout

        self._hdr_status.configure(text="CONNECTING…", fg=P["amber_dim"])
        self._append_sys(f"Connecting as {cfg['nick']}…")

        def _worker():
            try:
                gt = gt_mod.GeoTalk(
                    nick=cfg["nick"],
                    relay_host=cfg["relay"],
                    relay_port=cfg["relay_port"],
                    local_if=cfg["local_if"],
                )
                if cfg["country"] in gt_mod.KNOWN_COUNTRIES:
                    gt._current_country = cfg["country"]
                gt.start()
                self._q.put(("connected", gt, cfg))
            except Exception as e:
                self._q.put(("connect_error", str(e)))

        threading.Thread(target=_worker, daemon=True, name="gt-connect").start()

    def _on_connected(self, gt: "gt_mod.GeoTalk", cfg: dict):
        """Called on the main thread once the background connect worker succeeds."""
        self.gt = gt

        mode = (f"relay → {cfg['relay']}:{cfg['relay_port']}"
                if cfg["relay"] else "LAN multicast")
        self._hdr_status.configure(
            text=f"{cfg['nick']}  ·  {cfg['country']}  ·  {mode}",
            fg=P["green"])
        self._hdr_right.configure(text=f"v{gt_mod.VERSION}")
        self._append_sys(f"Connected as {cfg['nick']}  [{mode}]")

        # Persist settings for next launch
        self._prefs.update({
            "nick":         cfg["nick"],
            "relay":        cfg["relay"],
            "relay_port":   cfg["relay_port"],
            "join":         cfg["join"],
            "local_if":     cfg["local_if"] if cfg["local_if"] != "0.0.0.0" else "",
            "country":      cfg["country"],
            "auto_channel": cfg.get("auto_channel", False),
            "join_active":  cfg.get("join_active",  False),
        })
        _save_prefs(self._prefs)

        # Auto-channel: detect location from IP
        if cfg.get("auto_channel"):
            self._append_sys("Detecting location from public IP…")
            def _do_auto():
                try:
                    postal, city, cc = gt_mod.detect_postal_from_ip()
                    if postal:
                        channel = gt_mod._best_auto_channel(postal)
                        city_str = f"{city}, {cc}" if city else cc
                        if cc and cc in gt_mod.KNOWN_COUNTRIES:
                            self.gt._current_country = cc
                        self.root.after(0, lambda: (
                            self._append_sys(f"Location: {postal} ({city_str}) → #{channel}"),
                            self._ingest_result(self.gt.join_channel(channel)),
                            self._refresh_channels(),
                        ))
                    else:
                        self.root.after(0, lambda: self._append_sys(
                            "Could not detect location from IP (VPN? offline?)"))
                except Exception as e:
                    self.root.after(0, lambda: self._append_sys(f"Auto-channel error: {e}"))
            threading.Thread(target=_do_auto, daemon=True).start()

        # Join-active: query relay for live channels and join them all
        if cfg.get("join_active"):
            if not cfg["relay"]:
                self._append_sys("Join-active ignored — relay not configured")
            else:
                self._append_sys("Querying relay for active channels…")
                def _do_join_active():
                    try:
                        keys = gt_mod._fetch_active_channels(self.gt, timeout=5.0)
                        def _apply(keys=keys):
                            if keys:
                                self._append_sys(
                                    f"Found {len(keys)} active channel"
                                    f"{'s' if len(keys) != 1 else ''}: "
                                    f"{', '.join('#' + k for k in keys[:8])}"
                                    f"{'…' if len(keys) > 8 else ''}")
                                for key in keys:
                                    result = self.gt.join_channel(key)
                                    if result:
                                        self._ingest_result(result)
                                self._refresh_channels()
                            else:
                                self._append_sys(
                                    "No active channels found on relay (or timed out)")
                        self.root.after(0, _apply)
                    except Exception as e:
                        self.root.after(0, lambda: self._append_sys(
                            f"Join-active error: {e}"))
                threading.Thread(target=_do_join_active, daemon=True).start()

        # System channels — join #INFO and #EMERGENCY automatically in relay mode
        if self.gt.relay_mode:
            for ch in ("INFO", "EMERGENCY"):
                result = self.gt.join_channel(ch)
                if result:
                    self._ingest_result(result)

        # Auto-join channels
        if cfg["join"]:
            for raw in cfg["join"].split():
                result = gt_mod.handle_command(raw if raw.startswith("#") else "#" + raw, self.gt)
                if result:
                    self._ingest_result(result)

        self._refresh_channels()

    # ── PTT ───────────────────────────────────────────────────────────────────

    def _ptt_down(self, event):
        if not self.gt or self._ptt_pressed:
            return
        self._ptt_pressed = True
        result = self.gt.ptt_push()
        self._ptt_btn.configure(bg=P["ptt_active"], fg="white",
                                highlightbackground=P["red"])
        self._prompt_lbl.configure(fg=P["red"])
        self._append_line("▶ PTT ON", "voice")
        _ = result

    def _ptt_up(self, event):
        if not self.gt or not self._ptt_pressed:
            return
        self._ptt_pressed = False
        result = self.gt.ptt_release()
        self._ptt_btn.configure(bg=P["ptt_idle"], fg=P["red_dim"],
                                highlightbackground=P["red_dim"])
        self._prompt_lbl.configure(fg=P["amber"])
        self._append_line("■ PTT OFF", "ping")
        _ = result

    def _ptt_toggle(self):
        if self._ptt_pressed:
            self._ptt_up(None)
        else:
            self._ptt_down(None)

    # ── Mute ─────────────────────────────────────────────────────────────────

    def _toggle_mute(self):
        if not self.gt:
            return
        result = self.gt.mute_toggle()
        is_muted = (self.gt.audio.is_muted
                    if gt_mod.AUDIO_AVAILABLE and self.gt.audio.pa else False)
        if is_muted:
            self._mute_btn.configure(bg=P["mute"], fg=P["bg"],
                                     highlightbackground=P["mute"])
        else:
            self._mute_btn.configure(bg=P["bg2"], fg=P["text_dim"],
                                     highlightbackground=P["border"])
        self._append_sys(strip_ansi(result or ""))

    # ── WAV playback ──────────────────────────────────────────────────────────

    def _play_clicked(self):
        """Click handler for the PLAY button.
        - If a file is already playing/looping: stop it.
        - Otherwise: open the file-browser popup and start playback
          (looped if the Loop checkbox is checked).
        """
        if not self.gt:
            self._append_sys("[PLAY] Not connected.")
            return
        if self._play_busy:
            self._play_stop()
        else:
            self._play_browse()

    def _play_browse(self):
        """Open a file-browser popup, then transmit the selected WAV."""
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Select WAV file to transmit",
            filetypes=[
                ("WAV audio", "*.wav *.WAV"),
                ("All files",  "*.*"),
            ],
            initialdir=os.path.expanduser("~"),
        )
        if not path:
            return   # user cancelled

        loop = self._play_loop_var.get()
        if loop:
            result = self.gt.play_wav_loop(path)
        else:
            result = self.gt.play_wav(path)
        self._append_sys(strip_ansi(result))

        # If playback started successfully, monitor the thread for completion
        t = getattr(self.gt, "_play_thread", None)
        if t and t.is_alive():
            self._play_set_busy(True, loop=loop)
            self._play_watch()

    def _play_stop(self):
        """Stop an in-progress playback or loop."""
        if not self.gt:
            return
        result = self.gt.play_stop()
        self._append_sys(strip_ansi(result))
        self._play_set_busy(False)

    def _play_set_busy(self, busy: bool, loop: bool = False):
        """Update button appearance to reflect playing / looping / idle state."""
        self._play_busy = busy
        if busy:
            if loop or self._play_loop_var.get():
                self._play_btn.configure(
                    text="■ LOOP",
                    bg=P["play_active"], fg=P["bg"],
                    highlightbackground=P["play_active"])
            else:
                self._play_btn.configure(
                    text="■ STOP",
                    bg=P["play_active"], fg=P["bg"],
                    highlightbackground=P["play_active"])
        else:
            self._play_btn.configure(
                text="▶ PLAY",
                bg=P["play_idle"], fg=P["green"],
                highlightbackground=P["play_dim"])

    def _play_watch(self):
        """Poll every 200 ms; reset button when the play thread finishes."""
        t = getattr(self.gt, "_play_thread", None) if self.gt else None
        if t and t.is_alive():
            self.root.after(200, self._play_watch)
        else:
            self._play_set_busy(False)

    # ── Audio recording ───────────────────────────────────────────────────────

    def _rec_clicked(self):
        """Click handler for the REC button.
        - If already recording: stop.
        - Otherwise: open a save-file dialog and start recording.
        """
        if self._rec_busy:
            self._rec_stop()
        else:
            self._rec_browse()

    def _rec_browse(self):
        """Open a save-file dialog, then start recording incoming audio."""
        if not self.gt:
            self._append_sys("[REC] Not connected.")
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save incoming audio as…",
            defaultextension=".wav",
            filetypes=[
                ("WAV audio", "*.wav *.WAV"),
                ("All files", "*.*"),
            ],
            initialdir=os.path.expanduser("~"),
            initialfile="geotalk-record.wav",
        )
        if not path:
            return   # user cancelled
        self._rec_start(path)

    def _rec_start(self, path: str):
        """Open the WAV file and register the recording callback."""
        try:
            wf = wave.open(path, "wb")
            wf.setnchannels(1)
            wf.setsampwidth(2)          # 16-bit PCM
            wf.setframerate(48000)
        except Exception as e:
            self._append_sys(f"[REC] Cannot open file: {e}")
            return

        with self._rec_lock:
            self._rec_wave = wf
            self._rec_path = path

        # Register callback — called from feed_audio() thread with raw PCM
        def _cb(pcm: bytes):
            with self._rec_lock:
                if self._rec_wave:
                    try:
                        self._rec_wave.writeframes(pcm)
                    except Exception:
                        pass

        if self.gt and self.gt.audio:
            self.gt.audio.set_record_callback(_cb)

        self._rec_busy = True
        self._rec_btn.configure(
            text="⏹ STOP",
            bg=P["rec_active"], fg="white",
            highlightbackground=P["rec_active"])
        self._append_sys(
            f"[REC] Recording to {os.path.basename(path)} — press REC to stop")

    def _rec_stop(self):
        """Stop recording and close the WAV file."""
        if self.gt and self.gt.audio:
            self.gt.audio.clear_record_callback()

        with self._rec_lock:
            wf = self._rec_wave
            self._rec_wave = None

        if wf:
            try:
                wf.close()
            except Exception:
                pass

        self._rec_busy = False
        self._rec_btn.configure(
            text="⏺ REC",
            bg=P["rec_idle"], fg=P["red"],
            highlightbackground=P["rec_dim"])
        name = os.path.basename(self._rec_path) if self._rec_path else "?"
        self._append_sys(f"[REC] Saved → {name}")

    # ── REPL ──────────────────────────────────────────────────────────────────

    def _on_repl_enter(self, event):
        line = self._repl_entry.get().strip()
        if not line:
            return
        self._repl_entry.delete(0, "end")

        # History
        if not self._repl_history or self._repl_history[-1] != line:
            self._repl_history.append(line)
        self._hist_pos = -1

        # Echo
        self._append_echo(line)

        if not self.gt:
            if line.lower() in ("/connect", "connect"):
                self._show_connect()
            else:
                self._append_line("Not connected. Type /connect", "error")
            return

        # Handle bare #CHANNEL shortcuts
        if line.startswith("#"):
            result = gt_mod.handle_command(line, self.gt)
        else:
            result = gt_mod.handle_command(line, self.gt)

        if result == "__QUIT__":
            self._quit()
            return
        if result:
            self._ingest_result(result)

        self._refresh_channels()

    def _hist_up(self, event):
        if not self._repl_history:
            return
        if self._hist_pos == -1:
            self._hist_pos = len(self._repl_history) - 1
        elif self._hist_pos > 0:
            self._hist_pos -= 1
        self._repl_entry.delete(0, "end")
        self._repl_entry.insert(0, self._repl_history[self._hist_pos])

    def _hist_down(self, event):
        if self._hist_pos == -1:
            return
        self._hist_pos += 1
        self._repl_entry.delete(0, "end")
        if self._hist_pos < len(self._repl_history):
            self._repl_entry.insert(0, self._repl_history[self._hist_pos])
        else:
            self._hist_pos = -1

    # ── Channel sidebar ───────────────────────────────────────────────────────

    def _on_join_entry(self, event):
        raw = self._join_entry.get().strip()
        if not raw or not self.gt:
            return
        self._join_entry.delete(0, "end")
        chan = raw if raw.startswith("#") else "#" + raw
        result = gt_mod.handle_command(chan, self.gt)
        if result:
            self._ingest_result(result)
        self._refresh_channels()

    def _on_chan_select(self, event):
        if not self.gt or self._chan_refreshing:
            return
        sel = self._chan_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._chan_keys):
            return
        key = self._chan_keys[idx]
        # Already active — nothing to do
        if key == self.gt.active:
            return
        # Internal keys for wildcard/regex channels carry a GLOB:/REGEX: prefix
        # that parse_channel does not understand — strip it first.
        raw = key
        for prefix in ("GLOB:", "REGEX:"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        result = self.gt.switch_channel(raw)
        if result:
            self._ingest_result(strip_ansi(result))
        # Fetch BBS messages for the newly active channel
        if self.gt.relay_mode and self.gt.active:
            self.gt.relay.send(
                gt_mod.encode_bbs_req(self.gt.nick, self.gt.active))
        self._refresh_channels()

    def _refresh_channels(self):
        if not self.gt:
            return
        self._chan_refreshing = True
        try:
            self._chan_list.delete(0, "end")
            self._chan_keys = []
            for k, ch in self.gt.channels.items():
                marker = "► " if k == self.gt.active else "  "
                display = ch.pattern.display()
                users = ch.active_users()
                # Include own nick in the count — own packets are not echoed back
                if self.gt.nick and self.gt.nick not in users:
                    users = [self.gt.nick] + users
                ustr = f" ({len(users)})" if users else ""
                self._chan_list.insert("end", f"{marker}#{display}{ustr}")
                self._chan_keys.append(k)
                if k == self.gt.active:
                    self._chan_list.selection_set("end")
        finally:
            self._chan_refreshing = False

    # ── Message display ───────────────────────────────────────────────────────

    def _append_line(self, text: str, tag: str = "body"):
        t = self._msg_text
        t.configure(state="normal")
        t.insert("end", text + "\n", tag)
        t.configure(state="disabled")
        t.see("end")

    def _append_echo(self, text: str):
        t = self._msg_text
        t.configure(state="normal")
        ts = time.strftime("%H:%M")
        t.insert("end", f"{ts} ", "ts")
        t.insert("end", "you: ", "nick")
        t.insert("end", text + "\n", "body")
        t.configure(state="disabled")
        t.see("end")

    def _append_sys(self, text: str):
        self._append_line(f"  {text}", "system")

    def _ingest_result(self, raw: str):
        """Parse and display a handle_command() result string."""
        text = strip_ansi(raw).strip()
        if not text:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            tag = self._classify_line(line)
            self._append_line(line, tag)

    def _classify_line(self, line: str) -> str:
        lo = line.lower()
        if any(x in lo for x in ("joined #", "→ relay=", "→ mcast=")):
            return "joined"
        if any(x in lo for x in ("left #", "no active channel", "no more channels")):
            return "error"
        if "active →" in lo or "switched" in lo:
            return "success"
        if any(x in lo for x in ("scan", "►", "responder")):
            return "scan"
        if any(x in lo for x in ("bbs", "📋")):
            return "bbs"
        if any(x in lo for x in ("error", "invalid", "unknown", "failed")):
            return "error"
        if any(x in lo for x in ("connected", "relay", "joined")):
            return "success"
        if line.startswith("#") or "sub-region" in lo:
            return "chan_hdr"
        return "system"

    # ── Queue polling (inbound messages from GeoTalk threads) ────────────────

    def _poll_queue(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "msg":
                    self._dispatch_incoming(item[1])
                elif kind == "connected":
                    self._on_connected(item[1], item[2])   # (gt, cfg)
                elif kind == "connect_error":
                    self._append_line(f"Connection failed: {item[1]}", "error")
                    self._hdr_status.configure(text="CONNECTION FAILED", fg=P["red"])
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self._poll_queue)

    def _dispatch_incoming(self, line: str):
        """Route an inbound stdout line to the right display slot."""
        lo = line.lower()
        if "[voice]" in lo:
            self._append_line(line, "voice")
        elif "→" in line and "online" in lo:
            self._append_line(line, "ping")
        elif any(x in lo for x in ("[", "]")) and ":" in line:
            # Likely a text message: HH:MM [NICK] (region) #chan: text
            self._parse_text_msg(line)
        elif "bbs" in lo or "📋" in lo:
            self._append_line(line, "bbs")
        elif any(x in lo for x in ("joined", "left", "active →")):
            self._append_line(line, "joined")
        elif any(x in lo for x in ("error", "failed", "invalid")):
            self._append_line(line, "error")
        else:
            self._append_line(line, "system")
        self._refresh_channels()

    def _parse_text_msg(self, line: str):
        """Try to render a GeoTalk text message with coloured parts."""
        # Format: "HH:MM [NICK] (region) #chan: body"
        m = re.match(r"^(\d{2}:\d{2})\s+\[([^\]]+)\]\s+(\([^)]*\))?\s*(#\S+)?:?\s*(.*)", line)
        if not m:
            self._append_line(line, "body")
            return
        ts, nick, region, chan, body = m.groups()
        t = self._msg_text
        t.configure(state="normal")
        t.insert("end", f"{ts} ", "ts")
        t.insert("end", f"[{nick}] ", "nick")
        if region:
            t.insert("end", f"{region} ", "region")
        if chan:
            t.insert("end", f"{chan}: ", "chan_hdr")
        t.insert("end", (body or line) + "\n", "body")
        t.configure(state="disabled")
        t.see("end")

    # ── Status bar refresh ────────────────────────────────────────────────────

    def _refresh_status(self):
        if self.gt:
            active_ch = self.gt.active or "—"
            ch = self.gt.channels.get(self.gt.active)
            users = ch.active_users() if ch else []
            # Own nick is never echoed back to us, so add it explicitly
            # (we are always present on any channel we have joined)
            if self.gt.nick and self.gt.nick not in users:
                users = [self.gt.nick] + users
            msgs = ch.msg_count if ch else 0
            ptt = "● PTT ON" if self.gt._ptt_held else ""
            try:
                muted = self.gt.audio.is_muted if gt_mod.AUDIO_AVAILABLE and self.gt.audio.pa else False
            except Exception:
                muted = False
            mute_str = "◉ MUTED" if muted else ""

            parts = [f"CH: #{active_ch}"]
            if users:
                parts.append(f"users: {', '.join(users[:4])}")
            if msgs:
                parts.append(f"msgs: {msgs}")
            self._status_line1.configure(
                text="  ".join(parts),
                fg=P["red"] if self.gt._ptt_held else P["text"])

            flags = []
            if ptt:
                flags.append(ptt)
            if mute_str:
                flags.append(mute_str)
            # Playing indicator
            play_thread = getattr(self.gt, "_play_thread", None)
            if play_thread and play_thread.is_alive():
                flags.append("▶ PLAYING")
            flags.append(f"country: {self.gt._current_country}")
            n_ch = len(self.gt.channels)
            if n_ch:
                flags.append(f"{n_ch} channel{'s' if n_ch!=1 else ''}")
            self._status_line2.configure(text="  ".join(flags))

            mode = ("RELAY" if self.gt.relay_mode else "LAN MCAST")
            relay_ok = (self.gt.relay.is_connected()
                        if self.gt.relay_mode else True)
            self._transport_lbl.configure(
                text=mode,
                fg=P["green"] if relay_ok else P["red"])
        else:
            self._status_line1.configure(text="Not connected", fg=P["text_dim"])
            self._status_line2.configure(text="")
            self._transport_lbl.configure(text="OFFLINE", fg=P["red"])

        self.root.after(self.STATUS_MS, self._refresh_status)

    # ── Periodic channel list refresh ────────────────────────────────────────

    def _refresh_channels_periodic(self):
        self._refresh_channels()
        self.root.after(self.CHAN_MS, self._refresh_channels_periodic)

    # ── Scheduling ────────────────────────────────────────────────────────────

    def _schedule_polls(self):
        self.root.after(self.POLL_MS,   self._poll_queue)
        self.root.after(self.STATUS_MS, self._refresh_status)
        self.root.after(self.CHAN_MS,   self._refresh_channels_periodic)

    # ── Quit ─────────────────────────────────────────────────────────────────

    def _quit(self):
        # Save window geometry before closing
        try:
            self._prefs["window_geometry"] = self.root.geometry()
            _save_prefs(self._prefs)
        except Exception:
            pass
        if self.gt:
            try:
                self.gt.stop()
            except Exception:
                pass
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr
        self.root.destroy()


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    root.title("GeoTalk")

    # Window icon (amber diamond drawn as a tiny bitmap)
    try:
        icon = tk.PhotoImage(width=32, height=32)
        for y in range(32):
            for x in range(32):
                if abs(x - 16) + abs(y - 16) < 12:
                    icon.put("#e8a030", (x, y))
                else:
                    icon.put("#0d0e0f", (x, y))
        root.iconphoto(True, icon)
    except Exception:
        pass

    root.geometry("960x640")

    app = GeoTalkGUI(root)
    root.protocol("WM_DELETE_WINDOW", app._quit)
    root.mainloop()


if __name__ == "__main__":
    main()
