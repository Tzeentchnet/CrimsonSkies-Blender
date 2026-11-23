"""Crimson Skies (PC, 2000) gamez.zbd / planes.zbd reader.

Port of CSVM's GameZZbd.cs (docs/formats/gamez-zbd.md in CrimsonSkies-csvm). Four regions:
a texture-name table, a material array, a sparse model array and a node array. Coordinates
are right-handed, Y-up, nose at -Z (Godot's frame); a polygon's visible side is the one its
corner loop runs counter-clockwise around.
"""

from __future__ import annotations

import math

from .binreader import FormatError, Reader

SIGNATURE = 0x02971222
VERSION = 42
HEADER_SIZE = 40
TEXTURE_ENTRY = 44
MATERIAL_ENTRY = 44  # 40-byte record + two u16 free-list links
MODEL_ENTRY = 104  # 100-byte record + data offset / free link
NODE_ENTRY = 212  # 208-byte record + tagged index

MODEL_TYPES = ("Default", "Facade", "Points")
FACADE_MODES = ("CylindricalY", "SphericalY", "CylindricalX", "CylindricalZ")
NODE_KINDS = {1: "Camera", 2: "World", 3: "Window", 4: "Display", 5: "Object3d", 6: "Lod", 9: "Light"}


class Material:
    __slots__ = ("texture", "color", "alpha", "soil", "cycle_textures", "cycle_speed", "cycle_loop", "flags")

    def __init__(self):
        self.texture = None  # texture name or None
        self.color = (1.0, 1.0, 1.0)
        self.alpha = 1.0
        self.soil = 0
        self.cycle_textures = []
        self.cycle_speed = 0.0
        self.cycle_loop = False
        self.flags = 0


class Polygon:
    __slots__ = ("verts", "normals", "material", "uvs", "colors", "overlays", "show_backface",
                 "priority", "strip", "no_clutter", "zone")

    def __init__(self):
        self.verts = []
        self.normals = None
        self.material = -1
        self.uvs = None
        self.colors = []
        self.overlays = None  # list of (material, uvs)
        self.show_backface = False
        self.priority = 0
        self.strip = False
        self.no_clutter = False
        self.zone = None


class Light:
    __slots__ = ("position", "color", "blink_period", "fade_far", "fade_slope")


class Mesh:
    __slots__ = ("index", "model_type", "facade_mode", "lit", "fog", "texture_scroll",
                 "vertices", "normals", "polygons", "lights")

    def __init__(self):
        self.vertices = []
        self.normals = []
        self.polygons = []
        self.lights = []


class Node:
    __slots__ = ("index", "name", "kind", "flags", "active", "zone", "mesh", "children", "parents",
                 "matrix", "rotation", "scale", "lod_range", "area", "partition_nodes", "has_transform")

    def __init__(self):
        self.mesh = -1
        self.children = []
        self.parents = []
        self.matrix = None  # 4x4 row-major (Blender convention: columns are basis axes)
        self.rotation = None
        self.scale = None
        self.lod_range = None
        self.area = None
        self.partition_nodes = None
        self.has_transform = False


class GameZ:
    def __init__(self):
        self.texture_names = []
        self.materials = []
        self.meshes = []
        self.nodes = []

    def roots(self):
        """Nodes nothing names as a child, in file order."""
        child = set()
        for n in self.nodes:
            child.update(c for c in n.children if 0 <= c < len(self.nodes))
        return [n for n in self.nodes if n.index not in child and n.kind != "Display"]


def _texture_name(raw: bytes) -> str:
    first = raw.find(b"\0")
    if first < 0:
        return raw.decode("latin-1")
    prefix = raw[:first].decode("latin-1")
    rest = raw[first + 1:]
    second = rest.find(b"\0")
    if second == 0 or len(rest) == 0:
        return prefix
    suffix = (rest if second < 0 else rest[:second]).decode("latin-1")
    return prefix + "." + suffix


def read(data: bytes) -> GameZ:
    r = Reader(data)
    if len(data) < HEADER_SIZE or r.u32_at(0) != SIGNATURE or r.u32_at(4) != VERSION:
        raise FormatError("not a Crimson Skies gamez/planes archive (bad signature)")
    tex_count = r.i32_at(12)
    mat_off, model_off, node_count, node_off = r.i32_at(20), r.i32_at(24), r.i32_at(28), r.i32_at(36)
    gz = GameZ()
    r.pos = HEADER_SIZE
    for _ in range(tex_count):
        entry = r.take(TEXTURE_ENTRY, "texture table")
        gz.texture_names.append(_texture_name(entry[12:32]))
    if r.pos != mat_off:
        raise FormatError("materials do not start where the header says")
    _read_materials(r, gz)
    if r.pos != model_off:
        raise FormatError("models do not start where the header says")
    dense = _read_models(r, gz)
    if r.pos != node_off:
        raise FormatError("nodes do not start where the header says")
    _read_nodes(r, gz, node_count, dense)
    for n in gz.nodes:
        for c in n.children:
            if 0 <= c < len(gz.nodes):
                gz.nodes[c].parents.append(n.index)
    return gz


def _tex(gz, idx):
    if 0 <= idx < len(gz.texture_names):
        return gz.texture_names[idx]
    return None


def _read_materials(r: Reader, gz: GameZ):
    array_size, count = r.i32_at(r.pos), r.i32_at(r.pos + 4)
    r.pos += 16
    cycled = []
    for _ in range(count):
        at = r.pos
        r.skip(MATERIAL_ENTRY)
        m = Material()
        m.flags = r.data[at + 1]
        m.soil = r.u32_at(at + 32)
        if m.flags & 0x01:
            m.texture = _tex(gz, r.i32_at(at + 16))
            if r.u32_at(at + 36) != 0:
                cycled.append(m)
        else:
            c = r.floats_at(at + 4, 3)
            m.color = (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
            m.alpha = r.data[at] / 255.0
        gz.materials.append(m)
    r.skip((array_size - count) * MATERIAL_ENTRY)
    for m in cycled:
        at = r.pos
        r.skip(28)
        m.cycle_loop = r.i32_at(at) != 0
        m.cycle_speed = r.f32_at(at + 12)
        frames = r.i32_at(at + 16)
        for _ in range(max(0, frames)):
            m.cycle_textures.append(_tex(gz, r.i32()))


def _read_models(r: Reader, gz: GameZ):
    array_size, count = r.i32_at(r.pos), r.i32_at(r.pos + 4)
    r.pos += 12
    dense = [-1] * array_size
    live = []
    for slot in range(array_size):
        at = r.pos
        r.skip(MODEL_ENTRY)
        if r.u32_at(at + 12) > 0:
            dense[slot] = len(live)
            live.append((at, r.i32_at(at + 100)))
    for record, data_at in live:
        if r.pos != data_at:
            raise FormatError(f"model data out of place at {r.pos}")
        mesh = _read_model(r, record)
        mesh.index = len(gz.meshes)
        gz.meshes.append(mesh)
    return dense


def _read_model(r: Reader, at: int) -> Mesh:
    mtype, facade, flags = r.u32_at(at), r.u32_at(at + 4), r.u32_at(at + 8)
    m = Mesh()
    m.model_type = MODEL_TYPES[mtype] if mtype < 3 else "Default"
    m.facade_mode = FACADE_MODES[facade] if facade < 4 else "CylindricalY"
    m.lit = bool(flags & 1)
    m.fog = bool(flags & 2)
    m.texture_scroll = (r.f32_at(at + 40), r.f32_at(at + 44))
    polys, verts, norms = r.i32_at(at + 16), r.i32_at(at + 20), r.i32_at(at + 24)
    morphs, lights, mrefs = r.i32_at(at + 28), r.i32_at(at + 32), r.i32_at(at + 92)
    v = r.floats(verts * 3)
    m.vertices = [v[i:i + 3] for i in range(0, len(v), 3)]
    n = r.floats(norms * 3)
    m.normals = [n[i:i + 3] for i in range(0, len(n), 3)]
    r.skip(morphs * 12)
    rec = r.pos
    r.skip(lights * 76)
    for i in range(lights):
        la = rec + i * 76
        points = r.i32_at(la + 12)
        if points <= 0:
            continue
        first = r.floats(3)
        r.skip((points - 1) * 12)
        L = Light()
        L.position = first
        L.color = tuple(c / 255.0 for c in r.floats_at(la + 28, 3))
        L.blink_period = r.f32_at(la + 8) if r.i32_at(la + 4) == 1 else 0.0
        L.fade_far = r.f32_at(la + 52)
        L.fade_slope = r.f32_at(la + 56) / 255.0
        m.lights.append(L)
    infos = r.pos
    r.skip(polys * 40)
    for i in range(polys):
        m.polygons.append(_read_polygon(r, infos + i * 40))
    r.skip(mrefs * 12)
    return m


def _read_polygon(r: Reader, info: int) -> Polygon:
    flags = r.u32_at(info)
    corners = flags & 0x3FF
    passes = r.i32_at(info + 16)
    zone_set = r.u32_at(info + 36)
    p = Polygon()
    p.show_backface = bool(flags & 0x0400)
    p.no_clutter = bool(flags & 0x0800)
    p.strip = bool(flags & 0x2000)
    p.priority = r.i32_at(info + 4)
    if zone_set & 0xFF:
        z = (zone_set >> 8) & 0xFF
        p.zone = z - 256 if z > 127 else z
    p.verts = list(r.i32s(corners))
    if r.u32_at(info + 12) != 0:
        p.normals = list(r.i32s(corners))
    mats = r.i32s(passes)
    has_uv = r.u32_at(info + 24) != 0
    for k in range(passes):
        uv = None
        if has_uv:
            f = r.floats(corners * 2)
            uv = [(f[i], f[i + 1]) for i in range(0, len(f), 2)]
        if k == 0:
            p.material = mats[0]
            p.uvs = uv
        else:
            if p.overlays is None:
                p.overlays = []
            p.overlays.append((mats[k], uv))
    c = r.floats(corners * 3)
    p.colors = [(c[i] / 255.0, c[i + 1] / 255.0, c[i + 2] / 255.0) for i in range(0, len(c), 3)]
    return p


def _read_nodes(r: Reader, gz: GameZ, count: int, dense):
    records = r.pos
    r.skip(count * NODE_ENTRY, "node table")
    for i in range(count):
        at = records + i * NODE_ENTRY
        kind = r.u32_at(at + 52)
        node = Node()
        node.index = i
        node.kind = NODE_KINDS.get(kind)
        if node.kind is None:
            raise FormatError(f"bad node class {kind} at {at + 52}")
        node.name = r.cstr_at(at, 36)
        node.flags = r.u32_at(at + 36)
        node.active = bool(node.flags & 0x04)
        z = r.data[at + 48]
        node.zone = z - 256 if z > 127 else z
        slot = r.i32_at(at + 60)
        if slot >= 0:
            if slot >= len(dense) or dense[slot] < 0:
                raise FormatError(f"node {node.name} names a free model slot")
            node.mesh = dense[slot]
        _read_node_data(r, node)
        parents, children = r.u16_at(at + 84), r.u16_at(at + 86)
        r.skip(parents * 4)
        node.children = list(r.i32s(children))
        gz.nodes.append(node)


def _read_node_data(r: Reader, node: Node):
    at = r.pos
    k = node.kind
    if k == "Camera":
        r.skip(488)
    elif k == "Display":
        r.skip(28)
    elif k == "Window":
        r.skip(248)
    elif k == "Light":
        r.skip(260)
    elif k == "Lod":
        r.skip(92)
        node.lod_range = math.sqrt(max(0.0, r.f32_at(at + 4)))
    elif k == "Object3d":
        r.skip(144)
        flags = r.u32_at(at)
        if not (flags & 0x08):
            m = r.floats_at(at + 48, 12)
            node.rotation = r.floats_at(at + 24, 3)
            node.scale = r.floats_at(at + 36, 3)
            # Stored transposed: m[0:3] is the X basis column, m[3:6] Y, m[6:9] Z, m[9:12] the
            # translation. Build a row-major 4x4 whose columns are those vectors.
            node.matrix = (
                (m[0], m[3], m[6], m[9]),
                (m[1], m[4], m[7], m[10]),
                (m[2], m[5], m[8], m[11]),
                (0.0, 0.0, 0.0, 1.0),
            )
            node.has_transform = True
    elif k == "World":
        r.skip(208)
        node.area = (r.f32_at(at + 52), r.f32_at(at + 56), r.f32_at(at + 68), r.f32_at(at + 72))
        cols, rows = r.i32_at(at + 152), r.i32_at(at + 156)
        members = []
        seen = set()
        for _ in range(rows * cols):
            cell = r.pos
            r.skip(88)
            values = r.u16_at(cell + 58)
            for _v in range(values):
                idx = r.i32_at(r.pos)
                r.skip(12)
                if idx not in seen:
                    seen.add(idx)
                    members.append(idx)
        node.partition_nodes = members
