"""Crimson Skies (PC) GOSDATA/ASSETS/crimson.rof: the UI resource archive, and the .BM paint
skins inside it (docs/formats/rof.md and paint.md in CrimsonSkies-csvm).

Archive: a tree of directory nodes, root at offset 0.
    node   u32 entry count, u32 name-pool length, entries x 24 bytes, name pool
    entry  u32 offset, u32 size, u32 stored size, u32 kind (0 stored, 1 directory, 2 zlib),
           u32 name length (with NUL), u32 name offset within this node's pool
Names are upper case. crimptch.rof has the same layout and overrides the base archive.

.BM skin: u16 height, u16 width, then planes of n = width * height texels, rows BOTTOM-UP:
    [0, 3n)   RGB shading map (near-greyscale panel lines and baked shading)
    [3n, 6n)  three 8-bit paint-region weights, slot 1, 2, 3 (they sum to ~255)
    [6n, 10n) RGBA decoration overlay (stripes, flashes), alpha = 4th channel
Painting, per texel: shading * (w1*c1 + w2*c2 + w3*c3) / 255, then the overlay over it.
"""

from __future__ import annotations

import os
import struct
import zlib

import numpy as np

from .binreader import FormatError

KIND_STORED, KIND_DIR, KIND_ZLIB = 0, 1, 2

# Pattern index -> folder (the engine's own 14-entry name table, paint.md)
PATTERNS = ("blackhat", "blckswan", "blake", "british", "fortune", "hollywd", "hughes", "medusas",
            "cccp", "sactrust", "german", "studio", "broadway", "itstaxi")

PATTERN_LABELS = {
    "blackhat": "Black Hats", "blckswan": "Black Swans", "blake": "Blake's Bandits",
    "british": "British (RAF)", "fortune": "Fortune Hunters", "hollywd": "Hollywood Knights",
    "hughes": "Hughes Aircraft", "medusas": "Medusas", "cccp": "Red Army (CCCP)",
    "sactrust": "Sacred Trust", "german": "German (Luftwaffe)", "studio": "Studio Security",
    "broadway": "Broadway", "itstaxi": "ITS Taxi",
}

# Default colours: the hangar's pattern table (colour index, shade index per slot) resolved through
# its swatch ramps (CSVM/data/hangar_patterns.json + hangar_swatches.json, transcribed from
# crimson.exe). Player Fortune Hunters red/black/white matches paint.md's confirmed triple.
PATTERN_COLOURS = {
    "blackhat": ((177, 130, 66), (119, 74, 43), (66, 39, 15)),
    "blckswan": ((23, 23, 21), (48, 47, 39), (196, 193, 186)),
    "blake": ((149, 163, 195), (89, 114, 159), (233, 228, 240)),
    "british": ((177, 130, 66), (48, 47, 39), (255, 255, 255)),
    "fortune": ((223, 0, 41), (25, 25, 25), (255, 255, 255)),
    "hollywd": ((108, 102, 169), (70, 40, 132), (212, 202, 225)),
    "hughes": ((243, 194, 0), (25, 25, 25), (255, 255, 255)),
    "medusas": ((95, 125, 143), (41, 14, 21), (141, 137, 93)),
    "cccp": ((57, 64, 68), (223, 0, 41), (243, 194, 0)),
    "sactrust": ((52, 38, 107), (243, 194, 0), (23, 23, 21)),
    "german": ((96, 115, 126), (25, 25, 25), (48, 47, 39)),
    "studio": ((32, 90, 167), (255, 255, 255), (25, 25, 25)),
    "broadway": ((70, 40, 132), (25, 25, 25), (238, 234, 0)),
    "itstaxi": ((249, 244, 0), (255, 255, 255), (25, 25, 25)),
}

# Default decals (nose, tail, wing) as indices into the numbered decal set; the AI schemes of
# vehicle.json where one exists (nose 21 = ace star, tail/wing = the squadron logo).
PATTERN_DECALS = {
    "blackhat": (21, 2, 2), "blckswan": (21, 5, 5), "blake": (21, 3, 3), "british": (21, 4, 4),
    "fortune": (21, 8, 7), "hollywd": (21, 10, 9), "hughes": (21, 11, 11), "medusas": (21, 14, 14),
    "cccp": (21, 6, 6), "sactrust": (21, 15, 15), "german": (21, 13, 13), "studio": (21, 11, 11),
    "broadway": (21, 21, 21), "itstaxi": (21, 12, 12),
}

DECALS = ("00BBomber_logo1", "01BBomber_logo2", "02BlackHat_logo1", "03Blake_logo1", "04British_logo1",
          "05BSwan_logo1", "06CCCP_logo1", "07Fhunter_logo1", "08Fhunter_logo2", "09Hknights_logo1",
          "10Hknights_logo2", "11Hughes_logo1", "12ITS_logo1", "13Luftwaffe_logo1", "14Medusa_logo1",
          "15Sacredtrust_logo1", "16Colorado_logo1", "17Hawaii_logo1", "18Pacifica_logo1", "19Texas_logo1",
          "20Dixie_logo1", "21Ace_star", "22AintyerHoney", "23BigHairy", "24BigLug", "25bigstink",
          "26biscuit", "27bushpilots", "28Crusher", "29DaddyO", "30FourofaKind", "31Fury", "32Giveemhell",
          "33GreenwEnvy", "34HaveaDay", "35hawgwild", "36joker", "37Lacey", "38LittleGreenMen",
          "39MissTake", "40ohSoBlue", "41SheerJoy", "42sittinDuck", "43SkyPirate", "44speeddemon",
          "45SuckerPunch", "46TomCat", "47Viking", "48Gypsy", "49Ace_Star2")

AIRCRAFT = {
    "agyro": "Hoplite (Autogyro)", "hel": "Hellhound", "bal": "Balmoral", "blo": "Bloodhawk",
    "bri": "Brigand", "dev": "Devastator", "fir": "Firebrand", "fur": "Fury", "kes": "Kestrel",
    "pea": "Peacemaker", "war": "Warhawk",
}

# The one .BM whose model texture has a different name (paint.md, "Which .BM ...")
BM_ALIAS = {"dev_fusalage": "dev_fusalage1"}


class RofArchive:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self.members = {}  # upper-case path -> (offset, size, stored, kind)
        self._walk(0, "", 0)

    def _walk(self, at: int, prefix: str, depth: int):
        d = self.data
        if depth > 32 or at + 8 > len(d):
            raise FormatError(f"{self.path}: bad directory node at {at}")
        count, pool_len = struct.unpack_from("<II", d, at)
        pool_at = at + 8 + 24 * count
        pool = d[pool_at:pool_at + pool_len]
        for i in range(count):
            off, size, stored, kind, nlen, noff = struct.unpack_from("<6I", d, at + 8 + 24 * i)
            name = pool[noff:noff + nlen].split(b"\0")[0].decode("latin-1")
            path = f"{prefix}{name}"
            if kind == KIND_DIR:
                self._walk(off, path + "/", depth + 1)
            else:
                self.members[path.upper()] = (off, size, stored, kind)

    def read(self, path: str) -> bytes:
        off, size, stored, kind = self.members[path.upper()]
        raw = self.data[off:off + stored]
        return zlib.decompress(raw) if kind == KIND_ZLIB else raw

    def has(self, path: str) -> bool:
        return path.upper() in self.members


class PaintLibrary:
    """The .BM skins of crimson.rof (with crimptch.rof applied), by pattern and skin name."""

    def __init__(self, install_dir: str):
        assets = os.path.join(install_dir, "GOSDATA", "ASSETS")
        self.archives = []
        for name in ("crimptch.rof", "crimson.rof"):  # patch first: it overrides
            p = os.path.join(assets, name)
            if os.path.isfile(p):
                self.archives.append(RofArchive(p))
        if not self.archives:
            raise FileNotFoundError(f"no crimson.rof under {assets}")
        self._cache = {}

    def _find(self, path):
        for a in self.archives:
            if a.has(path):
                return a
        return None

    def patterns_for(self, prefix: str):
        """Patterns that ship skins for an aircraft prefix, in the engine's pattern order."""
        out = []
        for pat in PATTERNS:
            head = f"ASSETS/GRAPHICS/{pat.upper()}/{prefix.upper()}_"
            if any(k.startswith(head) and k.endswith(".BM") for a in self.archives for k in a.members):
                out.append(pat)
        return out

    def skin(self, pattern: str, texture_name: str):
        """(shading RGB, weights (h,w,3), overlay RGBA) top-row-first float32 arrays, or None."""
        stem = os.path.splitext(texture_name)[0].lower()
        stem = BM_ALIAS.get(stem, stem)
        key = (pattern.lower(), stem)
        if key in self._cache:
            return self._cache[key]
        path = f"ASSETS/GRAPHICS/{pattern.upper()}/{stem.upper()}.BM"
        a = self._find(path)
        res = None
        if a is not None:
            res = decode_bm(a.read(path))
        self._cache[key] = res
        return res


def decode_bm(data: bytes):
    h, w = struct.unpack_from("<HH", data, 0)
    n = w * h
    if len(data) < 4 + 10 * n:
        raise FormatError("truncated .BM")
    p = np.frombuffer(data, dtype=np.uint8, offset=4, count=10 * n)
    shade = p[:3 * n].reshape(h, w, 3)[::-1]
    weights = np.stack([p[3 * n + k * n:3 * n + (k + 1) * n].reshape(h, w)[::-1] for k in range(3)], axis=-1)
    overlay = p[6 * n:10 * n].reshape(h, w, 4)[::-1]
    return shade, weights, overlay


def paint(skin, colours, base_alpha=None) -> np.ndarray:
    """RGBA uint8 (top row first): the original's per-texel paint plus the overlay."""
    shade, weights, overlay = skin
    c = np.asarray(colours, dtype=np.float32).reshape(3, 3)  # 0..255
    tint = weights.astype(np.float32) @ c / 255.0  # (h, w, 3), 0..255
    out = shade.astype(np.float32) * tint / 255.0
    a = overlay[..., 3:4].astype(np.float32) / 255.0
    out = out * (1.0 - a) + overlay[..., :3].astype(np.float32) * a
    rgba = np.empty(shade.shape[:2] + (4,), dtype=np.uint8)
    rgba[..., :3] = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    if base_alpha is not None and base_alpha.shape == shade.shape[:2]:
        rgba[..., 3] = base_alpha
    else:
        rgba[..., 3] = 255
    return rgba
