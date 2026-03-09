# GeoTalk 📡
**Pseudo-HAM Radio & Text Messaging — Geo-grouped by Postal Code**

GeoTalk is a Linux CLI tool that turns any postal code into a radio channel.
Users in the same postal zone share a UDP group; voice (PTT) and text
messages are broadcast to everyone on that channel — like a local
walkie-talkie net. Works on a LAN via IP multicast, or across the internet
via a relay server.

**Version 1.9.0**

---

## Features

| | Feature | Details |
|---|---|---|
| 📮 | Exact postal channels | Any EU/UK postal code is a channel — `#59601`, `#1234AB`, `#SW1A` |
| 🔍 | Wildcard channels | `#59**` = Venlo region · `#750**` = Paris · `#1***??` = Amsterdam |
| 🧩 | Regex channels | `/^[0-9]{4}[A-Z]{2}$/` — full Python regex between `//` |
| 🗺️ | Region database | 120+ EU postal prefixes resolved to human-readable region names |
| 💬 | Text messaging | Instant broadcast to all channel members |
| 🎙️ | Push-to-Talk (PTT) | Real mic audio over UDP — **Opus codec** (32 kbit/s) when `opuslib` installed, raw PCM fallback |
| 📻 | Multi-channel RX | Subscribe to many channels at once; all arrive in one terminal |
| 🔎 | Channel scan | `/scan 59**` — probe a region for live users, results stream in real time |
| 🌐 | Relay mode | `--relay HOST` for internet use — no multicast routing needed |
| 🔄 | Auto-reconnect | Client reconnects to relay with exponential back-off |
| 🔒 | LAN mode | Pure peer-to-peer UDP multicast, zero infrastructure required |
| 🔊 | Audio mixing | Simultaneous speakers are mixed in real time — no garbled interleaving |
| 🏙️ | Postal reverse lookup | `/postal Venlo` finds all matching channel patterns by city name |
| 📍 | Auto-channel | `--auto-channel` detects your location from public IP and joins the nearest channel automatically |
| 📋 | BBS | `/bbs TEXT` posts a persistent message to the channel bulletin board; messages are auto-delivered on join (relay mode only) |
| 🌍 | Country context | `/country NL` filters all region labels to one country — no cross-country noise on shared postal prefixes |
| 🖥️ | Desktop GUI | `geotalk-gui.py` — tkinter interface with channel sidebar, PTT button, message log, REPL, status bar, and saved settings |
| 📡 | Active channel list | `/active` queries the relay for all channels that currently have subscribers — shows nicks, user count, and region |
| 🔗 | Join-active startup | `--join-active` queries the relay on startup and immediately joins every channel that has at least one user |

---

## Files

| File | Role |
|---|---|
| `geotalk.py` | Client — text, PTT, channel management, scan |
| `geotalk-relay.py` | Relay server — internet bridge, runs on a VPS |
| `geotalk-gui.py` | Desktop GUI — tkinter frontend for the GeoTalk client |

---

## Installation

```bash
# Copy to PATH
cp geotalk.py geotalk-relay.py /usr/local/bin/
chmod +x /usr/local/bin/geotalk.py /usr/local/bin/geotalk-relay.py

# Python stdlib only — no mandatory dependencies.
# For voice / PTT, install pyaudio:
pip3 install pyaudio --break-system-packages

# On Debian / Ubuntu you may also need:
sudo apt install portaudio19-dev python3-pyaudio

# GUI — requires only tkinter (included in standard Python on most distros).
# On Debian / Ubuntu if tkinter is missing:
sudo apt install python3-tk

# For Opus codec (recommended — ~24x bandwidth reduction over raw PCM):
pip3 install opuslib --break-system-packages
# Opus is optional — GeoTalk falls back to raw PCM if not installed.
# Both modes interoperate: Opus and PCM clients can share the same channel.
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
python3 geotalk-relay.py --port 5073

# Step 2 — clients point at the relay
python3 geotalk.py --nick PA3XYZ --relay relay.example.com
python3 geotalk.py --nick PA3XYZ --relay 1.2.3.4 --relay-port 5073 --join 59** 750**
```

### Auto-channel — let GeoTalk find your location

```bash
# Detects your public IP, resolves it to a postal region, and joins automatically
python3 geotalk.py --nick PA3XYZ --auto-channel
python3 geotalk.py --nick PA3XYZ --relay relay.example.com --auto-channel
```

On startup you'll see:
```
  Location detected: 5944 (Tegelen, NL)  →  auto-joining #59**
  Joined #59**  → mcast=239.73.x.x:5156 + 128 sub-groups
```

Uses ip-api.com (primary) and ipinfo.io (fallback) — both free, no API key required.
If detection fails (VPN, offline) a message is shown and you can join manually with `#POSTCODE`.

### Join-active — join every live channel on the relay

```bash
# Query the relay on startup and join all channels that currently have subscribers
python3 geotalk.py --nick PA3XYZ --relay relay.example.com --join-active
```

On startup you'll see:
```
  Found 3 active channels  (#59**, #5911AB, #1***??)
  Joined #59**    → relay=relay.example.com:5073  Venlo regio
  Joined #5911AB  → relay=relay.example.com:5073  Venlo (NL)
  Joined #1***??  → relay=relay.example.com:5073  Amsterdam
```

GeoTalk sends a single `ACTIVE_REQ` to the relay, waits up to 5 seconds for the response, then joins each returned channel in one shot. If the relay has no active channels, or does not respond within the timeout, a prompt to join manually is shown instead. `--join-active` requires `--relay` and is silently ignored in LAN multicast mode.

---

## Desktop GUI

`geotalk-gui.py` is a tkinter frontend that wraps the full GeoTalk client in a
graphical interface. It imports `geotalk.py` directly — no subprocess, same
process — so all features are available: PTT, relay, scan, BBS, country context.

### Starting the GUI

```bash
# Must be in the same directory as geotalk.py
python3 geotalk-gui.py

# Or with PYTHONPATH if geotalk.py is elsewhere
PYTHONPATH=/path/to/geotalk python3 geotalk-gui.py
```

On first launch a connect dialog appears. Fill in your details and click **CONNECT**.
All settings are saved to `~/.config/geotalk/prefs.json` and pre-filled on the next launch.

### Connect dialog fields

| Field | Description |
|---|---|
| Callsign / nick | Your display name on the air |
| Relay host | Hostname or IP of relay server — leave empty for LAN multicast |
| Relay port | Default `5073` |
| Join on start | Space-separated channels to join immediately, e.g. `59** 1***??` |
| Interface IP | Local interface for multicast (leave empty for auto) |
| Country | Region label filter — default `NL` |
| Auto-channel | Detect location from public IP and join nearest channel automatically |

### Layout

```
┌─ ◈ GEOTALK  PA3XYZ · NL · LAN multicast ─────────────── v1.9.0 ─┐
├──────────────┬──────────────────────────────────────────────────────┤
│ CHANNELS     │  10:31 [CHARLIE] (NL · Venlo) #59**: hello there   │
│              │  10:32 [VOICE] BOB (NL · Tegelen) #5944 seq=14     │
│ ► #59**  (2) │  10:33 ▶ PTT ON                                    │
│   #5911AB    ├──────────────────────────────────────────────────────┤
│              │ ➤  /scan 59**_                                      │
│ [join entry] │                                                     │
├──────────────┴──────────────────────────────────────────────────────┤
│  ● PTT   ◉ MUTE │  CH: #59**   users: CHARLIE, BOB   msgs: 7      │
│                  │  country: NL   2 channels              LAN MCAST │
└──────────────────────────────────────────────────────────────────────┘
```

**Channels sidebar** — lists all joined channels with active user count. Click any
channel to switch the active TX channel. Type a pattern in the join box and press
Enter to join. The active channel is marked with `►`.

**Messages area** — inbound text messages, voice notifications, system output, scan
results, and BBS messages are colour-coded. Text messages are parsed into
timestamped `[NICK] (region) #channel: body` format.

**REPL input** — full access to all `/command` syntax. Command history with `↑` / `↓`.

**PTT button** — large push-and-hold button. Hold to transmit, release to stop.

**Status bar** — shows active channel, online users, message count, PTT/mute state,
country, channel count, and transport mode (LAN MCAST or RELAY).

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` (outside REPL) | PTT push-to-talk (hold) |
| `Ctrl+T` | Toggle PTT on/off |
| `Ctrl+M` | Toggle audio mute |
| `↑` / `↓` in REPL | Navigate command history |
| `Escape` | Focus the REPL input |

### Saved settings

All connect dialog fields are persisted to `~/.config/geotalk/prefs.json`.
Window size and position are also saved and restored on next launch.
Settings are written only on successful connect — cancelling the dialog
does not overwrite previously saved settings.

---

## Testing on One Machine

Run the relay and two clients on the same machine using loopback:

```bash
# Terminal 1 — relay
python3 geotalk-relay.py --port 5073

# Terminal 2 — ALICE
python3 geotalk.py --nick ALICE --relay 127.0.0.1 --relay-port 5073

# Terminal 3 — BOB
python3 geotalk.py --nick BOB --relay 127.0.0.1 --relay-port 5073
```

Then join the same channel in both clients (`#5944AV`) and send messages.
The relay terminal logs every JOIN, TEXT, and SCAN event.

For multicast testing without a relay, run two instances on the same machine
and pin multicast to your interface IP to avoid port bind conflicts:

```bash
python3 geotalk.py --nick ALICE --local-if 192.168.178.164 --debug
python3 geotalk.py --nick BOB   --local-if 192.168.178.164 --debug
```

---

## Client CLI Options

```
--nick NICK           Callsign / display name
--host HOST           Bind host for multicast (default 0.0.0.0)
--port PORT           Base UDP port (default 5073)
--local-if IP         Local interface IP for multicast (default 0.0.0.0 = auto)
                      Set to your Wi-Fi IP when the OS picks the wrong interface
                      e.g. --local-if 192.168.178.164
--relay HOST          Relay server hostname or IP  ← enables relay mode
--relay-port PORT     Relay server port (default 5073)
--join PATTERN ...    Channels to join on startup
--join-active         Query relay for active channels and join them all on startup
                      (relay mode only; ignored without --relay)
--auto-channel        Detect location from public IP and auto-join nearest channel
--debug               Print verbose multicast debug lines to stderr
```

---

## Channel Syntax

All three modes use the same `#` prefix:

### Exact postal code
```
#5911AB     NL — Venlo Centrum
#59601      DE — Mülheim/Ruhr
#75001      FR — Paris 1er
#SW1A       UK — London Westminster
```

### Wildcard (glob)
```
#59**       all NL codes 59xx    → Venlo regio
#591???     all NL codes 591xxx  → Venlo Centrum
#1***??     all NL codes 1xxx??  → Amsterdam
#750**      all FR codes 750xx   → Paris
#10***      all DE codes 10xxx   → Berlin
#SW*        all UK codes SW-     → London SW
```

`*` = exactly one character · `**` = one or more characters

When you join a wildcard channel (e.g. `#591*`), GeoTalk automatically subscribes
to all enumerated concrete groups within that pattern — so messages and audio from
peers on exact channels like `#5912` or `#5913` are received without any extra steps.
In relay mode, the client sends a `JOIN` for each concrete sub-channel; in multicast
mode it joins each corresponding multicast group.

### Regex
```
#/^59[0-9]{3}$/        all 5-digit codes starting with 59
#/^[0-9]{4}[A-Z]{2}$/  any valid Dutch postcode
#/^75[0-9]{3}$/        all Paris codes
```

Full Python regex syntax enclosed in `/` `/`.

### Free-form channel names

The `#` prefix accepts any string — the channel key does not have to be a postal code. This works in both relay and LAN multicast mode:

```
#LIMBURG        regional channel for the province of Limburg
#AMSTERDAM      city-wide channel
#REPEATER-PI4ZL link a repeater group to a GeoTalk channel
#EMERGENCY      ad-hoc coordination channel
#TEST           local testing
```

Any alphanumeric name (and most punctuation) is valid. The multicast address and port are derived from the raw string using the same hash as postal codes, so two peers typing `#LIMBURG` end up on exactly the same multicast group without any extra configuration. In relay mode the key is sent verbatim in `JOIN` packets.

The only difference from a postal code channel is that region lookup and wildcard expansion have no database entries to match against, so `/lookup` and `/scan` will not find sub-regions — they work purely as fixed exact-match channels.

### Quick input
```
#59**           Join / switch to a channel (exact, wildcard, or regex)
<text>          Send text to the active channel
```

### Channel discovery
```
/scan 59**      Probe Venlo region for active users (5 s timeout)
/scan 59** 10   Same with 10 s timeout
/scan 5911AB    Probe a single exact channel
/scan 750**     Probe Paris region
```

Responses stream live as peers reply. A summary table is printed at the end.
Requires all peers to be on v1.3.0 or later (relay requires v1.4.0+; auto-channel requires v1.5.0+; Opus requires v1.6.0+; BBS requires relay v1.7.1+; country context requires v1.8.1+; `/active` requires relay v1.9.0+).

### Channel management
```
/join 59**          Join a channel in the background
/leave              Leave the active channel (auto-switches to previous)
/leave 59**         Leave a specific channel
/sw 59**            Switch active TX channel
/ch                 List all joined channels (region, multicast/relay, stats)
/msg 59** hello!    Send text to a specific channel
```

When leaving the active channel, GeoTalk automatically switches to the most recently visited channel that is still joined. The terminal shows the new active channel and its region. If no other channels are joined, a prompt to join one is shown instead.

### Region lookup
```
/lookup 59**        Show all DB sub-regions covered by a pattern
/lookup 5911AB      Show region for an exact postcode
```

### Postal reverse lookup
```
/postal Venlo       Find all channel patterns covering Venlo
/postal Amsterdam   Find channels for Amsterdam
/postal Paris       Find channels for Paris
/postal Berlin      Find channels for Berlin
```

Results are grouped by country, show the DB pattern and a ready-to-use glob, and end with a tip for joining or scanning. Works for any city or region name in the 120+ entry database.

### Country context

Many postal code prefixes are shared across countries — `#10***` is Berlin in Germany *and* Brussels in Belgium, `#59**` covers both Venlo (NL) and Lille (FR). The country context setting controls which country's region labels are shown in all output.

```
/country            Show current country setting and all supported codes
/country NL         Filter region labels to Netherlands (default)
/country DE         Filter region labels to Germany
/country FR         Filter region labels to France
```

When a match exists for the active country it is shown normally. When no match exists for that country (e.g. looking up a Dutch postcode while country is set to DE), GeoTalk falls back to the best match from any country so you never see a blank region.

`--auto-channel` sets the country automatically from the detected IP geolocation.

Supported country codes: `NL` `DE` `FR` `BE` `LU` `GB` `ES` `IT` `PT` `CH` `AT` `PL` `CZ` `DK` `SE` `NO` `FI`

### Voice / PTT
```
/ptt on     Open mic → stream to active channel
/ptt off    Release PTT
Ctrl+T      Toggle PTT on/off (works mid-line without submitting)
/mute       Toggle incoming audio mute (also: /m)
Ctrl+Y      Toggle audio mute on/off
```

While PTT is active, incoming audio from others is discarded (no playback,
no REPL output) — mirrors real radio behaviour. While muted, voice packet
lines are suppressed in the REPL but text and ping messages still appear.

When multiple peers transmit simultaneously, their audio streams are mixed
in real time before playback — each sender has an independent ring buffer,
and a mixer thread combines them sample-by-sample with saturation clipping
every 20 ms (one Opus frame at 48 kHz / 960 samples). You hear a clean
blend rather than interleaved fragments.

With `opuslib` installed, transmitted audio is Opus-encoded at 32 kbit/s
(~80 bytes per 20 ms frame) before sending and decoded on receipt — roughly
a 24× reduction vs raw PCM. Without `opuslib`, raw int16 PCM is used
automatically; the `codec` field in each packet's JSON header lets Opus
and PCM clients coexist on the same channel.

### BBS — Bulletin Board (relay mode only)

```
/bbs Hello world    Post a message to the active channel's BBS on the relay
/bbs                Fetch and display all stored BBS messages for the channel
```

BBS messages are stored persistently on the relay server per channel. When you join a channel in relay mode, stored messages are delivered automatically and displayed as a framed table:

```
────────────────────────────────────────────────────────────
  📋 BBS #5912  Venlo Centrum — 3 stored messages
  2026-03-08 14:22  [PA3XYZ]  Relay test from Tegelen
  2026-03-08 15:01  [DL2ABC]  Anyone active tonight?
  2026-03-08 15:44  [F4BCD]   Good signal here in Paris
────────────────────────────────────────────────────────────
```

BBS is not available in LAN multicast mode — use `--relay HOST` to enable it.

### Info & status
```
/users      Active users on current channel (last 5 min)
/active     Ask relay which channels currently have subscribers (relay mode only)
/info       Transport mode, audio settings, codec, all joined channels
/relay      Relay connection status (relay mode only)
/whoami     Your callsign
/help       Full in-app help
/quit  /q   Exit
```

`/active` sends a single `ACTIVE_REQ` packet to the relay, which responds with a snapshot of every channel that currently has at least one subscriber. Results are displayed with user counts, nicks, and region labels; channels you've already joined are marked `[joined]` and sorted to the top:

```
────────────────────────────────────────────────────────────
  📡 Active channels on relay  (as of 14:32:07 — 3 channels)
  #59**         [joined]  2 users  PA3XYZ, PE1ABC   Venlo regio
  #5911AB                 1 user   ON4XYZ            Venlo (NL)
  #1***??                 3 users  PD0XYZ, PA0XYZ, PI4ZZZ  Amsterdam
────────────────────────────────────────────────────────────
```

`/active` is relay-mode only — in LAN multicast mode use `/scan **` to discover peers instead.

---

## Channel Scan — How It Works

`/scan PATTERN` runs a three-phase probe:

1. **Expand** — the pattern is matched against the 120+ entry region DB to
   build a list of concrete channel keys to probe (up to 128), plus the
   wildcard key itself.
2. **Probe** — `SCAN_REQ` (0x06) packets are multicast (or sent via relay)
   to each candidate channel in batches of 16.
3. **Collect** — any GeoTalk node that has joined a matching channel
   automatically replies with a `SCAN_RSP` (0x07) containing its nick,
   the full list of recently-seen users on that channel, and its message
   count. The relay routes `SCAN_RSP` packets back only to the original
   requester (not broadcast).

Each scan session carries a random `scan_id` token so replies from
concurrent scans never mix. The timeout is configurable (1–60 s).

---

## Region Database

120+ EU postal prefixes ship built-in. Region names appear on every
received message and in `/ch`, `/lookup`, and `/scan` output.

Coverage: **NL · DE · FR · BE · LU · GB · ES · PT · IT · CH · AT · PL · CZ · DK · SE · NO · FI**

Example — NL Venlo sub-zones:

| Pattern | Region |
|---|---|
| `#590???` | Venlo Noord |
| `#591???` | Venlo Centrum |
| `#592???` | Venlo Zuid |
| `#593???` | Venlo / Blerick |
| `#594???` | Tegelen |
| `#595???` | Arcen / Bergen (L) |
| `#596???` | Venlo / Horst |
| `#597???` | Horst aan de Maas |
| `#598???` | Venray |
| `#59**`   | Venlo regio (catch-all) |

---

## Relay Server

### Starting the relay

```bash
# Minimal
python3 geotalk-relay.py

# Full options
python3 geotalk-relay.py \
  --host 0.0.0.0 \
  --port 5073 \
  --ttl 600 \
  --max-per-channel 64 \
  --bbs-file /var/lib/geotalk/bbs.json \
  --bbs-max 100 \
  --log-file /var/log/geotalk-relay.log \
  --quiet
```

### Relay CLI options

| Option | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `5073` | UDP port |
| `--ttl` | `300` | Seconds before idle client is evicted |
| `--max-per-channel` | `128` | Max clients per channel (anti-flood) |
| `--log-file` | _(none)_ | Append structured log lines to a file |
| `--quiet` | off | Suppress per-packet console output |
| `--bbs-file` | `geotalk-bbs.json` | JSON file for BBS persistence (use `''` to disable) |
| `--bbs-max` | `50` | Max BBS messages stored per channel |

### Relay console commands

Type these while the relay is running:

| Command | Action |
|---|---|
| `stats` | Summary: uptime, client count, channel count, RX/TX bytes |
| `channels` | Per-channel listing with subscriber nicks |
| `clients` | Per-client table: nick, IP, channels, uptime, idle, packet counts |
| `bbs` | List all BBS channels and message counts |
| `bbs CHANNEL` | Show all stored messages for a specific channel |
| `bbs-clear CHANNEL` | Delete all BBS messages for a channel |
| `kick NICK` | Evict a client by callsign |
| `ban IP` | Block an IP address (until restart or `unban`) |
| `unban IP` | Remove an IP ban |
| `bans` | List all banned IPs |
| `quit` | Graceful shutdown |

### Relay packet routing

| Packet | Relay action |
|---|---|
| `JOIN` (0x10) | Register sender as subscriber of the given channel |
| `LEAVE` (0x11) | Remove sender from all channel subscriptions |
| `TEXT` (0x01) | Fan-out to all channel subscribers (except sender); refreshes TTL |
| `AUDIO` (0x02) | Fan-out to all channel subscribers; refreshes TTL |
| `PING` (0x04) | Fan-out to all channel subscribers; refreshes TTL |
| `ACK` (0x03) | Client-to-client only — dropped by relay |
| `SCAN_REQ` (0x06) | Fan-out to channel + record `scan_id → requester addr` |
| `SCAN_RSP` (0x07) | **Unicast back to original requester only** |
| `BBS_POST` (0x12) | Store message in channel BBS; unicast confirmation to sender |
| `BBS_REQ` (0x13) | Fetch stored BBS messages; unicast response to requester |
| `BBS_RSP` (0x14) | **Unicast to requester only** — delivers stored message array |
| `ACTIVE_REQ` (0x15) | **Unicast response** — relay returns snapshot of all channels with active subscribers |
| `ACTIVE_RSP` (0x16) | **Unicast to requester only** — delivers `{channel: [nicks]}` map |

Stale subscriptions (idle > TTL) are pruned every 30 seconds in the background.
Scan sessions expire after 60 seconds.

---

## Protocol Reference

### Packet format

```
┌─────────┬────────┬─────────────┬──────────────────────┐
│ Magic   │ Type   │ Payload len │ JSON payload         │
│ "GT" 2B │ 1B     │ uint16 BE   │ variable             │
└─────────┴────────┴─────────────┴──────────────────────┘
AUDIO packets append the audio payload after the JSON header.
The JSON "codec" field specifies encoding: "opus" (compressed) or "pcm" (raw int16 LE).
```

### Packet types

| Type | Hex | JSON fields | Description |
|---|---|---|---|
| TEXT | 0x01 | `n` nick · `p` channel · `t` text · `id` · `ts` | Text message |
| AUDIO | 0x02 | `n` · `p` · `s` seq · `codec` ("opus"/"pcm") · _+ audio bytes_ | Voice chunk |
| ACK | 0x03 | `n` · `p` · `ts` | Client-to-client acknowledgement (not relayed) |
| PING | 0x04 | `n` · `p` · `ts` | Presence heartbeat (60 s interval) |
| SCAN_REQ | 0x06 | `n` · `p` channel · `sid` scan-id · `ts` | Scan probe |
| SCAN_RSP | 0x07 | `n` · `p` · `sid` · `u` user-list · `mc` msg-count · `ts` | Scan reply |
| JOIN | 0x10 | `n` · `p` · `ts` | Relay: subscribe |
| LEAVE | 0x11 | `n` · `p` · `ts` | Relay: unsubscribe |
| BBS_POST | 0x12 | `n` nick · `p` channel · `t` text · `ts` | Store BBS message |
| BBS_REQ | 0x13 | `n` · `p` · `ts` | Request BBS messages for channel |
| BBS_RSP | 0x14 | `p` · `msgs` [{id, n, p, t, ts}, …] | BBS message delivery |
| ACTIVE_REQ | 0x15 | `n` · `ts` | Request active channel list from relay |
| ACTIVE_RSP | 0x16 | `channels` {key: [nick, …]} · `ts` | Active channel snapshot |

### Multicast address mapping (LAN mode)

```
exact code  →  MD5(first 4 chars)  →  239.73.<b1>.<b2>
wildcard    →  MD5(full pattern)   →  239.73.<b1>.<b2>
port        →  5073 + MD5(key) % 253        (5074–5326)
```

All addresses fall in `239.73.0.0/16` — RFC 2365 organisation-local scope.

---

## Network Requirements

| Mode | Requirement |
|---|---|
| LAN | Any multicast-capable switch / AP (all consumer gear qualifies) |
| Wi-Fi | Use `--local-if <IP>` to pin to wireless interface; relay mode recommended |
| VPN mesh | WireGuard or OpenVPN `--dev tun` — multicast is forwarded |
| Internet | Run `geotalk-relay.py` on a VPS; clients use `--relay` |

Firewall — relay host:
```bash
sudo ufw allow 5073/udp
```

LAN multicast deployments:
```bash
sudo ufw allow 5073:5326/udp
```

---

## Postal Code Formats

| Country | Format | Exact | Wildcard |
|---|---|---|---|
| Netherlands | `1234AB` | `#5911AB` | `#59**` |
| Germany | `59601` | `#59601` | `#596**` |
| France | `75001` | `#75001` | `#750**` |
| Belgium | `1000` | `#1000` | `#10**` |
| UK | `SW1A` | `#SW1A` | `#SW*` |
| Spain | `28001` | `#28001` | `#280**` |
| Italy | `00100` | `#00100` | `#001**` |
| Poland | `00-001` | `#00-001` | `#00-**` |
| Switzerland | `1010` | `#1010` | `#10**` |
| Austria | `1010` | `#1010` | `#1***` |
| Denmark | `2100` | `#2100` | `#2***` |
| Sweden | `11321` | `#11321` | `#1**` |
| Norway | `0150` | `#0150` | `#01**` |

---

## Extending GeoTalk

| Idea | Notes |
|---|---|
| ~~OPUS codec~~ | ✅ Built-in since v1.6.0 — `pip install opuslib` to enable |
| AES-256 encryption | Per-channel key derivable from postal code + shared passphrase |
| Web UI | Flask + WebSocket bridge to the relay's UDP socket |
| D-STAR / DMR gateway | Link to licensed repeaters via `dvswitch` or `MMDVM` |
| GPS auto-channel | Resolve coordinates → postal code → auto-join nearest channel |
| TNC / APRS bridge | Encode messages as AX.25 UI frames for RF transmission |
| Relay clustering | Multiple relay nodes sharing a channel registry over a message bus |
| ~~BBS~~ | ✅ Built-in since v1.7.1 — persistent per-channel bulletin board on the relay |
| ~~Country context~~ | ✅ Built-in since v1.8.1 — `/country CODE` filters region labels; auto-set by `--auto-channel` |
| ~~Desktop GUI~~ | ✅ Built-in since v1.8.2 — `geotalk-gui.py` tkinter frontend with PTT, channel sidebar, saved settings |
| ~~Active channel list~~ | ✅ Built-in since v1.9.0 — `/active` queries relay for all live channels with subscriber counts and nicks |
| ~~Join-active startup~~ | ✅ Built-in since v1.9.0 — `--join-active` joins every live relay channel automatically on startup |

---

## Author

René Oudeweg / Claude

---

## License
MIT — free for personal, amateur radio, and community use.
Not for emergency services. Not a replacement for licensed radio equipment.
