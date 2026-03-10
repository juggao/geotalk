#!/usr/bin/env python3
"""
geotalk-relayd.py — GeoTalk Relay Daemon  v2.3.3
Author: René Oudeweg / Claude
─────────────────────────────────────────────────────────
Drop-in replacement for geotalk-relay.py that runs as a proper
background daemon and exposes a Unix-domain control socket so that
geotalk-relay-cli (or the GUI) can query and manage it at any time
without being attached to the process's stdin/stdout.

Daemon behaviour
  • Writes a PID file on startup, removes it on clean exit.
  • Optionally daemonises (double-fork) with --daemonize.
  • All console output goes to --log-file when daemonised.
  • SIGTERM / SIGINT trigger a clean shutdown.

Control socket  (--ctl-socket PATH)
  Default: /tmp/geotalk-relayd.sock  (or XDG_RUNTIME_DIR if set)
  Protocol: newline-delimited JSON over a Unix stream socket.
    Request :  {"cmd": "stats"}\\n
    Response:  {"ok": true,  "data": {...}}\\n
             | {"ok": false, "error": "..."}\\n
  Each connected CLI gets its own thread; multiple CLIs may be
  connected simultaneously (e.g. GUI + shell).

Supported control commands
  stats                        Runtime statistics snapshot
  channels                     Active channels with subscriber nicks
  clients                      All connected clients
  bbs [CHANNEL]                BBS summary or per-channel detail
  bbs-clear CHANNEL            Delete all BBS messages for a channel
  bbs-post CHANNEL TEXT        Post a BBS message as operator
  kick NICK                    Drop a client by nickname
  ban IP                       Block an IP address
  unban IP                     Remove an IP ban
  bans                         List banned IPs
  log [N]                      Last N log lines (default 100)
  quiet [on|off]               Toggle per-packet console output
  stop                         Initiate clean shutdown

GeoTalk packet protocol (unchanged from geotalk-relay.py)
  See geotalk-relay.py for full packet type documentation.

Usage
  python3 geotalk-relayd.py [options]

Options
  --host HOST            Bind address (default 0.0.0.0)
  --port PORT            UDP port     (default 5073)
  --ttl  SECONDS         Client subscription TTL (default 300)
  --max-per-channel N    Max clients per channel  (default 128)
  --bbs-file PATH        JSON file for BBS persistence
  --bbs-max N            Max BBS messages per channel (default 50)
  --log-file PATH        Log file path (required when --daemonize)
  --quiet                Suppress per-packet log lines
  --ctl-socket PATH      Control socket path
  --pid-file PATH        PID file path
  --daemonize            Fork to background (Unix only)
"""

import sys
import os
import socket
import threading
import struct
import time
import json
import argparse
import logging
import signal
import queue as _queue
import collections
import collections.abc
from collections import defaultdict, deque

# ══════════════════════════════════════════════════════════════════════════════
# VERSION & CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

VERSION  = "2.3.3"
MAGIC    = b"GT"
BUF_SIZE = 65536

PKT_TEXT       = 0x01
PKT_AUDIO      = 0x02
PKT_ACK        = 0x03
PKT_PING       = 0x04
PKT_SCAN_REQ   = 0x06
PKT_SCAN_RSP   = 0x07
PKT_JOIN       = 0x10
PKT_LEAVE      = 0x11
PKT_BBS_POST   = 0x12
PKT_BBS_REQ    = 0x13
PKT_BBS_RSP    = 0x14
PKT_ACTIVE_REQ = 0x15
PKT_ACTIVE_RSP = 0x16

PKT_NAMES = {
    PKT_TEXT:       "TEXT",
    PKT_AUDIO:      "AUDIO",
    PKT_ACK:        "ACK",
    PKT_PING:       "PING",
    PKT_SCAN_REQ:   "SCAN_REQ",
    PKT_SCAN_RSP:   "SCAN_RSP",
    PKT_JOIN:       "JOIN",
    PKT_LEAVE:      "LEAVE",
    PKT_BBS_POST:   "BBS_POST",
    PKT_BBS_REQ:    "BBS_REQ",
    PKT_BBS_RSP:    "BBS_RSP",
    PKT_ACTIVE_REQ: "ACTIVE_REQ",
    PKT_ACTIVE_RSP: "ACTIVE_RSP",
}

# Active keep-alive probe timing:
#   After PROBE_AFTER seconds of silence from a client, the relay sends a
#   PKT_PING directly to that client.  If the client does not respond within
#   PROBE_GRACE seconds (any inbound packet counts as a response), it is
#   evicted.  These values are intentionally tight relative to the client's
#   60 s heartbeat so that dead connections are detected within ~2 minutes.
PROBE_AFTER = 90   # seconds of client silence before sending a probe ping
PROBE_GRACE = 30   # seconds to wait for a probe response before evicting

SYSTEM_CHANNELS: dict[str, str] = {
    "INFO":      ("Welcome to #INFO — relay information and announcements. "
                  "This channel is managed by the relay operator. "
                  "BBS posts by clients are not permitted here."),
    "TEST":      ("Welcome to #TEST — use this channel to test your connection, "
                  "audio, and PTT before joining regional channels. "
                  "BBS posts by clients are not permitted here."),
    "EMERGENCY": ("⚠️  #EMERGENCY — reserved for urgent coordination only. "
                  "Join this channel if you need immediate assistance or to "
                  "report a critical situation. "
                  "BBS posts by clients are not permitted here."),
}

# Default control socket path
def _default_ctl_socket() -> str:
    run = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return os.path.join(run, "geotalk-relayd.sock")

def _default_pid_file() -> str:
    run = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return os.path.join(run, "geotalk-relayd.pid")

# ANSI colours (used for non-daemonised console output)
R  = "\033[0m"
B  = "\033[1m"
CY = "\033[96m"
GR = "\033[92m"
YL = "\033[93m"
RD = "\033[91m"
MG = "\033[95m"
DM = "\033[2m"

_START = time.time()


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _uptime(start: float) -> str:
    secs = int(time.time() - start)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def _fmt_bytes(n: int) -> str:
    if n < 1024:        return f"{n:.0f}B"
    if n < 1024 ** 2:   return f"{n / 1024:.1f}KB"
    return f"{n / 1024 ** 2:.1f}MB"

def _ts() -> str:
    return time.strftime("%H:%M:%S")

def decode_header(data: bytes) -> dict | None:
    if len(data) < 5 or data[:2] != MAGIC:
        return None
    ptype = data[2]
    try:
        plen = struct.unpack("!H", data[3:5])[0]
    except struct.error:
        return None
    if len(data) < 5 + plen:
        return None
    body = data[5:5 + plen]
    try:
        meta = json.loads(body)
    except Exception:
        meta = {}
    return {"ptype": ptype, "meta": meta, "raw": data}


# ══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY LOG BUFFER  — for the "log [N]" control command
# ══════════════════════════════════════════════════════════════════════════════

class _RingLog:
    """Thread-safe circular buffer of the last N log lines."""
    def __init__(self, maxlen: int = 2000):
        self._buf  = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str):
        with self._lock:
            self._buf.append(f"{_ts()}  {line}")

    def tail(self, n: int = 100) -> list[str]:
        with self._lock:
            buf = list(self._buf)
        return buf[-n:] if n < len(buf) else buf


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT  (unchanged from geotalk-relay.py)
# ══════════════════════════════════════════════════════════════════════════════

class Client:
    __slots__ = ("addr", "nick", "channels", "first_seen", "last_seen",
                 "pkts_rx", "pkts_tx", "bytes_rx", "bytes_tx", "probe_sent")

    def __init__(self, addr: tuple, nick: str = ""):
        self.addr       = addr
        self.nick       = nick or f"{addr[0]}:{addr[1]}"
        self.channels   : set[str] = set()
        self.first_seen = time.time()
        self.last_seen  = time.time()
        self.pkts_rx    = 0
        self.pkts_tx    = 0
        self.bytes_rx   = 0
        self.bytes_tx   = 0
        self.probe_sent = 0.0   # monotonic time of last unanswered probe, 0 = none

    def touch(self, nick: str = ""):
        self.last_seen  = time.time()
        self.probe_sent = 0.0   # any inbound packet cancels a pending probe
        if nick:
            self.nick = nick

    def idle_seconds(self) -> float:
        return time.time() - self.last_seen

    def uptime_str(self) -> str:
        secs = int(time.time() - self.first_seen)
        h, r = divmod(secs, 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT REGISTRY  (unchanged from geotalk-relay.py)
# ══════════════════════════════════════════════════════════════════════════════

class ClientRegistry:
    def __init__(self, ttl: int = 300, max_per_channel: int = 128):
        self._lock           = threading.RLock()
        self._clients        : dict[tuple, Client]   = {}
        self._channels       : dict[str, set]        = defaultdict(set)
        self._scan_sessions  : dict[str, tuple]      = {}
        self._scan_ts        : dict[str, float]      = {}
        self._bans           : set[str]              = set()
        self.ttl             = ttl
        self.max_per_channel = max_per_channel
        self.total_pkts_rx   = 0
        self.total_pkts_tx   = 0
        self.total_bytes_rx  = 0
        self.total_bytes_tx  = 0

    def ban(self, ip: str):
        with self._lock:
            self._bans.add(ip)

    def unban(self, ip: str):
        with self._lock:
            self._bans.discard(ip)

    def is_banned(self, addr: tuple) -> bool:
        with self._lock:
            return addr[0] in self._bans

    def banned_ips(self) -> list[str]:
        with self._lock:
            return sorted(self._bans)

    def get_or_create(self, addr: tuple, nick: str = "") -> Client:
        with self._lock:
            if addr not in self._clients:
                self._clients[addr] = Client(addr, nick)
            c = self._clients[addr]
            c.touch(nick)
            return c

    def subscribe(self, addr: tuple, channel: str, nick: str = ""):
        with self._lock:
            if (channel not in self._channels or
                    len(self._channels[channel]) >= self.max_per_channel):
                if addr in (self._channels.get(channel) or set()):
                    pass   # re-subscribe is always allowed
                elif len(self._channels.get(channel, set())) >= self.max_per_channel:
                    return
            c = self.get_or_create(addr, nick)
            self._channels[channel].add(addr)
            c.channels.add(channel)

    def unsubscribe_all(self, addr: tuple):
        with self._lock:
            c = self._clients.pop(addr, None)
            if c:
                for ch in list(c.channels):
                    self._channels[ch].discard(addr)

    def kick_nick(self, nick: str) -> bool:
        with self._lock:
            for addr, c in list(self._clients.items()):
                if c.nick.lower() == nick.lower():
                    for ch in list(c.channels):
                        self._channels[ch].discard(addr)
                    del self._clients[addr]
                    return True
        return False

    def subscribers(self, channel: str, exclude: tuple = None) -> list[tuple]:
        with self._lock:
            subs = list(self._channels.get(channel, set()))
        if exclude:
            subs = [s for s in subs if s != exclude]
        return subs

    def active_channels(self) -> dict[str, list[str]]:
        with self._lock:
            result = {}
            for ch, addrs in self._channels.items():
                if not addrs:
                    continue
                nicks = []
                for addr in addrs:
                    client = self._clients.get(addr)
                    nicks.append(client.nick if client else f"{addr[0]}:{addr[1]}")
                result[ch] = sorted(nicks)
            return result

    def record_rx(self, addr: tuple, nbytes: int):
        with self._lock:
            c = self._clients.get(addr)
            if c:
                c.pkts_rx  += 1
                c.bytes_rx += nbytes
        self.total_pkts_rx  += 1
        self.total_bytes_rx += nbytes

    def record_tx(self, addr: tuple, nbytes: int):
        with self._lock:
            c = self._clients.get(addr)
            if c:
                c.pkts_tx  += 1
                c.bytes_tx += nbytes
        self.total_pkts_tx  += 1
        self.total_bytes_tx += nbytes

    def register_scan(self, scan_id: str, requester: tuple):
        with self._lock:
            self._scan_sessions[scan_id] = requester
            self._scan_ts[scan_id]       = time.time()

    def requester_for_scan(self, scan_id: str) -> tuple | None:
        with self._lock:
            return self._scan_sessions.get(scan_id)

    def prune_scan_sessions(self, max_age: float = 60.0):
        now = time.time()
        with self._lock:
            stale = [k for k, t in self._scan_ts.items() if now - t > max_age]
            for k in stale:
                self._scan_sessions.pop(k, None)
                self._scan_ts.pop(k, None)

    def prune_stale(self) -> int:
        cutoff = time.time() - self.ttl
        with self._lock:
            stale = [addr for addr, c in self._clients.items()
                     if c.last_seen < cutoff]
            for addr in stale:
                c = self._clients.pop(addr)
                for ch in list(c.channels):
                    self._channels[ch].discard(addr)
        return len(stale)

    def probe_and_prune(self, sendto_fn) -> tuple[list, list]:
        """
        Active keep-alive pass.  Called from the prune loop.

        Returns (probed, dropped) — lists of (addr, nick) tuples for logging.

        Algorithm per client:
          1. If last_seen > PROBE_AFTER ago AND no probe pending → send a
             PKT_PING directly to the client and record probe_sent.
          2. If probe_sent is set AND probe_sent > PROBE_GRACE ago → evict.
             (The client has had PROBE_GRACE seconds to respond and has not.)
          3. Passive backstop: if last_seen > ttl ago → evict regardless
             (catches clients that somehow slipped through probe logic).
        """
        now     = time.time()
        probed  = []
        dropped = []

        with self._lock:
            for addr, c in list(self._clients.items()):
                idle = now - c.last_seen

                # ── evict: probe unanswered ────────────────────────────────
                if c.probe_sent and (now - c.probe_sent) >= PROBE_GRACE:
                    dropped.append((addr, c.nick))
                    del self._clients[addr]
                    for ch in list(c.channels):
                        self._channels[ch].discard(addr)
                    continue

                # ── evict: passive TTL backstop ────────────────────────────
                if idle >= self.ttl:
                    dropped.append((addr, c.nick))
                    del self._clients[addr]
                    for ch in list(c.channels):
                        self._channels[ch].discard(addr)
                    continue

                # ── probe: client has been silent long enough ──────────────
                if idle >= PROBE_AFTER and not c.probe_sent:
                    # Build a minimal PKT_PING addressed to one of the
                    # client's channels so it can be decoded normally.
                    channel = next(iter(c.channels), "")
                    payload = json.dumps({
                        "n":  "relay",
                        "p":  channel,
                        "ts": int(now),
                    }).encode()
                    pkt = (MAGIC + bytes([PKT_PING]) +
                           struct.pack("!H", len(payload)) + payload)
                    sendto_fn(pkt, addr)
                    c.probe_sent = now
                    probed.append((addr, c.nick))

        return probed, dropped

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def channel_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._channels.values() if s)

    # ── data accessors for control commands ──────────────────────────────

    def stats_dict(self, port: int = 0) -> dict:
        return {
            "version":       VERSION,
            "uptime":        _uptime(_START),
            "clients":       self.client_count(),
            "channels":      self.channel_count(),
            "pkts_rx":       self.total_pkts_rx,
            "pkts_tx":       self.total_pkts_tx,
            "bytes_rx":      self.total_bytes_rx,
            "bytes_tx":      self.total_bytes_tx,
            "bytes_rx_fmt":  _fmt_bytes(self.total_bytes_rx),
            "bytes_tx_fmt":  _fmt_bytes(self.total_bytes_tx),
            "port":          port,
        }

    def channels_dict(self) -> dict:
        return self.active_channels()

    def clients_list(self) -> list[dict]:
        with self._lock:
            rows = []
            for addr, c in sorted(self._clients.items(),
                                   key=lambda x: x[1].last_seen, reverse=True):
                rows.append({
                    "nick":      c.nick,
                    "ip":        addr[0],
                    "port":      addr[1],
                    "channels":  sorted(c.channels),
                    "uptime":    c.uptime_str(),
                    "idle_s":    round(c.idle_seconds(), 1),
                    "pkts_rx":   c.pkts_rx,
                    "pkts_tx":   c.pkts_tx,
                })
        return rows


# ══════════════════════════════════════════════════════════════════════════════
# BBS STORE  (unchanged from geotalk-relay.py)
# ══════════════════════════════════════════════════════════════════════════════

class BbsStore:
    def __init__(self, max_per_channel: int = 50, bbs_file: str = ""):
        self._lock          = threading.Lock()
        self._msgs          : dict[str, collections.deque] = \
            defaultdict(lambda: collections.deque(maxlen=self.max_per_channel))
        self.max_per_channel = max_per_channel
        self.bbs_file        = bbs_file
        self._next_id        = 1
        if bbs_file:
            self._load()

    def _load(self):
        try:
            if os.path.exists(self.bbs_file):
                with open(self.bbs_file, "r") as f:
                    data = json.load(f)
                with self._lock:
                    max_id = 0
                    for ch, msgs in data.items():
                        self._msgs[ch] = collections.deque(
                            msgs, maxlen=self.max_per_channel)
                        for m in msgs:
                            if isinstance(m.get("id"), int):
                                max_id = max(max_id, m["id"])
                    self._next_id = max_id + 1
        except Exception as e:
            print(f"{YL}BBS load error: {e}{R}")

    def _save(self):
        if not self.bbs_file:
            return
        try:
            with self._lock:
                data = {ch: list(msgs) for ch, msgs in self._msgs.items()}
            with open(self.bbs_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"{YL}BBS save error: {e}{R}")

    def post(self, nick: str, channel: str, text: str) -> dict:
        with self._lock:
            record = {
                "id": self._next_id,
                "n":  nick,
                "p":  channel,
                "t":  text,
                "ts": int(time.time()),
            }
            self._next_id += 1
            self._msgs[channel].append(record)
        self._save()
        return record

    def get(self, channel: str) -> list[dict]:
        with self._lock:
            return list(self._msgs.get(channel, []))

    def clear(self, channel: str) -> int:
        with self._lock:
            n = len(self._msgs.get(channel, []))
            self._msgs.pop(channel, None)
        self._save()
        return n

    def channel_count(self) -> int:
        with self._lock:
            return sum(1 for v in self._msgs.values() if v)

    def total_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._msgs.values())

    def summary_dict(self) -> dict:
        with self._lock:
            channels = {ch: len(msgs)
                        for ch, msgs in self._msgs.items() if msgs}
        return {
            "total":    sum(channels.values()),
            "channels": channels,
            "file":     self.bbs_file or "(in-memory)",
            "max_per_channel": self.max_per_channel,
        }

    def detail_dict(self, channel: str) -> list[dict]:
        return self.get(channel.upper())

    def seed_system_channel(self, channel: str, text: str) -> bool:
        with self._lock:
            if self._msgs.get(channel):
                return False
        self.post("relay", channel, text)
        return True


# ══════════════════════════════════════════════════════════════════════════════
# CONTROL SESSION  — one per connected CLI client
# ══════════════════════════════════════════════════════════════════════════════

class ControlSession:
    """
    Handles one control socket connection.
    Reads newline-delimited JSON requests, dispatches to RelayDaemon,
    writes newline-delimited JSON responses.
    """
    def __init__(self, conn: socket.socket, addr, daemon: "RelayDaemon"):
        self._conn   = conn
        self._addr   = addr
        self._daemon = daemon
        self._rbuf   = b""

    def run(self):
        try:
            self._conn.settimeout(60.0)
            # Send a greeting so the CLI knows the daemon version immediately
            self._send({"ok": True, "event": "connected",
                        "version": VERSION, "uptime": _uptime(_START)})
            while self._daemon._running:
                try:
                    chunk = self._conn.recv(4096)
                except socket.timeout:
                    # Send a heartbeat so the CLI can detect dead daemons
                    self._send({"ok": True, "event": "heartbeat",
                                "ts": int(time.time())})
                    continue
                if not chunk:
                    break
                self._rbuf += chunk
                while b"\n" in self._rbuf:
                    line, self._rbuf = self._rbuf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = json.loads(line)
                    except json.JSONDecodeError as e:
                        self._send({"ok": False, "error": f"JSON parse error: {e}"})
                        continue
                    self._dispatch(req)
        except (OSError, ConnectionResetError):
            pass
        finally:
            try:
                self._conn.close()
            except Exception:
                pass

    def _send(self, obj: dict):
        try:
            self._conn.sendall(json.dumps(obj).encode() + b"\n")
        except OSError:
            pass

    def _dispatch(self, req: dict):
        cmd  = str(req.get("cmd", "")).strip().lower()
        arg  = str(req.get("arg", "")).strip()
        arg2 = str(req.get("arg2", "")).strip()

        d   = self._daemon
        reg = d.registry
        bbs = d.bbs

        try:
            if cmd == "stats":
                data = reg.stats_dict(port=d.port)
                data["bbs"]      = bbs.summary_dict()
                data["bans"]     = len(reg.banned_ips())
                data["quiet"]    = d.quiet
                data["ctl_sock"] = d.ctl_socket
                self._send({"ok": True, "cmd": cmd, "data": data})

            elif cmd == "channels":
                self._send({"ok": True, "cmd": cmd,
                            "data": reg.channels_dict()})

            elif cmd in ("clients", "who"):
                self._send({"ok": True, "cmd": cmd,
                            "data": reg.clients_list()})

            elif cmd == "bbs":
                if arg:
                    self._send({"ok": True, "cmd": cmd,
                                "data": bbs.detail_dict(arg),
                                "channel": arg.upper()})
                else:
                    self._send({"ok": True, "cmd": cmd,
                                "data": bbs.summary_dict()})

            elif cmd == "bbs-clear":
                if not arg:
                    self._send({"ok": False, "error": "Usage: bbs-clear CHANNEL"})
                    return
                n = bbs.clear(arg.upper())
                self._send({"ok": True, "cmd": cmd,
                            "cleared": n, "channel": arg.upper()})

            elif cmd == "bbs-post":
                # arg = channel, arg2 = text
                if not arg or not arg2:
                    self._send({"ok": False,
                                "error": "Usage: bbs-post CHANNEL TEXT"})
                    return
                ch = arg.upper()
                record = bbs.post("operator", ch, arg2)
                self._send({"ok": True, "cmd": cmd, "record": record})

            elif cmd == "kick":
                if not arg:
                    self._send({"ok": False, "error": "Usage: kick NICK"})
                    return
                ok = reg.kick_nick(arg)
                self._send({"ok": ok, "cmd": cmd, "nick": arg,
                            "error": f"Nick '{arg}' not found" if not ok else None})

            elif cmd == "ban":
                if not arg:
                    self._send({"ok": False, "error": "Usage: ban IP"})
                    return
                reg.ban(arg)
                self._send({"ok": True, "cmd": cmd, "ip": arg})

            elif cmd == "unban":
                if not arg:
                    self._send({"ok": False, "error": "Usage: unban IP"})
                    return
                reg.unban(arg)
                self._send({"ok": True, "cmd": cmd, "ip": arg})

            elif cmd == "bans":
                self._send({"ok": True, "cmd": cmd,
                            "data": reg.banned_ips()})

            elif cmd == "log":
                n = int(arg) if arg.isdigit() else 100
                self._send({"ok": True, "cmd": cmd,
                            "data": d.ringlog.tail(n), "n": n})

            elif cmd == "quiet":
                if arg in ("on", "1", "true"):
                    d.quiet = True
                elif arg in ("off", "0", "false"):
                    d.quiet = False
                self._send({"ok": True, "cmd": cmd, "quiet": d.quiet})

            elif cmd == "stop":
                self._send({"ok": True, "cmd": cmd,
                            "msg": "Relay daemon shutting down…"})
                d._shutdown()

            elif cmd == "ping":
                self._send({"ok": True, "cmd": cmd, "pong": True})

            else:
                self._send({"ok": False,
                            "error": f"Unknown command: {cmd!r}",
                            "commands": [
                                "stats", "channels", "clients",
                                "bbs [CHANNEL]", "bbs-clear CHANNEL",
                                "bbs-post CHANNEL TEXT",
                                "kick NICK", "ban IP", "unban IP", "bans",
                                "log [N]", "quiet [on|off]", "stop",
                            ]})
        except Exception as e:
            self._send({"ok": False, "error": f"Internal error: {e}"})


# ══════════════════════════════════════════════════════════════════════════════
# RELAY DAEMON
# ══════════════════════════════════════════════════════════════════════════════

class RelayDaemon:
    """
    Full relay server + Unix-socket control interface.
    Inherits all packet-handling logic from geotalk-relay.py,
    replaces _console_loop with _ctl_server_loop.
    """

    def __init__(self, host: str, port: int,
                 ttl: int = 300, max_per_channel: int = 128,
                 log_file: str = "", quiet: bool = False,
                 bbs_max: int = 50, bbs_file: str = "",
                 ctl_socket: str = "", pid_file: str = ""):
        self.host        = host
        self.port        = port
        self.quiet       = quiet
        self.ctl_socket  = ctl_socket or _default_ctl_socket()
        self.pid_file    = pid_file   or _default_pid_file()
        self.registry    = ClientRegistry(ttl=ttl,
                                          max_per_channel=max_per_channel)
        self.bbs         = BbsStore(max_per_channel=bbs_max,
                                    bbs_file=bbs_file)
        self.ringlog     = _RingLog(maxlen=2000)
        self._running    = False
        self._sock       : socket.socket | None = None
        self._ctl_sock   : socket.socket | None = None
        self._send_lock  = threading.Lock()
        self._work_q     : _queue.Queue         = _queue.Queue(maxsize=2048)
        self._log        : logging.Logger | None = None

        if log_file:
            logging.basicConfig(
                filename=log_file, level=logging.INFO,
                format="%(asctime)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            self._log = logging.getLogger("geotalk-relayd")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        # UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.settimeout(2.0)
        self._running = True

        # Write PID file
        self._write_pid()

        # Control socket
        self._start_ctl_server()

        # Signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT,  self._handle_signal)

        self._print_banner()

        # Seed system channels
        for ch, seed_text in SYSTEM_CHANNELS.items():
            seeded = self.bbs.seed_system_channel(ch, seed_text)
            if seeded:
                self._log_line(f"System channel seeded: #{ch}")

        # Background threads
        threading.Thread(target=self._prune_loop, daemon=True,
                         name="prune").start()

        # Worker pool
        _POOL_SIZE = 8
        for _ in range(_POOL_SIZE):
            threading.Thread(target=self._worker, daemon=True,
                             name="relay-worker").start()

        # Main RX loop
        try:
            while self._running:
                try:
                    data, addr = self._sock.recvfrom(BUF_SIZE)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    self._work_q.put_nowait((data, addr))
                except _queue.Full:
                    pass
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _shutdown(self):
        if not self._running:
            return
        self._running = False
        msg = (f"Relay daemon shut down.  "
               f"Forwarded {self.registry.total_pkts_tx} packets.")
        self._log_line(msg)
        print(f"\n{DM}{msg}{R}")

        # Close sockets
        try:
            self._sock.close()
        except Exception:
            pass
        try:
            self._ctl_sock.close()
        except Exception:
            pass

        # Remove control socket file and PID file
        for path in (self.ctl_socket, self.pid_file):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        # Drain worker pool
        for _ in range(8):
            try:
                self._work_q.put_nowait(None)
            except Exception:
                pass

    def _handle_signal(self, signum, frame):
        self._log_line(f"Received signal {signum} — shutting down")
        self._shutdown()

    def _write_pid(self):
        try:
            pid_dir = os.path.dirname(self.pid_file)
            if pid_dir:
                os.makedirs(pid_dir, exist_ok=True)
            with open(self.pid_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception as e:
            print(f"{YL}Warning: could not write PID file {self.pid_file}: {e}{R}")

    def _print_banner(self):
        lines = [
            f"",
            f"{B}{CY}  ◈ GeoTalk Relay Daemon  v{VERSION}{R}",
            f"  {DM}UDP   : {self.host}:{self.port}{R}",
            f"  {DM}CTL   : {self.ctl_socket}{R}",
            f"  {DM}PID   : {self.pid_file}  ({os.getpid()}){R}",
            f"  {DM}BBS   : {self.bbs.bbs_file or 'in-memory'}"
            f"  (max {self.bbs.max_per_channel}/ch){R}",
            f"  {DM}System: {', '.join('#' + ch for ch in SYSTEM_CHANNELS)}{R}",
            f"  {DM}Quiet : {'yes' if self.quiet else 'no (per-packet log enabled)'}{R}",
            f"",
        ]
        print("\n".join(lines))

    # ── worker ────────────────────────────────────────────────────────────────

    def _worker(self):
        while self._running:
            try:
                item = self._work_q.get(timeout=1.0)
            except _queue.Empty:
                continue
            if item is None:
                break
            data, addr = item
            try:
                self._handle(data, addr)
            except Exception:
                pass

    # ── packet handler (identical to geotalk-relay.py) ────────────────────────

    def _handle(self, data: bytes, addr: tuple):
        if self.registry.is_banned(addr):
            return
        pkt = decode_header(data)
        if not pkt:
            return

        ptype  = pkt["ptype"]
        meta   = pkt["meta"]
        postal = meta.get("p", "").strip().upper()
        nick   = meta.get("n", "")

        self.registry.record_rx(addr, len(data))

        if ptype == PKT_JOIN:
            if not postal:
                return
            self.registry.subscribe(addr, postal, nick)
            line = f"+JOIN {nick or addr[0]} → #{postal}"
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {GR}+JOIN {R}{GR}{nick or addr[0]}{R} → {CY}#{postal}{R}")
            self._log_line(line)
            self._logf("JOIN nick=%s addr=%s:%s channel=%s", nick, addr[0], addr[1], postal)
            return

        if ptype == PKT_LEAVE:
            # Fan out the LEAVE to all channels this client is on so that
            # other subscribers can remove the nick from their user lists
            # immediately — before we unsubscribe and lose the channel list.
            client = self.registry._clients.get(addr)
            if client:
                for ch in list(client.channels):
                    self._fanout(data, ch, exclude=addr)
            self.registry.unsubscribe_all(addr)
            line = f"-LEAVE {nick or addr[0]}"
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {YL}-LEAVE{R} {YL}{nick or addr[0]}{R}")
            self._log_line(line)
            self._logf("LEAVE nick=%s addr=%s:%s", nick, addr[0], addr[1])
            return

        if ptype == PKT_SCAN_REQ:
            scan_id = meta.get("sid", "")
            if scan_id:
                self.registry.register_scan(scan_id, addr)
            self._fanout(data, postal, exclude=addr)
            line = f"SCAN_REQ {nick or addr[0]} → #{postal or '*'}"
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {MG}SCAN_REQ{R} "
                      f"{nick or addr[0]} → {CY}#{postal or '*'}{R}")
            self._log_line(line)
            self._logf("SCAN_REQ nick=%s channel=%s sid=%s", nick, postal, scan_id)
            return

        if ptype == PKT_SCAN_RSP:
            scan_id   = meta.get("sid", "")
            requester = self.registry.requester_for_scan(scan_id)
            if requester:
                self._sendto(data, requester)
                line = f"SCAN_RSP {nick or addr[0]} → {requester[0]}:{requester[1]}"
                if not self.quiet:
                    print(f"{DM}{_ts()}{R} {MG}SCAN_RSP{R} "
                          f"{nick or addr[0]} → {requester[0]}:{requester[1]}")
                self._log_line(line)
                self._logf("SCAN_RSP nick=%s channel=%s → %s:%s",
                           nick, postal, requester[0], requester[1])
            return

        if ptype in (PKT_TEXT, PKT_AUDIO, PKT_PING):
            if postal:
                self.registry.subscribe(addr, postal, nick)
            self._fanout(data, postal, exclude=addr)
            if ptype != PKT_AUDIO and not self.quiet:
                label   = PKT_NAMES.get(ptype, hex(ptype))
                preview = (f"  \"{meta.get('t','')[:40]}\""
                           if ptype == PKT_TEXT else "")
                line = f"{label:<8} {nick or addr[0]} → #{postal}{preview}"
                print(f"{DM}{_ts()}{R} {label:<8} "
                      f"{nick or addr[0]} → {CY}#{postal}{R}{preview}")
                self._log_line(line)
            self._logf("%s nick=%s channel=%s",
                       PKT_NAMES.get(ptype, hex(ptype)), nick, postal)
            return

        if ptype == PKT_BBS_POST:
            if not postal or not nick:
                return
            text = meta.get("t", "").strip()
            if not text:
                return
            if postal in SYSTEM_CHANNELS:
                err_payload = json.dumps({
                    "p": postal, "msgs": [], "error": True,
                    "error_msg": f"#{postal} is a system channel — BBS is read-only",
                }).encode()
                err_rsp = (MAGIC + bytes([PKT_BBS_RSP]) +
                           struct.pack("!H", len(err_payload)) + err_payload)
                self._sendto(err_rsp, addr)
                line = f"BBS_POST REJECTED {nick} → #{postal} (system channel)"
                if not self.quiet:
                    print(f"{DM}{_ts()}{R} {YL}BBS_POST REJECTED{R} "
                          f"{nick} → {CY}#{postal}{R} (system channel)")
                self._log_line(line)
                return
            record = self.bbs.post(nick, postal, text)
            rsp_payload = json.dumps({"p": postal, "msgs": [record],
                                      "echo": True}).encode()
            rsp = (MAGIC + bytes([PKT_BBS_RSP]) +
                   struct.pack("!H", len(rsp_payload)) + rsp_payload)
            self._sendto(rsp, addr)
            line = f"BBS_POST {nick} → #{postal}  \"{text[:50]}\""
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {MG}BBS_POST{R} "
                      f"{GR}{nick}{R} → {CY}#{postal}{R}  \"{text[:50]}\"")
            self._log_line(line)
            self._logf("BBS_POST nick=%s channel=%s text=%s",
                       nick, postal, text[:80])
            return

        if ptype == PKT_BBS_REQ:
            if not postal:
                return
            msgs = self.bbs.get(postal)
            rsp_payload = json.dumps({"p": postal, "msgs": msgs}).encode()
            rsp = (MAGIC + bytes([PKT_BBS_RSP]) +
                   struct.pack("!H", len(rsp_payload)) + rsp_payload)
            self._sendto(rsp, addr)
            line = f"BBS_REQ {nick or addr[0]} → #{postal} ({len(msgs)} msgs)"
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {MG}BBS_REQ{R}  "
                      f"{nick or addr[0]} → {CY}#{postal}{R} ({len(msgs)} msg(s))")
            self._log_line(line)
            return

        if ptype == PKT_ACTIVE_REQ:
            active = self.registry.active_channels()
            rsp_payload = json.dumps({"channels": active,
                                      "ts": int(time.time())}).encode()
            rsp = (MAGIC + bytes([PKT_ACTIVE_RSP]) +
                   struct.pack("!H", len(rsp_payload)) + rsp_payload)
            self._sendto(rsp, addr)
            line = f"ACTIVE_REQ {nick or addr[0]} → {len(active)} channels"
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {MG}ACTIVE_REQ{R} "
                      f"{nick or addr[0]} → {len(active)} channel(s)")
            self._log_line(line)
            return

        self._logf("UNKNOWN ptype=0x%02x addr=%s:%s", ptype, addr[0], addr[1])

    # ── fanout & send ─────────────────────────────────────────────────────────

    def _fanout(self, data: bytes, channel: str, exclude: tuple = None):
        if not channel:
            return
        for sub in self.registry.subscribers(channel, exclude=exclude):
            self._sendto(data, sub)

    def _sendto(self, data: bytes, addr: tuple):
        with self._send_lock:
            try:
                self._sock.sendto(data, addr)
                self.registry.record_tx(addr, len(data))
            except OSError:
                pass

    # ── prune loop ────────────────────────────────────────────────────────────

    def _prune_loop(self):
        while self._running:
            time.sleep(15)
            probed, dropped = self.registry.probe_and_prune(self._sendto)
            self.registry.prune_scan_sessions()

            for addr, nick in probed:
                line = f"Probe sent → {nick} ({addr[0]}:{addr[1]})"
                if not self.quiet:
                    print(f"{DM}{_ts()}  {YL}{line}{R}")
                self._log_line(line)

            for addr, nick in dropped:
                line = f"Dropped (no probe response): {nick} ({addr[0]}:{addr[1]})"
                print(f"{DM}{_ts()}  {RD}{line}{R}")
                self._log_line(line)

    # ── control socket server ─────────────────────────────────────────────────

    def _start_ctl_server(self):
        """Open the Unix-domain control socket and start the accept loop."""
        sock_path = self.ctl_socket
        # Remove stale socket file if present
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass

        sock_dir = os.path.dirname(sock_path)
        if sock_dir:
            os.makedirs(sock_dir, exist_ok=True)

        self._ctl_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._ctl_sock.bind(sock_path)
        os.chmod(sock_path, 0o660)   # owner+group only
        self._ctl_sock.listen(8)
        self._ctl_sock.settimeout(2.0)

        threading.Thread(target=self._ctl_accept_loop, daemon=True,
                         name="ctl-accept").start()
        self._log_line(f"Control socket listening on {sock_path}")

    def _ctl_accept_loop(self):
        while self._running:
            try:
                conn, addr = self._ctl_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            sess = ControlSession(conn, addr, self)
            threading.Thread(target=sess.run, daemon=True,
                             name="ctl-session").start()

    # ── log helpers ───────────────────────────────────────────────────────────

    def _log_line(self, line: str):
        """Append to the in-memory ring log (always) and file log (if configured)."""
        self.ringlog.append(line)

    def _logf(self, fmt: str, *args):
        if self._log:
            self._log.info(fmt, *args)


# ══════════════════════════════════════════════════════════════════════════════
# DAEMONISE  (Unix double-fork)
# ══════════════════════════════════════════════════════════════════════════════

def _daemonize(log_file: str):
    """Double-fork to detach from the controlling terminal."""
    if not log_file:
        raise SystemExit(
            "Error: --log-file is required when using --daemonize")

    # First fork
    if os.fork() > 0:
        raise SystemExit(0)   # parent exits

    os.setsid()

    # Second fork
    if os.fork() > 0:
        raise SystemExit(0)

    # Redirect standard streams to log file
    sys.stdout.flush()
    sys.stderr.flush()
    with open(log_file, "a") as lf:
        os.dup2(lf.fileno(), sys.stdout.fileno())
        os.dup2(lf.fileno(), sys.stderr.fileno())
    with open(os.devnull) as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=f"GeoTalk Relay Daemon v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  python3 geotalk-relayd.py
  python3 geotalk-relayd.py --port 5073 --ttl 600
  python3 geotalk-relayd.py --daemonize --log-file /var/log/geotalk-relayd.log
  python3 geotalk-relayd.py --ctl-socket /run/geotalk/relay.sock

Control via CLI:
  python3 geotalk-relay-cli.py stats
  python3 geotalk-relay-cli.py --interactive
  python3 geotalk-relay-cli.py --socket /run/geotalk/relay.sock channels

Default control socket: {_default_ctl_socket()}
Default PID file:       {_default_pid_file()}
""")
    parser.add_argument("--host",             default="0.0.0.0",
                        help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port",             type=int, default=5073,
                        help="UDP port (default 5073)")
    parser.add_argument("--ttl",              type=int, default=300,
                        metavar="SECONDS",
                        help="Client TTL in seconds (default 300)")
    parser.add_argument("--max-per-channel",  type=int, default=128,
                        metavar="N",
                        help="Max clients per channel (default 128)")
    parser.add_argument("--log-file",         default="", metavar="PATH",
                        help="Log file path (required for --daemonize)")
    parser.add_argument("--quiet",            action="store_true",
                        help="Suppress per-packet output")
    parser.add_argument("--bbs-file",         default="geotalk-bbs.json",
                        metavar="PATH",
                        help="BBS persistence file (default: geotalk-bbs.json)")
    parser.add_argument("--bbs-max",          type=int, default=50,
                        metavar="N",
                        help="Max BBS messages per channel (default 50)")
    parser.add_argument("--ctl-socket",       default="", metavar="PATH",
                        help=f"Control socket path "
                             f"(default: {_default_ctl_socket()})")
    parser.add_argument("--pid-file",         default="", metavar="PATH",
                        help=f"PID file path "
                             f"(default: {_default_pid_file()})")
    parser.add_argument("--daemonize",        action="store_true",
                        help="Fork to background (requires --log-file)")
    args = parser.parse_args()

    if args.daemonize:
        _daemonize(args.log_file)

    RelayDaemon(
        host            = args.host,
        port            = args.port,
        ttl             = args.ttl,
        max_per_channel = args.max_per_channel,
        log_file        = args.log_file,
        quiet           = args.quiet,
        bbs_max         = args.bbs_max,
        bbs_file        = args.bbs_file,
        ctl_socket      = args.ctl_socket,
        pid_file        = args.pid_file,
    ).start()


if __name__ == "__main__":
    main()
