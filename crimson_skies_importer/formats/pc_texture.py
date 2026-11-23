"""Crimson Skies (PC) texture archives: texture.zbd, rtextureN.zbd, rimage.zbd.

Port of CSVM's TextureZbd.cs (docs/formats/texture-archive.md). A 24-byte header, N 40-byte
entries (name[32], record offset, global palette index), P global palettes, then each
texture's 16-byte header and pixels. Pixels are RGB565 or 8-bit palette indices, with an
optional 8-bit alpha plane. Rows are stored top-down.
"""

from __future__ import annotations

import os

import numpy as np

from .binreader import FormatError, Reader, rename_unique

_W5 = np.array([int(v * 255.0 / 31 + 0.5) for v in range(32)], dtype=np.uint8)
_W6 = np.array([int(v * 255.0 / 63 + 0.5) for v in range(64)], dtype=np.uint8)


def rgb565_to_rgb(c: np.ndarray) -> np.ndarray:
    c = c.astype(np.uint16)
    out = np.empty(c.shape + (3,), dtype=np.uint8)
    out[..., 0] = _W5[c >> 11]
    out[..., 1] = _W6[(c >> 5) & 0x3F]
    out[..., 2] = _W5[c & 0x1F]
    return out


class TextureEntry:
    __slots__ = ("name", "key", "offset", "palette", "archive")


class DecodedTexture:
    __slots__ = ("name", "width", "height", "rgba", "alpha_class", "render_flags")

    def __init__(self, name, width, height, rgba, alpha_class, render_flags):
        self.name = name
        self.width = width
        self.height = height
        self.rgba = rgba  # uint8 array (h, w, 4), top row first
        self.alpha_class = alpha_class  # "None", "Simple", "Full"
        self.render_flags = render_flags

    @property
    def has_alpha(self):
        return self.alpha_class != "None"


class TextureArchive:
    """One archive file, its table read eagerly and its pixels decoded on demand."""

    def __init__(self, path: str, data: bytes | None = None):
        self.path = path
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        self.data = data
        r = Reader(data)
        if len(data) < 24 or r.i32_at(0) != 0 or r.i32_at(4) != 1:
            raise FormatError(f"{path}: not a texture archive")
        pal_count, count = r.i32_at(8), r.i32_at(12)
        table_end = 24 + 40 * count
        self.palettes = []
        for p in range(pal_count):
            raw = np.frombuffer(data, dtype="<u2", count=256, offset=table_end + p * 512)
            self.palettes.append(rgb565_to_rgb(raw))
        self.entries = {}
        self.order = []
        seen = set()
        for i in range(count):
            at = 24 + i * 40
            e = TextureEntry()
            e.name = r.cstr_at(at, 32)
            e.key = rename_unique(seen, e.name)
            e.offset = r.i32_at(at + 32)
            e.palette = r.i32_at(at + 36)
            e.archive = self
            self.order.append(e)
            self.entries.setdefault(e.key.lower(), e)

    def names(self):
        return [e.key for e in self.order]

    def find(self, name: str):
        return self.entries.get(name.lower())

    def decode(self, entry: TextureEntry) -> DecodedTexture:
        d = self.data
        at = entry.offset
        flags = d[at]
        w = int.from_bytes(d[at + 4:at + 6], "little")
        h = int.from_bytes(d[at + 6:at + 8], "little")
        pal_count = int.from_bytes(d[at + 12:at + 14], "little")
        render_flags = int.from_bytes(d[at + 14:at + 16], "little")
        if flags & 0x04:
            alpha_class = "None"
        elif flags & 0x08:
            alpha_class = "Full"
        else:
            alpha_class = "Simple"
        full = alpha_class == "Full"
        n = w * h
        pos = at + 16
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        if pal_count == 0:
            c = np.frombuffer(d, dtype="<u2", count=n, offset=pos).reshape(h, w)
            pos += n * 2
            rgba[..., :3] = rgb565_to_rgb(c)
            if full:
                rgba[..., 3] = np.frombuffer(d, dtype=np.uint8, count=n, offset=pos).reshape(h, w)
            elif alpha_class == "Simple":
                rgba[..., 3] = np.where(c == 0, 0, 255).astype(np.uint8)
            else:
                rgba[..., 3] = 255
        else:
            idx = np.frombuffer(d, dtype=np.uint8, count=n, offset=pos).reshape(h, w)
            pos += n
            plane = None
            if full:
                plane = np.frombuffer(d, dtype=np.uint8, count=n, offset=pos).reshape(h, w)
                pos += n
            if entry.palette >= 0 and entry.palette < len(self.palettes):
                pal = self.palettes[entry.palette]
            else:
                raw = np.frombuffer(d, dtype="<u2", count=pal_count, offset=pos)
                pal = rgb565_to_rgb(raw)
            idx = np.minimum(idx, len(pal) - 1)
            rgba[..., :3] = pal[idx]
            rgba[..., 3] = plane if plane is not None else 255
            if not full:
                alpha_class = "None"  # paletted Simple textures decode opaque (see the docs)
        return DecodedTexture(entry.key, w, h, rgba, alpha_class, render_flags)


class TextureLibrary:
    """Looks textures up by name across several archives, first archive wins."""

    def __init__(self):
        self.archives = []
        self._cache = {}

    def add(self, path: str):
        if path and os.path.isfile(path):
            try:
                self.archives.append(TextureArchive(path))
            except FormatError:
                pass

    def find(self, name: str):
        if not name:
            return None
        key = name.lower()
        if key in self._cache:
            return self._cache[key]
        candidates = [key]
        # gamez names carry the file extension ("sky1.tif"); archive names do not.
        dot = key.rfind(".")
        if dot > 0:
            candidates.append(key[:dot])
            if key[:dot].endswith("."):
                candidates.append(key[:dot - 1])
        result = None
        for arch in self.archives:
            for c in candidates:
                e = arch.find(c)
                if e is not None:
                    result = arch.decode(e)
                    break
            if result is not None:
                break
        self._cache[key] = result
        return result


def chapter_archives(install_dir: str, chapter: str | None, prefer_tier: str = "base"):
    """Texture archive paths for a chapter, best first: texture.zbd (full quality) then tiers."""
    zbd = os.path.join(install_dir, "ZBD")
    out = []
    if chapter:
        cdir = os.path.join(zbd, chapter)
        if os.path.isdir(cdir):
            base = os.path.join(cdir, "texture.zbd")
            tiers = sorted(
                (f for f in os.listdir(cdir) if f.lower().startswith("rtexture") and f.lower().endswith(".zbd")),
                key=lambda f: -int("".join(ch for ch in f if ch.isdigit()) or 0),
            )
            if prefer_tier == "base":
                out.append(base)
                out += [os.path.join(cdir, t) for t in tiers]
            else:
                out += [os.path.join(cdir, t) for t in tiers]
                out.append(base)
    return [p for p in out if os.path.isfile(p)]


def all_chapters(install_dir: str):
    zbd = os.path.join(install_dir, "ZBD")
    if not os.path.isdir(zbd):
        return []
    return sorted(d for d in os.listdir(zbd) if os.path.isdir(os.path.join(zbd, d)) and
                  os.path.isfile(os.path.join(zbd, d, "gamez.zbd")))
