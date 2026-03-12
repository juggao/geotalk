#!/usr/bin/env python3
"""
geotalk-radio1.py — GeoTalk NPO Radio 1 Streaming Daemon  v2.7.0
Author: René Oudeweg / Claude
──────────────────────────────────────────────────────────────────────────────
Joins channel #STREAM:NPO-Radio1 on a GeoTalk relay and re-broadcasts the
NPO Radio 1 Icecast stream to all subscribers.

Stream source:
  https://icecast.omroep.nl/radio1-bb-aac  (AAC 2ch 48 kHz)

Audio pipeline:
  ffmpeg → s16le mono 48 kHz on stdout
  → 960-sample chunks (20 ms Opus frames)
  → opuslib encode at 96 kbit/s
  → PKT_AUDIO packets → relay → subscribers

The relay drops packets for channels with no subscribers, so the daemon
always streams — no subscriber-polling complexity.  ICY now-playing titles
are parsed from ffmpeg stderr and sent as PKT_TEXT when the title changes.

Requirements:
  ffmpeg            apt install ffmpeg
  opuslib           pip install opuslib   (strongly recommended)

Usage:
  python3 geotalk-radio1.py --relay HOST [OPTIONS]

Options:
  --relay HOST      GeoTalk relay hostname or IP  (required)
  --port  PORT      Relay UDP port                (default 5073)
  --nick  NICK      Bot nick                      (default Radio1)
  --channel NAME    Channel key                    (default STREAM:NPO-Radio1)
  --stream-url URL  Icecast URL
  --bitrate BPS     Opus bitrate bits/s            (default 96000)
  --daemonize       Fork to background
  --log-file PATH   Log file (required with --daemonize)
  --pid-file PATH   PID file
  --dry-run         Decode stream, do not transmit
  --verbose         Debug logging

Examples:
  python3 geotalk-radio1.py --relay geotalk.net
  python3 geotalk-radio1.py --relay geotalk.net --daemonize \
          --log-file /var/log/geotalk/radio1.log
"""

import sys
import os
import socket
import struct
import time
import json
import argparse
import logging
import threading
import subprocess
import signal
import re

VERSION            = "2.7.0"
MAGIC              = b"GT"
PKT_TEXT           = 0x01
PKT_AUDIO          = 0x02
PKT_PING           = 0x04
PKT_JOIN           = 0x10
PKT_LEAVE          = 0x11

AUDIO_RATE         = 48000
AUDIO_CHUNK        = 960
AUDIO_BYTES        = AUDIO_CHUNK * 2   # int16 LE mono = 1920 bytes/frame

DEFAULT_STREAM_URL = "https://icecast.omroep.nl/radio1-bb-aac"
DEFAULT_CHANNEL    = "STREAM:NPO-Radio1"
DEFAULT_BITRATE    = 96000
PING_INTERVAL      = 45
RECONNECT_BASE     = 3.0
RECONNECT_MAX      = 60.0

log = logging.getLogger("geotalk-radio1")

def _setup_logging(log_file: str = "", verbose: bool = False):
    level    = logging.DEBUG if verbose else logging.INFO
    fmt      = "%(asctime)s  %(levelname)-7s  %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)

# ── packet encoding ────────────────────────────────────────────────────────

def _pack(ptype, payload):
    return MAGIC + bytes([ptype]) + struct.pack("!H", len(payload)) + payload

def encode_join(nick, channel):
    return _pack(PKT_JOIN,
                 json.dumps({"n": nick, "p": channel,
                             "ts": int(time.time())}).encode())

def encode_leave(nick, channel):
    return _pack(PKT_LEAVE,
                 json.dumps({"n": nick, "p": channel,
                             "ts": int(time.time())}).encode())

def encode_text(nick, channel, text):
    return _pack(PKT_TEXT,
                 json.dumps({"n": nick, "p": channel, "t": text,
                             "id": 0, "ts": int(time.time())}).encode())

def encode_audio(nick, channel, seq, audio, codec="opus"):
    hdr = json.dumps({"n": nick, "p": channel,
                      "s": seq, "codec": codec}).encode()
    return (MAGIC + bytes([PKT_AUDIO])
            + struct.pack("!H", len(hdr)) + hdr
            + struct.pack("!H", len(audio)) + audio)

def encode_ping(nick, channel):
    return _pack(PKT_PING,
                 json.dumps({"n": nick, "p": channel,
                             "ts": int(time.time())}).encode())

# ── opus ───────────────────────────────────────────────────────────────────

def _make_opus(bitrate):
    try:
        import opuslib
        enc = opuslib.Encoder(AUDIO_RATE, 1, opuslib.APPLICATION_AUDIO)
        enc.bitrate = bitrate
        return enc
    except Exception:
        return None

# ── ffmpeg command ─────────────────────────────────────────────────────────

def _ffmpeg_cmd(url):
    return [
        "ffmpeg",
        "-loglevel",           "info",
        "-reconnect",          "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max","5",
        "-i",                  url,
        "-f",                  "s16le",
        "-ac",                 "1",
        "-ar",                 "48000",
        "-vn",
        "pipe:1",
    ]

_ICY_RE  = re.compile(r"icy-title\s*:\s*(.+)",  re.IGNORECASE)
_ICY_RE2 = re.compile(r"StreamTitle='([^']*)'", re.IGNORECASE)

def _parse_icy(line):
    m = _ICY_RE.search(line)
    if m:
        return m.group(1).strip()
    m = _ICY_RE2.search(line)
    if m:
        return m.group(1).strip()
    return None

# ── daemon ─────────────────────────────────────────────────────────────────

class Radio1Daemon:
    def __init__(self, relay, port, nick, channel, stream_url, bitrate, dry_run):
        self.relay      = relay
        self.port       = port
        self.nick       = nick
        self.channel    = channel
        self.stream_url = stream_url
        self.dry_run    = dry_run

        self._opus        = _make_opus(bitrate)
        self._seq         = 0
        self._frames_sent = 0
        self._reconnects  = 0
        self._running     = False
        self._sock        = None
        self._icy_title   = ""

    def start(self):
        self._running = True

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(1.0)
        try:
            ip = socket.gethostbyname(self.relay)
            self._sock.connect((ip, self.port))
        except socket.gaierror as e:
            log.error(f"Cannot resolve relay '{self.relay}': {e}")
            sys.exit(1)

        log.info(f"Relay  : {self.relay}:{self.port}")
        log.info(f"Channel: #{self.channel}")
        log.info(f"URL    : {self.stream_url}")
        log.info(f"Opus   : {'%d kbit/s' % (self._opus.bitrate // 1000) if self._opus else 'unavailable — raw PCM'}")

        if not self.dry_run:
            self._send(encode_join(self.nick, self.channel))
            log.info(f"JOIN sent → #{self.channel}")

        threading.Thread(target=self._ping_loop, daemon=True,
                         name="r1-ping").start()
        try:
            self._run_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            if not self.dry_run:
                self._send(encode_leave(self.nick, self.channel))
            self._sock.close()
            log.info(f"Stopped — frames={self._frames_sent}  "
                     f"reconnects={self._reconnects}")

    def stop(self):
        self._running = False

    def _ping_loop(self):
        while self._running:
            time.sleep(PING_INTERVAL)
            if self._running and not self.dry_run:
                self._send(encode_ping(self.nick, self.channel))
                log.debug("PING sent")


    def _run_loop(self):
        """Outer reconnect loop — starts ffmpeg, streams PCM, reconnects on exit."""
        backoff = RECONNECT_BASE
        while self._running:
            log.info("Starting ffmpeg…")
            try:
                proc = subprocess.Popen(
                    _ffmpeg_cmd(self.stream_url),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except FileNotFoundError:
                log.error("ffmpeg not found — install: sudo apt install ffmpeg")
                sys.exit(1)

            log.info(f"ffmpeg started  pid={proc.pid}")

            threading.Thread(target=self._stderr_reader, args=(proc,),
                             daemon=True, name="r1-stderr").start()

            clean = self._stream_pcm(proc.stdout)

            # Shut down ffmpeg
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

            if not self._running:
                break

            self._reconnects += 1
            backoff = RECONNECT_BASE if clean else min(backoff * 2, RECONNECT_MAX)
            log.warning(f"Stream ended — reconnect #{self._reconnects} "
                        f"in {backoff:.0f}s")
            deadline = time.monotonic() + backoff
            while self._running and time.monotonic() < deadline:
                time.sleep(0.5)

    def _stream_pcm(self, stdout):
        """
        Read s16le mono 48 kHz from stdout, encode to Opus, send PKT_AUDIO.
        Returns True on clean EOF, False on read error.
        """
        frame_dur = AUDIO_CHUNK / AUDIO_RATE   # 0.020 s
        deadline  = time.monotonic()
        logged    = False

        while self._running:
            # Read exactly AUDIO_BYTES (one 20 ms frame)
            buf = bytearray()
            while len(buf) < AUDIO_BYTES:
                try:
                    chunk = stdout.read(AUDIO_BYTES - len(buf))
                except (OSError, ValueError):
                    return False
                if not chunk:
                    return True   # clean EOF
                buf.extend(chunk)

            if not logged:
                log.info("Audio flowing ✓")
                logged = True

            if self._opus:
                try:
                    audio = self._opus.encode(bytes(buf), AUDIO_CHUNK)
                    codec = "opus"
                except Exception as e:
                    log.debug(f"Opus error: {e}")
                    audio = bytes(buf)
                    codec = "pcm"
            else:
                audio = bytes(buf)
                codec = "pcm"

            if not self.dry_run:
                self._send(encode_audio(self.nick, self.channel,
                                        self._seq, audio, codec))

            self._seq         += 1
            self._frames_sent += 1

            if self._frames_sent % 500 == 0:
                log.debug(f"frames={self._frames_sent}")

            # Real-time pacing
            deadline += frame_dur
            slack = deadline - time.monotonic()
            if slack > 0.001:
                time.sleep(slack)
            elif slack < -0.5:
                deadline = time.monotonic()

        return True

    def _stderr_reader(self, proc):
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                log.debug(f"[ffmpeg] {line}")
                title = _parse_icy(line)
                if title and title != self._icy_title:
                    self._icy_title = title
                    log.info(f"Now playing: {title}")
                    if not self.dry_run:
                        self._send(encode_text(
                            self.nick, self.channel, f"🎵 {title}"))
        except (OSError, ValueError):
            pass

    def _send(self, data):
        try:
            self._sock.send(data)
        except OSError as e:
            log.debug(f"Send error: {e}")

# ── daemonise ──────────────────────────────────────────────────────────────

def _daemonize(log_file):
    if not log_file:
        print("ERROR: --log-file required with --daemonize", file=sys.stderr)
        sys.exit(1)
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    sys.stdin  = open(os.devnull, "r")
    sys.stdout = open(log_file, "a", buffering=1)
    sys.stderr = sys.stdout

def _default_pid():
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                        "geotalk-radio1.pid")

# ── main ───────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        prog="geotalk-radio1",
        description=f"GeoTalk NPO Radio 1 Streaming Daemon v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--relay",      required=True)
    p.add_argument("--port",       type=int, default=5073)
    p.add_argument("--nick",       default="Radio1")
    p.add_argument("--channel",    default=DEFAULT_CHANNEL)
    p.add_argument("--stream-url", default=DEFAULT_STREAM_URL)
    p.add_argument("--bitrate",    type=int, default=DEFAULT_BITRATE)
    p.add_argument("--daemonize",  action="store_true")
    p.add_argument("--log-file",   default="")
    p.add_argument("--pid-file",   default="")
    p.add_argument("--dry-run",    action="store_true")
    p.add_argument("--verbose",    action="store_true")
    p.add_argument("--version",    action="version",
                   version=f"geotalk-radio1 {VERSION}")
    args = p.parse_args()

    if args.daemonize:
        _daemonize(args.log_file)

    _setup_logging(args.log_file, args.verbose)
    log.info(f"geotalk-radio1 v{VERSION}")

    pid = args.pid_file or _default_pid()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(pid)), exist_ok=True)
        open(pid, "w").write(str(os.getpid()) + "\n")
    except OSError:
        pass

    daemon = Radio1Daemon(
        relay      = args.relay,
        port       = args.port,
        nick       = args.nick,
        channel    = args.channel,
        stream_url = args.stream_url,
        bitrate    = args.bitrate,
        dry_run    = args.dry_run,
    )

    signal.signal(signal.SIGTERM, lambda s, f: daemon.stop())
    signal.signal(signal.SIGINT,  lambda s, f: daemon.stop())

    try:
        daemon.start()
    finally:
        try:
            os.unlink(pid)
        except OSError:
            pass

if __name__ == "__main__":
    main()
