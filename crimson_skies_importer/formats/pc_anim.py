"""Crimson Skies (PC) compiled animation archives: cam_anim.zbd and mis_anim.zbd.

Port of the container walk in CSVM's AnimZbd.cs (docs/formats/anim-definitions/
compiled-archives.md). We need the SI-script pool: per-node keyframe scripts ("*.zan") whose
frames carry translate / rotate / scale channels with per-axis cubic splines.

Container: 16-byte header {0x08170616, 53, nBase, nAnimFiles}, nBase x 132 bytes, nAnimFiles x
84 bytes, a 108-byte info block (def_count u16 @10, script_count u32 @16), then def_count
272-byte AnimDef records each followed by its support arrays and sequences, then the script
pool to EOF. Definitions are walked (not decoded) to reach the pool; their names and the
script indices they use are kept so scripts can be grouped by the animation that plays them.
"""

from __future__ import annotations

import math
import struct

from .binreader import FormatError, Reader, rename_unique

SIGNATURE = 0x08170616
VERSION = 53
INFO_SIZE = 108
DEF_SIZE = 272
SEQ_HEADER = 64
SCRIPT_HEADER = 28
CHANNEL = 76


class Channel:
    """Translate/scale: absolute cubics. Rotate: half-angle cubics relative to base."""

    __slots__ = ("base", "delta", "cubics")

    def __init__(self, base, delta, cubics):
        self.base = base
        self.delta = delta
        self.cubics = cubics  # 3 x (c0, c1, c2, c3)


class Frame:
    __slots__ = ("start", "end", "translate", "rotate", "scale")

    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.translate = None
        self.rotate = None
        self.scale = None


class Script:
    __slots__ = ("name", "script_name", "object_name", "spline", "frames")

    @property
    def duration(self):
        return self.frames[-1].end if self.frames else 0.0

    def frame_at(self, t):
        fr = self.frames
        lo, hi = 0, len(fr) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fr[mid].start <= t:
                lo = mid
            else:
                hi = mid - 1
        return fr[lo] if fr else None

    def sample(self, t):
        """(translate, rotate_quat_wxyz, scale) at t seconds; any may be None."""
        f = self.frame_at(t)
        if f is None:
            return None, None, None
        dt = max(0.0, t - f.start)
        tr = _eval_vec(f.translate, dt) if f.translate else None
        sc = _eval_vec(f.scale, dt) if f.scale else None
        rq = _eval_rot(f.rotate, dt) if f.rotate else None
        return tr, rq, sc


class AnimDef:
    __slots__ = ("name", "anim_name", "root_name", "script_ids")


class AnimArchive:
    def __init__(self):
        self.defs = []
        self.scripts = []


def _finite(v):
    return all(math.isfinite(x) for x in v)


def _cubic(c, t):
    v = c[0] + c[1] * t + c[2] * t * t + c[3] * t * t * t
    if math.isfinite(v):
        return v
    return c[0] if math.isfinite(c[0]) else 0.0


def _eval_vec(ch, dt):
    return tuple(_cubic(ch.cubics[i], dt) for i in range(3))


def _qmul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def _eval_rot(ch, dt):
    v = [_cubic(ch.cubics[i], dt) for i in range(3)]
    ln = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if not math.isfinite(ln) or ln < 1e-9:
        return ch.base
    s = math.sin(ln) / ln
    e = (math.cos(ln), v[0] * s, v[1] * s, v[2] * s)
    q = _qmul(e, ch.base)
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return tuple(x / n for x in q)


def read(data: bytes) -> AnimArchive:
    r = Reader(data)
    if len(data) < 16 or r.u32_at(0) != SIGNATURE or r.u32_at(4) != VERSION:
        raise FormatError("not a Crimson Skies compiled animation archive")
    info_at = 16 + r.i32_at(8) * 132 + r.i32_at(12) * 84
    def_count = r.u16_at(info_at + 10)
    script_count = r.i32_at(info_at + 16)
    pos = info_at + INFO_SIZE + DEF_SIZE  # the first record is the reserved placeholder
    arch = AnimArchive()
    for _ in range(1, def_count):
        d, pos = _walk_def(r, pos)
        arch.defs.append(d)
    arch.scripts = _read_scripts(r, pos, script_count)
    return arch


def _walk_def(r: Reader, pos: int):
    at = pos
    pos += DEF_SIZE
    d = AnimDef()
    d.anim_name = r.cstr_at(at, 32)
    d.name = r.cstr_at(at + 40, 32)
    d.root_name = r.cstr_at(at + 76, 32)
    path_len = r.i32_at(at + 36)
    pos += path_len * 36
    u8 = lambda o: r.data[at + o]
    pos += u8(217) * 92  # objects
    pos += u8(218) * 44  # nodes
    pos += u8(219) * 44  # lights
    pos += u8(220) * 44  # puffers
    pos += u8(221) * 44  # dynamic sounds
    pos += u8(222) * 40  # static sounds
    pos += u8(224) * 48  # activation prerequisites
    pos += u8(226) * 72  # anim refs
    n_ids = u8(227)
    d.script_ids = list(struct.unpack_from(f"<{n_ids}I", r.data, pos)) if n_ids else []
    pos += n_ids * 4
    if pos > len(r.data):
        raise FormatError("anim def support arrays run past the end")
    if r.u32_at(at + 208) != 0:
        pos = _skip_seq(r, pos)
    for _ in range(u8(216)):
        pos = _skip_seq(r, pos)
    if r.u32_at(at + 212) != 0:
        pos = _skip_seq(r, pos)
    return d, pos


def _skip_seq(r: Reader, pos: int) -> int:
    size = r.i32_at(pos + 60)
    if size < 0 or pos + SEQ_HEADER + size > len(r.data):
        raise FormatError(f"bad sequence length at {pos}")
    return pos + SEQ_HEADER + size


def _read_scripts(r: Reader, pos: int, count: int):
    headers = pos
    pos += count * SCRIPT_HEADER
    seen = set()
    out = []
    data = r.data
    for i in range(count):
        h = headers + i * SCRIPT_HEADER
        name_len, obj_len = data[h + 8], data[h + 9]
        spline = r.u32_at(h + 12) != 0
        frame_count, length = r.i32_at(h + 16), r.i32_at(h + 20)
        s = Script()
        s.script_name = r.cstr_at(pos, name_len)
        s.object_name = r.cstr_at(pos + name_len, obj_len)
        s.spline = spline
        pos += name_len + obj_len
        end = pos + length
        frames = []
        for _ in range(frame_count):
            flags = r.u32_at(pos)
            start, stop = r.f32_at(pos + 4), r.f32_at(pos + 8)
            f = Frame(start, stop)
            pos += 12
            for key, flag in (("translate", 1), ("rotate", 2), ("scale", 4)):
                if not flags & flag:
                    continue
                c = struct.unpack_from("<19f", data, pos)
                if flag == 2:
                    base = (c[0], c[1], c[2], c[3])  # file order w, x, y, z
                    if not _finite(base) or sum(x * x for x in base) < 1e-6:
                        base = (1.0, 0.0, 0.0, 0.0)
                    else:
                        n = math.sqrt(sum(x * x for x in base))
                        base = tuple(x / n for x in base)
                else:
                    base = (c[0], c[1], c[2])
                delta = (c[4], c[5], c[6])
                if not _finite(delta):
                    delta = (0.0, 0.0, 0.0)
                if spline:
                    cubics = (c[7:11], c[11:15], c[15:19])
                elif flag == 2:
                    cubics = tuple((0.0, delta[k], 0.0, 0.0) for k in range(3))
                else:
                    cubics = tuple((base[k], delta[k], 0.0, 0.0) for k in range(3))
                setattr(f, key, Channel(base, delta, cubics))
                pos += CHANNEL
            frames.append(f)
        if pos != end:
            raise FormatError(f"script {s.script_name} length mismatch")
        s.frames = frames
        stem = s.script_name[:-4] if s.script_name.endswith(".zan") else s.script_name
        stem = stem.lstrip(".\\").replace("\\", "-")
        s.name = rename_unique(seen, f"{stem}-{s.object_name}")
        out.append(s)
    if pos != len(data):
        raise FormatError("script pool does not end at the end of the file")
    return out
