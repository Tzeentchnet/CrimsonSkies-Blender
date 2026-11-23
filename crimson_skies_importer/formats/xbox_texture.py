"""Crimson Skies: High Road to Revenge (Xbox) texture entries (package entry type 2).

Header, 11 bytes:
    u16 width, u16 height, u8 mip count, u8 flags, u8 flags2, u32 format, then per mip
    {u32 byte size, data}.
Format codes seen on the disc (bytes per pixel of the top mip in brackets):
    1  DXT1 (0.5)          3  DXT3-style (1)       5  DXT5 (1)
    6  16-bit, linear rows padded to 64 bytes (alpha "~a" masks)
    14 A4R4G4B4 swizzled (2)   15/16/20 A8R8G8B8 swizzled (4)   17 16-bit linear (2)
    19 P8 swizzled, 256 x BGRA palette after the mips (lightmap atlases)
Uncompressed Xbox textures are stored swizzled (Morton order) unless flagged linear.
"""

from __future__ import annotations

import struct

import numpy as np

from .binreader import FormatError


class XTexture:
    __slots__ = ("name", "width", "height", "mips", "format", "flags", "rgba", "has_alpha")


def _unpack565(c):
    c = c.astype(np.uint32)
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], axis=-1).astype(np.int32)


def _dxt_color_blocks(blocks: np.ndarray, dxt1: bool):
    """blocks: (N, 8) uint8 colour halves -> (N, 16, 4) RGBA uint8."""
    c0 = blocks[:, 0].astype(np.uint16) | (blocks[:, 1].astype(np.uint16) << 8)
    c1 = blocks[:, 2].astype(np.uint16) | (blocks[:, 3].astype(np.uint16) << 8)
    bits = (blocks[:, 4].astype(np.uint32) | (blocks[:, 5].astype(np.uint32) << 8) |
            (blocks[:, 6].astype(np.uint32) << 16) | (blocks[:, 7].astype(np.uint32) << 24))
    p0, p1 = _unpack565(c0), _unpack565(c1)
    four = (c0 > c1) | (not dxt1)
    four = np.asarray(four)
    if four.ndim == 0:
        four = np.full(len(blocks), bool(four))
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], axis=1)  # (N, 4, 3)
    alpha = np.full((len(blocks), 4), 255, dtype=np.int32)
    if dxt1:
        alpha[~four, 3] = 0
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None, :]) & 3  # (N, 16)
    rows = np.arange(len(blocks))[:, None]
    rgb = pal[rows, idx]  # (N, 16, 3)
    a = alpha[rows, idx]
    return np.concatenate([rgb, a[..., None]], axis=-1).astype(np.uint8)


def _dxt5_alpha(blocks: np.ndarray):
    a0 = blocks[:, 0].astype(np.int32)
    a1 = blocks[:, 1].astype(np.int32)
    bits = np.zeros(len(blocks), dtype=np.uint64)
    for i in range(6):
        bits |= blocks[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    idx = ((bits[:, None] >> (3 * np.arange(16, dtype=np.uint64))[None, :]) & np.uint64(7)).astype(np.int32)
    pal = np.zeros((len(blocks), 8), dtype=np.int32)
    pal[:, 0], pal[:, 1] = a0, a1
    big = a0 > a1
    for i in range(1, 7):
        pal[:, 1 + i] = np.where(big, ((7 - i) * a0 + i * a1) // 7, 0)
    for i in range(1, 5):
        pal[:, 1 + i] = np.where(big, pal[:, 1 + i], ((5 - i) * a0 + i * a1) // 5)
    pal[:, 6] = np.where(big, pal[:, 6], 0)
    pal[:, 7] = np.where(big, pal[:, 7], 255)
    return pal[np.arange(len(blocks))[:, None], idx].astype(np.uint8)


def _dxt3_alpha(blocks: np.ndarray):
    nib = np.empty((len(blocks), 16), dtype=np.uint8)
    nib[:, 0::2] = blocks[:, :8] & 0x0F
    nib[:, 1::2] = blocks[:, :8] >> 4
    return nib * 17


def decode_dxt(data: bytes, w: int, h: int, kind: str) -> np.ndarray:
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    bsize = 8 if kind == "DXT1" else 16
    need = bw * bh * bsize
    raw = np.frombuffer(data, dtype=np.uint8, count=min(need, len(data)))
    if len(raw) < need:
        raw = np.concatenate([raw, np.zeros(need - len(raw), dtype=np.uint8)])
    blk = raw.reshape(bw * bh, bsize)
    if kind == "DXT1":
        px = _dxt_color_blocks(blk, True)
    else:
        px = _dxt_color_blocks(blk[:, 8:], False)
        px[:, :, 3] = _dxt5_alpha(blk[:, :8]) if kind == "DXT5" else _dxt3_alpha(blk[:, :8])
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(img[:h, :w])


def _morton_table(n: int, bit_offset: int, width_bits: int, height_bits: int):
    pass


def unswizzle(raw: np.ndarray, w: int, h: int) -> np.ndarray:
    """Xbox (NV2A) swizzle: texel (x, y) lives at the interleave of x and y bits, x first,
    for the bits both dimensions share; leftover bits of the longer side come after."""
    xs = np.arange(w, dtype=np.int64)
    ys = np.arange(h, dtype=np.int64)
    ox = np.zeros(w, dtype=np.int64)
    oy = np.zeros(h, dtype=np.int64)
    bit = 0
    xb = yb = 0
    wb, hb = (w - 1).bit_length(), (h - 1).bit_length()
    while xb < wb or yb < hb:
        if xb < wb:
            ox |= ((xs >> xb) & 1) << bit
            xb += 1
            bit += 1
        if yb < hb:
            oy |= ((ys >> yb) & 1) << bit
            yb += 1
            bit += 1
    idx = oy[:, None] + ox[None, :]
    return raw[idx]


def _argb8888(raw: np.ndarray):
    # memory order B, G, R, A
    return np.stack([raw[..., 2], raw[..., 1], raw[..., 0], raw[..., 3]], axis=-1)


def _argb4444(v: np.ndarray):
    v = v.astype(np.uint16)
    return np.stack([((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17, ((v >> 12) & 15) * 17],
                    axis=-1).astype(np.uint8)


def _argb1555(v: np.ndarray):
    v = v.astype(np.uint32)
    return np.stack([((v >> 10) & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31, (v & 31) * 255 // 31,
                     np.where(v & 0x8000, 255, 0)], axis=-1).astype(np.uint8)


def _rgb565(v: np.ndarray):
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31,
                     np.full(v.shape, 255)], axis=-1).astype(np.uint8)


FORMATS = {
    1: ("DXT1", 0.5),
    3: ("DXT3", 1),
    5: ("DXT5", 1),
    19: ("P8", 1),
    6: ("A8L8LIN", 2),
    14: ("RGB565", 2),
    17: ("RGB565LIN", 2),
    15: ("ARGB8888", 4),
    16: ("ARGB8888", 4),
    20: ("ARGB8888", 4),
}


def parse(data: bytes, name: str = "") -> XTexture:
    if len(data) < 15:
        raise FormatError(f"texture {name}: too short")
    w, h = struct.unpack_from("<HH", data, 0)
    mips, f1, f2, fmt = data[4], data[5], data[6], data[7]
    size = struct.unpack_from("<I", data, 11)[0]
    payload = data[15:15 + size]
    t = XTexture()
    t.name, t.width, t.height, t.mips, t.format, t.flags = name, w, h, mips, fmt, (f1, f2)
    kind = FORMATS.get(fmt, (None, 0))[0]
    if kind is None:
        # unknown: guess from the size
        bpp = size / max(1, w * h)
        kind = {0.5: "DXT1", 1: "DXT5", 2: "ARGB4444", 4: "ARGB8888"}.get(bpp)
        if kind is None:
            raise FormatError(f"texture {name}: unknown format {fmt}")
    if kind == "P8":
        # 8-bit swizzled indices; the 256-colour B,G,R,A palette follows the mip chain
        pal = np.frombuffer(data, dtype=np.uint8, count=1024, offset=len(data) - 1024).reshape(256, 4)
        idx = unswizzle(np.frombuffer(payload, dtype=np.uint8, count=w * h), w, h).reshape(h, w)
        rgba = _argb8888(pal[idx])
        rgba[..., 3] = 255
    elif kind.startswith("DXT"):
        rgba = decode_dxt(payload, w, h, kind)
    elif kind == "ARGB8888":
        raw = np.frombuffer(payload, dtype=np.uint8, count=w * h * 4).reshape(-1, 4)
        rgba = _argb8888(unswizzle(raw, w, h)).reshape(h, w, 4)
    elif kind == "ARGB4444":
        raw = np.frombuffer(payload, dtype="<u2", count=w * h)
        rgba = _argb4444(unswizzle(raw, w, h)).reshape(h, w, 4)
    elif kind == "RGB565":
        raw = np.frombuffer(payload, dtype="<u2", count=w * h)
        rgba = _rgb565(unswizzle(raw, w, h).reshape(h, w))
    elif kind in ("A8L8LIN", "RGB565LIN"):
        pitch = len(payload) // max(1, h)
        rows = np.frombuffer(payload, dtype=np.uint8, count=pitch * h).reshape(h, pitch)[:, :w * 2]
        v = rows.copy().view("<u2").reshape(h, w)
        if kind == "RGB565LIN":
            rgba = _rgb565(v)
        else:  # two 8-bit channels; the high one is the mask the "~a" maps carry
            m = (v >> 8).astype(np.uint8)
            rgba = np.stack([m, m, m, np.full(m.shape, 255, np.uint8)], axis=-1)
    else:
        raise FormatError(f"texture {name}: unsupported {kind}")
    t.rgba = rgba
    if rgba.ndim == 2:
        rgba = rgba.reshape(h, w, 4)
    t.rgba = rgba
    t.has_alpha = bool((rgba[..., 3] < 255).any())
    return t
