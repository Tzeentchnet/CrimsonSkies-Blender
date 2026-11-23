"""Read-only access to an Xbox XDVDFS disc image (.iso / .xiso), no extraction needed.

Layout: 2048-byte sectors. The volume descriptor sits at sector 32 of the game partition
("MICROSOFT*XBOX*MEDIA", root directory sector u32, root directory size u32). A trimmed
xiso starts the partition at byte 0; a full Redump image starts it at 0x18300000.

A directory is a binary tree of 4-byte-aligned entries:
    u16 left (dwords), u16 right (dwords), u32 sector, u32 size, u8 attributes,
    u8 name length, name bytes.
Attribute 0x10 marks a directory.
"""

from __future__ import annotations

import os
import struct

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
PARTITION_OFFSETS = (0, 0x18300000, 0x0FD90000, 0x02080000)
ATTR_DIRECTORY = 0x10


class XisoEntry:
    __slots__ = ("name", "sector", "size", "is_dir", "path")

    def __init__(self, name, sector, size, is_dir, path):
        self.name = name
        self.sector = sector
        self.size = size
        self.is_dir = is_dir
        self.path = path

    def __repr__(self):
        return f"XisoEntry({self.path!r}, size={self.size}, dir={self.is_dir})"


class XisoImage:
    """An open XDVDFS image. Paths use '/' and compare case-insensitively."""

    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "rb")
        self.base = None
        for off in PARTITION_OFFSETS:
            self._fh.seek(off + 32 * SECTOR)
            if self._fh.read(20) == MAGIC:
                self.base = off
                break
        if self.base is None:
            raise ValueError(f"{path}: not an XDVDFS image")
        self._fh.seek(self.base + 32 * SECTOR + 20)
        root_sector, root_size = struct.unpack("<II", self._fh.read(8))
        self.entries: dict[str, XisoEntry] = {}
        self._walk(root_sector, root_size, "")

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read_at(self, sector: int, offset: int, size: int) -> bytes:
        self._fh.seek(self.base + sector * SECTOR + offset)
        return self._fh.read(size)

    def _walk(self, sector: int, size: int, prefix: str):
        if size == 0:
            return
        table = self._read_at(sector, 0, size)
        seen = set()
        stack = [0]
        while stack:
            off = stack.pop()
            if off in seen or off + 14 > len(table):
                continue
            seen.add(off)
            left, right, sec, fsize, attrs, nlen = struct.unpack_from("<HHIIBB", table, off)
            if left == 0xFFFF and right == 0xFFFF:
                continue
            name = table[off + 14: off + 14 + nlen].decode("latin-1")
            path = f"{prefix}{name}"
            is_dir = bool(attrs & ATTR_DIRECTORY)
            self.entries[path.lower()] = XisoEntry(name, sec, fsize, is_dir, path)
            if is_dir:
                self._walk(sec, fsize, path + "/")
            if left and left != 0xFFFF:
                stack.append(left * 4)
            if right and right != 0xFFFF:
                stack.append(right * 4)

    def files(self):
        return sorted((e for e in self.entries.values() if not e.is_dir), key=lambda e: e.path.lower())

    def exists(self, path: str) -> bool:
        return path.replace("\\", "/").lower() in self.entries

    def read(self, path: str) -> bytes:
        e = self.entries.get(path.replace("\\", "/").lower())
        if e is None or e.is_dir:
            raise FileNotFoundError(path)
        return self._read_at(e.sector, 0, e.size)

    def extract(self, path: str, dest_dir: str) -> str:
        data = self.read(path)
        out = os.path.join(dest_dir, *path.replace("\\", "/").split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(data)
        return out


if __name__ == "__main__":
    import sys

    with XisoImage(sys.argv[1]) as iso:
        for e in iso.files():
            print(f"{e.size:12d}  {e.path}")
