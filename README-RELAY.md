# GeoTalk Relay Daemon

`geotalk-relayd` · `geotalk-relay-cli` · v2.1.0

---

`geotalk-relayd` is the production relay server for GeoTalk. It bridges
UDP traffic between clients across the internet, hosts the BBS, and exposes
a **Unix-domain control socket** so that `geotalk-relay-cli` (or the desktop
GUI) can inspect and manage the running daemon at any time without restarting
it or attaching to its terminal.

Clients connect to the relay exactly as they did with `geotalk-relay.py` —
the UDP wire protocol is unchanged.

---

## Files

| File | Role |
|---|---|
| `geotalk-relayd.py` | Relay daemon — UDP server + control socket |
| `geotalk-relay-cli.py` | Operator CLI — talks to the daemon over the control socket |
| `geotalk-relay-gui.py` | Operator GUI — tkinter interface, same control socket |
| `geotalk-bbs.json` | BBS persistence (created automatically on first run) |

---

## Quick Start

```bash
# Foreground — output to terminal, Ctrl-C to stop
python3 geotalk-relayd.py

# Background daemon (stdout/stderr go to the log file)
python3 geotalk-relayd.py \
    --daemonize \
    --log-file /var/log/geotalk-relayd.log

# Clients connect the same way as always
python3 geotalk.py --nick PA3XYZ --relay your-server.example.com
```

Once the daemon is running, open a second terminal on the same machine and
connect the CLI:

```bash
python3 geotalk-relay-cli.py            # interactive REPL
python3 geotalk-relay-cli.py stats      # one-shot, then exit
python3 geotalk-relay-cli.py channels
```

---

## Daemon Options

| Option | Default | Description |
|---|---|---|
| `--host HOST` | `0.0.0.0` | UDP bind address |
| `--port PORT` | `5073` | UDP port that clients connect to |
| `--ttl SECONDS` | `300` | Idle client eviction timeout in seconds |
| `--max-per-channel N` | `128` | Maximum subscribers per channel |
| `--bbs-file PATH` | `geotalk-bbs.json` | BBS persistence file. Pass `''` to keep BBS in memory only |
| `--bbs-max N` | `50` | Maximum BBS messages stored per channel |
| `--log-file PATH` | _(stdout)_ | Append structured log lines to this file. **Required** when using `--daemonize` |
| `--quiet` | off | Suppress per-packet log lines (JOIN/LEAVE/BBS activity still logged) |
| `--ctl-socket PATH` | `$XDG_RUNTIME_DIR/geotalk-relayd.sock` | Unix control socket path |
| `--pid-file PATH` | `$XDG_RUNTIME_DIR/geotalk-relayd.pid` | PID file path |
| `--daemonize` | off | Double-fork to background. Unix only. Requires `--log-file` |

**Socket and PID paths** — if `$XDG_RUNTIME_DIR` is set (typical on systemd
systems, e.g. `/run/user/1000`) that directory is used; otherwise `/tmp`.

---

## Daemon Behaviour

### Startup sequence

1. Bind the UDP socket on `--host:--port`
2. Write the PID file
3. Create the Unix control socket and start accepting connections
4. Register `SIGTERM` and `SIGINT` handlers for clean shutdown
5. Print the startup banner
6. Seed system channel BBS messages — only if those channels are currently empty in the BBS file (see [System Channels](#system-channels))
7. Start the prune loop (background thread, every 30 s)
8. Start the worker pool (8 threads)
9. Enter the main UDP receive loop

### Shutdown

Shutdown is triggered by Ctrl-C, `SIGTERM`, or the `stop` command from the
CLI. On shutdown the daemon:

- Closes both the UDP socket and the control socket
- Removes the control socket file and PID file
- Logs a final forwarded-packet count
- Signals the worker threads to drain and exit

### Daemonizing

```bash
python3 geotalk-relayd.py \
    --daemonize \
    --log-file /var/log/geotalk-relayd.log \
    --ctl-socket /run/geotalk/relay.sock \
    --pid-file   /run/geotalk/relay.pid
```

`--daemonize` performs a Unix double-fork, detaches from the controlling
terminal, and redirects stdout and stderr into the log file. Without
`--log-file` the daemon prints an error and refuses to start.

To stop a daemonized relay:

```bash
# Graceful shutdown via the CLI
python3 geotalk-relay-cli.py stop

# Or via signal
kill "$(cat /run/geotalk/relay.pid)"
```

---

## Control Socket

The daemon listens on a **Unix-domain stream socket**. This means only
processes on the same machine can connect — the control interface is never
exposed on the network and requires no authentication or firewall rules.

Multiple clients may be connected simultaneously; each gets its own thread.

### Wire format

Newline-delimited JSON. One JSON object per line in each direction.

```
→  {"cmd": "stats"}\n
←  {"ok": true,  "cmd": "stats", "data": {...}}\n
←  {"ok": false, "error": "Usage: kick NICK"}\n
```

### Server-push messages

The daemon sends two unsolicited messages that carry an `"event"` key and
are **not** responses to commands:

```jsonc
// Sent immediately on connect
{"ok": true, "event": "connected", "version": "2.1.0", "uptime": "00:05:23"}

// Sent every 60 s of idle time (keepalive)
{"ok": true, "event": "heartbeat", "ts": 1741536000}
```

`geotalk-relay-cli` silently discards messages with an `"event"` key while
waiting for a command response, so heartbeats never cause a misparse.

---

## CLI Usage

### One-shot mode

Pass a command on the command line. The CLI connects, runs the command,
prints the result, and exits. Useful in scripts and cron jobs.

```bash
python3 geotalk-relay-cli.py stats
python3 geotalk-relay-cli.py channels
python3 geotalk-relay-cli.py clients
python3 geotalk-relay-cli.py bbs
python3 geotalk-relay-cli.py bbs 1010
python3 geotalk-relay-cli.py bbs-clear 1010
python3 geotalk-relay-cli.py bbs-post INFO Net tonight 20:00 UTC on 1010
python3 geotalk-relay-cli.py kick PA3XYZ
python3 geotalk-relay-cli.py ban 203.0.113.99
python3 geotalk-relay-cli.py unban 203.0.113.99
python3 geotalk-relay-cli.py bans
python3 geotalk-relay-cli.py log 50
python3 geotalk-relay-cli.py quiet on
python3 geotalk-relay-cli.py stop
```

### Interactive REPL

Invoke with no command (or `-i`). Provides readline history and tab
completion.

```bash
python3 geotalk-relay-cli.py
python3 geotalk-relay-cli.py --interactive
```

```
◈ geotalk-relayd v2.1.0  up 00:12:44
relay@geotalk-relayd.sock ▸ stats
relay@geotalk-relayd.sock ▸ bbs-post INFO Net tonight 20:00 UTC
relay@geotalk-relayd.sock ▸ quit
```

- **Tab** — complete any command name
- **↑ / ↓** — scroll command history
- History is saved to `~/.local/share/geotalk/relay-cli-history` and reloaded on next session
- `help` — list all commands; `help COMMAND` — per-command description

### CLI flags

| Flag | Description |
|---|---|
| `--socket PATH` / `-s PATH` | Connect to a non-default control socket |
| `--json` | Print raw JSON responses instead of formatted output |
| `--no-color` / `--no-colour` | Disable ANSI colour output |
| `--interactive` / `-i` | Force REPL even when a command is given |

---

## Commands

### `stats`

Runtime statistics: version, uptime, client and channel counts, cumulative
RX/TX traffic, BBS state, ban count, and quiet-mode status.

```
relay@… ▸ stats

  ◈ GeoTalk Relay Daemon  v2.1.0
────────────────────────────────────────────────────────────────────────
  Uptime              01:23:45
  UDP port            :5073
  Clients             4
  Channels            3
  RX                  2.4MB  (18432 pkts)
  TX                  11.2MB (87040 pkts)
  Quiet mode          off
  Bans                1
········································································
  BBS messages        7
  BBS file            /var/lib/geotalk/bbs.json
  BBS channels        #INFO:1  #1010:3  #59**:3
```

---

### `channels`

All active channels with subscriber nicks and counts. System channels are
highlighted separately from user channels.

```
relay@… ▸ channels

  Active channels  (3)
────────────────────────────────────────────────────────────────────────
  #EMERGENCY               [  2]  PA3XYZ  PE1ABC
  #INFO                    [  3]  PA3XYZ  PE1ABC  PI4VNL
  #1010                    [  1]  PA3XYZ
```

---

### `clients` / `who`

All connected clients, sorted by most recently active. Shows nick, IP:port,
subscribed channels, session uptime, idle time, and packet counts.

```
relay@… ▸ clients

  Connected clients  (2)
────────────────────────────────────────────────────────────────────────
  NICK              ADDRESS                 CHANNELS             UP        IDLE    RX    TX
  ────────────────  ──────────────────────  ───────────────────  ────────  ────  ────  ────
  PA3XYZ            192.168.1.10:54321      #INFO #1010 #59**    00:12:34    3s   412   876
  PE1ABC            10.0.0.5:49876          #INFO #EMERGENCY     00:04:11   45s    88   192
```

---

### `bbs`

Summary of all channels that have stored BBS messages.

```
relay@… ▸ bbs

  BBS  —  4 message(s)
────────────────────────────────────────────────────────────────────────
  File          /var/lib/geotalk/bbs.json
  Max/channel   50

  CHANNEL                   MESSAGES
  ────────────────────────  ────────
  #INFO                     1
  #1010                     3
```

### `bbs CHANNEL`

All stored messages for one channel, with timestamps and nicks.

```
relay@… ▸ bbs 1010

  BBS #1010  —  3 message(s)
────────────────────────────────────────────────────────────────────────
  2026-03-09 19:00:12  PA3XYZ          Net tonight at 20:00 UTC
  2026-03-09 19:15:44  PE1ABC          73, see you there
  2026-03-09 19:58:03  PA3XYZ          Starting now — join #1010
```

---

### `bbs-clear CHANNEL`

Delete all stored BBS messages for a channel. Saves to the BBS file
immediately.

```
relay@… ▸ bbs-clear 1010
  ✓  Cleared 3 message(s) from #1010
```

---

### `bbs-post CHANNEL TEXT`

Post a BBS message as `operator`. Text after the channel name is taken
verbatim — spaces are included and no quoting is needed.

```
relay@… ▸ bbs-post INFO Relay maintenance: Saturday 02:00–04:00 UTC
  ✓  Posted to #INFO as operator
```

Works on all channels, including the three system channels. Regular clients
cannot post to system channels — those packets are rejected by the relay.
Operator posts via the CLI are not restricted.

---

### `kick NICK`

Evict a client by callsign. The client's subscriptions are removed
immediately. Matching is case-insensitive. Clients with auto-reconnect will
reconnect shortly afterwards.

```
relay@… ▸ kick PE1ABC
  ✓  Kicked PE1ABC
```

---

### `ban IP`

Block an IP address. All subsequent packets from that address are silently
dropped. The ban is in-memory only and does not survive a daemon restart.

```
relay@… ▸ ban 203.0.113.99
  ✓  Banned 203.0.113.99
```

---

### `unban IP`

Remove a ban.

```
relay@… ▸ unban 203.0.113.99
  ✓  Unbanned 203.0.113.99
```

---

### `bans`

List all currently active bans.

```
relay@… ▸ bans

  Banned IPs  (2)
────────────────────────────────────────────────────────────────────────
  203.0.113.99
  198.51.100.0
```

---

### `log [N]`

Show the last N lines from the daemon's in-memory ring buffer (default 100).
The buffer holds up to 2000 lines and is separate from `--log-file`.

```
relay@… ▸ log 10

  Server log  (last 10 lines)
────────────────────────────────────────────────────────────────────────
  19:00:05  +JOIN PA3XYZ → #INFO
  19:00:06  +JOIN PA3XYZ → #1010
  19:00:12  BBS_POST PA3XYZ → #1010  "Net tonight at 20:00 UTC"
  19:03:14  +JOIN PE1ABC → #INFO
  ...
```

---

### `quiet [on|off]`

Toggle per-packet console output on the daemon. When on, AUDIO and PING
packets are suppressed; JOIN, LEAVE, TEXT, and BBS events are always
logged. With no argument, reports the current state.

```
relay@… ▸ quiet on
  ✓  Quiet mode: on

relay@… ▸ quiet
  ✓  Quiet mode: on
```

Takes effect immediately on the running daemon.

---

### `ping`

Verify the daemon is alive and the control socket is responding.

```
relay@… ▸ ping
  ✓  pong
```

---

### `stop`

Initiate a clean shutdown. The CLI exits automatically after receiving the
acknowledgement.

```
relay@… ▸ stop
  ◈  Relay daemon shutting down…
```

---

### `quit` / `exit`

Close the CLI session. Does **not** stop the daemon.

---

## System Channels

The daemon automatically creates three protected channels on first startup:

| Channel | Auto-joined by clients | Purpose |
|---|---|---|
| `#INFO` | yes | Relay announcements and operator information |
| `#TEST` | no | Connection and audio testing — join manually |
| `#EMERGENCY` | yes | Urgent coordination |

Each gets a default seed BBS message the very first time it is created. On
subsequent restarts, if the BBS file already has content for a system channel
the seed is skipped — **operator edits are never overwritten by a restart**.

### Editing system channel BBS content

The workflow is `bbs-clear` followed by `bbs-post`:

```bash
# One-shot
python3 geotalk-relay-cli.py bbs-clear INFO
python3 geotalk-relay-cli.py bbs-post INFO PI4VNL relay — QRV 144.825 MHz — ops@pi4vnl.nl

# Or interactively
relay@… ▸ bbs-clear INFO
relay@… ▸ bbs-post INFO PI4VNL relay — QRV 144.825 MHz — ops@pi4vnl.nl
```

The replacement message is written to the BBS file immediately. On the next
daemon restart it is loaded from the file and the seed does not fire.

### Restoring the factory seed

To go back to the default seed text, clear the channel and restart without
adding a replacement first:

```bash
python3 geotalk-relay-cli.py bbs-clear INFO
# restart daemon — #INFO is now empty in the file, so the seed fires
```

---

## Deployment

### systemd service

Create `/etc/systemd/system/geotalk-relayd.service`:

```ini
[Unit]
Description=GeoTalk Relay Daemon
After=network.target

[Service]
Type=simple
User=geotalk
ExecStart=/usr/bin/python3 /usr/local/bin/geotalk-relayd.py \
    --host 0.0.0.0 \
    --port 5073 \
    --log-file /var/log/geotalk-relayd.log \
    --bbs-file /var/lib/geotalk/bbs.json \
    --ctl-socket /run/geotalk/relay.sock \
    --pid-file   /run/geotalk/relay.pid \
    --quiet
RuntimeDirectory=geotalk
StateDirectory=geotalk
LogsDirectory=geotalk
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`RuntimeDirectory=geotalk` tells systemd to create `/run/geotalk/` owned by
the service user before the process starts, so the socket and PID paths above
work without any manual `mkdir`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now geotalk-relayd
sudo systemctl status geotalk-relayd
```

Connect the CLI once the service is running:

```bash
python3 geotalk-relay-cli.py --socket /run/geotalk/relay.sock
```

### Firewall

Only the UDP port needs to be open to the internet. The control socket is a
local Unix socket and is never reachable from the network.

```bash
# ufw
sudo ufw allow 5073/udp

# iptables
sudo iptables -A INPUT -p udp --dport 5073 -j ACCEPT
```

### Log rotation

The daemon appends to `--log-file` for the lifetime of the process.
`logrotate` with `copytruncate` works without any signal:

```
/var/log/geotalk-relayd.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
```

---

## Scripting

The `--json` flag makes every command print raw JSON:

```bash
# Print current client count
python3 geotalk-relay-cli.py --json stats \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['clients'])"

# Check whether a specific nick is online
python3 geotalk-relay-cli.py --json clients \
  | python3 -c "
import sys, json
resp = json.load(sys.stdin)
if any(c['nick'] == 'PA3XYZ' for c in resp['data']):
    print('PA3XYZ is online')
"
```

All responses use the same envelope:

```jsonc
// Success
{"ok": true,  "cmd": "stats", "data": { ... }}

// Error
{"ok": false, "error": "Usage: kick NICK"}
```

Commands that confirm an action use specific fields rather than `"data"`:

```jsonc
{"ok": true, "cmd": "kick",      "nick": "PA3XYZ"}
{"ok": true, "cmd": "ban",       "ip": "203.0.113.99"}
{"ok": true, "cmd": "bbs-clear", "cleared": 3, "channel": "1010"}
{"ok": true, "cmd": "bbs-post",  "record": {"id": 7, "n": "operator", "p": "INFO", "t": "...", "ts": 1741536000}}
{"ok": true, "cmd": "quiet",     "quiet": true}
```

---

## Comparison with `geotalk-relay.py`

`geotalk-relayd` is a drop-in replacement. The UDP wire protocol and all
client-facing behaviour are identical.

| | `geotalk-relay.py` | `geotalk-relayd.py` |
|---|---|---|
| Control interface | stdin console — must stay attached | Unix socket — detached, any time |
| Multiple operators | no | yes — any number of simultaneous connections |
| Background operation | manual `nohup` / `screen` | `--daemonize` |
| PID file | no | yes |
| Signal handling | SIGINT only | SIGTERM + SIGINT |
| In-memory log buffer | no | yes — last 2000 lines via `log [N]` |
| UDP wire protocol | v2.1.0 | v2.1.0 — identical |
| Client compatibility | ✅ | ✅ unchanged |

---

## Troubleshooting

**`Control socket not found: … Is geotalk-relayd running?`**  
The daemon is not running, or was started with a different `--ctl-socket`
path. Check `ps aux | grep geotalk-relayd`. If you used a custom socket
path, pass it to the CLI with `--socket PATH`.

**`Cannot connect to …: Connection refused`**  
The socket file exists but the daemon is no longer running (stale socket
left after a crash). Remove it and restart:

```bash
rm /tmp/geotalk-relayd.sock
python3 geotalk-relayd.py
```

**`Error: --log-file is required when using --daemonize`**  
Add `--log-file /path/to/relay.log` to the daemon command.

**`No response from daemon (timeout)`**  
The daemon is running but the control socket is not responding — possibly
under extreme load. Check the log file. Send SIGTERM to restart cleanly:

```bash
kill "$(cat /tmp/geotalk-relayd.pid)"
```

**Bans are lost after a restart**  
Bans are held in memory only. For persistent blocks use firewall rules:

```bash
sudo ufw deny from 203.0.113.99
```

---

## Author

René Oudeweg / Claude

---

## License

MIT — free for personal, amateur radio, and community use.
