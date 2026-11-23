"""Small bounds-checked little-endian reader shared by the format modules (no bpy)."""

from __future__ import annotations

import struct

_U8 = struct.Struct("<B")
_U16 = struct.Struct("<H")
_I16 = struct.Struct("<h")
_U32 = struct.Struct("<I")
_I32 = struct.Struct("<i")
_F32 = struct.Struct("<f")


class FormatError(ValueError):
    pass


class Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def __len__(self):
        return len(self.data)

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def need(self, n: int, what: str = "data"):
        if n < 0 or self.pos + n > len(self.data):
            raise FormatError(f"{what}: read of {n} bytes past the end at offset {self.pos}")

    def skip(self, n: int, what: str = "data"):
        self.need(n, what)
        self.pos += n

    def take(self, n: int, what: str = "data") -> bytes:
        self.need(n, what)
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        self.need(1)
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        self.need(2)
        v = _U16.unpack_from(self.data, self.pos)[0]
        self.pos += 2
        return v

    def i16(self) -> int:
        self.need(2)
        v = _I16.unpack_from(self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        self.need(4)
        v = _U32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self) -> int:
        self.need(4)
        v = _I32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self) -> float:
        self.need(4)
        v = _F32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return v

    def floats(self, n: int) -> tuple:
        self.need(4 * n)
        v = struct.unpack_from(f"<{n}f", self.data, self.pos)
        self.pos += 4 * n
        return v

    def u16s(self, n: int) -> tuple:
        self.need(2 * n)
        v = struct.unpack_from(f"<{n}H", self.data, self.pos)
        self.pos += 2 * n
        return v

    def i32s(self, n: int) -> tuple:
        self.need(4 * n)
        v = struct.unpack_from(f"<{n}i", self.data, self.pos)
        self.pos += 4 * n
        return v

    # fixed-offset peeks (absolute offsets)
    def u8_at(self, at: int) -> int:
        return self.data[at]

    def u16_at(self, at: int) -> int:
        return _U16.unpack_from(self.data, at)[0]

    def u32_at(self, at: int) -> int:
        return _U32.unpack_from(self.data, at)[0]

    def i32_at(self, at: int) -> int:
        return _I32.unpack_from(self.data, at)[0]

    def f32_at(self, at: int) -> float:
        return _F32.unpack_from(self.data, at)[0]

    def floats_at(self, at: int, n: int) -> tuple:
        return struct.unpack_from(f"<{n}f", self.data, at)

    def cstr_at(self, at: int, maxlen: int) -> str:
        raw = self.data[at:at + maxlen]
        end = raw.find(b"\0")
        if end >= 0:
            raw = raw[:end]
        return raw.decode("latin-1")

    def lstr(self) -> str:
        """u32 length (including the NUL) followed by the bytes."""
        n = self.u32()
        if n > 4096:
            raise FormatError(f"implausible string length {n} at offset {self.pos - 4}")
        raw = self.take(n, "string")
        end = raw.find(b"\0")
        if end >= 0:
            raw = raw[:end]
        return raw.decode("latin-1")


def rename_unique(seen: set, name: str) -> str:
    """The collision rule shared by the PC archives: a repeat becomes stem-N.ext."""
    if name not in seen:
        seen.add(name)
        return name
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    n = 1
    while f"{stem}-{n}{ext}" in seen:
        n += 1
    new = f"{stem}-{n}{ext}"
    seen.add(new)
    return new
