# GeoTalk — LAN Multicast Setup Guide

This guide covers everything needed to get GeoTalk multicast working between
two or more Linux machines on the same LAN. Follow the checklist in order —
most home LAN failures are caused by missing IGMP rules or a missing multicast
route, not the UDP port rules.

**Test environment used in this guide:**
- Machine A: `192.168.178.164`
- Machine B: `192.168.178.160`
- Network: `192.168.178.0/24`
- GeoTalk multicast range: `239.73.0.0/16`

---

## Quick checklist

| # | Check | Fix section |
|---|---|---|
| 1 | UFW allows multicast subnet | [§1](#1-ufw-rules) |
| 2 | IGMP allowed in iptables | [§2](#2-igmp) |
| 3 | Interface has `MULTICAST` flag | [§3](#3-network-interface) |
| 4 | Multicast route exists for `239.73.0.0/16` | [§4](#4-multicast-route) |
| 5 | Raw multicast works (socat test) | [§5](#5-smoke-test-with-socat) |
| 6 | Packets visible on the wire (tcpdump) | [§6](#6-tcpdump-packet-capture) |
| 7 | Router IGMP snooping not blocking joins | [§7](#7-router--switch-igmp-snooping) |

If you want to skip all of this and just start talking, jump to
[§8 — Relay mode workaround](#8-relay-mode-workaround).

---

## 1. UFW rules

A plain `ufw allow 5073:5326/udp` only covers unicast traffic. You need to
explicitly permit traffic addressed to the multicast subnet `239.73.0.0/16`.

Run on **both machines**:

```bash
# Allow all UDP to the GeoTalk multicast range
sudo ufw allow in to 239.73.0.0/16
sudo ufw allow in proto udp to 239.73.0.0/16

# Verify
sudo ufw status verbose
```

The output should include lines like:

```
239.73.0.0/16              ALLOW IN    Anywhere
239.73.0.0/16              ALLOW IN    Anywhere (v6)
```

---

## 2. IGMP

Multicast group membership is negotiated via **IGMP** (Internet Group
Management Protocol). UFW does not enable IGMP by default, so kernel
netfilter will silently drop IGMP packets, which prevents the OS from
ever joining a multicast group.

### Check whether IGMP is currently blocked

```bash
sudo iptables -L INPUT -v -n | grep -i igmp
```

If the output is empty, IGMP is being handled by the default policy (which
may be DROP).

### Allow IGMP now (takes effect immediately, lost on reboot)

```bash
sudo iptables -I INPUT  -p igmp -j ACCEPT
sudo iptables -I OUTPUT -p igmp -j ACCEPT
```

### Make it permanent via UFW's before.rules

Edit `/etc/ufw/before.rules` on **both machines**. Find the block that
ends with `COMMIT` and add the three lines immediately before it:

```
# --- GeoTalk: allow IGMP for multicast group membership ---
-A ufw-before-input   -p igmp -j ACCEPT
-A ufw-before-output  -p igmp -j ACCEPT
-A ufw-before-forward -p igmp -j ACCEPT

COMMIT
```

Reload UFW:

```bash
sudo ufw reload
```

Verify the rules are loaded:

```bash
sudo iptables -L | grep -i igmp
# Should show three ACCEPT rules
```

---

## 3. Network interface

The network interface must have the `MULTICAST` flag enabled.

### Check

```bash
ip link show
```

Look for your active interface (`eth0`, `enp3s0`, `wlan0`, etc.) and
confirm `MULTICAST` appears in the angle-bracket flags:

```
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ...
```

### Enable if missing

```bash
sudo ip link set eth0 multicast on
# Replace eth0 with your actual interface name
```

### Find your interface name

```bash
ip route get 192.168.178.1 | awk '{print $5; exit}'
```

---

## 4. Multicast route

The kernel needs a route telling it which interface to use for the
`239.73.0.0/16` range. Without this route, outgoing multicast packets are
silently discarded.

### Check

```bash
ip route show | grep 239
```

### Add if missing

```bash
# Replace eth0 with your actual interface name
sudo ip route add 239.73.0.0/16 dev eth0
```

### Make it permanent

Add a line to `/etc/network/interfaces` (Debian/Ubuntu classic):

```
post-up ip route add 239.73.0.0/16 dev eth0
```

Or create a systemd-networkd route file at
`/etc/systemd/network/10-multicast.network`:

```ini
[Match]
Name=eth0

[Route]
Destination=239.73.0.0/16
```

Or for NetworkManager, add to the connection profile:

```bash
nmcli connection modify "Wired connection 1" \
  +ipv4.routes "239.73.0.0/16"
```

---

## 5. Smoke test with socat

Before running GeoTalk, verify that raw UDP multicast actually travels
between the two machines. Install socat if needed:

```bash
sudo apt install socat
```

### On the receiver (run on both machines simultaneously)

```bash
# Replace eth0 with your interface name
socat UDP4-RECVFROM:5074,ip-add-membership=239.73.147.1:0.0.0.0,fork -
```

### On the sender (run on one machine)

```bash
# Replace eth0 with your interface name
echo "hello geotalk" | socat - \
  UDP4-DATAGRAM:239.73.147.1:5074,ip-multicast-if=eth0
```

**Expected result:** the text `hello geotalk` appears in both receiver
terminals (including the sender's own receiver window).

If the receiver on the **same machine** sees it but the **other machine**
does not, the problem is at the network layer — see §7 (router/switch).

If **neither** machine receives it, the problem is local — re-check §2
(IGMP) and §4 (route).

---

## 6. tcpdump packet capture

Use tcpdump to confirm packets are leaving one machine and arriving on the
other, independently of GeoTalk.

### Watch for GeoTalk multicast traffic

```bash
# Run on both machines at the same time
sudo tcpdump -i eth0 -n "net 239.73.0.0/16"

# Or watch the GeoTalk port range specifically
sudo tcpdump -i eth0 -n "udp portrange 5074-5326"

# To also see packet contents (hex + ASCII)
sudo tcpdump -i eth0 -n -X "net 239.73.0.0/16"
```

### What to look for

| Observation | Meaning |
|---|---|
| Packets visible on sender, not receiver | Network layer dropping them — see §7 |
| Packets visible on receiver, GeoTalk silent | UFW or iptables blocking at application level |
| No packets on sender either | Multicast route missing — see §4 |
| Packets say `GT` in hex dump | GeoTalk packets confirmed on wire |

---

## 7. Router / switch IGMP snooping

**IGMP snooping** is a switch/router feature that only forwards multicast
frames to ports where a membership report (IGMP join) was actually received.
If the join is dropped before it reaches the switch — because of the IGMP
firewall issue in §2 — the switch never learns about it and silently drops
all subsequent multicast frames for that group.

### Fritz!Box (common in NL/DE/BE)

1. Open `http://fritz.box` in a browser
2. Go to **Home Network → Network → IPv4 Addresses**
3. Find **IGMP Snooping** and temporarily **disable** it for testing
4. If GeoTalk starts working, the root cause is that IGMP joins were not
   reaching the router — fix §2 and re-enable snooping

### TP-Link / Asus / Netgear

Look under **Advanced → Multicast** or **LAN → IGMP Snooping** and
disable it for testing.

### Managed switches

On a managed switch, check that the VLAN carrying `192.168.178.0/24` has
IGMP snooping either disabled or has a querier configured. Without an IGMP
querier on the segment, memberships time out and multicast stops flowing
after ~3 minutes.

---

## 8. Relay mode workaround

If you want to start communicating immediately while debugging multicast,
use GeoTalk's built-in relay. This uses plain unicast UDP and requires no
multicast configuration at all.

Run on **Machine A** (`192.168.178.164`) — one terminal for the relay,
one for the client:

```bash
# Terminal 1 — relay server
python3 geotalk-relay.py --port 5073

# Terminal 2 — client on machine A
python3 geotalk.py --nick PC-A --relay 127.0.0.1 --relay-port 5073
```

Run on **Machine B** (`192.168.178.160`):

```bash
python3 geotalk.py --nick PC-B --relay 192.168.178.164 --relay-port 5073
```

Then on either machine, join a channel:

```
#59**
Hello from PC-B!
```

UFW rule needed on Machine A only (one port, not a range):

```bash
sudo ufw allow 5073/udp
```

The relay workaround is also the recommended approach for any deployment
that crosses subnet boundaries or the internet.

---

## Complete fix sequence (copy-paste)

Run this block on **both machines**, replacing `eth0` with your interface:

```bash
IFACE=eth0   # ← change this

# 1. UFW multicast rules
sudo ufw allow in to 239.73.0.0/16
sudo ufw allow in proto udp to 239.73.0.0/16

# 2. IGMP via iptables (immediate)
sudo iptables -I INPUT  -p igmp -j ACCEPT
sudo iptables -I OUTPUT -p igmp -j ACCEPT

# 3. IGMP persistent via UFW before.rules
sudo sed -i '/^COMMIT/i # GeoTalk IGMP\n-A ufw-before-input   -p igmp -j ACCEPT\n-A ufw-before-output  -p igmp -j ACCEPT\n-A ufw-before-forward -p igmp -j ACCEPT' \
  /etc/ufw/before.rules
sudo ufw reload

# 4. Multicast route
sudo ip route add 239.73.0.0/16 dev $IFACE 2>/dev/null || true

# 5. Verify
echo "--- UFW status ---"
sudo ufw status verbose | grep 239
echo "--- IGMP rules ---"
sudo iptables -L INPUT -n | grep igmp
echo "--- Multicast route ---"
ip route show | grep 239
echo "--- Interface flags ---"
ip link show $IFACE | grep -o '<[^>]*>'
```

After running this block on both machines, start GeoTalk and join the same
channel. If you still see no packets, run the `tcpdump` command from §6
on both machines and compare.

---

## Still not working?

| Symptom | Most likely cause |
|---|---|
| `tcpdump` shows packets on sender but not receiver | Router IGMP snooping (§7) |
| `tcpdump` shows nothing on sender | Missing multicast route (§4) |
| `tcpdump` shows traffic, GeoTalk silent | Wrong interface / port — check `--port` matches on both ends |
| Works for ~3 minutes then stops | No IGMP querier on LAN — disable IGMP snooping on router |
| Works on wired, not on Wi-Fi | Some APs block multicast on wireless — use relay mode (§8) |
| VirtualBox / VMware guest | Virtual switch usually blocks multicast — use relay mode (§8) |
