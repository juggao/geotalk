#!/usr/bin/env python3
"""
geotalk-relay.py — GeoTalk Relay / Bridge Server  v1.8.0
Author: René Oudeweg / Claude
─────────────────────────────────────────────────────────
Bridges GeoTalk UDP traffic across subnets and the internet.
Clients subscribe to postal-code channels via PKT_JOIN; the relay
fans out all traffic to every subscriber of that channel.

The relay is codec-transparent: AUDIO packets carry a "codec" field in
their JSON header ("opus" or "pcm") which is forwarded untouched.
Clients negotiate codec capability independently; the relay never
decodes or re-encodes audio payloads.

BBS (Bulletin Board System)
  Per-channel persistent message store.  Clients post with PKT_BBS_POST
  and fetch with PKT_BBS_REQ; the relay delivers stored messages via
  PKT_BBS_RSP.  Messages are auto-delivered when a client joins a channel.
  Storage is in memory (capped per channel) with optional JSON persistence.

Supported packet types
  0x01  TEXT       fan-out to channel subscribers
  0x02  AUDIO      fan-out to channel subscribers  (Opus or PCM, transparent)
  0x03  ACK        client-to-client only — dropped by relay
  0x04  PING       fan-out to channel subscribers  (keeps subscription alive)
  0x06  SCAN_REQ   fan-out to channel subscribers  (probe for active users)
  0x07  SCAN_RSP   unicast back to original requester only
  0x10  JOIN       subscribe this client to a channel
  0x11  LEAVE      unsubscribe this client from all channels
  0x12  BBS_POST   store a message in the channel BBS
  0x13  BBS_REQ    request stored BBS messages for a channel
  0x14  BBS_RSP    relay → client: deliver stored BBS messages

Usage
  python3 geotalk-relay.py [options]

Options
  --host HOST          Bind address (default 0.0.0.0)
  --port PORT          UDP port     (default 5073)
  --ttl  SECONDS       Client subscription TTL (default 300)
  --max-per-channel N  Max clients per channel  (default 128)
  --bbs-file PATH      JSON file for BBS persistence (default: geotalk-bbs.json)
  --bbs-max N          Max BBS messages per channel (default 50)
  --log-file PATH      Append log lines to file (default: stdout only)
  --quiet              Suppress per-packet log lines

Console commands (type while running)
  stats               Short summary: clients, channels, traffic, BBS
  channels            Detailed per-channel listing
  clients             All connected clients
  bbs [CHANNEL]       List BBS messages (all channels or one specific)
  bbs-clear CHANNEL   Delete all BBS messages for a channel
  kick NICK           Drop a client by nickname
  ban IP              Block an IP address
  unban IP            Remove an IP ban
  bans                List banned IPs
  quit / q            Shut down relay

Clients connect with:
  python3 geotalk.py --nick PA3XYZ --relay relay.example.com --relay-port 5073
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
import queue as _queue
import collections
from collections import defaultdict

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

VERSION  = "1.8.2"
MAGIC    = b"GT"
BUF_SIZE = 65536   # large enough for any codec frame (Opus ~80 B, PCM ~4 KB)

# Packet type bytes
PKT_TEXT     = 0x01
PKT_AUDIO    = 0x02
PKT_ACK      = 0x03   # client-to-client only — relay drops silently
PKT_PING     = 0x04
PKT_SCAN_REQ = 0x06
PKT_SCAN_RSP = 0x07
PKT_JOIN     = 0x10
PKT_LEAVE    = 0x11
# BBS — persistent per-channel bulletin board
PKT_BBS_POST = 0x12   # client → relay: store a BBS message
PKT_BBS_REQ  = 0x13   # client → relay: request stored messages for a channel
PKT_BBS_RSP  = 0x14   # relay → client: deliver stored messages

PKT_NAMES = {
    PKT_TEXT:     "TEXT",
    PKT_AUDIO:    "AUDIO",
    PKT_ACK:      "ACK",
    PKT_PING:     "PING",
    PKT_SCAN_REQ: "SCAN_REQ",
    PKT_SCAN_RSP: "SCAN_RSP",
    PKT_JOIN:     "JOIN",
    PKT_LEAVE:    "LEAVE",
    PKT_BBS_POST: "BBS_POST",
    PKT_BBS_REQ:  "BBS_REQ",
    PKT_BBS_RSP:  "BBS_RSP",
}

# ANSI colours
R  = "\033[0m"
B  = "\033[1m"
CY = "\033[96m"
GR = "\033[92m"
YL = "\033[93m"
RD = "\033[91m"
MG = "\033[95m"
DM = "\033[2m"

# ══════════════════════════════════════════════════════════════════════════════
# PACKET DECODER
# ══════════════════════════════════════════════════════════════════════════════

def decode_header(data: bytes) -> dict | None:
    """
    Decode just the JSON header of any GeoTalk packet.
    Returns {"ptype": int, "meta": dict, "raw": bytes} or None.
    """
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
# CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class Client:
    __slots__ = ("addr", "nick", "channels", "first_seen", "last_seen",
                 "pkts_rx", "pkts_tx", "bytes_rx", "bytes_tx")

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

    def touch(self, nick: str = ""):
        self.last_seen = time.time()
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
# CLIENT REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

class ClientRegistry:
    """
    Thread-safe registry:
      addr        → Client
      channel_key → set of addr
      scan_id     → requester addr   (for SCAN_RSP routing)
    """

    def __init__(self, ttl: int = 300, max_per_channel: int = 128):
        self._lock           = threading.RLock()
        self._clients        : dict[tuple, Client]   = {}
        self._channels       : dict[str, set]        = defaultdict(set)
        self._scan_sessions  : dict[str, tuple]      = {}
        self._scan_ts        : dict[str, float]      = {}
        self._bans           : set[str]              = set()
        self.ttl             = ttl
        self.max_per_channel = max_per_channel
        # global counters (not under lock — atomic enough for stats display)
        self.total_pkts_rx  = 0
        self.total_pkts_tx  = 0
        self.total_bytes_rx = 0
        self.total_bytes_tx = 0

    # ── bans ──────────────────────────────────────────────────────────────

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

    # ── client lifecycle ──────────────────────────────────────────────────

    def get_or_create(self, addr: tuple, nick: str = "") -> Client:
        with self._lock:
            if addr not in self._clients:
                self._clients[addr] = Client(addr, nick)
            else:
                self._clients[addr].touch(nick)
            return self._clients[addr]

    def subscribe(self, addr: tuple, channel: str, nick: str = ""):
        with self._lock:
            if addr[0] in self._bans:
                return
            client = self.get_or_create(addr, nick)
            if (addr not in self._channels[channel] and
                    len(self._channels[channel]) >= self.max_per_channel):
                return
            client.channels.add(channel)
            self._channels[channel].add(addr)

    def unsubscribe_all(self, addr: tuple):
        with self._lock:
            client = self._clients.get(addr)
            if client:
                for ch in list(client.channels):
                    self._channels[ch].discard(addr)
                client.channels.clear()
            self._clients.pop(addr, None)

    def kick_nick(self, nick: str) -> bool:
        with self._lock:
            for addr, client in list(self._clients.items()):
                if client.nick.lower() == nick.lower():
                    self.unsubscribe_all(addr)
                    return True
        return False

    # ── subscriber lookup ─────────────────────────────────────────────────

    def subscribers(self, channel: str, exclude: tuple = None) -> list[tuple]:
        with self._lock:
            return [a for a in self._channels.get(channel, set())
                    if a != exclude]

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

    # ── scan session tracking ─────────────────────────────────────────────

    def register_scan(self, scan_id: str, requester: tuple):
        with self._lock:
            self._scan_sessions[scan_id] = requester
            self._scan_ts[scan_id]       = time.time()

    def requester_for_scan(self, scan_id: str) -> tuple | None:
        with self._lock:
            return self._scan_sessions.get(scan_id)

    def prune_scan_sessions(self, max_age: float = 60.0):
        cutoff = time.time() - max_age
        with self._lock:
            stale = [s for s, t in self._scan_ts.items() if t < cutoff]
            for s in stale:
                self._scan_sessions.pop(s, None)
                self._scan_ts.pop(s, None)

    # ── stale pruning ─────────────────────────────────────────────────────

    def prune_stale(self) -> int:
        cutoff = time.time() - self.ttl
        removed = 0
        with self._lock:
            stale = [a for a, c in self._clients.items()
                     if c.last_seen < cutoff]
            for addr in stale:
                self.unsubscribe_all(addr)
                removed += 1
        return removed

    # ── reporting ─────────────────────────────────────────────────────────

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def channel_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._channels.values() if s)

    def stats_summary(self) -> str:
        with self._lock:
            n_c = len(self._clients)
            n_ch = sum(1 for s in self._channels.values() if s)
        return "\n".join([
            f"{B}GeoTalk Relay {VERSION}{R}  uptime={_uptime(_START)}",
            f"  Clients  : {GR}{n_c}{R}",
            f"  Channels : {CY}{n_ch}{R}",
            f"  RX total : {self.total_pkts_rx} pkts  {_fmt_bytes(self.total_bytes_rx)}",
            f"  TX total : {self.total_pkts_tx} pkts  {_fmt_bytes(self.total_bytes_tx)}",
            f"  Codec    : {DM}transparent (Opus/PCM forwarded as-is){R}",
        ])

    def channels_detail(self) -> str:
        with self._lock:
            active = {ch: addrs for ch, addrs in self._channels.items() if addrs}
        if not active:
            return "  (no active channels)"
        lines = [f"{B}Channels ({len(active)}){R}"]
        for ch, addrs in sorted(active.items()):
            with self._lock:
                nicks = [self._clients[a].nick if a in self._clients
                         else f"{a[0]}:{a[1]}" for a in addrs]
            lines.append(f"  {CY}#{ch:<22}{R} [{len(addrs)}]  "
                         + ", ".join(nicks))
        return "\n".join(lines)

    def clients_detail(self) -> str:
        with self._lock:
            snapshot = list(self._clients.items())
        if not snapshot:
            return "  (no clients)"
        lines = [f"{B}Clients ({len(snapshot)}){R}"]
        for addr, c in sorted(snapshot, key=lambda x: x[1].last_seen, reverse=True):
            lines.append(
                f"  {GR}{c.nick:<16}{R}  {addr[0]}:{addr[1]:<6}"
                f"  ch={len(c.channels):<3}"
                f"  up={c.uptime_str()}"
                f"  idle={c.idle_seconds():.0f}s"
                f"  rx={c.pkts_rx} tx={c.pkts_tx}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# BBS STORE
# ══════════════════════════════════════════════════════════════════════════════

class BbsStore:
    """
    Persistent per-channel bulletin board.

    Messages are kept in memory (deque, capped at max_per_channel) and
    optionally persisted to a JSON file so they survive relay restarts.

    Each message record:
        {"id": int, "n": nick, "p": channel, "t": text, "ts": unix_timestamp}
    """

    def __init__(self, max_per_channel: int = 50, bbs_file: str = ""):
        self._lock            = threading.RLock()
        self._store           : dict[str, collections.deque] = defaultdict(
            lambda: collections.deque(maxlen=self.max_per_channel))
        self.max_per_channel  = max_per_channel
        self.bbs_file         = bbs_file
        self._next_id         = 1

        if bbs_file:
            self._load()

    # ── persistence ──────────────────────────────────────────────────────

    def _load(self):
        try:
            with open(self.bbs_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                for channel, msgs in data.get("channels", {}).items():
                    dq = collections.deque(maxlen=self.max_per_channel)
                    for m in msgs:
                        dq.append(m)
                        if m.get("id", 0) >= self._next_id:
                            self._next_id = m["id"] + 1
                    self._store[channel] = dq
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"{YL}BBS: could not load {self.bbs_file}: {e}{R}")

    def _save(self):
        if not self.bbs_file:
            return
        try:
            with self._lock:
                data = {"channels": {ch: list(msgs)
                                     for ch, msgs in self._store.items()
                                     if msgs}}
            with open(self.bbs_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"{YL}BBS: could not save {self.bbs_file}: {e}{R}")

    # ── public API ────────────────────────────────────────────────────────

    def post(self, nick: str, channel: str, text: str) -> dict:
        """Store one message; returns the saved record."""
        record = {
            "id": self._next_id,
            "n":  nick,
            "p":  channel,
            "t":  text,
            "ts": int(time.time()),
        }
        with self._lock:
            self._next_id += 1
            self._store[channel].append(record)
        self._save()
        return record

    def get(self, channel: str) -> list[dict]:
        """Return all stored messages for a channel (oldest first)."""
        with self._lock:
            return list(self._store.get(channel, []))

    def clear(self, channel: str) -> int:
        """Delete all messages for a channel; returns count removed."""
        with self._lock:
            n = len(self._store.get(channel, []))
            self._store.pop(channel, None)
        self._save()
        return n

    def channel_count(self) -> int:
        with self._lock:
            return sum(1 for msgs in self._store.values() if msgs)

    def total_count(self) -> int:
        with self._lock:
            return sum(len(msgs) for msgs in self._store.values())

    def summary(self) -> str:
        with self._lock:
            active = {ch: list(msgs) for ch, msgs in self._store.items() if msgs}
        if not active:
            return "  (no BBS messages stored)"
        lines = [f"{B}BBS ({sum(len(v) for v in active.values())} messages, "
                 f"{len(active)} channel(s)){R}"]
        for ch, msgs in sorted(active.items()):
            last_ts = msgs[-1]["ts"] if msgs else 0
            try:
                last_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(last_ts))
            except Exception:
                last_str = "?"
            lines.append(f"  {CY}#{ch:<22}{R} [{len(msgs)} msg(s)]  "
                         f"last: {DM}{last_str}{R}")
        return "\n".join(lines)

    def detail(self, channel: str) -> str:
        msgs = self.get(channel)
        if not msgs:
            return f"  (no BBS messages for #{channel})"
        lines = [f"{B}BBS #{channel}{R}  ({len(msgs)} message(s))"]
        for m in msgs:
            try:
                ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["ts"]))
            except Exception:
                ts_str = "?"
            lines.append(f"  {DM}{ts_str}{R}  {B}{CY}[{m['n']}]{R}  {m['t']}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_START = time.time()

def _uptime(start: float) -> str:
    s = int(time.time() - start)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"

def _ts() -> str:
    return time.strftime("%H:%M:%S")


# ══════════════════════════════════════════════════════════════════════════════
# RELAY SERVER
# ══════════════════════════════════════════════════════════════════════════════

class RelayServer:
    def __init__(self, host: str, port: int,
                 ttl: int = 300, max_per_channel: int = 128,
                 log_file: str = "", quiet: bool = False,
                 bbs_max: int = 50, bbs_file: str = ""):
        self.host       = host
        self.port       = port
        self.quiet      = quiet
        self.registry   = ClientRegistry(ttl=ttl, max_per_channel=max_per_channel)
        self.bbs        = BbsStore(max_per_channel=bbs_max, bbs_file=bbs_file)
        self._running   = False
        self._sock      : socket.socket | None = None
        self._send_lock = threading.Lock()
        self._log       : logging.Logger | None = None

        if log_file:
            logging.basicConfig(
                filename=log_file, level=logging.INFO,
                format="%(asctime)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            self._log = logging.getLogger("geotalk-relay")

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.settimeout(2.0)
        self._running = True

        self._print_banner()
        threading.Thread(target=self._prune_loop,   daemon=True, name="prune").start()
        threading.Thread(target=self._console_loop, daemon=True, name="console").start()

        # Worker pool — avoids spawning a thread per AUDIO packet
        _POOL_SIZE = 8
        self._work_q: "_queue.Queue[tuple[bytes,tuple]|None]" = _queue.Queue(maxsize=2048)
        for _ in range(_POOL_SIZE):
            threading.Thread(target=self._worker, daemon=True,
                             name="relay-worker").start()

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
                    pass   # drop under extreme load rather than block RX loop
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            self._sock.close()
            # Signal workers to exit
            for _ in range(8):
                try:
                    self._work_q.put_nowait(None)
                except Exception:
                    pass
            print(f"\n{DM}Relay shut down.  "
                  f"Forwarded {self.registry.total_pkts_tx} packets.{R}")

    def _worker(self):
        """Drain the work queue and call _handle for each packet."""
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

    def _print_banner(self):
        print(f"""
{B}{CY}   ██████╗  ███████╗  ██████╗  ████████╗  █████╗  ██╗      ██╗  ██╗{R}
{B}{CY}  ██╔════╝  ██╔════╝ ██╔═══██╗ ╚══██╔══╝ ██╔══██╗ ██║      ██║ ██╔╝{R}
{B}{CY}  ██║  ███╗ █████╗   ██║   ██║    ██║    ███████║ ██║      █████╔╝ {R}
{B}{CY}  ██║   ██║ ██╔══╝   ██║   ██║    ██║    ██╔══██║ ██║      ██╔═██╗ {R}
{B}{CY}  ╚██████╔╝ ███████╗ ╚██████╔╝    ██║    ██║  ██║ ███████╗ ██║  ██╗{R}
{B}{CY}   ╚═════╝  ╚══════╝  ╚═════╝     ╚═╝    ╚═╝  ╚═╝ ╚══════╝ ╚═╝  ╚═╝{R}
{DM}  📡  Relay Server  v{VERSION}   UDP :{self.port}{R}
  {DM}BBS: {self.bbs.bbs_file or 'in-memory only'}  (max {self.bbs.max_per_channel}/channel){R}
  {DM}Commands: stats · channels · clients · bbs · kick · ban · quit{R}
""")

    # ── packet handler ────────────────────────────────────────────────────

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

        # ── JOIN ──────────────────────────────────────────────────────────
        if ptype == PKT_JOIN:
            if not postal:
                return
            self.registry.subscribe(addr, postal, nick)
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {GR}+JOIN {R} "
                      f"{GR}{nick or addr[0]}{R} → {CY}#{postal}{R}")
            self._logf("JOIN nick=%s addr=%s:%s channel=%s",
                       nick, addr[0], addr[1], postal)
            return

        # ── LEAVE ─────────────────────────────────────────────────────────
        if ptype == PKT_LEAVE:
            self.registry.unsubscribe_all(addr)
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {YL}-LEAVE{R} "
                      f"{YL}{nick or addr[0]}{R}")
            self._logf("LEAVE nick=%s addr=%s:%s", nick, addr[0], addr[1])
            return

        # ── SCAN_REQ — record session, fan out to channel ─────────────────
        if ptype == PKT_SCAN_REQ:
            scan_id = meta.get("sid", "")
            if scan_id:
                self.registry.register_scan(scan_id, addr)
            # Fan out to the target channel (or all channels if 'p' is empty)
            self._fanout(data, postal, exclude=addr)
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {MG}SCAN_REQ{R} "
                      f"{nick or addr[0]} → {CY}#{postal or '*'}{R}  "
                      f"sid={scan_id[:8] if scan_id else '?'}")
            self._logf("SCAN_REQ nick=%s channel=%s sid=%s",
                       nick, postal, scan_id)
            return

        # ── SCAN_RSP — unicast back to original requester only ────────────
        if ptype == PKT_SCAN_RSP:
            scan_id   = meta.get("sid", "")
            requester = self.registry.requester_for_scan(scan_id)
            if requester:
                self._sendto(data, requester)
                if not self.quiet:
                    print(f"{DM}{_ts()}{R} {MG}SCAN_RSP{R} "
                          f"{nick or addr[0]} → "
                          f"{requester[0]}:{requester[1]}  "
                          f"ch={CY}#{postal}{R}")
                self._logf("SCAN_RSP nick=%s channel=%s → %s:%s",
                           nick, postal, requester[0], requester[1])
            return

        # ── TEXT / AUDIO / PING — fan out to channel ──────────────────────
        if ptype in (PKT_TEXT, PKT_AUDIO, PKT_PING):
            if postal:
                # Receiving traffic also refreshes the subscription
                self.registry.subscribe(addr, postal, nick)
            self._fanout(data, postal, exclude=addr)
            if ptype != PKT_AUDIO and not self.quiet:
                label   = PKT_NAMES.get(ptype, hex(ptype))
                preview = (f"  {DM}\"{meta.get('t','')[:40]}\"{R}"
                           if ptype == PKT_TEXT else "")
                print(f"{DM}{_ts()}{R} {label:<8} "
                      f"{nick or addr[0]} → {CY}#{postal}{R}{preview}")
            self._logf("%s nick=%s channel=%s",
                       PKT_NAMES.get(ptype, hex(ptype)), nick, postal)
            return

        # ── BBS_POST — store message, unicast confirmation ────────────────
        if ptype == PKT_BBS_POST:
            if not postal or not nick:
                return
            text = meta.get("t", "").strip()
            if not text:
                return
            record = self.bbs.post(nick, postal, text)
            # Echo the stored record back to sender as confirmation
            rsp_payload = json.dumps({
                "p": postal, "msgs": [record], "echo": True
            }).encode()
            rsp = MAGIC + bytes([PKT_BBS_RSP]) + struct.pack("!H", len(rsp_payload)) + rsp_payload
            self._sendto(rsp, addr)
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {MG}BBS_POST{R} "
                      f"{GR}{nick}{R} → {CY}#{postal}{R}  "
                      f"{DM}\"{text[:50]}\"{R}")
            self._logf("BBS_POST nick=%s channel=%s text=%s", nick, postal, text[:80])
            return

        # ── BBS_REQ — deliver stored messages to requester ────────────────
        if ptype == PKT_BBS_REQ:
            if not postal:
                return
            msgs = self.bbs.get(postal)
            rsp_payload = json.dumps({"p": postal, "msgs": msgs}).encode()
            rsp = MAGIC + bytes([PKT_BBS_RSP]) + struct.pack("!H", len(rsp_payload)) + rsp_payload
            self._sendto(rsp, addr)
            if not self.quiet:
                print(f"{DM}{_ts()}{R} {MG}BBS_REQ{R}  "
                      f"{nick or addr[0]} → {CY}#{postal}{R}  "
                      f"{DM}({len(msgs)} msg(s) delivered){R}")
            self._logf("BBS_REQ nick=%s channel=%s msgs=%d", nick, postal, len(msgs))
            return

        # Unknown — drop silently but log
        self._logf("UNKNOWN ptype=0x%02x addr=%s:%s", ptype, addr[0], addr[1])

    # ── fan-out & send ────────────────────────────────────────────────────

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

    # ── background prune ─────────────────────────────────────────────────

    def _prune_loop(self):
        while self._running:
            time.sleep(30)
            removed = self.registry.prune_stale()
            self.registry.prune_scan_sessions()
            if removed and not self.quiet:
                print(f"{DM}{_ts()}  pruned {removed} stale client(s){R}")

    # ── console ───────────────────────────────────────────────────────────

    def _console_loop(self):
        while self._running:
            try:
                line = input().strip()
            except EOFError:
                break
            if not line:
                continue
            parts = line.split(maxsplit=1)
            cmd   = parts[0].lower()
            arg   = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "stats":
                print(self.registry.stats_summary())
                print(self.bbs.summary())
            elif cmd == "channels":
                print(self.registry.channels_detail())
            elif cmd in ("clients", "who"):
                print(self.registry.clients_detail())
            elif cmd == "bbs":
                if arg:
                    print(self.bbs.detail(arg.upper()))
                else:
                    print(self.bbs.summary())
            elif cmd in ("bbs-clear", "bbsclear"):
                if not arg:
                    print(f"{YL}Usage: bbs-clear CHANNEL{R}")
                else:
                    n = self.bbs.clear(arg.upper())
                    print(f"{GR}Cleared {n} BBS message(s) from #{arg.upper()}{R}")
            elif cmd == "kick":
                if not arg:
                    print(f"{YL}Usage: kick NICK{R}")
                elif self.registry.kick_nick(arg):
                    print(f"{GR}Kicked {arg}{R}")
                else:
                    print(f"{YL}Nick '{arg}' not found{R}")
            elif cmd == "ban":
                if not arg:
                    print(f"{YL}Usage: ban IP{R}")
                else:
                    self.registry.ban(arg)
                    print(f"{RD}Banned {arg}{R}")
            elif cmd == "unban":
                if not arg:
                    print(f"{YL}Usage: unban IP{R}")
                else:
                    self.registry.unban(arg)
                    print(f"{GR}Unbanned {arg}{R}")
            elif cmd == "bans":
                bans = self.registry.banned_ips()
                print("Banned IPs: " + (", ".join(bans) if bans else "none"))
            elif cmd in ("quit", "q", "exit"):
                self._running = False
                break
            else:
                print(f"{YL}Commands: stats  channels  clients  bbs [CHANNEL]  "
                      f"bbs-clear CHANNEL  kick NICK  ban IP  unban IP  bans  quit{R}")

    # ── log helper ────────────────────────────────────────────────────────

    def _logf(self, fmt: str, *args):
        if self._log:
            self._log.info(fmt, *args)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=f"GeoTalk Relay Server v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 geotalk-relay.py
  python3 geotalk-relay.py --port 5073 --ttl 600
  python3 geotalk-relay.py --log-file /var/log/geotalk-relay.log --quiet
""")
    parser.add_argument("--host",            default="0.0.0.0",
                        help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port",            type=int, default=5073,
                        help="UDP port (default 5073)")
    parser.add_argument("--ttl",             type=int, default=300,
                        metavar="SECONDS",
                        help="Client subscription TTL in seconds (default 300)")
    parser.add_argument("--max-per-channel", type=int, default=128,
                        metavar="N",
                        help="Max clients per channel (default 128)")
    parser.add_argument("--log-file",        default="", metavar="PATH",
                        help="Append structured log lines to this file")
    parser.add_argument("--quiet",           action="store_true",
                        help="Suppress per-packet console output")
    parser.add_argument("--bbs-file",        default="geotalk-bbs.json",
                        metavar="PATH",
                        help="JSON file for BBS persistence (default: geotalk-bbs.json, "
                             "use '' to disable)")
    parser.add_argument("--bbs-max",         type=int, default=50,
                        metavar="N",
                        help="Max BBS messages stored per channel (default 50)")
    args = parser.parse_args()

    RelayServer(
        host            = args.host,
        port            = args.port,
        ttl             = args.ttl,
        max_per_channel = args.max_per_channel,
        log_file        = args.log_file,
        quiet           = args.quiet,
        bbs_max         = args.bbs_max,
        bbs_file        = args.bbs_file,
    ).start()


if __name__ == "__main__":
    main()
