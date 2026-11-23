"""Crimson Skies: High Road to Revenge (Xbox) .anm animations (package entry type 4).

    u8 0, u32 frame count, u32-length name, u32 track count,
    tracks: u32-length target node name, u32 key count,
            keys x 56 bytes {u32 frame, f32 pos[3], f32 scale[3], f32 shear[3], f32 quat[4] (x,y,z,w)}
    u32 command count, commands {u32 frame, u32-length string}
This is the compiled AnimationSet / AnimationKeyNew of the authoring templates the game embeds.
Keys are local transforms of the named node, in the model's Direct3D frame. 30 frames a second.
"""

from __future__ import annotations

import numpy as np

from .binreader import FormatError, Reader


class Track:
    __slots__ = ("target", "frames", "pos", "scale", "shear", "quat")


class Animation:
    __slots__ = ("name", "frame_count", "tracks", "commands")


def read(data: bytes) -> Animation:
    r = Reader(data)
    flag = r.u8()
    if flag != 0:
        raise FormatError(f"unknown animation flag {flag}")
    a = Animation()
    a.frame_count = r.u32()
    a.name = r.lstr()
    a.tracks = []
    for _ in range(r.u32()):
        t = Track()
        t.target = r.lstr()
        n = r.u32()
        raw = np.frombuffer(r.take(56 * n, "keys"), dtype=np.uint8).reshape(n, 56)
        t.frames = raw[:, 0:4].copy().view("<u4").reshape(n).astype(np.float32)
        f = raw[:, 4:56].copy().view("<f4").reshape(n, 13)
        t.pos, t.scale, t.shear, t.quat = f[:, 0:3], f[:, 3:6], f[:, 6:9], f[:, 9:13]
        a.tracks.append(t)
    a.commands = []
    for _ in range(r.u32()):
        fr = r.u32()
        a.commands.append((fr, r.lstr()))
    return a
