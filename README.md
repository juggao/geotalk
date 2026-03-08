# GeoTalk 📡
**Pseudo-HAM Radio & Text Messaging — Geo-grouped by Postal Code**

GeoTalk is a Linux CLI tool that turns any postal code into a radio channel.
Users in the same postal zone share a UDP group; voice (PTT) and text
messages are broadcast to everyone on that channel — like a local
walkie-talkie net. Works on a LAN via IP multicast, or across the internet
via a relay server.

**Version 1.6.0**

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

---

## Files

| File | Role |
|---|---|
| `geotalk.py` | Client — text, PTT, channel management, scan |
| `geotalk-relay.py` | Relay server — internet bridge, runs on a VPS |

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

---

## Client Commands

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
Requires all peers to be on v1.3.0 or later (relay requires v1.4.0+; auto-channel requires v1.5.0+; Opus requires v1.6.0+).

### Channel management
```
/join 59**          Join a channel in the background
/leave 59**         Leave a channel
/sw 59**            Switch active TX channel
/ch                 List all joined channels (region, multicast/relay, stats)
/msg 59** hello!    Send text to a specific channel
```

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

### Info & status
```
/users      Active users on current channel (last 5 min)
/info       Transport mode, addresses, all joined channels
/relay      Relay connection status (relay mode only)
/whoami     Your callsign
/help       Full in-app help
/quit  /q   Exit
```

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

### Relay console commands

Type these while the relay is running:

| Command | Action |
|---|---|
| `stats` | Summary: uptime, client count, channel count, RX/TX bytes |
| `channels` | Per-channel listing with subscriber nicks |
| `clients` | Per-client table: nick, IP, channels, uptime, idle, packet counts |
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

---

## Author

René Oudeweg / Claude

---

## License
MIT — free for personal, amateur radio, and community use.
Not for emergency services. Not a replacement for licensed radio equipment.
