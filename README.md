# GeoTalk 📡
**Pseudo-HAM Radio & Text Messaging — Geo-grouped by Postal Code**

GeoTalk is a VoIP/Radio program that turns any postal code into a radio channel.
Users in the same postal zone share a UDP group; voice (PTT) and text
messages are broadcast to everyone on that channel — like a local
walkie-talkie net. Works on a LAN via IP multicast, or across the internet
via a relay server.

**Version 2.7.0** — private invite channels · split message/voice panes · USERS sidebar · LEAVE deduplication fix · clean disconnect on quit

---

## Features

| | Feature | Details |
|---|---|---|
| 📮 | Exact postal channels | Any EU/UK postal code is a channel — `#59601`, `#1234AB`, `#SW1A` |
| 🔍 | Wildcard channels | `#59**` = Venlo region · `#750**` = Paris · `#1***??` = Amsterdam |
| 🧩 | Regex channels | `/^[0-9]{4}[A-Z]{2}$/` — full Python regex between `//` |
| 📻 | Frequency channels | `#FREQ:145500` or `#FREQ:145.500` — join a virtual channel by MHz/kHz frequency; band name shown in sidebar |
| 🗺️ | Region database | 200+ NL postal prefixes (per-district, 10xx–99xx) + DE/FR/BE/GB/ES/IT/PT/CH/AT/DK/SE/NO |
| 💬 | Text messaging | Instant broadcast to all channel members |
| 🎙️ | Push-to-Talk (PTT) | Real mic audio over UDP — **Opus codec** (32 kbit/s) when `opuslib` installed, raw PCM fallback |
| 📡 | Multi-channel RX | Subscribe to many channels at once; all arrive in one terminal |
| 🔎 | Channel scan | `/scan 59**` — probe a region for live users, results stream in real time |
| 🌐 | Relay mode | `--relay HOST` for internet use — no multicast routing needed |
| 🔄 | Auto-reconnect | Client reconnects to relay with exponential back-off |
| 🔒 | LAN mode | Pure peer-to-peer UDP multicast, zero infrastructure required |
| 🏙️ | Postal reverse lookup | `/postal Venlo` finds all matching channel patterns by city name |
| 📍 | Auto-channel | `--auto-channel` detects your location from public IP and joins the nearest channel automatically |
| 📋 | BBS | `/bbs TEXT` posts a persistent message to the channel bulletin board; messages are auto-delivered on join (relay mode only) |
| 🌍 | Country context | `/country NL` filters all region labels to one country — no cross-country noise on shared postal prefixes |
| 🖥️ | Desktop GUI | `geotalk-gui.py` — tkinter interface with channel sidebar, PTT button, split message/voice panes, USERS panel, REPL, VU meter, and saved settings |
| 📡 | Active channel list | `/active` queries the relay for all channels that currently have subscribers — shows nicks, user count, and region |
| 🔗 | Join-active startup | `--join-active` queries the relay on startup and immediately joins every channel that has at least one user |
| 🚨 | System channels | Relay auto-creates `#INFO`, `#TEST`, `#EMERGENCY` with seed BBS messages. `#INFO` and `#EMERGENCY` are joined automatically on relay startup; `#TEST` is not. Client BBS posts to system channels are rejected |
| 🔁 | WAV loop playback | `/play-loop file.wav` transmits a WAV file on repeat until stopped — GUI has a **Loop** checkbox next to the PLAY button |
| ⏺ | Incoming audio recording | GUI **REC** button records all incoming audio to a WAV file in real time — opens a save-file dialog; stop by pressing REC again |
| 📊 | VU meter | 8-segment LED-style bar in the GUI header shows live audio level — green/yellow/red; reflects RX audio on active channel and TX mic during PTT |
| 🩺 | Active keep-alive probing | Relay actively probes silent clients with a ping after 90 s of inactivity; drops them within 30 s if unanswered — no more zombie subscriptions |
| 🔐 | Private invite channels | Click a nick in the USERS sidebar to open a temporary private channel — auto-joined by recipient, auto-closed when both leave |
| 👥 | USERS sidebar | GUI shows all active nicks across joined channels; transmitting nicks highlighted green |
| 🪟 | Split message panes | GUI message area split into MESSAGES (top) and VOICE ACTIVITY (bottom) — voice events no longer drown out text |
| ✉️ | Right-click copy | Right-click any line in the message panes to copy it to the clipboard |

---

## Files

| File | Role |
|---|---|
| `geotalk.py` | Client — text, PTT, channel management, scan, private channels |
| `geotalk-gui.py` | Desktop GUI — tkinter frontend for the GeoTalk client |
| `geotalk-relayd.py` | Relay daemon — UDP server with Unix-domain control socket, PID file, `--daemonize` |
| `geotalk-relay-cli.py` | Operator CLI — connects to `geotalk-relayd` control socket; one-shot and interactive REPL |
| `geotalk-relay-gui.py` | Operator GUI — tkinter relay management interface |
| `geotalk-radio1.py` | NPO Radio 1 streaming daemon — rebroadcasts a live stream as GeoTalk audio |
| `geotalk-timed.py` | Time announcement daemon — periodically announces the time on a channel |
| `geotalk-relay.py` | Legacy relay server — superseded by `geotalk-relayd.py`, kept for reference |

---

## Installation

```bash
# Python 3.10+ required. No mandatory dependencies — stdlib only for text mode.

# For voice / PTT:
sudo apt install portaudio19-dev python3-pyaudio   # Debian / Ubuntu
pip3 install pyaudio --break-system-packages        # or via pip

# Opus codec (recommended — ~24x bandwidth reduction over raw PCM):
pip3 install opuslib --break-system-packages
# Optional — GeoTalk falls back to raw PCM if not installed.

# GUI — tkinter only (included in standard Python on most distros):
sudo apt install python3-tk    # Debian / Ubuntu if tkinter is missing
```

---

## Quick Start

### LAN / multicast mode (same network, no server needed)

```bash
python3 geotalk.py --nick PA3XYZ
python3 geotalk.py --nick PA3XYZ --join 59** 1***??
```

### Internet / relay mode

```bash
# Step 1 — start the relay on a VPS (one-time setup)
python3 geotalk-relayd.py --port 5073

# Step 2 — clients point at the relay
python3 geotalk.py --nick PA3XYZ --relay relay.example.com
python3 geotalk.py --nick PA3XYZ --relay 1.2.3.4 --relay-port 5073 --join 59** 750**
```

### Auto-channel — let GeoTalk find your location

```bash
python3 geotalk.py --nick PA3XYZ --auto-channel
python3 geotalk.py --nick PA3XYZ --relay relay.example.com --auto-channel
```

On startup you'll see:
```
  Location detected: 5944 (Tegelen, NL)  →  auto-joining #59**
  Joined #59**  → mcast=239.73.x.x:5156 + 128 sub-groups
```

Uses ip-api.com (primary) and ipinfo.io (fallback) — both free, no API key required.

### Join-active — join every live channel on the relay

```bash
python3 geotalk.py --nick PA3XYZ --relay relay.example.com --join-active
```

---

## Desktop GUI

`geotalk-gui.py` is a tkinter frontend that wraps the full GeoTalk client in a
graphical interface. It imports `geotalk.py` directly — no subprocess, same
process — so all features are available.

### Starting the GUI

```bash
# Must be in the same directory as geotalk.py
python3 geotalk-gui.py

# Or with PYTHONPATH if geotalk.py is elsewhere
PYTHONPATH=/path/to/geotalk python3 geotalk-gui.py
```

### Layout

```
+-- GEOTALK  PA3XYZ · NL · relay:5073 ─────────────── [VU] [☾] [v2.7.0] --+
| CHANNELS     | MESSAGES                      | USERS                      |
|              | 10:31 [BOB] #59**: hello      | BOB                        |
| ► #59**  (2) | 10:33 <- RENE left #59**      | RENE                       |
|   #5911AB    +-------------------------------+                            |
|              | VOICE ACTIVITY                |                            |
|              | 10:32 [VOICE] BOB #59**       |                            |
| [join entry] | 10:34 [VOICE] BOB #59**       |                            |
+--------------+-------------------------------------------------------+----+
|  ● PTT  ◉ MUTE  ▶ PLAY  ☐ Loop  ⏺ REC  |  CH: #59**  users: 2          |
+---------------------------------------------------------------------------+
```

**Channels sidebar** — lists all joined channels with active user count. Click to
switch active TX channel. The active channel is marked with `►`.

**MESSAGES pane (top)** — text messages, JOIN/LEAVE events, BBS messages, system
output, scan results.

**VOICE ACTIVITY pane (bottom)** — voice packet notifications and ping events only.
The sash between the two panes is draggable; defaults to 70/30 split.

**USERS sidebar** — all nicks seen across joined channels. Transmitting nicks are
highlighted green. Click a nick to open a private invite channel.

**Right-click copy** — right-click any line in either message pane to copy it.

### Private invite channels

Click any nick in the USERS sidebar:

1. A confirmation dialog asks *"Auto join with [NICK] in a temporary invite channel?"*
2. On YES, GeoTalk derives a channel name from both nicks (e.g. `#ALICE-BOB`), joins it,
   and sends an invite message on every shared channel.
3. The recipient's client auto-detects the invite and switches to the private channel.
4. When either party leaves, the channel is automatically closed on both ends.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` (outside REPL) | PTT push-to-talk (hold) — or stop a playing loop |
| `Ctrl+T` | Toggle PTT on/off |
| `Ctrl+M` | Toggle audio mute |
| `↑` / `↓` in REPL | Navigate command history |
| `Escape` | Focus the REPL input |

---

## Testing on One Machine

```bash
# Terminal 1 — relay
python3 geotalk-relayd.py --port 5073

# Terminal 2 — ALICE
python3 geotalk.py --nick ALICE --relay 127.0.0.1 --relay-port 5073

# Terminal 3 — BOB
python3 geotalk.py --nick BOB --relay 127.0.0.1 --relay-port 5073
```

---

## Client CLI Options

```
--nick NICK           Callsign / display name
--host HOST           Bind host for multicast (default 0.0.0.0)
--port PORT           Base UDP port (default 5073)
--local-if IP         Local interface IP for multicast (default 0.0.0.0 = auto)
--relay HOST          Relay server hostname or IP
--relay-port PORT     Relay server port (default 5073)
--join PATTERN ...    Channels to join on startup
--join-active         Query relay for active channels and join them all on startup
--auto-channel        Detect location from public IP and auto-join nearest channel
--debug               Print verbose multicast debug lines to stderr
```

---

## Channel Syntax

### Exact postal code
```
#5911AB     NL — Venlo Centrum
#59601      DE — Mülheim/Ruhr
#75001      FR — Paris 1er
#SW1A       UK — London Westminster
```

### Wildcard (glob)
```
#59**       all NL codes 5900-5999   → Venlo regio
#1***??     all NL codes 1xxx??      → Amsterdam
#750**      all FR codes 75000-75099 → Paris
```

### Regex
```
#/^59[0-9]{3}$/        all 5-digit codes starting with 59
#/^[0-9]{4}[A-Z]{2}$/  any valid Dutch postcode
```

### Free-form channel names
```
#LIMBURG        regional channel
#EMERGENCY      ad-hoc coordination
#TEST           local testing
```

### Channel commands
```
/join 59**          Join a channel in the background
/leave              Leave the active channel
/leave 59**         Leave a specific channel
/sw 59**            Switch active TX channel
/ch                 List all joined channels
/msg 59** hello!    Send text to a specific channel
```

### Voice / PTT
```
/ptt on     Open mic
/ptt off    Release PTT
Ctrl+T      Toggle PTT on/off
/mute       Toggle incoming audio mute
Ctrl+Y      Toggle audio mute on/off
```

### BBS (relay mode only)
```
/bbs TEXT   Post a message to the active channel bulletin board
/bbs        Fetch and display all stored BBS messages
```

### Info & status
```
/users      Active users on current channel
/active     Ask relay which channels currently have subscribers
/info       Transport mode, audio settings, codec, all joined channels
/relay      Relay connection status
/whoami     Your callsign
/help       Full in-app help
/quit  /q   Exit
```

---

## Relay Server

> See **[README-RELAY.md](README-RELAY.md)** for the full operator guide,
> systemd unit, log rotation, and troubleshooting.

### Starting the relay

```bash
# Foreground (development / testing)
python3 geotalk-relayd.py

# Background daemon
python3 geotalk-relayd.py \
  --daemonize \
  --log-file /var/log/geotalk-relayd.log \
  --bbs-file /var/lib/geotalk/bbs.json \
  --ctl-socket /run/geotalk/relay.sock \
  --quiet
```

### Relay options

| Option | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `5073` | UDP port |
| `--ttl` | `300` | Seconds before idle client is evicted |
| `--max-per-channel` | `128` | Max clients per channel |
| `--bbs-file` | `geotalk-bbs.json` | BBS persistence file |
| `--log-file` | _(stdout)_ | Log file — required for `--daemonize` |
| `--quiet` | off | Suppress per-packet log lines |
| `--ctl-socket` | `$XDG_RUNTIME_DIR/geotalk-relayd.sock` | Unix control socket path |
| `--pid-file` | `$XDG_RUNTIME_DIR/geotalk-relayd.pid` | PID file path |
| `--daemonize` | off | Double-fork to background (Unix only) |

### Operator CLI

```bash
python3 geotalk-relay-cli.py stats
python3 geotalk-relay-cli.py clients
python3 geotalk-relay-cli.py kick PA3XYZ
python3 geotalk-relay-cli.py bbs-post INFO Net tonight 20:00 UTC
python3 geotalk-relay-cli.py stop
python3 geotalk-relay-cli.py          # interactive REPL
```

### System channels

| Channel | Auto-joined | Purpose |
|---|---|---|
| `#INFO` | yes | Relay announcements |
| `#TEST` | no | Connection testing — join manually |
| `#EMERGENCY` | yes | Urgent coordination |

---

## Protocol Reference

### Packet format

```
+----------+--------+-------------+------------------+
| Magic    | Type   | Payload len | JSON payload     |
| "GT" 2B  | 1B     | uint16 BE   | variable         |
+----------+--------+-------------+------------------+
AUDIO packets append raw audio bytes after the JSON header.
```

### Packet types

| Type | Hex | Description |
|---|---|---|
| TEXT | 0x01 | Text message |
| AUDIO | 0x02 | Voice chunk (Opus or raw PCM) |
| PING | 0x04 | Presence heartbeat (60 s interval) |
| SCAN_REQ | 0x06 | Scan probe |
| SCAN_RSP | 0x07 | Scan reply |
| JOIN | 0x10 | Relay: subscribe to channel |
| LEAVE | 0x11 | Relay: unsubscribe from channel |
| BBS_POST | 0x12 | Store BBS message |
| BBS_REQ | 0x13 | Request BBS messages |
| BBS_RSP | 0x14 | BBS message delivery |
| ACTIVE_REQ | 0x15 | Request active channel list |
| ACTIVE_RSP | 0x16 | Active channel snapshot |

---

## Network Requirements

| Mode | Requirement |
|---|---|
| LAN | Any multicast-capable switch / AP |
| Wi-Fi | Use `--local-if <IP>`; relay mode recommended |
| Internet | Run `geotalk-relayd.py` on a VPS; clients use `--relay` |

```bash
# Relay host firewall
sudo ufw allow 5073/udp

# LAN multicast
sudo ufw allow 5073:5326/udp
```

---

## Changelog

| Version | Highlights |
|---|---|
| **2.7.0** | Private invite channels · USERS sidebar · split MESSAGES/VOICE ACTIVITY panes · right-click copy · LEAVE deduplication fix · clean quit |
| 2.5.0 | VU meter · audio engine rewrite · active-channel sync fixes |
| 2.4.0 | Per-channel Opus decoders · single active-channel playback |
| 2.3.0 | Incoming audio recording · active keep-alive probing |
| 2.2.0 | WAV loop playback · GUI Loop checkbox |
| 2.1.0 | WAV file playback · full NL postcode DB · frequency channels |
| 2.0.0 | Desktop GUI · relay operator GUI · frequency channel type |
| 1.9.2 | System channels (`#INFO`, `#TEST`, `#EMERGENCY`) |
| 1.9.0 | `/active` · `--join-active` |
| 1.8.2 | `geotalk-gui.py` tkinter frontend |
| 1.8.1 | Country context (`/country`) |
| 1.7.1 | BBS — per-channel bulletin board |
| 1.6.0 | Opus codec support |
| 1.5.0 | `--auto-channel` IP geolocation |
| 1.4.0 | Relay mode |
| 1.3.0 | Channel scan (`/scan`) |

---

## Author

René Oudeweg / Claude

---

## License
MIT — free for personal, amateur radio, and community use.
Not for emergency services. Not a replacement for licensed radio equipment.
