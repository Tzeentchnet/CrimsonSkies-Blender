"""Crimson Skies: High Road to Revenge (Xbox) resource packages and where they live.

A package (.bin: level, aircraft, menu) is
    header   {u32 ?, u32 count} (aircraft)  or  {u32 ?, u32 150, u32 count, u32 meshes} (levels)
    entries  count x {u32 type, u32 name length, name, u32 size, data}
             type 1 compiled .x model, 2 texture, 3 cube/volume .dds, 4 .anm animation,
             5 .cin cinematic, 6 sound, 7 light/device state
    world    (levels only) prop-name table, scatter grids, paths, then the terrain and water
             chunks: each a small wrapper around one mesh record (see xbox_model.read_mesh).
The disc keeps aircraft packages inside airplanes.zip (stored, uncompressed) and the levels as
loose .bin files at the root; this module reads either straight from the .iso.
"""

from __future__ import annotations

import fnmatch
import io
import os
import re
import struct
import zipfile

from .binreader import FormatError
from .xiso import XisoImage

TYPE_MODEL, TYPE_TEXTURE, TYPE_CUBE, TYPE_ANIM, TYPE_CINEMATIC, TYPE_SOUND, TYPE_LIGHT = 1, 2, 3, 4, 5, 6, 7
TYPE_NAMES = {1: "model", 2: "texture", 3: "cubemap", 4: "animation", 5: "cinematic", 6: "sound", 7: "light"}


class Entry:
    __slots__ = ("type", "name", "offset", "size", "package")

    def data(self) -> bytes:
        return self.package.data[self.offset:self.offset + self.size]

    @property
    def base_name(self):
        return self.name.split("|", 1)[0]

    @property
    def instance_name(self):
        return self.name.split("|", 1)[1] if "|" in self.name else None

    def __repr__(self):
        return f"Entry({TYPE_NAMES.get(self.type, self.type)}, {self.name!r}, {self.size})"


class Package:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self.data = data
        self.entries = []
        self.world_offset = None
        self.declared_meshes = 0
        self._parse()

    def _parse(self):
        d = self.data
        if len(d) < 16:
            raise FormatError(f"{self.name}: too short for a package")
        h = struct.unpack_from("<4I", d, 0)
        if h[1] == 150:
            count, off = h[2], 16
            self.declared_meshes = h[3]
        else:
            count, off = h[1], 8
        for _ in range(count):
            if off + 12 > len(d):
                raise FormatError(f"{self.name}: entry table runs past the end")
            t, nl = struct.unpack_from("<II", d, off)
            if not (1 <= t <= 32) or not (0 < nl < 512):
                raise FormatError(f"{self.name}: bad entry at {off}")
            name = d[off + 8:off + 8 + nl].split(b"\0")[0].decode("latin-1")
            size = struct.unpack_from("<I", d, off + 8 + nl)[0]
            e = Entry()
            e.type, e.name, e.offset, e.size, e.package = t, name, off + 12 + nl, size, self
            self.entries.append(e)
            off = e.offset + size
        if off < len(d):
            self.world_offset = off

    def of_type(self, t):
        return [e for e in self.entries if e.type == t]

    def find(self, name: str, t=None):
        n = name.lower()
        for e in self.entries:
            if (t is None or e.type == t) and e.name.lower() == n:
                return e
        return None

    def textures(self):
        out = {}
        for e in self.entries:
            if e.type == TYPE_TEXTURE:
                out.setdefault(e.name.lower(), e)
        return out

    @property
    def is_level(self):
        return self.world_offset is not None

    def world_props(self):
        """The world section's prop-name table (scatter models)."""
        if self.world_offset is None:
            return []
        d, o = self.data, self.world_offset
        n = struct.unpack_from("<I", d, o)[0]
        if n > 4096:
            return []
        o += 4
        names = []
        for _ in range(n):
            ln = struct.unpack_from("<I", d, o)[0]
            names.append(d[o + 4:o + 4 + ln].split(b"\0")[0].decode("latin-1"))
            o += 4 + ln
        return names


_TEX_ANCHOR = re.compile(rb"[\x05-\x80]\x00\x00\x00[ -~]{1,120}\.(?:tga|dds|TGA|DDS)\x00")
_CHUNK_NAME = re.compile(rb"([\x02-\x40])\x00\x00\x00([A-Za-z_][ -~]{0,62})\x00")


def world_meshes(pkg: Package):
    """Yields (chunk name, mesh) for the terrain/water chunks of a level's world section.

    The chunk wrappers are not decoded; each mesh record is found by validating the whole mesh
    structure at every texture-name anchor, which the 590 chunks of SH_Tutorial (the count the
    header declares) all pass and nothing else does."""
    from . import xbox_model

    if pkg.world_offset is None:
        return
    d = pkg.data
    base = pkg.world_offset
    view = memoryview(d)[base:]
    last_end = 0
    for m in _TEX_ANCHOR.finditer(view):
        o = m.start() - 16
        if o < last_end:
            continue
        mesh = xbox_model.try_read_mesh(d, base + o, skinned=False)
        if mesh is None:
            continue
        # the chunk's own name is the last short string before the mesh record
        lo = max(last_end, o - 160)
        name = "chunk"
        for nm in _CHUNK_NAME.finditer(view[lo:o]):
            if nm.group(1)[0] == len(nm.group(2)) + 1:
                name = nm.group(2).decode("latin-1")
        last_end = mesh.end - base
        yield name, mesh


# ------------------------------------------------------------------------------------- sources

class Source:
    """A place packages come from: a disc image, a zip, a single .bin, or a folder of files."""

    def __init__(self, path: str):
        self.path = path
        self.iso = None
        self.zip = None
        self.folder = None
        low = path.lower()
        if os.path.isdir(path):
            self.folder = path
        elif low.endswith((".iso", ".xiso")):
            self.iso = XisoImage(path)
        elif low.endswith(".zip"):
            self.zip = zipfile.ZipFile(path)
        self._zip_cache = {}

    def close(self):
        if self.iso:
            self.iso.close()

    # names: "SH_Tutorial.bin", "airplanes.zip/ca/res/PF_Bandito.bin"
    def package_names(self):
        names = []
        if self.iso:
            for e in self.iso.files():
                low = e.path.lower()
                if low.endswith(".bin") and "/" not in e.path and _looks_like_package(self.iso.read_head(e.path)):
                    names.append(e.path)
                elif low == "airplanes.zip":
                    z = self._open_zip(e.path)
                    names += [f"{e.path}/{n}" for n in z.namelist() if n.lower().endswith(".bin")]
        elif self.zip:
            names = [n for n in self.zip.namelist() if n.lower().endswith(".bin")]
        elif self.folder:
            for root, _dirs, files in os.walk(self.folder):
                for f in files:
                    if f.lower().endswith(".bin"):
                        p = os.path.join(root, f)
                        with open(p, "rb") as fh:
                            if _looks_like_package(fh.read(64)):
                                names.append(os.path.relpath(p, self.folder).replace("\\", "/"))
        else:
            names = [os.path.basename(self.path)]
        return sorted(names, key=str.lower)

    def _open_zip(self, iso_path):
        z = self._zip_cache.get(iso_path.lower())
        if z is None:
            z = zipfile.ZipFile(io.BytesIO(self.iso.read(iso_path)))
            self._zip_cache[iso_path.lower()] = z
        return z

    def read(self, name: str) -> bytes:
        if self.iso:
            low = name.lower()
            idx = low.find(".zip/")
            if idx >= 0:
                z = self._open_zip(name[:idx + 4])
                return z.read(_zip_member(z, name[idx + 5:]))
            return self.iso.read(name)
        if self.zip:
            return self.zip.read(_zip_member(self.zip, name))
        if self.folder:
            return open(os.path.join(self.folder, name), "rb").read()
        return open(self.path, "rb").read()

    def package(self, name: str) -> Package:
        return Package(name, self.read(name))

    def resolve(self, pattern: str):
        """Package names matching a name, a basename or a wildcard pattern."""
        names = self.package_names()
        pats = [p.strip() for p in pattern.split(",") if p.strip()]
        out = []
        for p in pats:
            pl = p.lower()
            for n in names:
                nl = n.lower()
                base = nl.rsplit("/", 1)[-1]
                if nl == pl or base == pl or base == pl + ".bin" or fnmatch.fnmatch(base, pl) or fnmatch.fnmatch(nl, pl):
                    if n not in out:
                        out.append(n)
        return out


def _zip_member(z, name):
    low = name.lower()
    for n in z.namelist():
        if n.lower() == low or n.lower().endswith("/" + low):
            return n
    raise FileNotFoundError(name)


def _looks_like_package(head: bytes) -> bool:
    if len(head) < 24:
        return False
    h = struct.unpack_from("<4I", head, 0)
    off = 16 if h[1] == 150 else 8
    if off + 8 > len(head):
        return False
    t, nl = struct.unpack_from("<II", head, off)
    return 1 <= t <= 7 and 0 < nl < 256


def _iso_read_head(self, path, n=64):
    e = self.entries.get(path.replace("\\", "/").lower())
    if e is None:
        return b""
    return self._read_at(e.sector, 0, min(n, e.size))


XisoImage.read_head = _iso_read_head
