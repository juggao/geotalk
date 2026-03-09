#!/usr/bin/env python3
"""
geotalk-relay-cli.py — GeoTalk Relay CLI  v2.1.0
Author: René Oudeweg / Claude
─────────────────────────────────────────────────────────
Local control client for geotalk-relayd.  Connects to the
daemon's Unix-domain control socket and exposes every control
command as either a one-shot invocation or an interactive REPL.

One-shot mode  (exits after printing the result)
  python3 geotalk-relay-cli.py stats
  python3 geotalk-relay-cli.py channels
  python3 geotalk-relay-cli.py clients
  python3 geotalk-relay-cli.py bbs
  python3 geotalk-relay-cli.py bbs 1010
  python3 geotalk-relay-cli.py bbs-clear 1010
  python3 geotalk-relay-cli.py bbs-post 1010 "Hello from operator"
  python3 geotalk-relay-cli.py kick PA3XYZ
  python3 geotalk-relay-cli.py ban 1.2.3.4
  python3 geotalk-relay-cli.py unban 1.2.3.4
  python3 geotalk-relay-cli.py bans
  python3 geotalk-relay-cli.py log [N]
  python3 geotalk-relay-cli.py quiet [on|off]
  python3 geotalk-relay-cli.py stop

Interactive REPL  (readline history + tab completion)
  python3 geotalk-relay-cli.py
  python3 geotalk-relay-cli.py --interactive

Options
  --socket PATH   Control socket path (overrides auto-detect)
  --json          Print raw JSON responses instead of formatted output
  --no-color      Disable ANSI colour output
  -i, --interactive  Force interactive REPL mode
"""

import sys
import os
import socket
import json
import argparse
import time
import textwrap
import shutil

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS & DEFAULTS
# ══════════════════════════════════════════════════════════════════════════════

VERSION = "2.1.0"

def _default_ctl_socket() -> str:
    run = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return os.path.join(run, "geotalk-relayd.sock")

CONNECT_TIMEOUT = 3.0
RECV_TIMEOUT    = 10.0

# Commands that take no argument, one argument, or two
_CMD_META: dict[str, tuple[int, int, str]] = {
    # cmd         min_args  max_args  usage_hint
    "stats":      (0, 0, ""),
    "channels":   (0, 0, ""),
    "clients":    (0, 0, ""),
    "who":        (0, 0, ""),
    "bbs":        (0, 1, "[CHANNEL]"),
    "bbs-clear":  (1, 1, "CHANNEL"),
    "bbs-post":   (2, 9, "CHANNEL TEXT…"),
    "kick":       (1, 1, "NICK"),
    "ban":        (1, 1, "IP"),
    "unban":      (1, 1, "IP"),
    "bans":       (0, 0, ""),
    "log":        (0, 1, "[N]"),
    "quiet":      (0, 1, "[on|off]"),
    "stop":       (0, 0, ""),
    "ping":       (0, 0, ""),
    "help":       (0, 1, "[COMMAND]"),
    "quit":       (0, 0, ""),
    "exit":       (0, 0, ""),
}


# ══════════════════════════════════════════════════════════════════════════════
# COLOUR PALETTE  — same amber CRT theme as geotalk-gui / geotalk-relay-gui
# ══════════════════════════════════════════════════════════════════════════════

class _C:
    """ANSI colour constants.  All disabled when --no-color is given."""
    enabled = True

    R   = "\033[0m"
    B   = "\033[1m"
    DM  = "\033[2m"
    CY  = "\033[96m"
    GR  = "\033[92m"
    YL  = "\033[93m"
    RD  = "\033[91m"
    MG  = "\033[95m"
    AM  = "\033[38;5;214m"   # amber
    AMD = "\033[38;5;136m"   # amber dim
    BL  = "\033[94m"

    @classmethod
    def disable(cls):
        cls.enabled = False
        for attr in ("R","B","DM","CY","GR","YL","RD","MG","AM","AMD","BL"):
            setattr(cls, attr, "")

C = _C()


# ══════════════════════════════════════════════════════════════════════════════
# TERMINAL WIDTH
# ══════════════════════════════════════════════════════════════════════════════

def _tw() -> int:
    return shutil.get_terminal_size((100, 40)).columns


# ══════════════════════════════════════════════════════════════════════════════
# CONNECTION
# ══════════════════════════════════════════════════════════════════════════════

class DaemonConnection:
    """
    Persistent connection to the geotalk-relayd control socket.
    Sends JSON requests, reads JSON responses (newline-delimited).
    """

    def __init__(self, sock_path: str):
        self._path = sock_path
        self._sock : socket.socket | None = None
        self._rbuf  = b""

    def connect(self):
        if not os.path.exists(self._path):
            raise FileNotFoundError(
                f"Control socket not found: {self._path}\n"
                f"Is geotalk-relayd running?")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(CONNECT_TIMEOUT)
        try:
            s.connect(self._path)
        except (ConnectionRefusedError, OSError) as e:
            raise ConnectionRefusedError(
                f"Cannot connect to {self._path}: {e}\n"
                f"Is geotalk-relayd running?") from e
        s.settimeout(RECV_TIMEOUT)
        self._sock = s
        # Read the greeting the daemon sends on connect
        greeting = self._read_raw()
        return greeting

    def send(self, obj: dict) -> dict:
        if not self._sock:
            raise RuntimeError("Not connected")
        data = json.dumps(obj).encode() + b"\n"
        self._sock.sendall(data)
        return self._read_one()

    def _read_raw(self) -> dict:
        """Read exactly one newline-terminated JSON object from the stream."""
        while b"\n" not in self._rbuf:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                raise TimeoutError("No response from daemon (timeout)")
            if not chunk:
                raise ConnectionError("Daemon closed the connection")
            self._rbuf += chunk
        line, self._rbuf = self._rbuf.split(b"\n", 1)
        return json.loads(line)

    def _read_one(self) -> dict:
        """Read the next command response, skipping unsolicited server events
        (heartbeats, connected greeting) that carry an 'event' key."""
        while True:
            msg = self._read_raw()
            if "event" not in msg:
                return msg

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


# ══════════════════════════════════════════════════════════════════════════════
# RENDERER  — turns JSON response dicts into human-readable terminal output
# ══════════════════════════════════════════════════════════════════════════════

class Renderer:

    @staticmethod
    def _bar(char: str = "─") -> str:
        return C.DM + char * min(_tw(), 72) + C.R

    @staticmethod
    def _hdr(text: str) -> str:
        return f"{C.B}{C.AM}  {text}{C.R}"

    @staticmethod
    def _kv(key: str, val, key_w: int = 18) -> str:
        return f"  {C.AMD}{key:<{key_w}}{C.R}  {C.AM}{val}{C.R}"

    # ── stats ─────────────────────────────────────────────────────────────────

    @classmethod
    def stats(cls, data: dict):
        print()
        print(cls._hdr(f"◈ GeoTalk Relay Daemon  v{data.get('version','?')}"))
        print(cls._bar())
        print(cls._kv("Uptime",    data.get("uptime", "?")))
        port = data.get("port", 0)
        print(cls._kv("UDP port",  f":{port}" if port else "(unknown)"))
        print(cls._kv("Clients",   f"{C.GR}{data.get('clients', 0)}{C.R}"))
        print(cls._kv("Channels",  f"{C.CY}{data.get('channels', 0)}{C.R}"))
        print(cls._kv("RX",
                      f"{data.get('bytes_rx_fmt','?')}  "
                      f"{C.DM}({data.get('pkts_rx',0)} pkts){C.R}"))
        print(cls._kv("TX",
                      f"{data.get('bytes_tx_fmt','?')}  "
                      f"{C.DM}({data.get('pkts_tx',0)} pkts){C.R}"))
        print(cls._kv("Quiet mode",
                      f"{C.YL}on{C.R}" if data.get("quiet")
                      else f"{C.DM}off{C.R}"))
        bans = data.get("bans", 0)
        if bans:
            print(cls._kv("Bans", f"{C.RD}{bans}{C.R}"))

        bbs = data.get("bbs", {})
        if bbs:
            print(cls._bar("·"))
            print(cls._kv("BBS messages", bbs.get("total", 0)))
            print(cls._kv("BBS file",     bbs.get("file", "(in-memory)")))
            if bbs.get("channels"):
                ch_line = "  ".join(
                    f"{C.CY}#{k}{C.R}:{C.DM}{v}{C.R}"
                    for k, v in sorted(bbs["channels"].items()))
                print(cls._kv("BBS channels", ch_line))
        print()

    # ── channels ──────────────────────────────────────────────────────────────

    @classmethod
    def channels(cls, data: dict):
        SYS = {"INFO", "TEST", "EMERGENCY"}
        if not data:
            print(f"\n  {C.DM}(no active channels){C.R}\n")
            return
        print()
        print(cls._hdr(f"Active channels  ({len(data)})"))
        print(cls._bar())
        for ch, nicks in sorted(data.items()):
            colour = C.BL if ch in SYS else C.GR
            nick_str = (f"{C.DM}" +
                        "  ".join(nicks[:12]) +
                        (f"  +{len(nicks)-12} more" if len(nicks) > 12 else "") +
                        f"{C.R}")
            print(f"  {colour}#{ch:<22}{C.R}  "
                  f"{C.DM}[{len(nicks):>3}]{C.R}  {nick_str}")
        print()

    # ── clients ───────────────────────────────────────────────────────────────

    @classmethod
    def clients(cls, data: list):
        if not data:
            print(f"\n  {C.DM}(no connected clients){C.R}\n")
            return
        print()
        print(cls._hdr(f"Connected clients  ({len(data)})"))
        print(cls._bar())
        col_w = [16, 22, 26, 8, 8, 8, 8]
        hdr = (f"  {C.AMD}{'NICK':<{col_w[0]}}  {'ADDRESS':<{col_w[1]}}"
               f"  {'CHANNELS':<{col_w[2]}}  {'UP':<{col_w[3]}}"
               f"  {'IDLE':>{col_w[4]}}  {'RX':>{col_w[5]}}"
               f"  {'TX':>{col_w[6]}}{C.R}")
        print(hdr)
        print(f"  {C.DM}{'─'*col_w[0]}  {'─'*col_w[1]}  {'─'*col_w[2]}"
              f"  {'─'*col_w[3]}  {'─'*col_w[4]}  {'─'*col_w[5]}"
              f"  {'─'*col_w[6]}{C.R}")
        for c in data:
            addr     = f"{c['ip']}:{c['port']}"
            channels = " ".join(f"#{ch}" for ch in c.get("channels", []))
            idle     = f"{c.get('idle_s', 0):.0f}s"
            print(f"  {C.GR}{c['nick']:<{col_w[0]}}{C.R}  "
                  f"{C.DM}{addr:<{col_w[1]}}{C.R}  "
                  f"{C.CY}{channels:<{col_w[2]}}{C.R}  "
                  f"{C.DM}{c.get('uptime','?'):<{col_w[3]}}{C.R}  "
                  f"{C.DM}{idle:>{col_w[4]}}{C.R}  "
                  f"{C.DM}{c.get('pkts_rx',0):>{col_w[5]}}{C.R}  "
                  f"{C.DM}{c.get('pkts_tx',0):>{col_w[6]}}{C.R}")
        print()

    # ── bbs summary ───────────────────────────────────────────────────────────

    @classmethod
    def bbs_summary(cls, data: dict):
        total = data.get("total", 0)
        print()
        print(cls._hdr(f"BBS  —  {total} message(s)"))
        print(cls._bar())
        print(cls._kv("File",       data.get("file", "(in-memory)")))
        print(cls._kv("Max/channel",data.get("max_per_channel", "?")))
        channels = data.get("channels", {})
        if channels:
            print()
            print(f"  {C.AMD}{'CHANNEL':<24}  MESSAGES{C.R}")
            print(f"  {C.DM}{'─'*24}  {'─'*8}{C.R}")
            for ch, n in sorted(channels.items()):
                print(f"  {C.CY}#{ch:<23}{C.R}  {C.AM}{n}{C.R}")
        else:
            print(f"\n  {C.DM}(no messages){C.R}")
        print()

    # ── bbs detail ────────────────────────────────────────────────────────────

    @classmethod
    def bbs_detail(cls, data: list, channel: str = ""):
        hdr_text = f"BBS #{channel}" if channel else "BBS"
        print()
        print(cls._hdr(f"{hdr_text}  —  {len(data)} message(s)"))
        print(cls._bar())
        if not data:
            print(f"  {C.DM}(no messages){C.R}")
        for msg in data:
            ts   = time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(msg.get("ts", 0)))
            nick = msg.get("n", "?")
            text = msg.get("t", "")
            print(f"  {C.DM}{ts}{C.R}  {C.GR}{nick:<14}{C.R}  "
                  f"{C.AM}{text}{C.R}")
        print()

    # ── bans ─────────────────────────────────────────────────────────────────

    @classmethod
    def bans(cls, data: list):
        print()
        if not data:
            print(f"  {C.DM}(no banned IPs){C.R}")
        else:
            print(cls._hdr(f"Banned IPs  ({len(data)})"))
            print(cls._bar())
            for ip in data:
                print(f"  {C.RD}{ip}{C.R}")
        print()

    # ── log ───────────────────────────────────────────────────────────────────

    @classmethod
    def log(cls, data: list, n: int = 0):
        print()
        label = f"last {n}" if n else f"{len(data)}"
        print(cls._hdr(f"Server log  ({label} lines)"))
        print(cls._bar())
        for line in data:
            # Colour by content (mirrors relay's own classification)
            lo = line.lower()
            if any(w in lo for w in ("error", "exception", "rejected")):
                colour = C.RD
            elif "+join" in lo or "seeded" in lo:
                colour = C.GR
            elif "-leave" in lo or "prune" in lo:
                colour = C.DM
            elif "bbs_post" in lo or "bbs_req" in lo:
                colour = C.MG
            elif "scan" in lo:
                colour = C.CY
            elif "warn" in lo or "timeout" in lo:
                colour = C.YL
            else:
                colour = C.AMD
            print(f"  {colour}{line}{C.R}")
        print()

    # ── simple acknowledgements ───────────────────────────────────────────────

    @classmethod
    def ok(cls, resp: dict):
        """Render a simple ok/error response."""
        cmd = resp.get("cmd", "")
        if not resp.get("ok"):
            print(f"\n  {C.RD}✗  {resp.get('error', 'Unknown error')}{C.R}\n")
            return

        if cmd == "kick":
            print(f"\n  {C.GR}✓  Kicked {resp.get('nick','?')}{C.R}\n")
        elif cmd == "ban":
            print(f"\n  {C.RD}✓  Banned {resp.get('ip','?')}{C.R}\n")
        elif cmd == "unban":
            print(f"\n  {C.GR}✓  Unbanned {resp.get('ip','?')}{C.R}\n")
        elif cmd == "bbs-clear":
            print(f"\n  {C.GR}✓  Cleared {resp.get('cleared',0)} message(s) "
                  f"from #{resp.get('channel','?')}{C.R}\n")
        elif cmd == "bbs-post":
            rec = resp.get("record", {})
            print(f"\n  {C.GR}✓  Posted to #{rec.get('channel','?')} "
                  f"as operator{C.R}\n")
        elif cmd == "quiet":
            state = f"{C.YL}on{C.R}" if resp.get("quiet") else f"{C.DM}off{C.R}"
            print(f"\n  {C.GR}✓  Quiet mode: {state}\n")
        elif cmd == "stop":
            print(f"\n  {C.YL}◈  {resp.get('msg','Shutdown initiated')}{C.R}\n")
        elif cmd == "ping":
            print(f"\n  {C.GR}✓  pong{C.R}\n")
        else:
            print(f"\n  {C.GR}✓  OK{C.R}\n")

    # ── error ─────────────────────────────────────────────────────────────────

    @classmethod
    def error(cls, msg: str):
        print(f"\n  {C.RD}✗  {msg}{C.R}\n", file=sys.stderr)

    # ── help ─────────────────────────────────────────────────────────────────

    @classmethod
    def help(cls, topic: str = ""):
        HELP = {
            "stats":     "Show runtime statistics: uptime, clients, traffic, BBS.",
            "channels":  "List active channels and their subscribers.",
            "clients":   "List all connected clients with traffic stats.",
            "bbs":       "bbs [CHANNEL]  — BBS summary, or per-channel message list.",
            "bbs-clear": "bbs-clear CHANNEL  — Delete all BBS messages for a channel.",
            "bbs-post":  "bbs-post CHANNEL TEXT  — Post a message as 'operator' (system channels allowed).",
            "kick":      "kick NICK  — Disconnect a client by nickname.",
            "ban":       "ban IP  — Block an IP address permanently.",
            "unban":     "unban IP  — Remove an IP ban.",
            "bans":      "List all currently banned IP addresses.",
            "log":       "log [N]  — Show last N server log lines (default 100).",
            "quiet":     "quiet [on|off]  — Toggle per-packet log output.",
            "stop":      "Initiate a clean daemon shutdown.",
            "ping":      "Check that the daemon is alive.",
            "help":      "help [COMMAND]  — Show help.",
            "quit":      "Exit the interactive CLI (does NOT stop the daemon).",
        }
        print()
        if topic and topic in HELP:
            print(f"  {C.AM}{topic}{C.R}  —  {HELP[topic]}")
        else:
            print(cls._hdr("Commands"))
            print(cls._bar())
            for cmd, desc in HELP.items():
                if cmd in ("exit",):
                    continue
                usage = _CMD_META.get(cmd, (0,0,""))[2]
                full  = f"{cmd} {usage}".strip()
                print(f"  {C.AM}{full:<32}{C.R}  {C.DM}{desc}{C.R}")
        print()


# ══════════════════════════════════════════════════════════════════════════════
# COMMAND DISPATCHER
# ══════════════════════════════════════════════════════════════════════════════

def _build_request(cmd: str, args: list[str]) -> dict | None:
    """Turn a command + arg list into a JSON request dict, or None on error."""
    req: dict = {"cmd": cmd}
    if cmd in ("stats", "channels", "clients", "who", "bans",
               "stop", "ping", "quit", "exit"):
        pass   # no args
    elif cmd in ("log", "quiet"):
        if args:
            req["arg"] = args[0]
    elif cmd == "bbs":
        if args:
            req["arg"] = args[0]
    elif cmd in ("bbs-clear", "kick", "ban", "unban"):
        if not args:
            return None   # missing required arg
        req["arg"] = args[0]
    elif cmd == "bbs-post":
        if len(args) < 2:
            return None
        req["arg"]  = args[0]
        req["arg2"] = " ".join(args[1:])
    return req


def dispatch(conn: DaemonConnection, cmd: str, args: list[str],
             raw_json: bool = False) -> bool:
    """
    Send one command, render the response.
    Returns False if the CLI should exit (quit/exit/stop).
    """
    if cmd in ("help",):
        Renderer.help(args[0] if args else "")
        return True
    if cmd in ("quit", "exit"):
        return False

    req = _build_request(cmd, args)
    if req is None:
        meta = _CMD_META.get(cmd, (0, 0, ""))
        Renderer.error(f"Usage: {cmd} {meta[2]}")
        return True

    try:
        resp = conn.send(req)
    except (TimeoutError, ConnectionError, OSError) as e:
        Renderer.error(str(e))
        return True

    if raw_json:
        print(json.dumps(resp, indent=2))
        return True

    if not resp.get("ok"):
        Renderer.error(resp.get("error", "Unknown error"))
        return True

    rc = resp.get("cmd", cmd)

    if rc == "stats":
        Renderer.stats(resp["data"])
    elif rc == "channels":
        Renderer.channels(resp["data"])
    elif rc in ("clients", "who"):
        Renderer.clients(resp["data"])
    elif rc == "bbs":
        if "channel" in resp:
            Renderer.bbs_detail(resp["data"], resp["channel"])
        else:
            Renderer.bbs_summary(resp["data"])
    elif rc == "bans":
        Renderer.bans(resp["data"])
    elif rc == "log":
        Renderer.log(resp["data"], resp.get("n", 0))
    elif rc == "stop":
        Renderer.ok(resp)
        return False   # daemon is going away
    else:
        Renderer.ok(resp)

    return True


# ══════════════════════════════════════════════════════════════════════════════
# INTERACTIVE REPL
# ══════════════════════════════════════════════════════════════════════════════

def _setup_readline():
    """Enable readline history + tab completion if available."""
    try:
        import readline
        import atexit
        import pathlib

        hist_path = pathlib.Path.home() / ".local" / "share" / "geotalk" / \
                    "relay-cli-history"
        hist_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            readline.read_history_file(str(hist_path))
        except FileNotFoundError:
            pass
        readline.set_history_length(500)
        atexit.register(readline.write_history_file, str(hist_path))

        cmds = sorted(_CMD_META.keys())

        def completer(text, state):
            options = [c for c in cmds if c.startswith(text)]
            return options[state] if state < len(options) else None

        readline.set_completer(completer)
        readline.parse_and_bind(
            "tab: complete" if sys.platform != "darwin"
            else "bind ^I rl_complete")
    except ImportError:
        pass


def _prompt(conn_info: str) -> str:
    return f"{C.AM}relay{C.R}{C.DM}@{C.R}{C.CY}{conn_info}{C.R}{C.AMD} ▸ {C.R}"


def interactive_repl(conn: DaemonConnection, sock_path: str,
                     raw_json: bool = False):
    """Run an interactive control REPL."""
    _setup_readline()

    # Show a compact banner
    sock_name = os.path.basename(sock_path)
    print()
    print(f"{C.B}{C.AM}  ◈ GeoTalk Relay CLI  v{VERSION}{C.R}")
    print(f"  {C.DM}Connected to {sock_path}{C.R}")
    print(f"  {C.DM}Type 'help' for commands, 'quit' to exit{C.R}")
    print()

    prompt = _prompt(sock_name)

    while True:
        try:
            line = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        parts = line.split()
        cmd   = parts[0].lower()
        args  = parts[1:]

        keep_going = dispatch(conn, cmd, args, raw_json=raw_json)
        if not keep_going:
            break

    print(f"  {C.DM}Disconnected.{C.R}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="geotalk-relay-cli",
        description=f"GeoTalk Relay CLI v{VERSION} — control geotalk-relayd",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Commands:
  stats                    Runtime statistics
  channels                 Active channels
  clients                  Connected clients
  bbs [CHANNEL]            BBS summary or channel detail
  bbs-clear CHANNEL        Clear BBS for a channel
  bbs-post CHANNEL TEXT    Post a BBS message as operator
  kick NICK                Kick a client
  ban IP                   Ban an IP
  unban IP                 Unban an IP
  bans                     List banned IPs
  log [N]                  Last N log lines (default 100)
  quiet [on|off]           Toggle per-packet logging
  stop                     Shut down the daemon

Default socket: {_default_ctl_socket()}

Examples:
  geotalk-relay-cli stats
  geotalk-relay-cli kick PA3XYZ
  geotalk-relay-cli --interactive
  geotalk-relay-cli --socket /run/geotalk/relay.sock channels
""")
    parser.add_argument("cmd_args", nargs="*",
                        metavar="COMMAND [ARGS…]",
                        help="Command to run (omit for interactive mode)")
    parser.add_argument("--socket", "-s", default="",
                        metavar="PATH",
                        help="Control socket path")
    parser.add_argument("--json",   action="store_true",
                        help="Print raw JSON responses")
    parser.add_argument("--no-color", "--no-colour", action="store_true",
                        help="Disable colour output")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Force interactive REPL mode")
    args = parser.parse_args()

    if args.no_color:
        C.disable()

    sock_path = args.socket or _default_ctl_socket()

    # ── Connect ───────────────────────────────────────────────────────────────
    conn = DaemonConnection(sock_path)
    try:
        greeting = conn.connect()
    except (FileNotFoundError, ConnectionRefusedError) as e:
        print(f"{C.RD}✗  {e}{C.R}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(greeting, indent=2))
    elif greeting.get("event") == "connected":
        ver = greeting.get("version", "?")
        up  = greeting.get("uptime", "?")
        print(f"{C.DM}◈ geotalk-relayd v{ver}  up {up}{C.R}")

    # ── One-shot or interactive ───────────────────────────────────────────────
    try:
        if args.cmd_args and not args.interactive:
            cmd  = args.cmd_args[0].lower()
            cargs = args.cmd_args[1:]
            dispatch(conn, cmd, cargs, raw_json=args.json)
        else:
            interactive_repl(conn, sock_path, raw_json=args.json)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
