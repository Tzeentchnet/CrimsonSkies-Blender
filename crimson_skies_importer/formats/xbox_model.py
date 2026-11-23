"""Crimson Skies: High Road to Revenge (Xbox) compiled .x models (package entry type 1).

Reverse-engineered from the disc (every model of the shipped packages parses to its exact end):

file    u32 version (0, 2 = skinned), f32[6] bounds, u32 flags (0x100 lightmapped instance), node
node    u32 name length, name, f32[12] 4x3 matrix (rows: X, Y, Z axes, translation; D3D row
        vectors), f32[6] bounds, u32 kind, u32 mesh count, mesh*, u32 child count, node*
mesh    u32 shader flags, u32 colour (ARGB), u8 vertex format, u8 b1, u8 b2, u32 anim rate,
        u8 texture mask, one u32-length string per set mask bit (slot 0..7),
        u16 vertex count, vertices x 32 bytes:
            f32 position[3], u32 normal (NORMPACKED3 11:11:10), s16 uv[4][2]
        f32 uv scale[4] (uv = s16 * scale, per set),
        u8 has tangent frames, [u16 count, count x 48 bytes],
        u16 index count, u16 indices (triangle list), u16 ?, u16 n, u16[n] (strip/remap list),
        u8 has bounding tree, [u32 nodes, nodes x 16 bytes],
        version 2 only: u16 count, count x {f32 weight[3], f32 register[4]}
version 2 files end with a per-mesh joint table {u32 count, count x {u32 node index (depth-first
over the whole tree), f32[16] inverse bind matrix}} after a run of padding.
A weight's register r names joint slot (r - 3) / 3 of its mesh's table.
Coordinates are Direct3D's: left-handed, Y up.
"""

from __future__ import annotations

import struct

import numpy as np

from .binreader import FormatError, Reader

VERTEX_SIZE = 32


class XMesh:
    __slots__ = ("shader", "color", "fmt", "b1", "b2", "anim_rate", "textures", "positions", "normals",
                 "uvs", "indices", "weights", "registers", "joints", "end", "uv_sets")

    def __init__(self):
        self.textures = {}  # slot -> name
        self.weights = None
        self.registers = None
        self.joints = None  # [(node index, 4x4 inverse bind)]


class XNode:
    __slots__ = ("name", "matrix", "bounds", "kind", "meshes", "children", "index", "parent")

    def __init__(self):
        self.meshes = []
        self.children = []
        self.parent = None


class XModel:
    __slots__ = ("name", "version", "flags", "bounds", "root", "nodes")

    def all_meshes(self):
        for n in self.nodes:
            for m in n.meshes:
                yield n, m


def _decode_normals(packed: np.ndarray) -> np.ndarray:
    p = packed.astype(np.uint32)
    x = (p & 0x7FF).astype(np.int32)
    y = ((p >> 11) & 0x7FF).astype(np.int32)
    z = ((p >> 22) & 0x3FF).astype(np.int32)
    x = np.where(x >= 1024, x - 2048, x) / 1023.0
    y = np.where(y >= 1024, y - 2048, y) / 1023.0
    z = np.where(z >= 512, z - 1024, z) / 511.0
    return np.stack([x, y, z], axis=-1).astype(np.float32)


def read_mesh(r: Reader, skinned: bool) -> XMesh:
    m = XMesh()
    start = r.pos
    m.shader = r.u32()
    m.color = r.u32()
    m.fmt, m.b1, m.b2 = r.u8(), r.u8(), r.u8()
    m.anim_rate = r.u32()
    mask = r.u8()
    for slot in range(8):
        if mask >> slot & 1:
            m.textures[slot] = r.lstr()
    nv = r.u16()
    raw = np.frombuffer(r.take(nv * VERTEX_SIZE, "vertices"), dtype=np.uint8).reshape(nv, VERTEX_SIZE)
    scales = r.floats(4)
    m.positions = raw[:, 0:12].copy().view("<f4").reshape(nv, 3)
    m.normals = _decode_normals(raw[:, 12:16].copy().view("<u4").reshape(nv))
    uv = raw[:, 16:32].copy().view("<i2").reshape(nv, 4, 2).astype(np.float32)
    uv *= np.asarray(scales, dtype=np.float32)[None, :, None]
    m.uvs = uv
    m.uv_sets = [k for k in range(4) if nv and np.any(raw[:, 16 + 4 * k:20 + 4 * k])]
    if r.u8():
        if r.u16() != nv:
            raise FormatError(f"tangent count mismatch at {r.pos}")
        r.skip(48 * nv)
    ni = r.u16()
    idx = np.asarray(r.u16s(ni), dtype=np.int32)
    if ni % 3 or (ni and idx.max() >= nv):
        raise FormatError(f"bad index list at {r.pos}")
    m.indices = idx.reshape(-1, 3)
    r.u16()
    r.skip(2 * r.u16())
    if r.u8():
        n = r.u32()
        if n > 1_000_000:
            raise FormatError("implausible bounding tree")
        r.skip(16 * n)
    if skinned:
        nw = r.u16()
        w = np.frombuffer(r.take(28 * nw, "weights"), dtype="<f4").reshape(nw, 7)
        m.weights = w[:, :3].copy()
        m.registers = w[:, 3:7].copy()
    m.end = r.pos
    _ = start
    return m


def try_read_mesh(data: bytes, offset: int, skinned=False):
    """A mesh record at offset, or None when the bytes there are not one."""
    try:
        r = Reader(data, offset)
        shader = r.u32_at(offset)
        mask = data[offset + 15]
        if shader > 512 or mask == 0 or mask > 0x7F:
            return None
        m = read_mesh(r, skinned)
        if not all(t.lower().endswith((".tga", ".dds")) for t in m.textures.values()):
            return None
        return m
    except (FormatError, struct.error, ValueError, IndexError):
        return None


def _read_node(r: Reader, model: XModel, skinned: bool, depth=0) -> XNode:
    if depth > 200:
        raise FormatError("node tree too deep")
    n = XNode()
    n.index = len(model.nodes)
    model.nodes.append(n)
    n.name = r.lstr()
    n.matrix = r.floats(12)
    n.bounds = r.floats(6)
    n.kind = r.u32()
    for _ in range(r.u32()):
        n.meshes.append(read_mesh(r, skinned))
    count = r.u32()
    if count > 10000:
        raise FormatError(f"implausible child count at {r.pos}")
    for _ in range(count):
        c = _read_node(r, model, skinned, depth + 1)
        c.parent = n
        n.children.append(c)
    return n


def read(data: bytes, name: str = "") -> XModel:
    r = Reader(data)
    model = XModel()
    model.name = name
    model.version = r.u32()
    model.bounds = r.floats(6)
    model.flags = r.u32()
    model.nodes = []
    skinned = model.version == 2
    model.root = _read_node(r, model, skinned)
    if skinned:
        _read_joint_tables(data, r.pos, model)
    elif r.pos != len(data):
        raise FormatError(f"model {name}: {len(data) - r.pos} unread bytes")
    return model


def _read_joint_tables(data: bytes, pos: int, model: XModel):
    meshes = [m for _n, m in model.all_meshes()]
    count = len(meshes)

    def chain(p):
        tables = []
        for _ in range(count):
            if p + 4 > len(data):
                return None
            c = struct.unpack_from("<I", data, p)[0]
            if c > 256 or p + 4 + 68 * c > len(data):
                return None
            p += 4
            t = []
            for _j in range(c):
                idx = struct.unpack_from("<I", data, p)[0]
                mat = struct.unpack_from("<16f", data, p + 4)
                t.append((idx, mat))
                p += 68
            tables.append(t)
        return tables if p == len(data) else None

    for start in range(pos, min(len(data), pos + 65536), 4):
        tables = chain(start)
        if tables is not None:
            for m, t in zip(meshes, tables):
                m.joints = t
            return
    raise FormatError(f"model {model.name}: joint tables not found")
