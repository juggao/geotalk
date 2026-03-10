#!/usr/bin/env python3
"""
geotalk-timed.py — GeoTalk Time Announcement Daemon  v2.3.3
Author: René Oudeweg / Claude
──────────────────────────────────────────────────────────────────────────────
Connects to a GeoTalk relay as a bot on channel #TIME.  On a configurable
schedule (default: top of every minute) it:

  1. Speaks the current date and time via TTS and transmits the audio as
     Opus-encoded PKT_AUDIO frames to all #TIME subscribers.
  2. Sends a PKT_TEXT message with the same date/time string.

TTS backend priority (first available wins):
  1. espeak-ng  — apt install espeak-ng          (recommended, zero Python deps)
  2. pyttsx3    — pip install pyttsx3             (uses espeak-ng / Festival)
  3. gtts       — pip install gtts + ffmpeg       (Google TTS, needs internet)
  4. Tone-only  — synthesised 1-kHz beep + text   (fallback, no speech)

Audio pipeline:
  espeak-ng / pyttsx3 → 16-bit mono PCM at 48 kHz
  → chunked into 960-sample Opus frames (20 ms each)
  → sent as PKT_AUDIO packets to the relay

Usage
─────
  python3 geotalk-timed.py --relay HOST [OPTIONS]

Options
  --relay HOST        Relay server hostname or IP  (required)
  --port PORT         Relay UDP port               (default 5073)
  --nick NICK         Bot nick                     (default TIME)
  --postal CODE       Postal code for region tag   (default 00000)
  --interval SECS     Seconds between announcements (default 60)
  --align             Align announcements to clock boundaries (default on)
  --no-align          Disable clock alignment
  --channel NAME      Channel key to join          (default TIME)
  --language LANG     TTS language tag             (default en)
  --voice VOICE       espeak-ng voice name         (default en)
  --daemonize         Fork to background
  --log-file PATH     Log file (required with --daemonize)
  --pid-file PATH     PID file path
  --dry-run           Generate audio locally, do not transmit

Examples
  python3 geotalk-timed.py --relay geotalk.net
  python3 geotalk-timed.py --relay 192.168.1.10 --interval 3600 --no-align
  python3 geotalk-timed.py --relay geotalk.net --daemonize --log-file /var/log/geotalk-timed.log
"""

import sys
import os
import socket
import struct
import time
import json
import math
import wave
import io
import argparse
import logging
import threading
import subprocess
import tempfile
import signal
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# VERSION
# ─────────────────────────────────────────────────────────────────────────────
VERSION = "2.3.3"

# ─────────────────────────────────────────────────────────────────────────────
# WIRE PROTOCOL  (matches geotalk.py / geotalk-relayd.py exactly)
# ─────────────────────────────────────────────────────────────────────────────
MAGIC       = b"GT"
PKT_TEXT    = 0x01
PKT_AUDIO   = 0x02
PKT_PING    = 0x04
PKT_JOIN    = 0x10
PKT_LEAVE   = 0x11

AUDIO_RATE   = 48000
AUDIO_CHUNK  = 960      # 20 ms at 48 kHz
AUDIO_BYTES  = AUDIO_CHUNK * 2  # int16 LE

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("geotalk-timed")


def _setup_logging(log_file: str = "", verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt   = "%(asctime)s  %(levelname)-7s  %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


# ─────────────────────────────────────────────────────────────────────────────
# PACKET ENCODING
# ─────────────────────────────────────────────────────────────────────────────

def _pack(ptype: int, payload: bytes) -> bytes:
    return MAGIC + bytes([ptype]) + struct.pack("!H", len(payload)) + payload


def encode_join(nick: str, channel: str) -> bytes:
    payload = json.dumps({"n": nick, "p": channel,
                          "ts": int(time.time())}).encode()
    return _pack(PKT_JOIN, payload)


def encode_leave(nick: str, channel: str) -> bytes:
    payload = json.dumps({"n": nick, "p": channel,
                          "ts": int(time.time())}).encode()
    return _pack(PKT_LEAVE, payload)


def encode_text(nick: str, channel: str, text: str, postal: str = "00000") -> bytes:
    payload = json.dumps({"n": nick, "p": channel, "t": text,
                          "id": 0, "ts": int(time.time())}).encode()
    return _pack(PKT_TEXT, payload)


def encode_audio(nick: str, channel: str, seq: int,
                 audio: bytes, codec: str = "opus") -> bytes:
    header = json.dumps({"n": nick, "p": channel,
                         "s": seq, "codec": codec}).encode()
    return (MAGIC + bytes([PKT_AUDIO])
            + struct.pack("!H", len(header)) + header
            + struct.pack("!H", len(audio)) + audio)


def encode_ping(nick: str, channel: str) -> bytes:
    payload = json.dumps({"n": nick, "p": channel,
                          "ts": int(time.time())}).encode()
    return _pack(PKT_PING, payload)


# ─────────────────────────────────────────────────────────────────────────────
# TTS BACKENDS
# ─────────────────────────────────────────────────────────────────────────────

def _tts_espeak(text: str, voice: str = "en") -> bytes | None:
    """
    Use espeak-ng to synthesise text → raw 16-bit mono PCM at 48 kHz.
    espeak-ng outputs WAV at its native rate (typically 22050 Hz);
    _wav_bytes_to_pcm48k handles the resampling to 48 kHz.
    NOTE: --rate controls words-per-minute in espeak-ng, NOT sample rate.
    """
    try:
        result = subprocess.run(
            ["espeak-ng",
             "-v", voice,
             "-s", "140",        # words per minute (speaking speed)
             "-a", "180",        # amplitude 0-200
             "--stdout",         # write WAV to stdout
             text],
            capture_output=True, timeout=15)
        if result.returncode != 0 or not result.stdout:
            log.warning(f"espeak-ng returned rc={result.returncode} "
                        f"stdout={len(result.stdout)}B "
                        f"stderr={result.stderr[:120]!r}")
            return None
        log.debug(f"espeak-ng produced {len(result.stdout)} WAV bytes")
        pcm = _wav_bytes_to_pcm48k(result.stdout)
        if pcm is None:
            log.warning("espeak-ng WAV → PCM conversion returned None")
        return pcm
    except FileNotFoundError:
        log.debug("espeak-ng not found in PATH")
        return None
    except Exception as e:
        log.warning(f"espeak-ng exception: {e}")
        return None


def _tts_pyttsx3(text: str, lang: str = "en") -> bytes | None:
    """Use pyttsx3 to generate speech → WAV → PCM."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 140)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        try:
            engine.save_to_file(text, tmp)
            engine.runAndWait()
            if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
                return None
            with open(tmp, "rb") as f:
                return _wav_bytes_to_pcm48k(f.read())
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except (ImportError, Exception):
        return None


def _tts_gtts(text: str, lang: str = "en") -> bytes | None:
    """Use gTTS (Google Text-to-Speech) → MP3 → PCM via ffmpeg."""
    try:
        from gtts import gTTS
        buf = io.BytesIO()
        gTTS(text=text, lang=lang).write_to_fp(buf)
        mp3_data = buf.getvalue()
        if not mp3_data:
            return None
        # Convert MP3 → raw PCM 48 kHz mono via ffmpeg
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", "pipe:0",
             "-f", "s16le", "-ac", "1", "-ar", "48000", "pipe:1"],
            input=mp3_data, capture_output=True, timeout=20)
        if result.returncode != 0 or not result.stdout:
            return None
        return result.stdout
    except (ImportError, FileNotFoundError, Exception):
        return None


def _tts_tone_fallback(text: str) -> bytes:
    """
    Synthesise a 1-kHz identification tone followed by silence.
    Used when no TTS engine is available — at least signals the time slot.
    Duration ~1.5 s.
    """
    duration   = 1.5   # seconds
    n_samples  = int(AUDIO_RATE * duration)
    tone_len   = int(AUDIO_RATE * 0.3)   # 0.3 s tone burst
    freq       = 1000
    samples    = []
    for i in range(n_samples):
        if i < tone_len:
            # Hann-windowed sine to avoid clicks at start/end
            window = 0.5 * (1 - math.cos(2 * math.pi * i / tone_len))
            s = int(16000 * window * math.sin(2 * math.pi * freq * i / AUDIO_RATE))
        else:
            s = 0
        samples.append(max(-32767, min(32767, s)))
    return struct.pack(f"<{n_samples}h", *samples)


def _wav_bytes_to_pcm48k(wav_data: bytes) -> bytes | None:
    """
    Read a WAV blob and return raw 16-bit mono PCM at 48 kHz.
    Conversion pipeline (first available):
      1. Already 48 kHz mono int16 → return as-is
      2. ffmpeg available          → resample via ffmpeg
      3. Pure-Python linear resample (no deps, slightly lower quality)
    """
    try:
        buf = io.BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            src_rate = wf.getframerate()
            n_ch     = wf.getnchannels()
            sampw    = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw      = wf.readframes(n_frames)
    except Exception as e:
        log.warning(f"WAV decode error: {e}")
        return None

    # ── step 1: downmix to mono if needed ────────────────────────────────────
    if n_ch == 2 and sampw == 2:
        # simple average of L+R int16 samples
        n = len(raw) // 4
        mono = bytearray(n * 2)
        for i in range(n):
            l = struct.unpack_from("<h", raw, i * 4)[0]
            r = struct.unpack_from("<h", raw, i * 4 + 2)[0]
            m = max(-32768, min(32767, (l + r) >> 1))
            struct.pack_into("<h", mono, i * 2, m)
        raw  = bytes(mono)
        n_ch = 1

    # ── already correct? ─────────────────────────────────────────────────────
    if src_rate == AUDIO_RATE and n_ch == 1 and sampw == 2:
        return raw

    # ── step 2: try ffmpeg ────────────────────────────────────────────────────
    try:
        fmt = f"s{sampw * 8}le"
        result = subprocess.run(
            ["ffmpeg", "-y",
             "-f", fmt, "-ac", str(n_ch), "-ar", str(src_rate),
             "-i", "pipe:0",
             "-f", "s16le", "-ac", "1", "-ar", "48000", "pipe:1"],
            input=raw, capture_output=True, timeout=20)
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except (FileNotFoundError, Exception):
        pass

    # ── step 3: pure-Python linear resample ───────────────────────────────────
    if sampw != 2 or n_ch != 1:
        log.warning("Pure-Python resampler only handles mono int16 input")
        return None
    try:
        n_src = len(raw) // 2
        src   = struct.unpack(f"<{n_src}h", raw)
        n_dst = int(n_src * AUDIO_RATE / src_rate)
        out   = []
        for i in range(n_dst):
            pos  = i * src_rate / AUDIO_RATE
            lo   = int(pos)
            hi   = min(lo + 1, n_src - 1)
            frac = pos - lo
            s    = int(src[lo] * (1 - frac) + src[hi] * frac)
            out.append(max(-32768, min(32767, s)))
        return struct.pack(f"<{n_dst}h", *out)
    except Exception as e:
        log.warning(f"Pure-Python resample error: {e}")
        return None


def _detect_tts(voice: str, lang: str) -> tuple[str, callable]:
    """
    Probe available TTS engines in priority order.
    Returns (backend_name, callable(text) -> pcm_bytes).
    """
    # 1. espeak-ng
    try:
        r = subprocess.run(["espeak-ng", "--version"],
                            capture_output=True, timeout=5)
        if r.returncode == 0:
            log.info("TTS backend: espeak-ng")
            return ("espeak-ng", lambda t: _tts_espeak(t, voice))
    except (FileNotFoundError, Exception):
        pass

    # 2. pyttsx3
    try:
        import pyttsx3  # noqa: F401
        log.info("TTS backend: pyttsx3")
        return ("pyttsx3", lambda t: _tts_pyttsx3(t, lang))
    except ImportError:
        pass

    # 3. gtts + ffmpeg
    try:
        from gtts import gTTS  # noqa: F401
        r = subprocess.run(["ffmpeg", "-version"],
                            capture_output=True, timeout=5)
        if r.returncode == 0:
            log.info("TTS backend: gtts + ffmpeg")
            return ("gtts", lambda t: _tts_gtts(t, lang))
    except (ImportError, FileNotFoundError, Exception):
        pass

    # 4. Tone fallback
    log.warning("No TTS engine found — using 1-kHz tone fallback. "
                "Install espeak-ng for speech: sudo apt install espeak-ng")
    return ("tone", lambda t: _tts_tone_fallback(t))


# ─────────────────────────────────────────────────────────────────────────────
# PCM → Opus encoder
# ─────────────────────────────────────────────────────────────────────────────

def _make_opus_encoder():
    try:
        import opuslib
        enc = opuslib.Encoder(AUDIO_RATE, 1, opuslib.APPLICATION_VOIP)
        enc.bitrate = 32000
        return enc
    except (ImportError, Exception):
        return None


def _pcm_to_frames(pcm: bytes) -> list[bytes]:
    """Chop raw int16 PCM into AUDIO_CHUNK-sample frames, zero-padding the last."""
    frames = []
    pos = 0
    while pos < len(pcm):
        chunk = pcm[pos: pos + AUDIO_BYTES]
        if len(chunk) < AUDIO_BYTES:
            chunk = chunk + b"\x00" * (AUDIO_BYTES - len(chunk))
        frames.append(chunk)
        pos += AUDIO_BYTES
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# ANNOUNCEMENT TEXT
# ─────────────────────────────────────────────────────────────────────────────

def _make_announcement(now: datetime) -> tuple[str, str]:
    """
    Return (tts_text, display_text) for the current time.
    tts_text  — natural language spoken form, e.g. "GeoTalk time. Monday, 10 March 2026. The time is 14 hours and 5 minutes."
    display_text — compact string for the text packet, e.g. "📡 TIME  Mon 10 Mar 2026  14:05"
    """
    day_name  = now.strftime("%A")
    day_num   = now.day
    month     = now.strftime("%B")
    year      = now.year
    hour      = now.hour
    minute    = now.minute

    # Natural spoken text
    min_str = "0" if minute == 0 else str(minute)
    if minute == 0:
        min_phrase = "exactly"
    elif minute == 1:
        min_phrase = "1 minute"
    else:
        min_phrase = f"{minute} minutes"

    tts = (f"GeoTalk time. "
           f"{day_name}, {day_num} {month} {year}. "
           f"The time is {hour} hours"
           + (f" and {min_phrase}." if minute != 0 else f", {min_phrase}."))

    display = (f"📡 TIME  {now.strftime('%a %d %b %Y')}  "
               f"{hour:02d}:{minute:02d}")

    return tts, display


# ─────────────────────────────────────────────────────────────────────────────
# RELAY CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class TimedClient:
    """
    Minimal UDP relay client that joins #TIME, sends periodic announcements,
    and keeps the subscription alive with PING packets.
    """

    PING_INTERVAL = 60   # seconds between keep-alive pings

    def __init__(self, relay: str, port: int, nick: str,
                 postal: str, channel: str,
                 tts_fn: callable, opus_enc,
                 interval: int, align: bool,
                 dry_run: bool):
        self.relay    = relay
        self.port     = port
        self.nick     = nick
        self.postal   = postal
        self.channel  = channel
        self.tts_fn   = tts_fn
        self.opus_enc = opus_enc
        self.interval = interval
        self.align    = align
        self.dry_run  = dry_run

        self._sock    = None
        self._seq     = 0
        self._running = False

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self._running = True

        # Open UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(5.0)
        try:
            self._dest = (socket.gethostbyname(self.relay), self.port)
        except socket.gaierror as e:
            log.error(f"Cannot resolve relay host '{self.relay}': {e}")
            sys.exit(1)

        log.info(f"Connecting to relay {self._dest[0]}:{self._dest[1]} "
                 f"as nick={self.nick!r}  channel={self.channel!r}")

        if not self.dry_run:
            self._send(encode_join(self.nick, self.channel))
            log.info(f"Joined #{self.channel}")

        # Start ping thread
        ping_t = threading.Thread(target=self._ping_loop, daemon=True,
                                   name="timed-ping")
        ping_t.start()

        # Main announcement loop
        try:
            self._announce_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            if not self.dry_run:
                self._send(encode_leave(self.nick, self.channel))
            self._sock.close()
            log.info("Stopped.")

    def stop(self):
        self._running = False

    # ── ping keep-alive ───────────────────────────────────────────────────────

    def _ping_loop(self):
        while self._running:
            time.sleep(self.PING_INTERVAL)
            if self._running and not self.dry_run:
                self._send(encode_ping(self.nick, self.channel))
                log.debug("PING sent")

    # ── announcement loop ─────────────────────────────────────────────────────

    def _announce_loop(self):
        log.info(f"Announcement interval: {self.interval}s  "
                 f"align={'on' if self.align else 'off'}")

        while self._running:
            # Wait until the next aligned boundary (or just interval seconds)
            self._wait_for_next()
            if not self._running:
                break
            now = datetime.now()
            self._announce(now)

    def _wait_for_next(self):
        """Sleep until the next aligned interval boundary."""
        if self.align and self.interval >= 10:
            # Sleep until the next multiple of interval seconds past the epoch
            now   = time.time()
            nxt   = (math.floor(now / self.interval) + 1) * self.interval
            delay = nxt - now
        else:
            delay = self.interval

        log.debug(f"Next announcement in {delay:.1f}s")

        # Sleep in short chunks so we can respond to stop quickly
        deadline = time.monotonic() + delay
        while self._running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))

    # ── single announcement ───────────────────────────────────────────────────

    def _announce(self, now: datetime):
        tts_text, display_text = _make_announcement(now)
        log.info(f"Announcing: {display_text}")

        # Generate PCM via TTS
        pcm = self.tts_fn(tts_text)
        if pcm is None:
            log.warning("TTS failed — using tone fallback")
            pcm = _tts_tone_fallback(tts_text)

        if self.dry_run:
            log.info(f"[DRY-RUN] Would transmit {len(pcm)} PCM bytes "
                     f"({len(pcm)/AUDIO_BYTES} frames) + text: {display_text}")
            return

        # Send text packet first
        self._send(encode_text(self.nick, self.channel, display_text))

        # Encode and stream audio frames
        frames     = _pcm_to_frames(pcm)
        frame_dur  = AUDIO_CHUNK / AUDIO_RATE  # ~0.020 s
        n_frames   = len(frames)
        t0         = time.monotonic()

        log.debug(f"Streaming {n_frames} audio frames "
                  f"({n_frames * frame_dur:.1f}s)")

        for i, raw_frame in enumerate(frames):
            if not self._running:
                break

            if self.opus_enc:
                try:
                    audio = self.opus_enc.encode(raw_frame, AUDIO_CHUNK)
                    codec = "opus"
                except Exception:
                    audio = raw_frame
                    codec = "pcm"
            else:
                audio = raw_frame
                codec = "pcm"

            pkt = encode_audio(self.nick, self.channel, self._seq,
                                audio, codec)
            self._send(pkt)
            self._seq += 1

            # Pace frames to real-time so the receiver's jitter buffer
            # is not flooded (and doesn't have to buffer the whole utterance)
            target = t0 + (i + 1) * frame_dur
            sleep  = target - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)

        log.debug("Announcement complete")

    # ── socket send ───────────────────────────────────────────────────────────

    def _send(self, data: bytes):
        try:
            self._sock.sendto(data, self._dest)
        except OSError as e:
            log.warning(f"Send error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# DAEMONISE (double-fork, Unix only)
# ─────────────────────────────────────────────────────────────────────────────

def _daemonize(log_file: str):
    if not log_file:
        print("ERROR: --log-file is required with --daemonize", file=sys.stderr)
        sys.exit(1)
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    sys.stdin  = open(os.devnull, "r")
    sys.stdout = open(log_file, "a", buffering=1)
    sys.stderr = sys.stdout


def _write_pid(path: str):
    if path:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                f.write(str(os.getpid()) + "\n")
        except OSError as e:
            log.warning(f"Could not write PID file {path}: {e}")


def _remove_pid(path: str):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def _default_pid_file() -> str:
    rt = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return os.path.join(rt, "geotalk-timed.pid")


def main():
    parser = argparse.ArgumentParser(
        prog="geotalk-timed",
        description=f"GeoTalk Time Announcement Daemon v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 geotalk-timed.py --relay geotalk.net
  python3 geotalk-timed.py --relay 192.168.1.10 --interval 3600 --no-align
  python3 geotalk-timed.py --relay geotalk.net --language nl --voice nl
  python3 geotalk-timed.py --relay geotalk.net --daemonize \\
          --log-file /var/log/geotalk-timed.log

TTS setup (choose one):
  sudo apt install espeak-ng          # recommended
  pip install pyttsx3                 # alternative
  pip install gtts && apt install ffmpeg  # Google TTS (internet required)
""")

    parser.add_argument("--relay",    required=True, metavar="HOST",
                        help="Relay server hostname or IP")
    parser.add_argument("--port",     type=int, default=5073, metavar="PORT",
                        help="Relay UDP port (default 5073)")
    parser.add_argument("--nick",     default="TIME", metavar="NICK",
                        help="Bot nick (default TIME)")
    parser.add_argument("--postal",   default="00000", metavar="CODE",
                        help="Postal code / region tag (default 00000)")
    parser.add_argument("--channel",  default="TIME", metavar="NAME",
                        help="Channel key to join (default TIME)")
    parser.add_argument("--interval", type=int, default=60, metavar="SECS",
                        help="Seconds between announcements (default 60)")
    parser.add_argument("--align",    dest="align", action="store_true",
                        default=True,
                        help="Align to clock boundaries (default on)")
    parser.add_argument("--no-align", dest="align", action="store_false",
                        help="Disable clock alignment")
    parser.add_argument("--language", default="en", metavar="LANG",
                        help="TTS language code (default en)")
    parser.add_argument("--voice",    default="en", metavar="VOICE",
                        help="espeak-ng voice name (default en)")
    parser.add_argument("--daemonize", action="store_true",
                        help="Fork to background (Unix only)")
    parser.add_argument("--log-file", default="", metavar="PATH",
                        help="Log file path (required with --daemonize)")
    parser.add_argument("--pid-file", default="", metavar="PATH",
                        help=f"PID file (default: {_default_pid_file()})")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Generate audio but do not transmit")
    parser.add_argument("--verbose",  action="store_true",
                        help="Enable debug logging")
    parser.add_argument("--version",  action="version",
                        version=f"geotalk-timed {VERSION}")

    args = parser.parse_args()

    if args.daemonize:
        _daemonize(args.log_file)

    _setup_logging(args.log_file, args.verbose)
    log.info(f"geotalk-timed v{VERSION} starting")

    pid_file = args.pid_file or _default_pid_file()
    _write_pid(pid_file)

    # Detect TTS
    backend, tts_fn = _detect_tts(args.voice, args.language)
    log.info(f"TTS backend: {backend}  voice/lang={args.voice}/{args.language}")

    # Opus encoder (optional)
    opus_enc = _make_opus_encoder()
    if opus_enc:
        log.info("Opus encoder: available (32 kbit/s)")
    else:
        log.info("Opus encoder: unavailable — transmitting raw PCM")

    client = TimedClient(
        relay    = args.relay,
        port     = args.port,
        nick     = args.nick,
        postal   = args.postal,
        channel  = args.channel,
        tts_fn   = tts_fn,
        opus_enc = opus_enc,
        interval = args.interval,
        align    = args.align,
        dry_run  = args.dry_run,
    )

    # Clean shutdown on signal
    def _sig(sig, frame):
        log.info(f"Signal {sig} received — stopping")
        client.stop()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    try:
        client.start()
    finally:
        _remove_pid(pid_file)


if __name__ == "__main__":
    main()
