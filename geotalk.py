#!/usr/bin/env python3
"""
GeoTalk - Pseudo-HAM Radio & Text Messaging over UDP
Geo-grouped by postal code (Europe-focused)
Usage: python3 geotalk.py [--host 0.0.0.0] [--port 5000] [--nick CALLSIGN]

Author: René Oudeweg / Claude

Channel syntax
  Exact:    #5911AB  #59601  #75001
  Wildcard: #59**    #5***   #75***  (glob-style, * = any digit/char)
  Regex:    #/^59[0-9]{3}$/  (full Python regex between //)
"""

import sys
import os
import re
import fnmatch
import socket
import threading
import queue
import collections
import time
import json
import struct
import argparse
import hashlib
import readline
import signal
from datetime import datetime
from collections import defaultdict

# ── Optional audio deps ────────────────────────────────────────────────────────
try:
    import pyaudio
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

try:
    import opuslib
    OPUS_AVAILABLE = True
except ImportError:
    OPUS_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

VERSION      = "1.6.0"
DEFAULT_PORT   = 5073          # GeoTalk default UDP port
MCAST_GROUP    = "239.73.0."   # Multicast base: 239.73.<postal-hash-byte>.<sub>
BUFFER_SIZE    = 65536
AUDIO_CHUNK    = 960           # Opus 20 ms frame @ 48 kHz
AUDIO_RATE     = 48000         # 48 kHz — native Opus rate; falls back to PCM
AUDIO_CHANNELS = 1
AUDIO_FORMAT   = 8             # pyaudio.paInt16 = 8
OPUS_BITRATE   = 32000         # 32 kbit/s (~80 B/frame)

# Packet types
PKT_TEXT     = 0x01
PKT_AUDIO    = 0x02
PKT_ACK      = 0x03
PKT_PING     = 0x04
PKT_USERS    = 0x05
PKT_SCAN_REQ = 0x06   # scan: "is anyone on this channel?"
PKT_SCAN_RSP = 0x07   # scan: "yes, I am — here is my info"
# Relay control (mirrors geotalk-relay.py)
PKT_JOIN     = 0x10
PKT_LEAVE    = 0x11

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
# GEO-REGION DATABASE  (EU postal prefix → region name)
# Covers NL · DE · FR · BE · LU · UK · IE · ES · PT · IT · CH · AT
#         PL · CZ · SK · HU · RO · BG · HR · SI · DK · SE · NO · FI
# ══════════════════════════════════════════════════════════════════════════════

# Each entry: (glob_pattern, country_code, region_label)
# Patterns use * as wildcard for any character(s).
# More-specific patterns must come BEFORE less-specific ones.

_GEO_REGIONS: list[tuple[str, str, str]] = [
    # ── Netherlands ──────────────────────────────────────────────────────────
    ("1***??", "NL", "Amsterdam"),
    ("2***??", "NL", "South Holland / The Hague area"),
    ("3***??", "NL", "Utrecht / South Holland"),
    ("40**??", "NL", "Arnhem"),
    ("41**??", "NL", "Arnhem"),
    ("42**??", "NL", "Arnhem / Nijmegen"),
    ("43**??", "NL", "Arnhem / Doetinchem"),
    ("44**??", "NL", "Arnhem"),
    ("45**??", "NL", "Arnhem"),
    ("46**??", "NL", "Arnhem / Nijmegen"),
    ("47**??", "NL", "Nijmegen"),
    ("48**??", "NL", "Nijmegen"),
    ("49**??", "NL", "Nijmegen"),
    ("50**??", "NL", "Nijmegen / Gennep"),
    ("51**??", "NL", "Boxmeer / Cuijk"),
    ("52**??", "NL", "Helmond"),
    ("53**??", "NL", "Helmond / Eindhoven"),
    ("54**??", "NL", "Den Bosch"),
    ("55**??", "NL", "Den Bosch"),
    ("56**??", "NL", "Eindhoven"),
    ("57**??", "NL", "Eindhoven"),
    ("58**??", "NL", "Eindhoven / Weert"),
    ("590???", "NL", "Venlo Noord"),
    ("591???", "NL", "Venlo Centrum"),
    ("592???", "NL", "Venlo Zuid"),
    ("593???", "NL", "Venlo / Blerick"),
    ("594???", "NL", "Tegelen"),
    ("595???", "NL", "Arcen / Bergen (L)"),
    ("596???", "NL", "Venlo / Horst"),
    ("597???", "NL", "Horst aan de Maas"),
    ("598???", "NL", "Venray"),
    ("599???", "NL", "Bergen / Gennep"),
    ("59****", "NL", "Venlo regio"),          # catch-all for #59**
    ("60**??", "NL", "Weert"),
    ("61**??", "NL", "Roermond"),
    ("62**??", "NL", "Maastricht"),
    ("63**??", "NL", "Maastricht / Sittard"),
    ("64**??", "NL", "Heerlen"),
    ("65**??", "NL", "Heerlen / Kerkrade"),
    ("66**??", "NL", "Venlo / Tegelen"),
    ("67**??", "NL", "Roermond"),
    ("68**??", "NL", "Echt-Susteren"),
    ("69**??", "NL", "Sittard-Geleen"),
    ("70**??", "NL", "Den Haag"),
    ("71**??", "NL", "Delft / Den Haag"),
    ("72**??", "NL", "Leiden"),
    ("73**??", "NL", "Dordrecht"),
    ("74**??", "NL", "Dordrecht / Gorinchem"),
    ("75**??", "NL", "Rotterdam"),
    ("76**??", "NL", "Rotterdam"),
    ("77**??", "NL", "Breda"),
    ("78**??", "NL", "Breda / Tilburg"),
    ("79**??", "NL", "Tilburg / Waalwijk"),
    ("80**??", "NL", "Utrecht"),
    ("81**??", "NL", "Utrecht"),
    ("82**??", "NL", "Amersfoort"),
    ("83**??", "NL", "Amersfoort"),
    ("84**??", "NL", "Amersfoort / Veenendaal"),
    ("85**??", "NL", "Veenendaal / Wageningen"),
    ("86**??", "NL", "Wageningen / Ede"),
    ("87**??", "NL", "Apeldoorn"),
    ("88**??", "NL", "Apeldoorn"),
    ("89**??", "NL", "Apeldoorn / Zutphen"),
    ("90**??", "NL", "Deventer"),
    ("91**??", "NL", "Deventer / Almelo"),
    ("92**??", "NL", "Almelo / Enschede"),
    ("93**??", "NL", "Enschede"),
    ("94**??", "NL", "Hengelo"),
    ("95**??", "NL", "Hengelo / Borne"),
    ("96**??", "NL", "Zwolle"),
    ("97**??", "NL", "Zwolle / Meppel"),
    ("98**??", "NL", "Groningen"),
    ("99**??", "NL", "Groningen"),

    # ── Germany (5-digit) ─────────────────────────────────────────────────
    ("596**", "DE", "Mülheim/Ruhr"),
    ("597**", "DE", "Mülheim/Ruhr"),
    ("598**", "DE", "Essen"),
    ("599**", "DE", "Essen"),
    ("40***", "DE", "Hamburg Mitte"),
    ("41***", "DE", "Hamburg"),
    ("42***", "DE", "Bremen"),
    ("44***", "DE", "Dortmund"),
    ("45***", "DE", "Essen"),
    ("46***", "DE", "Dortmund Ost"),
    ("47***", "DE", "Duisburg"),
    ("48***", "DE", "Münster"),
    ("50***", "DE", "Köln"),
    ("51***", "DE", "Köln / Bonn"),
    ("52***", "DE", "Aachen"),
    ("53***", "DE", "Bonn"),
    ("55***", "DE", "Mainz"),
    ("60***", "DE", "Frankfurt am Main"),
    ("61***", "DE", "Frankfurt Rhein-Main"),
    ("63***", "DE", "Offenbach"),
    ("65***", "DE", "Wiesbaden"),
    ("68***", "DE", "Mannheim"),
    ("69***", "DE", "Heidelberg"),
    ("70***", "DE", "Stuttgart"),
    ("80***", "DE", "München"),
    ("81***", "DE", "München"),
    ("90***", "DE", "Nürnberg"),
    ("10***", "DE", "Berlin Mitte"),
    ("12***", "DE", "Berlin"),
    ("13***", "DE", "Berlin Nord"),
    ("20***", "DE", "Hamburg"),
    ("30***", "DE", "Hannover"),

    # ── France (5-digit) ──────────────────────────────────────────────────
    ("750**", "FR", "Paris"),
    ("751**", "FR", "Paris 1–4"),
    ("752**", "FR", "Paris 5–8"),
    ("753**", "FR", "Paris 9–12"),
    ("754**", "FR", "Paris 13–16"),
    ("755**", "FR", "Paris 17–20"),
    ("756**", "FR", "Paris suburbs"),
    ("130**", "FR", "Marseille"),
    ("690**", "FR", "Lyon"),
    ("310**", "FR", "Toulouse"),
    ("330**", "FR", "Bordeaux"),
    ("590**", "FR", "Lille"),
    ("060**", "FR", "Nice"),
    ("380**", "FR", "Grenoble"),
    ("670**", "FR", "Strasbourg"),
    ("440**", "FR", "Nantes"),

    # ── Belgium ───────────────────────────────────────────────────────────
    ("10**", "BE", "Brussels"),
    ("11**", "BE", "Brussels / Laeken"),
    ("12**", "BE", "Brussels / Etterbeek"),
    ("13**", "BE", "Brussels / Ixelles"),
    ("14**", "BE", "Brussels / Uccle"),
    ("15**", "BE", "Brussels / Anderlecht"),
    ("20**", "BE", "Antwerp"),
    ("21**", "BE", "Antwerp"),
    ("22**", "BE", "Antwerp"),
    ("30**", "BE", "Leuven"),
    ("40**", "BE", "Liège"),
    ("50**", "BE", "Namur"),
    ("60**", "BE", "Charleroi"),
    ("70**", "BE", "Mons"),
    ("80**", "BE", "Bruges"),
    ("90**", "BE", "Ghent"),

    # ── Luxembourg ────────────────────────────────────────────────────────
    ("1***", "LU", "Luxembourg City"),
    ("2***", "LU", "Luxembourg / Esch-sur-Alzette"),
    ("3***", "LU", "Esch-sur-Alzette"),
    ("4***", "LU", "Differdange"),

    # ── UK (outward codes) ────────────────────────────────────────────────
    ("SW*", "GB", "London South West"),
    ("SE*", "GB", "London South East"),
    ("EC*", "GB", "London City"),
    ("WC*", "GB", "London West Central"),
    ("W**", "GB", "London West"),
    ("N**", "GB", "London North"),
    ("NW*", "GB", "London North West"),
    ("E**", "GB", "London East"),
    ("EN*", "GB", "Enfield"),
    ("M**", "GB", "Manchester"),
    ("B**", "GB", "Birmingham"),
    ("LS*", "GB", "Leeds"),
    ("BS*", "GB", "Bristol"),
    ("EH*", "GB", "Edinburgh"),
    ("G**", "GB", "Glasgow"),
    ("CF*", "GB", "Cardiff"),
    ("BT*", "GB", "Belfast"),

    # ── Spain (5-digit) ───────────────────────────────────────────────────
    ("280**", "ES", "Madrid"),
    ("290**", "ES", "Málaga"),
    ("080**", "ES", "Barcelona"),
    ("460**", "ES", "Valencia"),
    ("410**", "ES", "Sevilla"),
    ("500**", "ES", "Zaragoza"),
    ("480**", "ES", "Bilbao"),
    ("150**", "ES", "A Coruña"),

    # ── Italy (5-digit) ───────────────────────────────────────────────────
    ("001**", "IT", "Rome"),
    ("002**", "IT", "Rome"),
    ("201**", "IT", "Milan"),
    ("801**", "IT", "Naples"),
    ("501**", "IT", "Florence"),
    ("401**", "IT", "Bologna"),
    ("101**", "IT", "Turin"),

    # ── Portugal ──────────────────────────────────────────────────────────
    ("1***-***", "PT", "Lisbon"),
    ("4***-***", "PT", "Porto"),
    ("3***-***", "PT", "Coimbra"),

    # ── Switzerland ───────────────────────────────────────────────────────
    ("10**", "CH", "Zürich"),
    ("12**", "CH", "Zürich"),
    ("30**", "CH", "Bern"),
    ("40**", "CH", "Basel"),
    ("12**", "CH", "Geneva"),

    # ── Austria ───────────────────────────────────────────────────────────
    ("1***", "AT", "Vienna"),
    ("4***", "AT", "Linz"),
    ("5***", "AT", "Salzburg"),
    ("6***", "AT", "Innsbruck"),
    ("8***", "AT", "Graz"),

    # ── Poland ────────────────────────────────────────────────────────────
    ("00-***", "PL", "Warsaw"),
    ("30-***", "PL", "Kraków"),
    ("50-***", "PL", "Wrocław"),
    ("60-***", "PL", "Poznań"),
    ("80-***", "PL", "Gdańsk"),
    ("90-***", "PL", "Łódź"),

    # ── Czech Republic ────────────────────────────────────────────────────
    ("1** **", "CZ", "Prague"),
    ("6** **", "CZ", "Brno"),
    ("7** **", "CZ", "Ostrava"),

    # ── Denmark ───────────────────────────────────────────────────────────
    ("1***", "DK", "Copenhagen"),
    ("2***", "DK", "Copenhagen suburbs"),
    ("5***", "DK", "Odense"),
    ("8***", "DK", "Aarhus"),
    ("9***", "DK", "Aalborg"),

    # ── Sweden ────────────────────────────────────────────────────────────
    ("1** **", "SE", "Stockholm"),
    ("4** **", "SE", "Göteborg"),
    ("2** **", "SE", "Malmö"),

    # ── Norway ────────────────────────────────────────────────────────────
    ("0***", "NO", "Oslo"),
    ("4***", "NO", "Stavanger"),
    ("5***", "NO", "Bergen"),
    ("7***", "NO", "Trondheim"),

    # ── Finland ───────────────────────────────────────────────────────────
    ("001**", "FI", "Helsinki"),
    ("002**", "FI", "Helsinki"),
    ("330**", "FI", "Tampere"),
    ("200**", "FI", "Turku"),
]

# Pre-compile regex equivalents of all glob patterns for fast matching
_compiled_regions: list[tuple[re.Pattern, str, str]] | None = None

def _get_compiled() -> list[tuple[re.Pattern, str, str]]:
    global _compiled_regions
    if _compiled_regions is not None:
        return _compiled_regions
    result = []
    for pat, cc, label in _GEO_REGIONS:
        regex = _glob_to_regex(pat)
        try:
            result.append((re.compile(regex, re.IGNORECASE), cc, label))
        except re.error:
            pass
    _compiled_regions = result
    return result

def _glob_to_regex(pattern: str) -> str:
    """
    Convert a GeoTalk glob pattern to a Python regex.
    Rules:
      *  → matches any single non-space character (one char wildcard)
      ** → matches one or more non-space characters (multi-char wildcard)
    We do a two-pass replace to avoid double-escaping.
    """
    # Escape all regex-special chars except *
    escaped = ""
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i+1] == "*":
                escaped += "__MULTI__"
                i += 2
            else:
                escaped += "__SINGLE__"
                i += 1
        else:
            escaped += re.escape(ch)
            i += 1
    escaped = escaped.replace("__MULTI__", "[^\\s]+")
    escaped = escaped.replace("__SINGLE__", "[^\\s]")
    return f"^{escaped}$"


# ══════════════════════════════════════════════════════════════════════════════
# CHANNEL PATTERN RESOLUTION
# A "channel key" is the canonical, normalised string used as the dict key
# and as the multicast seed. For wildcards / regex it is the pattern itself.
# ══════════════════════════════════════════════════════════════════════════════

class ChannelPattern:
    """
    Represents a single channel subscription, which can be:
      • exact  — a specific postal code   e.g. 5911AB
      • glob   — wildcard pattern          e.g. 59**
      • regex  — Python regex              e.g. /^59[0-9]{3}$/
    """

    def __init__(self, raw: str):
        raw = raw.strip().upper().replace(" ", "")
        self.raw = raw

        if raw.startswith("/") and raw.endswith("/") and len(raw) > 2:
            # ── explicit regex ────────────────────────────────────────────
            self.kind   = "regex"
            self.source = raw[1:-1]
            try:
                self._re = re.compile(self.source, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex: {e}")
            # Use a normalised key for multicast — hash of the pattern
            self.key = "REGEX:" + self.source

        elif "*" in raw or "?" in raw:
            # ── glob / wildcard ───────────────────────────────────────────
            self.kind   = "glob"
            self.source = raw
            regex_src   = _glob_to_regex(raw)
            self._re    = re.compile(regex_src, re.IGNORECASE)
            self.key    = "GLOB:" + raw

        else:
            # ── exact postal code ─────────────────────────────────────────
            self.kind   = "exact"
            self.source = raw
            self._re    = re.compile(f"^{re.escape(raw)}$", re.IGNORECASE)
            self.key    = raw

    def matches(self, postal: str) -> bool:
        return bool(self._re.match(postal.strip().upper().replace(" ", "")))

    def is_wildcard(self) -> bool:
        return self.kind in ("glob", "regex")

    def display(self) -> str:
        if self.kind == "regex":
            return f"/{self.source}/"
        return self.source

    def region_info(self) -> str:
        """Return human-readable region name(s) matching this pattern."""
        if self.kind == "exact":
            return _lookup_region(self.source)
        # For wildcards, list all DB entries that overlap
        hits = []
        for pat, cc, label in _GEO_REGIONS:
            # Does the DB pattern match what this wildcard could produce?
            # Heuristic: check if the pattern text overlaps
            if _patterns_overlap(self.source, pat):
                hits.append(f"{cc} · {label}")
        if hits:
            return "; ".join(dict.fromkeys(hits))   # deduplicate, keep order
        return "unknown region"

    def __repr__(self):
        return f"ChannelPattern({self.kind}, {self.source!r})"


def _lookup_region(postal: str) -> str:
    """Find the best-matching region label for an exact postal code."""
    postal_norm = postal.strip().upper().replace(" ", "")
    for compiled_re, cc, label in _get_compiled():
        if compiled_re.match(postal_norm):
            return f"{cc} · {label}"
    return "unknown region"


def _patterns_overlap(user_glob: str, db_glob: str) -> bool:
    """
    Rough check: does the user-entered glob prefix overlap with a DB pattern?
    We compare the non-wildcard prefix of the user glob against the DB pattern.
    """
    prefix = user_glob.split("*")[0].split("?")[0].upper()
    if not prefix:
        return True  # bare wildcard matches everything
    # Check if DB pattern starts similarly
    db_prefix = db_glob.split("*")[0].split("?")[0].upper()
    return db_prefix.startswith(prefix) or prefix.startswith(db_prefix)


# ══════════════════════════════════════════════════════════════════════════════
# POSTAL CODE → MULTICAST MAPPING
# ══════════════════════════════════════════════════════════════════════════════

def _canonical_key(key: str) -> str:
    """Strip GLOB:/REGEX: prefix for hashing."""
    for prefix in ("GLOB:", "REGEX:"):
        if key.startswith(prefix):
            return key[len(prefix):]
    return key

def postal_to_multicast(key: str) -> str:
    """
    Derive a stable multicast address from a channel key.

    For exact codes the first 4 chars determine the /24 subnet so that
    nearby postcodes share a subnet (e.g. 5911AB and 5922CD → same /24).
    For wildcard/regex keys the full key is hashed.

    Result always in 239.73.0.0/16 (RFC 2365 organisation-local scope).
    """
    raw = _canonical_key(key).strip().upper().replace(" ", "")

    if not (raw.startswith("REGEX:") or "*" in raw or "?" in raw):
        # Exact postal: use prefix for geographic clustering
        prefix = raw[:4]
        h1 = int(hashlib.md5(prefix.encode()).hexdigest(), 16) % 254 + 1
        h2 = int(hashlib.sha1(raw.encode()).hexdigest(), 16) % 254 + 1
    else:
        # Pattern channel: hash the full pattern string
        h1 = int(hashlib.md5(raw.encode()).hexdigest(), 16) % 254 + 1
        h2 = int(hashlib.sha256(raw.encode()).hexdigest(), 16) % 254 + 1

    return f"239.73.{h1}.{h2}"


def postal_to_port(key: str) -> int:
    """Derive a stable port (5074–5327) from a channel key."""
    raw  = _canonical_key(key).strip().upper().replace(" ", "")
    base = int(hashlib.md5(raw.encode()).hexdigest(), 16) % 253
    return DEFAULT_PORT + 1 + base


def parse_channel(raw: str) -> ChannelPattern:
    """
    Parse a user-supplied channel string into a ChannelPattern.
    Accepts:
      59**         glob wildcard
      /^59\\d{3}$/ regex (between slashes)
      5911AB       exact
    """
    return ChannelPattern(raw)


def expand_wildcard_info(pattern: ChannelPattern) -> str:
    """
    For a wildcard/regex channel, return a formatted table of
    known sub-regions that fall within the pattern.
    """
    if not pattern.is_wildcard():
        region = _lookup_region(pattern.key)
        return f"  Region: {region}"

    lines   = []
    seen    = set()
    user_re = pattern._re

    for db_pat, cc, label in _GEO_REGIONS:
        # Generate a sample postal code from the DB pattern (replace * with 0)
        sample = db_pat.replace("*", "0").replace("?", "0")
        if user_re.match(sample) and (cc, label) not in seen:
            seen.add((cc, label))
            lines.append(f"    {CY}{cc}{R}  {label}")
        # Also test: does the DB pattern overlap with user pattern?
        elif _patterns_overlap(pattern.source, db_pat) and (cc, label) not in seen:
            seen.add((cc, label))
            lines.append(f"    {CY}{cc}{R}  {label}  {DM}(approx.){R}")

    if lines:
        return "\n".join([f"  Sub-regions covered by {YL}#{pattern.display()}{R}:"]
                         + lines[:20])   # cap at 20
    return f"  {DM}No known sub-regions for this pattern{R}"

# ══════════════════════════════════════════════════════════════════════════════
# PACKET CODEC
# ══════════════════════════════════════════════════════════════════════════════

MAGIC = b"GT"

def encode_text(nick: str, postal: str, text: str, msg_id: int = 0) -> bytes:
    payload = json.dumps({"n": nick, "p": postal, "t": text, "id": msg_id,
                          "ts": int(time.time())}).encode()
    return MAGIC + bytes([PKT_TEXT]) + struct.pack("!H", len(payload)) + payload

def encode_audio(nick: str, postal: str, seq: int, audio: bytes,
                 codec: str = "opus") -> bytes:
    # codec: "opus" (compressed) or "pcm" (raw int16 LE fallback)
    header = json.dumps({"n": nick, "p": postal, "s": seq,
                         "codec": codec}).encode()
    hlen   = struct.pack("!H", len(header))
    alen   = struct.pack("!H", len(audio))
    return MAGIC + bytes([PKT_AUDIO]) + hlen + header + alen + audio

def encode_ping(nick: str, postal: str) -> bytes:
    payload = json.dumps({"n": nick, "p": postal,
                          "ts": int(time.time())}).encode()
    return MAGIC + bytes([PKT_PING]) + struct.pack("!H", len(payload)) + payload

def encode_relay_join(nick: str, postal: str) -> bytes:
    payload = json.dumps({"n": nick, "p": postal,
                          "ts": int(time.time())}).encode()
    return MAGIC + bytes([PKT_JOIN]) + struct.pack("!H", len(payload)) + payload

def encode_relay_leave(nick: str, postal: str) -> bytes:
    payload = json.dumps({"n": nick, "p": postal,
                          "ts": int(time.time())}).encode()
    return MAGIC + bytes([PKT_LEAVE]) + struct.pack("!H", len(payload)) + payload

def encode_scan_req(nick: str, scan_id: str) -> bytes:
    """
    Broadcast a scan probe.  scan_id is a random token so the requester can
    correlate replies to a specific scan session even when multiple scans
    overlap.  'p' is intentionally empty — the packet is sent to each candidate
    multicast group so receivers know which channel is being probed from context.
    """
    payload = json.dumps({"n": nick, "sid": scan_id,
                          "ts": int(time.time())}).encode()
    return MAGIC + bytes([PKT_SCAN_REQ]) + struct.pack("!H", len(payload)) + payload

def encode_scan_rsp(nick: str, postal: str, scan_id: str,
                    users: list[str], msg_count: int) -> bytes:
    """
    Reply to a scan probe.
    'p'   = the exact channel key this node is on
    'u'   = list of recently-seen nicks on this channel (including self)
    'mc'  = message count since joining
    'sid' = echoed scan_id for correlation
    """
    payload = json.dumps({"n": nick, "p": postal, "sid": scan_id,
                          "u": users, "mc": msg_count,
                          "ts": int(time.time())}).encode()
    return MAGIC + bytes([PKT_SCAN_RSP]) + struct.pack("!H", len(payload)) + payload

def decode_packet(data: bytes) -> dict | None:
    if len(data) < 5 or data[:2] != MAGIC:
        return None
    ptype = data[2]
    plen  = struct.unpack("!H", data[3:5])[0]
    body  = data[5:]

    if ptype == PKT_TEXT:
        try:
            return {"type": "text", **json.loads(body[:plen])}
        except Exception:
            return None

    if ptype == PKT_AUDIO:
        try:
            hdr   = json.loads(body[:plen])
            rest  = body[plen:]
            alen  = struct.unpack("!H", rest[:2])[0]
            audio = rest[2:2 + alen]
            return {"type": "audio", **hdr, "audio": audio}
        except Exception:
            return None

    if ptype == PKT_PING:
        try:
            return {"type": "ping", **json.loads(body[:plen])}
        except Exception:
            return None

    if ptype == PKT_SCAN_REQ:
        try:
            return {"type": "scan_req", **json.loads(body[:plen])}
        except Exception:
            return None

    if ptype == PKT_SCAN_RSP:
        try:
            return {"type": "scan_rsp", **json.loads(body[:plen])}
        except Exception:
            return None

    return None

# ══════════════════════════════════════════════════════════════════════════════
# RELAY TRANSPORT
# Unicast UDP bridge to a geotalk-relay server.
# When active it takes over all send() calls and adds an inbound RX thread
# that feeds decoded packets back into the same callback path as multicast.
# ══════════════════════════════════════════════════════════════════════════════

class RelayTransport:
    """
    Client-side relay transport.

    Lifecycle
    ---------
    1. Call connect(host, port) once at startup.
    2. Call subscribe(nick, channel_key)  for each channel join.
    3. Call unsubscribe(nick, channel_key) on leave.
    4. Call send(data) to push a packet to the relay.
    5. The relay_rx thread calls on_packet(data, addr) for each inbound packet.
    6. Call close() on exit.

    Thread safety: all public methods are safe to call from any thread.
    """

    def __init__(self):
        self._sock       : socket.socket | None = None
        self._relay_addr : tuple | None         = None
        self._running    = False
        self._rx_thread  : threading.Thread | None = None
        self.on_packet   = None          # callable(data: bytes, addr: tuple)
        self._lock       = threading.Lock()
        self._reconn_t   : threading.Thread | None = None
        self._subscribed : dict[str, str] = {}   # key → nick (for re-sub after reconnect)

    # ── connection management ─────────────────────────────────────────────

    def connect(self, host: str, port: int) -> bool:
        """
        Open a UDP socket pointed at the relay.
        Returns True on success.  Non-blocking (UDP has no real connect).
        """
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._sock.settimeout(2.0)
                # UDP "connect" just sets the default destination
                self._sock.connect((host, port))
                self._relay_addr = (host, port)
                self._running    = True
            except OSError as e:
                self._sock = None
                return False

        self._rx_thread = threading.Thread(
            target=self._rx_loop, daemon=True, name="relay-rx")
        self._rx_thread.start()
        return True

    def is_connected(self) -> bool:
        return self._sock is not None and self._running

    def relay_addr_str(self) -> str:
        if self._relay_addr:
            return f"{self._relay_addr[0]}:{self._relay_addr[1]}"
        return "none"

    # ── channel subscription ──────────────────────────────────────────────

    def subscribe(self, nick: str, channel_key: str):
        """Send PKT_JOIN to the relay so it routes traffic for this channel to us."""
        with self._lock:
            self._subscribed[channel_key] = nick
        self._send_raw(encode_relay_join(nick, channel_key))

    def unsubscribe(self, nick: str, channel_key: str):
        """Send PKT_LEAVE and forget this channel."""
        with self._lock:
            self._subscribed.pop(channel_key, None)
        self._send_raw(encode_relay_leave(nick, channel_key))

    def resubscribe_all(self):
        """Re-send all JOIN packets after a reconnect."""
        with self._lock:
            subs = dict(self._subscribed)
        for key, nick in subs.items():
            self._send_raw(encode_relay_join(nick, key))

    # ── data path ─────────────────────────────────────────────────────────

    def send(self, data: bytes):
        """Send a GeoTalk packet to the relay (relay fans it out)."""
        self._send_raw(data)

    def _send_raw(self, data: bytes):
        with self._lock:
            sock = self._sock
        if sock is None:
            return
        try:
            sock.send(data)
        except OSError:
            self._schedule_reconnect()

    # ── inbound RX ────────────────────────────────────────────────────────

    def _rx_loop(self):
        while self._running:
            with self._lock:
                sock = self._sock
            if sock is None:
                time.sleep(0.5)
                continue
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
                if self.on_packet:
                    self.on_packet(data, addr)
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    self._schedule_reconnect()
                break

    # ── reconnect ─────────────────────────────────────────────────────────

    def _schedule_reconnect(self):
        """Spawn a one-shot thread to reconnect after a brief pause."""
        with self._lock:
            if self._reconn_t and self._reconn_t.is_alive():
                return   # already reconnecting
            self._reconn_t = threading.Thread(
                target=self._reconnect_loop, daemon=True, name="relay-reconn")
            self._reconn_t.start()

    def _reconnect_loop(self):
        if not self._relay_addr:
            return
        host, port = self._relay_addr
        for delay in (2, 5, 10, 30, 60):
            time.sleep(delay)
            if not self._running:
                return
            if self.connect(host, port):
                self.resubscribe_all()
                return   # success

    # ── teardown ──────────────────────────────────────────────────────────

    def close(self):
        self._running = False
        with self._lock:
            sock, self._sock = self._sock, None
        if sock:
            try:
                sock.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# NETWORK LAYER
# ══════════════════════════════════════════════════════════════════════════════

class MulticastSocket:
    """
    Join / leave multicast groups and send/receive packets.

    local_if
    --------
    The local interface IP to bind multicast membership to.
    "0.0.0.0" lets the OS choose (fine for single-interface machines).
    Pass the actual IP of the interface you want to use (e.g. the IP of
    wlp2s0) when you have multiple interfaces or when the OS picks the
    wrong one.  Mirrors the ip-add-membership=GROUP:LOCAL_IF convention
    used by socat and the IP_ADD_MEMBERSHIP socket option.

    debug
    -----
    Set debug=True (or start with --debug) to print a timestamped line
    for every join, leave, send, receive, and error event.
    """

    def __init__(self, port: int, local_if: str = "0.0.0.0",
                 debug: bool = False):
        self.port     = port
        self.local_if = local_if   # IP of the local interface to use
        self.debug    = debug
        self.groups   = {}         # postal → (mcast_addr, sock)
        self._lock    = threading.Lock()
        self._dbg(f"MulticastSocket init  port={port}  local_if={local_if}")

    def _dbg(self, msg: str):
        if self.debug:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            sys.stderr.write(f"{DM}[MCAST {ts}] {msg}{R}\n")
            sys.stderr.flush()

    def _mreq(self, mcast_addr: str) -> bytes:
        """
        Build the ip_mreq struct for IP_ADD/DROP_MEMBERSHIP.
        Uses "4s4s" (multicast group addr + local interface addr) rather
        than "4sL" with INADDR_ANY so that local_if is always honoured,
        including when the value is "0.0.0.0" (equivalent to INADDR_ANY).
        """
        return struct.pack("4s4s",
                           socket.inet_aton(mcast_addr),
                           socket.inet_aton(self.local_if))

    def join(self, postal: str) -> socket.socket:
        addr = postal_to_multicast(postal)
        port = postal_to_port(postal)
        with self._lock:
            if postal in self.groups:
                self._dbg(f"join #{postal} → already joined, skipping")
                return self.groups[postal][1]
            self._dbg(f"join #{postal} → group={addr}:{port}  "
                      f"local_if={self.local_if}")
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                     socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    self._dbg(f"join #{postal} → SO_REUSEPORT set")
                except AttributeError:
                    self._dbg(f"join #{postal} → SO_REUSEPORT not available")
                # Bind to the channel's own port, not the base port.
                # UDP delivers a packet only when destination port matches
                # the socket's bound port.  Using self.port (base port) here
                # would mean no incoming multicast packets are ever delivered
                # to this socket because send() addresses them to postal_to_port().
                sock.bind(("", port))
                self._dbg(f"join #{postal} → bound to 0.0.0.0:{port}  "
                          f"(channel port, not base port {self.port})")
                mreq = self._mreq(addr)
                self._dbg(f"join #{postal} → IP_ADD_MEMBERSHIP  "
                          f"mreq={mreq.hex()}")
                sock.setsockopt(socket.IPPROTO_IP,
                                socket.IP_ADD_MEMBERSHIP, mreq)
                sock.settimeout(1.0)
                self.groups[postal] = (addr, sock)
                self._dbg(f"join #{postal} → OK  fd={sock.fileno()}")
                return sock
            except OSError as e:
                self._dbg(f"join #{postal} → ERROR: {e}")
                raise

    def leave(self, postal: str):
        with self._lock:
            if postal not in self.groups:
                self._dbg(f"leave #{postal} → not in groups, skipping")
                return
            addr, sock = self.groups.pop(postal)
            self._dbg(f"leave #{postal} → dropping group={addr}  "
                      f"fd={sock.fileno()}")
            try:
                mreq = self._mreq(addr)
                self._dbg(f"leave #{postal} → IP_DROP_MEMBERSHIP  "
                          f"mreq={mreq.hex()}")
                sock.setsockopt(socket.IPPROTO_IP,
                                socket.IP_DROP_MEMBERSHIP, mreq)
            except Exception as e:
                self._dbg(f"leave #{postal} → IP_DROP_MEMBERSHIP failed: {e}")
            sock.close()
            self._dbg(f"leave #{postal} → socket closed")

    def send(self, postal: str, data: bytes):
        addr = postal_to_multicast(postal)
        port = postal_to_port(postal)
        self._dbg(f"send #{postal} → {addr}:{port}  {len(data)}B  "
                  f"local_if={self.local_if}")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                              socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
            # Bind outgoing multicast to the same interface used for joins
            if self.local_if != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                             socket.inet_aton(self.local_if))
                self._dbg(f"send #{postal} → IP_MULTICAST_IF={self.local_if}")
            s.sendto(data, (addr, port))
            s.close()
            self._dbg(f"send #{postal} → sent OK")
        except OSError as e:
            self._dbg(f"send #{postal} → ERROR: {e}")
            # silently drop on network errors

    def log_rx(self, postal: str, addr: tuple, nbytes: int):
        """Call from the RX thread to log each received datagram."""
        self._dbg(f"recv #{postal} ← {addr[0]}:{addr[1]}  {nbytes}B")

    def close_all(self):
        self._dbg(f"close_all  groups={list(self.groups.keys())}")
        for postal in list(self.groups.keys()):
            self.leave(postal)


# ══════════════════════════════════════════════════════════════════════════════
# AUDIO ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class AudioEngine:
    """
    Capture mic → UDP / mix and play incoming UDP PCM streams.

    Mixing
    ──────
    Each remote sender gets its own ring buffer (deque of decoded PCM chunks).
    A dedicated mixer thread wakes every AUDIO_CHUNK/AUDIO_RATE seconds
    (~20 ms at 48 kHz / 960 samples).  It grabs one chunk per active sender,
    sums int16 samples with saturation clipping to ±32767, and writes the
    single mixed frame to the output stream.

    Senders that have not delivered a chunk within MIXER_SENDER_TIMEOUT
    seconds are considered silent and skipped.  This handles the common
    case where Alice stops talking before the mixer tick — her buffer goes
    empty, only Bob's audio is mixed, and there is no glitch.

    Codec
    ─────
    When opuslib is available, mic audio is Opus-encoded before transmission
    and decoded on receipt.  At 48 kHz / 960 samples / 32 kbit/s each packet
    is ~80 bytes vs ~1920 bytes raw PCM — a ~24x reduction.  Falls back to
    raw int16 PCM if opuslib is not installed; the "codec" JSON field allows
    both modes to interoperate seamlessly.
    """

    # How many chunks to buffer per sender before dropping (back-pressure)
    SENDER_QUEUE_DEPTH = 8
    # Seconds after last packet before a sender is evicted from the mixer
    SENDER_TIMEOUT     = 2.0
    # Mixer tick interval — should match one frame period
    MIXER_TICK         = AUDIO_CHUNK / AUDIO_RATE   # ~0.020 s at 48 kHz

    def __init__(self):
        self.pa          = None
        self._opus_enc   = None   # opuslib.Encoder  (None if unavailable)
        self._opus_dec   = None   # opuslib.Decoder  (None if unavailable)
        self._ptt        = False
        self._muted      = False
        self._running    = False
        self._tx_cb      = None   # called with (audio_bytes, seq)
        self._seq        = 0

        # Per-sender state  {nick: {"buf": deque[pcm_bytes], "last": float}}
        self._senders: dict[str, dict] = {}
        self._sender_lock = threading.Lock()

        if AUDIO_AVAILABLE:
            try:
                import os as _os
                devnull_fd = _os.open(_os.devnull, _os.O_WRONLY)
                saved_stderr_fd = _os.dup(2)
                _os.dup2(devnull_fd, 2)
                try:
                    self.pa = pyaudio.PyAudio()
                finally:
                    _os.dup2(saved_stderr_fd, 2)
                    _os.close(saved_stderr_fd)
                    _os.close(devnull_fd)
            except Exception:
                self.pa = None

        if OPUS_AVAILABLE:
            try:
                self._opus_enc = opuslib.Encoder(
                    AUDIO_RATE, AUDIO_CHANNELS, opuslib.APPLICATION_VOIP)
                self._opus_enc.bitrate = OPUS_BITRATE
                self._opus_dec = opuslib.Decoder(AUDIO_RATE, AUDIO_CHANNELS)
            except Exception:
                self._opus_enc = None
                self._opus_dec = None

    @property
    def codec(self) -> str:
        """Active codec name — 'opus' if opuslib is available, else 'pcm'."""
        return "opus" if self._opus_enc else "pcm"

    def start(self, tx_callback):
        """tx_callback(audio_bytes, seq) — called for each captured frame."""
        if not self.pa:
            return
        self._tx_cb   = tx_callback
        self._running = True
        self._rx_stream = self.pa.open(
            format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE, output=True,
            frames_per_buffer=AUDIO_CHUNK)
        self._tx_stream = self.pa.open(
            format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE, input=True,
            frames_per_buffer=AUDIO_CHUNK,
            stream_callback=self._capture_cb)
        self._mixer_thread = threading.Thread(target=self._mixer_loop,
                                              daemon=True, name="mixer")
        self._mixer_thread.start()

    def _capture_cb(self, in_data, frame_count, time_info, status):
        import pyaudio as _pa
        if self._ptt and self._tx_cb:
            if self._opus_enc:
                try:
                    audio = self._opus_enc.encode(in_data, AUDIO_CHUNK)
                except Exception:
                    audio = in_data   # fallback to raw PCM on encode error
            else:
                audio = in_data       # no opuslib — send raw PCM
            self._tx_cb(audio, self._seq)
            self._seq += 1
        return (None, _pa.paContinue)

    # ── mixer ─────────────────────────────────────────────────────────────

    def _mixer_loop(self):
        """
        Tick once per frame period.  Collect one chunk from every active
        sender, mix by summing int16 samples with clipping, write to output.
        If no sender has audio, write silence so the stream stays open.
        """
        import struct as _struct
        n_samples = AUDIO_CHUNK
        silence   = b"\x00" * (n_samples * 2)   # int16 = 2 bytes per sample
        tick      = self.MIXER_TICK

        while self._running:
            tick_start = time.monotonic()

            chunks    = []   # list of bytes chunks to mix this tick
            evict     = []

            with self._sender_lock:
                now = time.monotonic()
                for nick, state in list(self._senders.items()):
                    if now - state["last"] > self.SENDER_TIMEOUT:
                        evict.append(nick)
                        continue
                    buf = state["buf"]
                    if buf:
                        chunks.append(buf.popleft())

                for nick in evict:
                    del self._senders[nick]

            if not self._muted and chunks:
                if len(chunks) == 1:
                    mixed = chunks[0]
                else:
                    # Unpack all chunks to int16 lists and sum with clipping
                    fmt      = f"<{n_samples}h"
                    arrays   = [list(_struct.unpack(fmt, c[:n_samples * 2]))
                                for c in chunks]
                    mixed_s  = [
                        max(-32768, min(32767, sum(arrays[s][i]
                                                   for s in range(len(arrays)))))
                        for i in range(n_samples)
                    ]
                    mixed = _struct.pack(f"<{n_samples}h", *mixed_s)

                try:
                    self._rx_stream.write(mixed)
                except Exception:
                    pass
            else:
                # No audio this tick — write silence to prevent underrun
                try:
                    self._rx_stream.write(silence)
                except Exception:
                    pass

            # Sleep for the remainder of the tick period
            elapsed = time.monotonic() - tick_start
            sleep_t = tick - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    # ── public API ────────────────────────────────────────────────────────

    def push_ptt(self):
        self._ptt = True

    def release_ptt(self):
        self._ptt = False

    def mute(self):
        self._muted = True

    def unmute(self):
        self._muted = False

    @property
    def is_muted(self) -> bool:
        return self._muted

    def feed_audio(self, audio: bytes, nick: str = "", codec: str = "pcm"):
        """
        Deliver an audio frame from `nick` into that sender's PCM ring buffer.
        Opus frames are decoded to raw int16 PCM before buffering so the mixer
        always works with PCM regardless of the sender's codec.
        """
        if self._muted:
            return
        if not nick:
            nick = "_unknown_"
        # Decode Opus → PCM before buffering
        if codec == "opus" and self._opus_dec:
            try:
                pcm = self._opus_dec.decode(audio, AUDIO_CHUNK)
            except Exception:
                return   # drop corrupted frame rather than pass noise to mixer
        else:
            pcm = audio  # raw PCM (legacy peer or opuslib not installed)
        with self._sender_lock:
            if nick not in self._senders:
                self._senders[nick] = {
                    "buf":  collections.deque(maxlen=self.SENDER_QUEUE_DEPTH),
                    "last": time.monotonic(),
                }
            state = self._senders[nick]
            state["buf"].append(pcm)
            state["last"] = time.monotonic()

    def stop(self):
        self._running = False
        if self.pa:
            try:
                self._tx_stream.stop_stream()
                self._tx_stream.close()
                self._rx_stream.stop_stream()
                self._rx_stream.close()
                self.pa.terminate()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# CHANNEL MANAGER  (subscriptions, active senders, stats)
# ══════════════════════════════════════════════════════════════════════════════

class Channel:
    def __init__(self, pattern: "ChannelPattern"):
        self.pattern   = pattern
        self.postal    = pattern.key          # canonical key used in dicts
        self.multicast = postal_to_multicast(pattern.key)
        self.port      = postal_to_port(pattern.key)
        self.users     = {}   # nick → last_seen timestamp
        self.msg_count = 0
        self.joined_at = time.time()

    def seen(self, nick: str):
        self.users[nick] = time.time()

    def active_users(self, ttl=300) -> list:
        now = time.time()
        return [n for n, t in self.users.items() if now - t < ttl]

    def summary(self) -> str:
        users  = self.active_users()
        up     = int(time.time() - self.joined_at)
        region = _lookup_region(self.pattern.source) if self.pattern.kind == "exact" \
                 else self.pattern.region_info()
        return (f"{B}{CY}#{self.pattern.display()}{R}  "
                f"{DM}{region}{R}  "
                f"mcast={self.multicast}:{self.port}  "
                f"users=[{', '.join(users) or 'none'}]  "
                f"msgs={self.msg_count}  up={up}s")


# ══════════════════════════════════════════════════════════════════════════════
# CHANNEL SCANNER
#
# How it works
# ─────────────
# 1. User runs  /scan 59**  [timeout=5]
# 2. Scanner expands the glob/regex against the geo-region DB to get a list
#    of concrete candidate channel keys to probe.  For an exact code it probes
#    just that one.
# 3. For each candidate a PKT_SCAN_REQ is multicast (or sent via relay) and
#    a temporary UDP socket listens for PKT_SCAN_RSP replies.
# 4. Any GeoTalk node that receives a PKT_SCAN_REQ on a channel it has joined
#    replies with a PKT_SCAN_RSP containing its nick, user list, and msg count.
# 5. Results are streamed live to the terminal as they arrive; a summary table
#    is printed once the timeout expires.
# ══════════════════════════════════════════════════════════════════════════════

import uuid as _uuid

# Maximum number of candidate channels to probe in one scan
SCAN_MAX_CANDIDATES = 128
# Default probe timeout (seconds) — how long to wait for replies per batch
SCAN_DEFAULT_TIMEOUT = 5.0
# How many channels to probe in parallel (one multicast send per channel)
SCAN_BATCH_SIZE = 16


# Characters used to enumerate wildcard positions.
# Single * = exactly one char from this set; ** = 1-or-more chars.
_SCAN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _enumerate_glob(pattern: ChannelPattern,
                    max_codes: int = SCAN_MAX_CANDIDATES) -> list[str]:
    """
    Enumerate concrete codes that match a glob pattern by expanding each
    wildcard position over _SCAN_CHARS.

    Rules
    ─────
    *   → exactly one character from _SCAN_CHARS
    **  → one or two characters (we limit expansion depth to keep it bounded)
    ?   → treated like * (one char)

    Only codes longer than the fixed prefix are yielded.  Expansion stops
    once max_codes is reached so a broad pattern like #** does not explode.
    """
    src = pattern.source   # e.g. "591*" or "59**" or "1***??"

    # Split source into fixed prefix + wildcard suffix
    # e.g. "591*"  → prefix="591", suffix="*"
    #      "59**"  → prefix="59",  suffix="**"
    #      "1***??" → prefix="1", suffix="***??"
    first_wild = len(src)
    for i, ch in enumerate(src):
        if ch in ("*", "?"):
            first_wild = i
            break
    prefix = src[:first_wild]
    suffix = src[first_wild:]   # wildcard portion

    # Build list of suffix expansions
    def expand_suffix(s: str) -> list[str]:
        """
        Expand wildcard suffix into concrete strings, digits-first.
        Returns 1-char digit suffixes, then 1-char alpha, then 2-char
        digit/digit, then mixed — so numeric postal codes like 5910-5919
        are always included before alpha codes and the cap is reached.
        """
        if not s:
            return [""]
        if s[:2] == "**":
            digits = "0123456789"
            alpha  = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            results = []
            # Priority order: 2-char digit+digit first (covers 00-99, the
            # most common postal suffix range), then 1-char, then mixed.
            for c1 in digits:
                for c2 in digits:
                    results.append(c1 + c2)
            # 1-char digits then alpha
            for c in digits + alpha:
                results.append(c)
            # 2-char mixed
            for c1 in _SCAN_CHARS:
                for c2 in _SCAN_CHARS:
                    combo = c1 + c2
                    if combo not in results:
                        results.append(combo)
                    if len(results) >= max_codes * 8:
                        return results
            return results
        elif s[0] in ("*", "?"):
            results = []
            for c in _SCAN_CHARS:
                for rest in expand_suffix(s[1:]):
                    results.append(c + rest)
                    if len(results) >= max_codes * 8:
                        return results
            return results
        else:
            return [s[0] + rest for rest in expand_suffix(s[1:])]

    suffixes = expand_suffix(suffix)
    codes = []
    seen_local: set[str] = set()
    for sfx in suffixes:
        code = (prefix + sfx).upper()
        if code not in seen_local and pattern.matches(code):
            seen_local.add(code)
            codes.append(code)
        if len(codes) >= max_codes:
            break
    return codes


def _expand_scan_candidates(pattern: ChannelPattern) -> list[str]:
    """
    Return a list of concrete channel keys to probe for this pattern.

    Strategy
    ────────
    exact  → just that one key.

    glob   → three sources, merged in priority order:
             1. Enumerate all concrete codes by expanding wildcards
                (e.g. 591* → 5910…5919…591A…591Z, capped at SCAN_MAX_CANDIDATES).
             2. Walk the geo-region DB for samples the pattern matches
                (catches codes outside the enumerated set for broad patterns).
             3. Always include the wildcard key itself (GLOB:59**) so nodes
                that joined using the exact same glob pattern are found too.

    regex  → DB samples only (enumeration is not feasible for arbitrary regex)
             plus the REGEX: key itself.

    All results are deduped and capped at SCAN_MAX_CANDIDATES.
    """
    if pattern.kind == "exact":
        return [pattern.key]

    seen: set[str] = set()
    candidates: list[str] = []

    def add(key: str):
        if key not in seen and len(candidates) < SCAN_MAX_CANDIDATES:
            seen.add(key)
            candidates.append(key)

    # 1. Enumerate concrete codes from wildcard expansion (glob only)
    if pattern.kind == "glob":
        for code in _enumerate_glob(pattern, SCAN_MAX_CANDIDATES):
            add(code)

    # 2. DB-derived samples (works for both glob and regex)
    for db_pat, cc, label in _GEO_REGIONS:
        sample = db_pat.replace("*", "0").replace("?", "0").upper()
        if pattern.matches(sample):
            add(sample)
        prefix = db_pat.split("*")[0].split("?")[0].upper()
        if prefix and len(prefix) >= 3 and pattern.matches(prefix):
            add(prefix)

    # 3. The glob/regex key itself — nodes may have joined with the same pattern
    add(pattern.key)

    return candidates


class ScanResult:
    """One response received during a scan."""
    __slots__ = ("channel_key", "nick", "users", "msg_count", "region", "ts")

    def __init__(self, channel_key: str, nick: str, users: list,
                 msg_count: int, region: str):
        self.channel_key = channel_key
        self.nick        = nick
        self.users       = users
        self.msg_count   = msg_count
        self.region      = region
        self.ts          = time.time()


class ChannelScanner:
    """
    Probes a set of channel keys for live users and collects responses.
    Designed to be created fresh for each /scan invocation.

    How it works (multicast mode)
    ──────────────────────────────
    For each candidate channel key:
      1. Open a socket, join its multicast group, bind to its channel port.
      2. Send a SCAN_REQ to that multicast group (with 'p' set to the key).
      3. Any node that has joined that channel receives the SCAN_REQ via its
         normal _rx_loop_mcast thread, calls _handle_scan_req, and sends a
         SCAN_RSP back to the same multicast group+port.
      4. The scanner's per-channel socket receives the SCAN_RSP.

    Responses go back to the multicast group rather than unicast to the
    probe source address.  This avoids the source-port problem (the probe
    socket's ephemeral port is closed immediately after sending) and works
    correctly for two instances on the same machine.
    """

    def __init__(self, nick: str, local_if: str = "0.0.0.0",
                 relay: "RelayTransport | None" = None,
                 on_result=None):
        """
        nick      – our callsign (used in outgoing SCAN_REQ)
        local_if  – local interface IP for multicast (mirrors MulticastSocket)
        relay     – if set, send probes via relay instead of multicast
        on_result – callable(ScanResult) called live as replies arrive
        """
        self.nick      = nick
        self.local_if  = local_if
        self.relay     = relay
        self.on_result = on_result
        self.scan_id   = _uuid.uuid4().hex[:8]
        self._results  : dict[str, list[ScanResult]] = defaultdict(list)
        self._lock     = threading.Lock()
        self._socks    : list[socket.socket] = []   # one per candidate channel

    # ── public API ────────────────────────────────────────────────────────

    def run(self, candidates: list[str],
            timeout: float = SCAN_DEFAULT_TIMEOUT) -> list[ScanResult]:
        """
        Probe all candidates and block until timeout.
        Returns a flat list of ScanResult sorted by channel key.
        """
        if self.relay and self.relay.is_connected():
            self._run_relay(candidates, timeout)
        else:
            self._run_multicast(candidates, timeout)

        flat: list[ScanResult] = []
        with self._lock:
            for results in self._results.values():
                flat.extend(results)
        flat.sort(key=lambda r: r.channel_key)
        return flat

    # ── multicast path ────────────────────────────────────────────────────

    def _run_multicast(self, candidates: list[str], timeout: float):
        """
        For each candidate:
          - open a socket joined to its multicast group on its channel port
          - send a SCAN_REQ to that group
        Then listen on all sockets simultaneously for SCAN_RSP replies.
        """
        # Build one socket per unique (mcast_addr, port) — candidates may
        # hash to the same group, so deduplicate.
        key_to_sock: dict[str, socket.socket] = {}

        for key in candidates:
            mcast_addr = postal_to_multicast(key)
            mcast_port = postal_to_port(key)
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                     socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except AttributeError:
                    pass
                sock.bind(("", mcast_port))
                mreq = struct.pack("4s4s",
                                   socket.inet_aton(mcast_addr),
                                   socket.inet_aton(self.local_if))
                sock.setsockopt(socket.IPPROTO_IP,
                                socket.IP_ADD_MEMBERSHIP, mreq)
                sock.settimeout(0.2)
                key_to_sock[key] = sock
                self._socks.append(sock)
            except OSError:
                pass   # port already bound by joined channel — that's fine,
                       # our existing channel socket will receive the reply

        # Send probes in batches
        for i in range(0, len(candidates), SCAN_BATCH_SIZE):
            for key in candidates[i:i + SCAN_BATCH_SIZE]:
                self._probe_multicast(key)
            time.sleep(0.05)

        # Poll all sockets until timeout
        deadline = time.time() + timeout
        seen: set[str] = set()
        while time.time() < deadline:
            for sock in list(self._socks):
                try:
                    data, _ = sock.recvfrom(BUFFER_SIZE)
                    self._handle_rsp(data, seen)
                except socket.timeout:
                    pass
                except OSError:
                    pass

        # Clean up
        for sock in self._socks:
            try:
                sock.close()
            except Exception:
                pass
        self._socks.clear()

    def _probe_multicast(self, key: str):
        """Send one SCAN_REQ to the multicast group for `key`."""
        mcast_addr = postal_to_multicast(key)
        mcast_port = postal_to_port(key)
        # Always include 'p' so the responder knows which channel is being probed
        payload = json.dumps({"n": self.nick, "sid": self.scan_id,
                              "p": key, "ts": int(time.time())}).encode()
        pkt = MAGIC + bytes([PKT_SCAN_REQ]) + struct.pack("!H", len(payload)) + payload
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                              socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
            if self.local_if != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                             socket.inet_aton(self.local_if))
            s.sendto(pkt, (mcast_addr, mcast_port))
            s.close()
        except OSError:
            pass

    # ── relay path ────────────────────────────────────────────────────────

    def _run_relay(self, candidates: list[str], timeout: float):
        """Send SCAN_REQ via relay and collect SCAN_RSP from the relay RX thread."""
        # In relay mode the main relay socket is already receiving everything.
        # We register a temporary callback on the relay transport so SCAN_RSP
        # packets with our scan_id are forwarded here.
        result_q: "queue.Queue[bytes]" = queue.Queue()

        original_cb = self.relay.on_packet

        def patched_cb(data: bytes, addr: tuple):
            result_q.put(data)
            if original_cb:
                original_cb(data, addr)

        self.relay.on_packet = patched_cb

        for i in range(0, len(candidates), SCAN_BATCH_SIZE):
            for key in candidates[i:i + SCAN_BATCH_SIZE]:
                payload = json.dumps({"n": self.nick, "sid": self.scan_id,
                                      "p": key,
                                      "ts": int(time.time())}).encode()
                pkt = MAGIC + bytes([PKT_SCAN_REQ]) + struct.pack("!H", len(payload)) + payload
                self.relay.send(pkt)
            time.sleep(0.05)

        seen: set[str] = set()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = result_q.get(timeout=0.1)
                self._handle_rsp(data, seen)
            except queue.Empty:
                pass

        self.relay.on_packet = original_cb

    # ── shared response handler ───────────────────────────────────────────

    def _handle_rsp(self, data: bytes, seen: set):
        pkt = decode_packet(data)
        if not pkt or pkt.get("type") != "scan_rsp":
            return
        if pkt.get("sid") != self.scan_id:
            return

        nick        = pkt.get("n", "?")
        channel_key = pkt.get("p", "")
        users       = pkt.get("u", [])
        msg_count   = pkt.get("mc", 0)
        region      = _lookup_region(channel_key)

        dedup_key = f"{channel_key}:{nick}"
        if dedup_key in seen:
            return
        seen.add(dedup_key)

        result = ScanResult(channel_key, nick, users, msg_count, region)
        with self._lock:
            self._results[channel_key].append(result)

        if self.on_result:
            self.on_result(result)


# ══════════════════════════════════════════════════════════════════════════════
# GEOTALK CORE
# ══════════════════════════════════════════════════════════════════════════════

class GeoTalk:
    def __init__(self, nick: str, host: str = "0.0.0.0", port: int = DEFAULT_PORT,
                 relay_host: str = "", relay_port: int = DEFAULT_PORT,
                 local_if: str = "0.0.0.0", debug: bool = False):
        self.nick        = nick
        self.host        = host
        self.port        = port
        self.local_if    = local_if
        self.debug       = debug
        self.relay_host  = relay_host
        self.relay_port  = relay_port
        self.channels    = {}     # key → Channel
        self.active      = None   # current TX channel key
        self.mcast       = MulticastSocket(port, local_if=local_if,
                                           debug=debug)
        self._glob_socks : dict[str, list[str]] = {}  # wildcard key → [exact keys]
        self.relay       = RelayTransport()
        self.audio       = AudioEngine()
        self._running    = False
        self._rx_threads = {}     # key → Thread  (multicast RX)
        self._msg_id     = 0
        self._ptt_held   = False

    @property
    def relay_mode(self) -> bool:
        return bool(self.relay_host)

    # ── channel ops ───────────────────────────────────────────────────────────

    # ── multicast group helpers ──────────────────────────────────────────────

    def _join_mcast_groups(self, key: str, pat: ChannelPattern):
        """
        Join the multicast group(s) for a channel pattern.

        Exact channel  → one socket on that channel's group.
        Wildcard/regex → one socket on the pattern's own group (GLOB:59**)
                         PLUS one socket per enumerated concrete code
                         (5900, 5901, … 5999) so packets from peers on exact
                         channels are received.  All RX threads feed into the
                         same parent channel key so CHARLIE sees everything
                         in his #59** channel window.
        """
        def _start_rx(sock, hint_key):
            t = threading.Thread(target=self._rx_loop_mcast,
                                 args=(key, sock),
                                 kwargs={"hint_key_override": hint_key},
                                 daemon=True,
                                 name=f"rx-{hint_key[:20]}")
            self._rx_threads[hint_key] = t
            t.start()

        # Always join the pattern's own group
        sock = self.mcast.join(key)
        _start_rx(sock, key)

        if pat.kind == "exact":
            return   # nothing more to do

        # Wildcard/regex: enumerate concrete codes and join each group
        sub_keys: list[str] = []
        for code in _enumerate_glob(pat, SCAN_MAX_CANDIDATES):
            if code == key:
                continue   # already joined above
            try:
                sock = self.mcast.join(code)
                _start_rx(sock, code)
                sub_keys.append(code)
            except OSError:
                pass   # port already in use (another channel has same hash) — skip
        self._glob_socks[key] = sub_keys
        self.mcast._dbg(
            f"wildcard join #{pat.display()} → {1 + len(sub_keys)} groups")

    def _leave_mcast_groups(self, key: str):
        """Leave the multicast group(s) for a channel key."""
        self.mcast.leave(key)
        for sub_key in self._glob_socks.pop(key, []):
            self.mcast.leave(sub_key)
            self._rx_threads.pop(sub_key, None)

    def join_channel(self, raw: str) -> str:
        try:
            pat = parse_channel(raw)
        except ValueError as e:
            return f"{RD}Pattern error: {e}{R}"

        key = pat.key
        if key in self.channels:
            return f"{YL}Already on #{pat.display()}{R}"

        ch = Channel(pat)
        self.channels[key] = ch
        if self.active is None:
            self.active = key

        if self.relay_mode:
            # Relay path: subscribe the glob key AND all enumerated concrete
            # keys so the relay fans out packets from exact-channel peers
            # (e.g. BOB on #5912) to CHARLIE who joined #591*.
            self.relay.subscribe(self.nick, key)
            sub_keys: list[str] = []
            if pat.kind != "exact":
                for code in _enumerate_glob(pat, SCAN_MAX_CANDIDATES):
                    if code != key:
                        self.relay.subscribe(self.nick, code)
                        sub_keys.append(code)
                self._glob_socks[key] = sub_keys
            self.relay.send(encode_ping(self.nick, key))
        else:
            # Multicast path: join the pattern's own group AND, for wildcards,
            # all enumerated concrete groups so packets from exact-channel peers
            # (e.g. BOB on #5912) are received when CHARLIE joins #59**.
            self._join_mcast_groups(key, pat)
            self.mcast.send(key, encode_ping(self.nick, key))

        region_line = expand_wildcard_info(pat)
        n_extra     = len(self._glob_socks.get(key, []))
        extra       = f" + {n_extra} sub-channels" if n_extra else ""
        if self.relay_mode:
            transport = f"relay={self.relay.relay_addr_str()}{extra}"
        else:
            transport = f"mcast={ch.multicast}:{ch.port}{extra}"
        return (f"{GR}Joined #{pat.display()}{R}  "
                f"→ {transport}\n{region_line}")

    def leave_channel(self, raw: str) -> str:
        try:
            pat = parse_channel(raw)
        except ValueError as e:
            return f"{RD}Pattern error: {e}{R}"

        key = pat.key
        if key not in self.channels:
            matches = [k for k in self.channels if raw.upper() in k]
            if len(matches) == 1:
                key = matches[0]
            else:
                return f"{YL}Not on #{pat.display()}{R}"

        del self.channels[key]

        if self.relay_mode:
            self.relay.unsubscribe(self.nick, key)
            for sub_key in self._glob_socks.pop(key, []):
                self.relay.unsubscribe(self.nick, sub_key)
        else:
            self._leave_mcast_groups(key)

        if self.active == key:
            self.active = next(iter(self.channels), None)
        return f"{RD}Left #{pat.display()}{R}"

    def switch_channel(self, raw: str) -> str:
        try:
            pat = parse_channel(raw)
        except ValueError as e:
            return f"{RD}Pattern error: {e}{R}"

        key = pat.key
        if key not in self.channels:
            result = self.join_channel(raw)
            self.active = key
            return result
        self.active = key
        region = _lookup_region(pat.source) if pat.kind == "exact" \
                 else pat.region_info()
        return (f"{CY}Active → #{pat.display()}{R}  "
                f"{DM}{region}{R}")

    # ── messaging ─────────────────────────────────────────────────────────────

    def send_text(self, text: str, raw: str | None = None) -> str:
        target_key = self.active
        if raw:
            try:
                target_key = parse_channel(raw).key
            except ValueError as e:
                return f"{RD}Pattern error: {e}{R}"
        if not target_key:
            return f"{RD}No active channel. Use #POSTAL to join one.{R}"

        ch = self.channels.get(target_key)
        display = ch.pattern.display() if ch else target_key

        self._msg_id += 1
        pkt = encode_text(self.nick, target_key, text, self._msg_id)
        self._send(target_key, pkt)
        if ch:
            ch.msg_count += 1
        ts = datetime.now().strftime("%H:%M")
        return f"{DM}{ts}{R} {B}{GR}[{self.nick}]{R} → {B}{CY}#{display}{R}: {text}"

    def send_audio_chunk(self, audio: bytes, seq: int):
        if not self.active:
            return
        pkt = encode_audio(self.nick, self.active, seq, audio,
                           codec=self.audio.codec)
        self._send(self.active, pkt)

    def _send(self, key: str, data: bytes):
        """
        Route a packet to relay or multicast.

        Wildcard channels (GLOB:591*, GLOB:59**, …)
        ────────────────────────────────────────────
        The `p` field in the packet JSON names the sending channel.  Both the
        relay and multicast peers dispatch inbound packets by looking up the
        `p` field against their subscriptions.  BOB subscribed to #5912, not
        GLOB:591*, so a packet with p=GLOB:591* is invisible to him.

        Fix: re-encode the packet for each sub-key with that sub-key's `p`
        field and send each copy separately.  The relay then fans each one out
        to the right subscribers; in multicast mode each copy goes to the right
        multicast group.

        The GLOB key itself is still sent so peers who joined the same glob
        pattern (e.g. another CHARLIE on #591*) also receive the message.
        """
        sub_keys = self._glob_socks.get(key)   # None for exact channels

        if not sub_keys:
            # Exact channel or no sub-keys — single send as before
            if self.relay_mode:
                self.relay.send(data)
            else:
                self.mcast.send(key, data)
            return

        # Wildcard channel: send once per sub-key with rewritten `p` field,
        # plus once on the glob key itself.
        all_keys = [key] + sub_keys

        # Parse the packet to extract the JSON header so we can rewrite `p`
        # Format: MAGIC(2) + type(1) + len(2) + JSON + optional PCM tail
        if len(data) < 5 or data[:2] != MAGIC:
            # Malformed or non-standard packet — fall back to single send
            if self.relay_mode:
                self.relay.send(data)
            else:
                self.mcast.send(key, data)
            return

        pkt_type = data[2]
        json_len = struct.unpack("!H", data[3:5])[0]
        json_bytes = data[5:5 + json_len]
        tail = data[5 + json_len:]   # PCM for audio packets, empty otherwise

        try:
            payload = json.loads(json_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            if self.relay_mode:
                self.relay.send(data)
            else:
                self.mcast.send(key, data)
            return

        for send_key in all_keys:
            payload["p"] = send_key
            new_json = json.dumps(payload, separators=(",", ":")).encode()
            new_pkt  = (MAGIC + bytes([pkt_type])
                        + struct.pack("!H", len(new_json))
                        + new_json + tail)
            if self.relay_mode:
                self.relay.send(new_pkt)
            else:
                self.mcast.send(send_key, new_pkt)

    # ── PTT ───────────────────────────────────────────────────────────────────

    def ptt_push(self) -> str:
        if not self.active:
            return f"{RD}No active channel.{R}"
        if not AUDIO_AVAILABLE or not self.audio.pa:
            return f"{YL}[PTT] pyaudio not available — voice disabled.{R}"
        self._ptt_held = True
        self.audio.push_ptt()
        return f"{MG}[PTT ON]  Transmitting on #{self.active}  (type /ptt off or Ctrl+T){R}"

    def ptt_release(self) -> str:
        self._ptt_held = False
        self.audio.release_ptt()
        return f"{DM}[PTT OFF]{R}"

    def mute_toggle(self) -> str:
        if not AUDIO_AVAILABLE or not self.audio.pa:
            return f"{YL}[MUTE] Audio unavailable.{R}"
        if self.audio.is_muted:
            self.audio.unmute()
            return f"{GR}[MUTE OFF]{R}  Incoming audio enabled."
        else:
            self.audio.mute()
            return f"{YL}[MUTE ON]{R}   Incoming audio silenced."

    # ── channel scan ──────────────────────────────────────────────────────────

    def scan_channels(self, raw: str, timeout: float = SCAN_DEFAULT_TIMEOUT) -> str:
        """
        Probe for active users matching a pattern.
        Runs synchronously (blocks for `timeout` seconds) but streams live
        results to stdout as they arrive, then returns a summary table.
        """
        try:
            pat = parse_channel(raw)
        except ValueError as e:
            return f"{RD}Pattern error: {e}{R}"

        candidates = _expand_scan_candidates(pat)
        n_cand     = len(candidates)
        region_hint = pat.region_info() if pat.is_wildcard() else _lookup_region(pat.source)

        # Print header immediately so the user sees feedback
        sys.stdout.write(
            f"\n{B}Scanning {YL}#{pat.display()}{R}{B} "
            f"— {n_cand} channel(s) to probe, {timeout:.0f}s timeout{R}\n"
            f"  {DM}{region_hint}{R}\n\n")
        sys.stdout.flush()

        live_rows: list[str] = []
        live_lock = threading.Lock()

        def on_result(r: ScanResult):
            users_str = ", ".join(r.users) if r.users else r.nick
            row = (f"  {GR}►{R} {B}{CY}#{r.channel_key:<14}{R}"
                   f"  {B}{GR}{r.nick:<12}{R}"
                   f"  users=[{users_str}]"
                   f"  {DM}{r.region}{R}")
            with live_lock:
                live_rows.append(row)
            sys.stdout.write(f"\r{row}\n")
            sys.stdout.flush()
            self._redraw_prompt()

        relay = self.relay if self.relay_mode else None
        scanner = ChannelScanner(
            nick=self.nick,
            local_if=self.mcast.local_if,
            relay=relay,
            on_result=on_result,
        )

        results = scanner.run(candidates, timeout=timeout)

        # Summary
        if not results:
            summary = (f"{YL}  No active users found on #{pat.display()} "
                       f"within {timeout:.0f}s.{R}\n"
                       f"  {DM}(Nodes must be running GeoTalk v1.3.0+){R}\n")
        else:
            # Deduplicate by channel key for summary header
            seen_keys: dict[str, list[ScanResult]] = defaultdict(list)
            for r in results:
                seen_keys[r.channel_key].append(r)

            lines = [f"\n{B}Scan complete — {GR}{len(results)}{R}{B} "
                     f"responder(s) on {len(seen_keys)} channel(s){R}"]
            lines.append(f"  {'Channel':<16} {'Users':<40} {'Region'}")
            lines.append(f"  {'─'*16} {'─'*40} {'─'*24}")
            for key, rlist in sorted(seen_keys.items()):
                all_users = list(dict.fromkeys(u for r in rlist for u in (r.users or [r.nick])))
                region    = rlist[0].region
                lines.append(f"  {CY}#{key:<15}{R} {', '.join(all_users):<40} {DM}{region}{R}")
            summary = "\n".join(lines) + "\n"

        return summary

    # ── RX loops ──────────────────────────────────────────────────────────────

    def _rx_loop_mcast(self, key: str, sock: socket.socket,
                       hint_key_override: str | None = None):
        """
        Per-channel multicast receive thread.

        key              — the channel key this socket was opened for (used to
                           check whether the channel is still joined)
        hint_key_override — when a sub-group socket is used for a wildcard
                           channel, this is the sub-group's exact key (e.g.
                           "5912").  The parent wildcard key is `key`.
                           _dispatch_packet receives hint_key=override so it
                           knows which exact group the packet arrived on, but
                           the thread keeps running as long as the parent
                           wildcard channel (key) is still joined.
        """
        parent_key = key   # the channel whose lifetime governs this thread
        hint_key   = hint_key_override or key
        while parent_key in self.channels and self._running:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break
            self.mcast.log_rx(hint_key, addr, len(data))
            self._dispatch_packet(data, addr, hint_key=hint_key)

    def _relay_packet_cb(self, data: bytes, addr: tuple):
        """Called by RelayTransport for every inbound relay packet."""
        self._dispatch_packet(data, addr, hint_key=None)

    def _dispatch_packet(self, data: bytes, addr: tuple, hint_key: str | None):
        """
        Decode one UDP datagram and route it to the right channel.
        hint_key is set when called from a channel-specific mcast thread.
        """
        pkt = decode_packet(data)
        if not pkt:
            return

        pkt_type      = pkt.get("type")
        sender_postal = pkt.get("p", "").strip().upper()
        nick          = pkt.get("n", "???")

        # ── Handle scan requests: auto-reply without suppressing own nick ──
        if pkt_type == "scan_req":
            self._handle_scan_req(pkt, addr, hint_key)
            return

        # ── scan_rsp packets are handled by ChannelScanner, not here ──────
        if pkt_type == "scan_rsp":
            return

        if nick == self.nick:
            return   # suppress own echo for other packet types

        # Find which of our subscribed channels should receive this packet.
        # For multicast we already know the channel (hint_key).
        # For relay we fan out to all matching channels.
        if hint_key:
            candidate_keys = [hint_key] if hint_key in self.channels else []
        else:
            candidate_keys = list(self.channels.keys())

        for key in candidate_keys:
            ch = self.channels.get(key)
            if not ch:
                continue
            pat = ch.pattern
            # Match check
            if pat.kind == "exact":
                if sender_postal != pat.source and pkt.get("p") != key:
                    continue
            else:
                if not pat.matches(sender_postal) and pkt.get("p") != key:
                    continue

            ch.seen(nick)
            self._render_packet(pkt, nick, sender_postal, pat)
            break   # deliver to first matching channel only

    def _handle_scan_req(self, pkt: dict, addr: tuple, hint_key: str | None):
        """
        Respond to a SCAN_REQ from another node.

        Matching logic
        ──────────────
        The probe carries a 'p' field which may be:
          - an exact key  e.g. "5912"       → reply if we are on that channel
          - a glob key    e.g. "GLOB:59**"  → reply if the probe pattern matches
                                              any of our joined channel keys
          - empty                           → reply on hint_key or all channels

        Response routing
        ────────────────
        The SCAN_RSP is sent to the *probe's* multicast group (the group the
        SCAN_REQ arrived on), not to our own channel's group.  This is key:
        the scanner opened a listener socket on the probe group and is waiting
        there.  It has no socket on our exact channel group (e.g. #5912), so
        if we replied there the scanner would never see the response.
        """
        scan_id     = pkt.get("sid", "")
        probe_key   = pkt.get("p", "").strip().upper()   # may be empty
        requester   = pkt.get("n", "")

        if requester == self.nick:
            return   # don't reply to our own probes

        # Determine which of our channels to respond on.
        # Check BOTH directions:
        #   our_pattern.matches(probe_key)  — e.g. we're on GLOB:59**, probe is 5912
        #   probe_pattern.matches(our_key)  — e.g. we're on 5912, probe is GLOB:59**
        if probe_key:
            try:
                probe_pat = parse_channel(probe_key)
            except ValueError:
                probe_pat = None

            reply_keys = []
            for k, ch in self.channels.items():
                if k == probe_key:
                    reply_keys.append(k)
                elif ch.pattern.matches(probe_key):
                    # our pattern covers the probe key (e.g. we joined 59**, probe=5912)
                    reply_keys.append(k)
                elif probe_pat and probe_pat.matches(ch.pattern.source):
                    # probe pattern covers our channel key (e.g. probe=59**, we=5912)
                    reply_keys.append(k)
        elif hint_key and hint_key in self.channels:
            reply_keys = [hint_key]
        else:
            reply_keys = list(self.channels.keys())

        if not reply_keys:
            return

        # Determine where to send the response.
        # In multicast mode: send to the probe's multicast group so the scanner
        # receives it on the socket it already has open.
        # If hint_key is set, that IS the probe's channel — use it.
        # Otherwise fall back to each channel's own group.
        probe_mcast_addr = postal_to_multicast(hint_key or probe_key) if (hint_key or probe_key) else None
        probe_mcast_port = postal_to_port(hint_key or probe_key) if (hint_key or probe_key) else None

        for key in reply_keys:
            ch = self.channels.get(key)
            if not ch:
                continue
            users = ch.active_users()
            if self.nick not in users:
                users = [self.nick] + users
            rsp = encode_scan_rsp(self.nick, key, scan_id, users, ch.msg_count)
            if self.relay_mode:
                self.relay.send(rsp)
            else:
                # Send to probe group so scanner receives it; fall back to
                # own channel group if probe group is unknown.
                dst_addr = probe_mcast_addr or postal_to_multicast(key)
                dst_port = probe_mcast_port or postal_to_port(key)
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                      socket.IPPROTO_UDP)
                    s.setsockopt(socket.IPPROTO_IP,
                                 socket.IP_MULTICAST_TTL, 32)
                    if self.mcast.local_if != "0.0.0.0":
                        s.setsockopt(socket.IPPROTO_IP,
                                     socket.IP_MULTICAST_IF,
                                     socket.inet_aton(self.mcast.local_if))
                    s.sendto(rsp, (dst_addr, dst_port))
                    s.close()
                except OSError:
                    pass

    def _render_packet(self, pkt: dict, nick: str, sender_postal: str,
                       pat: "ChannelPattern"):
        """Print an inbound packet to the terminal."""
        ts     = datetime.now().strftime("%H:%M")
        region = _lookup_region(sender_postal)

        if pkt["type"] == "text":
            sys.stdout.write(
                f"\r{DM}{ts}{R} {B}{CY}[{nick}]{R} "
                f"{DM}({region}){R} "
                f"#{pat.display()}: {pkt.get('t','')}\n")

        elif pkt["type"] == "audio":
            if self._ptt_held:
                return   # we are transmitting — discard incoming audio from others
            self.audio.feed_audio(pkt.get("audio", b""), nick=nick,
                                   codec=pkt.get("codec", "pcm"))
            if not self.audio.is_muted:
                sys.stdout.write(
                    f"\r{DM}{ts}{R} {MG}[VOICE]{R} "
                    f"{nick} {DM}({region}){R} "
                    f"#{pat.display()} seq={pkt.get('s',0)}\n")
            else:
                return   # muted — nothing to print or redraw

        elif pkt["type"] == "ping":
            sys.stdout.write(
                f"\r{DM}{ts}{R} {DM}→ {nick} online "
                f"({region}) #{pat.display()}{R}\n")

        sys.stdout.flush()
        self._redraw_prompt()

    def _redraw_prompt(self):
        ch = self.channels.get(self.active)
        label = ch.pattern.display() if ch else (self.active or "?")
        prompt = f"{B}{GR}{self.nick}{R}@{B}{CY}#{label}{R}> "
        sys.stdout.write(prompt)
        sys.stdout.flush()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        if AUDIO_AVAILABLE and self.audio.pa:
            self.audio.start(self.send_audio_chunk)

        if self.relay_host:
            ok = self.relay.connect(self.relay_host, self.relay_port)
            self.relay.on_packet = self._relay_packet_cb
            status = (f"{GR}Relay connected{R} → "
                      f"{self.relay_host}:{self.relay_port}")  if ok else \
                     (f"{YL}Relay unreachable{R} ({self.relay_host}:{self.relay_port})"
                      f" — will retry automatically")
            # Defer printing until after banner; store for main() to show
            self._relay_status = status
        else:
            self._relay_status = ""

        t = threading.Thread(target=self._heartbeat, daemon=True,
                             name="heartbeat")
        t.start()

    def _heartbeat(self):
        while self._running:
            time.sleep(60)
            for key in list(self.channels.keys()):
                self._send(key, encode_ping(self.nick, key))
            # Keep relay subscription alive
            if self.relay_mode and self.relay.is_connected():
                for key, ch in list(self.channels.items()):
                    self.relay.subscribe(self.nick, key)

    def stop(self):
        self._running = False
        self.audio.stop()
        self.mcast.close_all()
        self.relay.close()


# ══════════════════════════════════════════════════════════════════════════════
# COMMAND PARSER
# ══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = f"""
{B}GeoTalk {VERSION} — Commands{R}

{B}Channel discovery{R}
  {YL}/scan 59**{R}          Probe for active users in Venlo region
  {YL}/scan 59** 10{R}       Same, with 10s timeout (default 5s)
  {YL}/scan 1***??{R}        Probe Amsterdam area
  {YL}/scan 5911AB{R}        Probe a single exact channel

{B}Channel commands{R}
  {YL}#59601{R}             Exact postal-code channel
  {YL}#59**{R}              Wildcard — Venlo region (all 59xx codes)
  {YL}#5***{R}              Wildcard — all NL postcodes starting with 5
  {YL}#75***{R}             Wildcard — Paris region
  {YL}#/^59[0-9]{{3}}$/{R}  Regex channel (Python regex between //)
  {YL}/join 59**{R}         Join wildcard channel (stays in background)
  {YL}/leave 59**{R}        Leave a channel
  {YL}/ch{R}                List joined channels with region info
  {YL}/sw 59**{R}           Switch active TX channel

{B}Messaging{R}
  {YL}<text>{R}             Send text to active channel
  {YL}/msg 59** hi!{R}      Send to specific channel

{B}Region lookup{R}
  {YL}/lookup 59**{R}       Show all known sub-regions for a pattern
  {YL}/lookup 5911AB{R}     Look up exact postal code region
  {YL}/postal Venlo{R}      Reverse lookup: find postal patterns by city or region name
  {YL}/postal Amsterdam{R}  Works for any EU city in the region database

{B}Voice / PTT{R}
  {YL}/ptt on{R}            Push-to-talk ON  (mic → multicast)
  {YL}/ptt off{R}           Push-to-talk OFF
  {YL}Ctrl+T{R}            Toggle PTT on/off
  {YL}Ctrl+Y{R}            Toggle audio mute on/off
  {YL}/mute{R}  {YL}/m{R}          Toggle incoming audio mute

{B}Info{R}
  {YL}/users{R}             Active users on current channel
  {YL}/info{R}              Network info (multicast addr / relay, port)
  {YL}/relay{R}             Relay connection status
  {YL}/whoami{R}            Show your callsign
  {YL}/help{R}              This help text
  {YL}/quit{R}  {YL}/q{R}        Exit GeoTalk

{B}Pattern syntax{R}
  {YL}*{R}    = any single character
  {YL}**{R}   = any one-or-more characters
  {YL}/regex/{R} = full Python regular expression

{B}Examples{R}
  {YL}#59**{R}         Venlo regio (NL 59xx)
  {YL}#1***??{R}       Amsterdam (NL 1xxx??)
  {YL}#750**{R}        Paris (FR 750xx)
  {YL}#10***{R}        Berlin or Brussels depending on context
  {YL}#/^[0-9]{{4}}[A-Z]{{2}}$/{R}  Any valid Dutch postcode
"""

def handle_command(cmd: str, gt: GeoTalk) -> str | None:
    """
    Returns a string to print, or None for unknown / empty input.
    Returns '' (empty) if consumed silently.
    """
    raw  = cmd.strip()
    if not raw:
        return None

    # ── postal shortcut: #POSTCODE ────────────────────────────────────────
    if raw.startswith("#"):
        postal = raw[1:].split()[0]
        if postal:
            return gt.switch_channel(postal)
        return f"{YL}Usage: #POSTCODE (e.g. #59601){R}"

    # ── slash commands ────────────────────────────────────────────────────
    if raw.startswith("/"):
        parts = raw[1:].split(maxsplit=2)
        cmd0  = parts[0].lower() if parts else ""

        if cmd0 in ("quit", "q", "exit", "bye"):
            return "__QUIT__"

        if cmd0 == "scan":
            if len(parts) < 2:
                return (f"{YL}Usage: /scan PATTERN [timeout_seconds]{R}\n"
                        f"  e.g.  /scan 59**\n"
                        f"        /scan 1***??  10\n"
                        f"        /scan 5911AB")
            pattern_raw = parts[1]
            timeout = SCAN_DEFAULT_TIMEOUT
            if len(parts) >= 3:
                try:
                    timeout = max(1.0, min(60.0, float(parts[2])))
                except ValueError:
                    return f"{RD}Invalid timeout '{parts[2]}' — must be a number (1–60){R}"
            # scan_channels blocks for `timeout` seconds and streams to stdout
            return gt.scan_channels(pattern_raw, timeout=timeout)

        if cmd0 == "lookup":
            if len(parts) < 2:
                return f"{YL}Usage: /lookup PATTERN  (e.g. /lookup 59** or /lookup 5911AB){R}"
            try:
                pat = parse_channel(parts[1])
            except ValueError as e:
                return f"{RD}{e}{R}"
            lines = [f"{B}Lookup: #{pat.display()}{R}  "
                     f"({pat.kind})  "
                     f"mcast={postal_to_multicast(pat.key)}:{postal_to_port(pat.key)}"]
            lines.append(expand_wildcard_info(pat))
            return "\n".join(lines)

        if cmd0 == "postal":
            if len(parts) < 2:
                return (f"{YL}Usage: /postal CITY_OR_REGION{R}\n"
                        f"  e.g.  /postal Venlo\n"
                        f"        /postal Amsterdam\n"
                        f"        /postal Paris\n"
                        f"        /postal Berlin")
            query = " ".join(parts[1:]).strip().lower()
            hits = []
            seen_pat: set[str] = set()
            for db_pat, cc, label in _GEO_REGIONS:
                if query in label.lower() and db_pat not in seen_pat:
                    seen_pat.add(db_pat)
                    # Build the channel pattern from the DB entry
                    # Strip trailing ? and * to get the shortest useful prefix
                    prefix = db_pat.rstrip("?*")
                    # Suggest a usable glob: prefix + ** if wildcards were stripped
                    if db_pat != prefix:
                        suggestion = prefix + "**"
                    else:
                        suggestion = db_pat
                    hits.append((cc, label, db_pat, suggestion))

            if not hits:
                return (f"{YL}No regions found matching '{query}'.{R}\n"
                        f"  Try a broader term, e.g. /postal Venlo, /postal Paris, /postal Berlin")

            lines = [f"{B}Postal lookup: '{query}'{R}  ({len(hits)} result{'s' if len(hits) != 1 else ''})\n"]
            prev_cc = None
            for cc, label, db_pat, suggestion in sorted(hits, key=lambda x: (x[0], x[1])):
                if cc != prev_cc:
                    lines.append(f"{DM}── {cc} ──────────────────────{R}")
                    prev_cc = cc
                lines.append(
                    f"  {B}{CY}#{suggestion:<14}{R}  "
                    f"{DM}(pattern: {db_pat}){R}  "
                    f"{label}"
                )
            lines.append(f"\n{DM}Tip: use #{suggestion} to join, or /scan {suggestion} to find active users{R}")
            return "\n".join(lines)

        if cmd0 == "help":
            return HELP_TEXT

        if cmd0 == "whoami":
            return f"{GR}{gt.nick}{R}"

        if cmd0 == "ch":
            if not gt.channels:
                return f"{YL}No active channels. Use #POSTCODE or #59** to join.{R}"
            lines = []
            for k, ch in gt.channels.items():
                marker = f"{GR}►{R} " if k == gt.active else "  "
                lines.append(marker + ch.summary())
            return "\n".join(lines)

        if cmd0 in ("join", "j"):
            if len(parts) < 2:
                return f"{YL}Usage: /join POSTCODE{R}"
            return gt.join_channel(parts[1])

        if cmd0 in ("leave", "part"):
            if len(parts) < 2:
                return f"{YL}Usage: /leave POSTCODE{R}"
            return gt.leave_channel(parts[1])

        if cmd0 in ("sw", "switch"):
            if len(parts) < 2:
                return f"{YL}Usage: /sw POSTCODE{R}"
            return gt.switch_channel(parts[1])

        if cmd0 in ("msg", "m"):
            if len(parts) < 3:
                return f"{YL}Usage: /msg POSTCODE message...{R}"
            return gt.send_text(parts[2], parts[1])

        if cmd0 == "ptt":
            sub = parts[1].lower() if len(parts) > 1 else "on"
            if sub == "on":
                return gt.ptt_push()
            elif sub == "off":
                return gt.ptt_release()
            return f"{YL}Usage: /ptt [on|off]{R}"

        if cmd0 in ("mute", "m"):
            return gt.mute_toggle()

        if cmd0 == "users":
            if not gt.active:
                return f"{YL}No active channel.{R}"
            ch = gt.channels.get(gt.active)
            if not ch:
                return f"{YL}Channel not found.{R}"
            users = ch.active_users()
            return (f"{CY}#{ch.pattern.display()}{R} active users (5 min): "
                    f"{', '.join(users) if users else 'none'}")

        if cmd0 == "info":
            lines = [f"{B}GeoTalk {VERSION}{R}  nick={GR}{gt.nick}{R}"]

            # ── transport ────────────────────────────────────────────────────
            lines.append(f"  Port base : {gt.port}")
            lines.append(f"  Interface : {gt.local_if}  "
                         f"{DM}(0.0.0.0 = OS default){R}")
            if gt.relay_mode:
                conn = f"{GR}connected{R}" if gt.relay.is_connected() \
                       else f"{RD}disconnected{R}"
                lines.append(f"  Transport : {CY}relay (unicast UDP){R}  "
                             f"{gt.relay_host}:{gt.relay_port}  [{conn}]")
            else:
                lines.append(f"  Transport : {CY}multicast UDP{R}  "
                             f"239.73.0.0/16")

            # ── audio ────────────────────────────────────────────────────────
            lines.append("")
            if not AUDIO_AVAILABLE:
                lines.append(f"  Audio     : {RD}unavailable{R}  "
                             f"{DM}(install pyaudio for PTT){R}")
            elif not gt.audio.pa:
                lines.append(f"  Audio     : {YL}pyaudio loaded but device init failed{R}")
            else:
                lines.append(f"  Audio     : {GR}available{R}")
                lines.append(f"  Sample rate : {AUDIO_RATE} Hz  "
                             f"({AUDIO_RATE // 1000} kHz)")
                lines.append(f"  Frame size  : {AUDIO_CHUNK} samples  "
                             f"({1000 * AUDIO_CHUNK // AUDIO_RATE} ms)")
                lines.append(f"  Channels    : {AUDIO_CHANNELS}  (mono)")
                lines.append(f"  Bit depth   : 16-bit int  (PCM s16le)")

            # ── codec ────────────────────────────────────────────────────────
            lines.append("")
            if OPUS_AVAILABLE and gt.audio.pa and gt.audio._opus_enc:
                lines.append(f"  Codec     : {GR}Opus{R}  "
                             f"{OPUS_BITRATE // 1000} kbit/s  "
                             f"~{OPUS_BITRATE * AUDIO_CHUNK // AUDIO_RATE // 8} B/frame")
                lines.append(f"  TX format : Opus-encoded  "
                             f"{DM}(~24× smaller than raw PCM){R}")
                lines.append(f"  RX compat : {GR}Opus{R} + {GR}PCM{R}  "
                             f"{DM}(legacy peers decoded automatically){R}")
            elif OPUS_AVAILABLE:
                lines.append(f"  Codec     : {YL}PCM{R}  "
                             f"{DM}(opuslib present but audio device unavailable){R}")
            else:
                bitrate = AUDIO_RATE * AUDIO_CHUNK * 16 // AUDIO_CHUNK
                lines.append(f"  Codec     : {YL}PCM (raw){R}  "
                             f"{bitrate // 1000} kbit/s  "
                             f"{AUDIO_CHUNK * 2} B/frame")
                lines.append(f"  TX format : raw int16 LE")
                lines.append(f"  Upgrade   : {DM}pip install opuslib  "
                             f"→ Opus at 32 kbit/s (~24× reduction){R}")

            # ── mute / ptt state ─────────────────────────────────────────────
            if AUDIO_AVAILABLE and gt.audio.pa:
                lines.append("")
                ptt_state  = f"{MG}ACTIVE{R}" if gt._ptt_held else f"{DM}off{R}"
                mute_state = f"{YL}MUTED{R}" if gt.audio.is_muted else f"{DM}off{R}"
                lines.append(f"  PTT state : {ptt_state}   "
                             f"Mute state : {mute_state}")

            # ── channels ─────────────────────────────────────────────────────
            if gt.channels:
                lines.append("")
                for k, ch in gt.channels.items():
                    region = _lookup_region(ch.pattern.source) \
                             if ch.pattern.kind == "exact" \
                             else ch.pattern.region_info().split(";")[0]
                    if gt.relay_mode:
                        net = f"relay={gt.relay.relay_addr_str()}"
                    else:
                        net = f"mcast={ch.multicast}:{ch.port}"
                    active_marker = f" {GR}[active]{R}" if k == gt.active else ""
                    lines.append(f"  #{ch.pattern.display():<18}"
                                 f" ({ch.pattern.kind}){active_marker}"
                                 f" → {net}  {DM}{region}{R}")

            return "\n".join(lines)

        if cmd0 == "relay":
            if not gt.relay_mode:
                return f"{YL}Not running in relay mode.  Start with --relay HOST [--relay-port PORT]{R}"
            conn = f"{GR}connected{R}" if gt.relay.is_connected() \
                   else f"{RD}disconnected{R}"
            return (f"Relay {gt.relay_host}:{gt.relay_port}  "
                    f"status={conn}  "
                    f"channels={len(gt.channels)}")

        return f"{YL}Unknown command /{cmd0}. Type /help{R}"

    # ── plain text → send to active channel ──────────────────────────────
    return gt.send_text(raw)



# ══════════════════════════════════════════════════════════════════════════════
# BANNER
# ══════════════════════════════════════════════════════════════════════════════

BANNER = f"""
{B}{CY}   ██████╗  ███████╗  ██████╗  ████████╗  █████╗  ██╗      ██╗  ██╗{R}
{B}{CY}  ██╔════╝  ██╔════╝ ██╔═══██╗ ╚══██╔══╝ ██╔══██╗ ██║      ██║ ██╔╝{R}
{B}{CY}  ██║  ███╗ █████╗   ██║   ██║    ██║    ███████║ ██║      █████╔╝ {R}
{B}{CY}  ██║   ██║ ██╔══╝   ██║   ██║    ██║    ██╔══██║ ██║      ██╔═██╗ {R}
{B}{CY}  ╚██████╔╝ ███████╗ ╚██████╔╝    ██║    ██║  ██║ ███████╗ ██║  ██╗{R}
{B}{CY}   ╚═════╝  ╚══════╝  ╚═════╝     ╚═╝    ╚═╝  ╚═╝ ╚══════╝ ╚═╝  ╚═╝{R}
{DM}  📡  Geo-grouped pseudo-HAM radio & text  •  v{VERSION}{R}
  Type {YL}/help{R} for commands  •  {YL}#59**{R} Venlo  •  {YL}#1***??{R} Amsterdam
"""


# ══════════════════════════════════════════════════════════════════════════════
# IP → POSTAL CODE AUTO-DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def detect_postal_from_ip(timeout: float = 5.0) -> tuple[str, str, str]:
    """
    Detect the user's approximate postal code from their public IP address.

    Strategy
    --------
    1. Try ip-api.com  (free, no key, returns postal + city + country in JSON)
    2. Try ipinfo.io   (free tier fallback)
    3. Return ("", "", "") if both fail.

    Returns (postal, city, country_code)  e.g. ("5944", "Tegelen", "NL")
    """
    import urllib.request as _req
    import json as _json

    endpoints = [
        (
            "http://ip-api.com/json/?fields=status,zip,city,countryCode",
            lambda d: (d.get("zip",""), d.get("city",""), d.get("countryCode",""))
                      if d.get("status") == "success" else ("","","")
        ),
        (
            "https://ipinfo.io/json",
            lambda d: (d.get("postal",""), d.get("city",""), d.get("country",""))
        ),
    ]

    for url, extract in endpoints:
        try:
            req = _req.Request(url, headers={"User-Agent": f"GeoTalk/{VERSION}"})
            with _req.urlopen(req, timeout=timeout) as resp:
                data = _json.loads(resp.read().decode())
            postal, city, cc = extract(data)
            postal = postal.strip().upper().replace(" ", "").replace("-", "")
            if postal:
                return postal, city, cc
        except Exception:
            continue

    return "", "", ""


def _best_auto_channel(postal: str) -> str:
    """
    Given a raw postal code from IP geolocation, return the most useful
    GeoTalk channel glob to auto-join.

    Walks the entire DB and picks the entry with the longest fixed prefix
    that still matches the postal code — i.e. the most specific region.
    Falls back to a 3-char prefix glob, then the raw code as exact channel.
    """
    if not postal:
        return ""
    postal_norm = postal.strip().upper()

    best_prefix = ""
    for db_pat, cc, label in _GEO_REGIONS:
        prefix = db_pat.rstrip("?*")
        if not prefix:
            continue
        # Only consider entries whose fixed prefix matches the start of postal
        if not postal_norm.startswith(prefix):
            continue
        try:
            pat = ChannelPattern(db_pat.replace("?", "*"))
            if pat.matches(postal_norm) and len(prefix) > len(best_prefix):
                best_prefix = prefix
        except ValueError:
            pass

    if best_prefix:
        return best_prefix + "**"

    # Fall back to 3-char prefix glob
    if len(postal_norm) >= 3:
        return postal_norm[:3] + "**"

    return postal_norm


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def build_prompt(gt: GeoTalk) -> str:
    ch = gt.channels.get(gt.active)
    label = ch.pattern.display() if ch else (gt.active or "?")
    ptt  = f" {MG}[PTT]{R}" if gt._ptt_held else ""
    mute = f" {YL}[M]{R}" if (AUDIO_AVAILABLE and gt.audio.pa and gt.audio.is_muted) else ""
    return f"{B}{GR}{gt.nick}{R}@{B}{CY}#{label}{R}{ptt}{mute}> "

def main():
    parser = argparse.ArgumentParser(
        description="GeoTalk — postal-code-based voice & text over UDP multicast/relay",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # LAN / multicast mode (default)
  python3 geotalk.py --nick PA3XYZ

  # LAN / multicast on Wi-Fi — pin to wireless interface IP
  python3 geotalk.py --nick PA3XYZ --local-if 192.168.178.164

  # Internet relay mode
  python3 geotalk.py --nick PA3XYZ --relay relay.example.com
  python3 geotalk.py --nick PA3XYZ --relay 1.2.3.4 --relay-port 5073

  # Join channels on startup
  python3 geotalk.py --nick PA3XYZ --relay relay.example.com --join 59** 1***??

  # Auto-join based on your public IP location
  python3 geotalk.py --nick PA3XYZ --auto-channel
  python3 geotalk.py --nick PA3XYZ --relay relay.example.com --auto-channel
""")
    parser.add_argument("--nick",       default="",
                        help="Your callsign/nickname")
    parser.add_argument("--host",       default="0.0.0.0",
                        help="Bind host for multicast (default 0.0.0.0)")
    parser.add_argument("--port",       type=int, default=DEFAULT_PORT,
                        help=f"Base UDP port (default {DEFAULT_PORT})")
    parser.add_argument("--local-if",   default="0.0.0.0", metavar="IP",
                        help="Local interface IP for multicast (default 0.0.0.0 = auto). "
                             "Set to your wlan/eth IP when the OS picks the wrong interface, "
                             "e.g. --local-if 192.168.178.164")
    parser.add_argument("--debug",      action="store_true",
                        help="Print verbose multicast debug lines to stderr")
    parser.add_argument("--relay",      default="", metavar="HOST",
                        help="Relay server hostname or IP (enables relay mode)")
    parser.add_argument("--relay-port", type=int, default=DEFAULT_PORT,
                        metavar="PORT",
                        help=f"Relay server UDP port (default {DEFAULT_PORT})")
    parser.add_argument("--join",         nargs="*", metavar="PATTERN",
                        help="Channel patterns to join on startup (e.g. 59** 1***??)")
    parser.add_argument("--auto-channel", action="store_true",
                        help="Detect location from public IP and auto-join nearest postal channel")
    args = parser.parse_args()

    # ── pick callsign ─────────────────────────────────────────────────────
    nick = args.nick.strip()
    if not nick:
        try:
            nick = input(f"{B}Enter your callsign/nickname: {R}").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
    if not nick:
        nick = f"user{os.getpid() % 9999}"

    print(BANNER)

    gt = GeoTalk(nick=nick, host=args.host, port=args.port,
                 relay_host=args.relay, relay_port=args.relay_port,
                 local_if=args.local_if, debug=args.debug)
    gt.start()


    # ── show transport status ─────────────────────────────────────────────
    if gt.relay_mode:
        print(gt._relay_status)
        print(f"  {DM}Mode: relay unicast UDP  (multicast disabled){R}\n")
    else:
        iface_note = (f"  if={gt.local_if}" if gt.local_if != "0.0.0.0"
                      else "  interface=auto")
        print(f"  {DM}Mode: LAN multicast UDP  (239.73.0.0/16){iface_note}{R}")
        print(f"  {DM}For internet use, add --relay <host>  •  "
              f"For Wi-Fi, add --local-if <your-IP>{R}\n")

    # ── auto-channel: detect location from public IP ─────────────────────
    if args.auto_channel:
        print(f"{DM}  Detecting location from public IP\u2026{R}", end="", flush=True)
        postal, city, cc = detect_postal_from_ip()
        if postal:
            channel = _best_auto_channel(postal)
            city_str = f"{city}, {cc}" if city else cc
            print(f"\r  {GR}Location detected:{R} {postal} ({city_str})  \u2192  auto-joining #{channel}")
            print(gt.join_channel(channel))
        else:
            print(f"\r  {YL}Could not detect location from IP (VPN? offline?){R}  "
                  f"\u2014 use {YL}#POSTCODE{R} to join manually")

    # ── auto-join channels ────────────────────────────────────────────────
    if args.join:
        for raw in args.join:
            print(gt.join_channel(raw))
    elif not args.auto_channel:
        print(f"{DM}Tip: {YL}#59**{DM} = Venlo region  •  {YL}#1***??{DM} = Amsterdam  •  {YL}/help{DM} for all commands{R}\n")

    # ── signal handler ────────────────────────────────────────────────────
    def sigint_handler(sig, frame):
        print(f"\n{RD}Interrupted. Use /quit to exit cleanly.{R}")

    signal.signal(signal.SIGINT, sigint_handler)


    # ── readline history & completion ─────────────────────────────────────
    readline.parse_and_bind("tab: complete")
    history_file = os.path.expanduser("~/.geotalk_history")
    try:
        readline.read_history_file(history_file)
    except FileNotFoundError:
        pass

    # ── Ctrl+T = PTT toggle ──────────────────────────────────────────────
    # Bind Ctrl+T to insert the sentinel string "__PTT__" and immediately
    # submit the line (via \n).  The REPL loop detects this sentinel and
    # toggles PTT without treating it as user text.
    readline.parse_and_bind(r'"\C-t": "\C-a\C-k__PTT__\C-m"')
    readline.parse_and_bind(r'"\C-y": "\C-a\C-k__MUTE__\C-m"')
    #  \C-a  move to start of line
    #  \C-k  kill to end (clears whatever the user had typed so far)
    #  sentinel  insert sentinel string
    #  \C-m  submit (Enter)
    # The original line content is lost on these keys — acceptable since
    # they are action keys, not typing keys.

    # ── REPL ──────────────────────────────────────────────────────────────
    try:
        while True:
            try:
                line = input(build_prompt(gt))
            except EOFError:
                break
            except KeyboardInterrupt:
                print()
                continue

            if line == "__PTT__":
                msg = gt.ptt_release() if gt._ptt_held else gt.ptt_push()
                print(msg)
                continue

            if line == "__MUTE__":
                print(gt.mute_toggle())
                continue

            result = handle_command(line, gt)
            if result == "__QUIT__":
                break
            if result:
                print(result)
    finally:
        gt.stop()
        try:
            readline.write_history_file(history_file)
        except Exception:
            pass
        print(f"\n{DM}73 de {nick}  —  GeoTalk off the air.{R}\n")


if __name__ == "__main__":
    main()
