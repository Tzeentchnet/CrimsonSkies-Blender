"""Crimson Skies: High Road to Revenge (Xbox): build Blender scenes from resource packages."""

from __future__ import annotations

import fnmatch
import math
import os
import re
import time

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from . import blender_util as bu
from .formats import xbox_anim, xbox_model, xbox_package, xbox_texture

# Direct3D left-handed Y-up -> Blender right-handed Z-up: swap Y and Z. Triangles are reversed
# on the way in: measured against the stored vertex normals, their order is the back face.
C3 = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], dtype=np.float64)
C4 = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))

_HIDE = re.compile(r"(_dmg[1-9]\d*$|^prop_fast|^fx|^hidden$|^boundbox$|_debris|^debris)", re.I)


class XboxOptions:
    def __init__(self, **kw):
        self.entry = kw.get("entry", "")
        self.mode = kw.get("mode", "AUTO")
        self.arrange_spacing = kw.get("arrange_spacing", 30.0)
        self.lightmaps = kw.get("lightmaps", True)
        self.hide_damage = kw.get("hide_damage", True)
        self.normal_maps = kw.get("normal_maps", True)
        self.vertex_colors = kw.get("vertex_colors", True)
        self.animations = kw.get("animations", True)
        self.pack_images = kw.get("pack_images", True)
        self.image_dir = kw.get("image_dir", "")
        self.fps = kw.get("fps", 30)
        self.team_skin = kw.get("team_skin", "NOTEAM")  # NOTEAM | TEAM1 | TEAM2 (multiplayer skins)


def d3d_matrix(m12):
    """A node's 4x3 D3D row-vector matrix as a Blender-space 4x4."""
    m = Matrix(((m12[0], m12[3], m12[6], m12[9]),
                (m12[1], m12[4], m12[7], m12[10]),
                (m12[2], m12[5], m12[8], m12[11]),
                (0.0, 0.0, 0.0, 1.0)))
    return C4 @ m @ C4


class Context:
    """Images and materials shared by everything one import builds."""

    def __init__(self, opts: XboxOptions):
        self.opts = opts
        self.images = bu.ImageCache("", pack=opts.pack_images, save_dir=opts.image_dir or None)
        self.textures = {}  # lower name -> Entry, first package wins
        self.materials = {}
        self.alpha_info = {}
        self.lightmap = None
        self.grid_index = 0
        self.stats = {"objects": 0, "meshes": 0}

    def add_package(self, pkg):
        for k, e in pkg.textures().items():
            self.textures.setdefault(k, e)

    def image(self, name):
        if not name:
            return None
        e = self.textures.get(name.lower())
        if e is None:
            return None

        def load():
            t = xbox_texture.parse(e.data(), e.name)
            a = t.rgba[..., 3]
            # Only DXT1's 1-bit punch-through alpha (and the uncompressed effect formats) is
            # transparency; DXT3/5 alpha carries blend weights and masks (sh_valcano: detail mix).
            cutout = t.format == 1 or t.format in (14, 15, 16, 20)
            self.alpha_info[name.lower()] = float(np.mean(a < 64)) if (t.has_alpha and cutout) else 0.0
            return t.rgba, t.has_alpha
        return self.images.get(name, load)

    def material(self, mesh: xbox_model.XMesh, lightmapped: bool, uv_names):
        tex = mesh.textures
        key = (tuple(sorted(tex.items())), mesh.color, lightmapped and self.lightmap is not None)
        mat = self.materials.get(key)
        if mat is not None:
            return mat
        names = list(tex.values())
        base = None
        for slot in sorted(tex):
            n = tex[slot]
            stem = os.path.splitext(n)[0]
            if "~" not in stem:
                base = n
                break
        if base is None and names:
            base = tex[min(tex)]
        if base and self.opts.team_skin in ("TEAM1", "TEAM2") and "_noteam" in base.lower():
            # multiplayer aircraft ship <name>_noteam / _Team1 / _Team2 variants of the same skin
            i = base.lower().rindex("_noteam")
            variant = base[:i] + ("_Team1" if self.opts.team_skin == "TEAM1" else "_Team2") + base[i + 7:]
            if variant.lower() in self.textures:
                base = variant
        spec = bu.MaterialSpec(os.path.splitext(base)[0] if base else "untextured")
        spec.image = self.image(base) if base else None
        if base and spec.image is None:
            spec.color = (1.0, 0.0, 1.0)
        if spec.image is not None:
            frac = self.alpha_info.get(base.lower(), 0.0)
            spec.use_alpha = frac > 0.005
        c = mesh.color
        rgb = (((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0)
        if c != 0xFFFFFFFF and c != 0:
            spec.color = rgb if spec.image is None else tuple(min(1.0, x) for x in rgb)
        spec.base_uv = uv_names[0] if uv_names else None
        spec.double_sided = False
        for slot in sorted(tex):
            n = tex[slot]
            if n == base:
                continue
            stem = os.path.splitext(n)[0].lower()
            img = self.image(n)
            if img is None:
                continue
            if self.opts.normal_maps and (stem.endswith("~n") or stem.endswith("~b")) and spec.normal_image is None:
                spec.normal_image = img
                continue
            uv = f"UV{slot}" if f"UV{slot}" in uv_names else (uv_names[0] if uv_names else None)
            spec.extra_images.append((f"slot {slot}: {n}", img, uv))
        if lightmapped and self.lightmap is not None and "UV3" in uv_names:
            spec.lightmap = self.lightmap
            spec.lightmap_uv = "UV3"
        mat = bu.build_material(spec)
        mat["cs_shader"] = mesh.shader
        mat["cs_textures"] = ", ".join(f"{k}:{v}" for k, v in sorted(tex.items()))
        self.materials[key] = mat
        return mat


def build_mesh_data(name, meshes, ctx: Context, lightmapped: bool):
    """One Blender mesh from a node's mesh records (one material slot per record)."""
    pos, loops, sizes, pmat = [], [], [], []
    uv_sets = sorted({k for m in meshes for k in m.uv_sets} | {0})
    uv_names = [f"UV{k}" for k in uv_sets]
    uvs = {k: [] for k in uv_sets}
    normals = []
    mats = []
    offset = 0
    for mi, m in enumerate(meshes):
        p = (m.positions.astype(np.float64) @ C3.T).astype(np.float32)
        pos.append(p)
        ind = m.indices
        ok = (ind[:, 0] != ind[:, 1]) & (ind[:, 1] != ind[:, 2]) & (ind[:, 0] != ind[:, 2])
        ind = ind[ok][:, ::-1]  # the game's front faces wind the other way (matches its normals)
        tri = ind + offset
        loops.append(tri.ravel())
        sizes.append(np.full(len(tri), 3, dtype=np.int32))
        pmat.append(np.full(len(tri), mi, dtype=np.int32))
        corner = ind.ravel()
        for k in uv_sets:
            uv = m.uvs[corner, k].copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            uvs[k].append(uv)
        normals.append((m.normals[corner].astype(np.float64) @ C3.T).astype(np.float32))
        mats.append(ctx.material(m, lightmapped, uv_names))
        offset += len(p)
    if not pos:
        return None
    me = bu.build_mesh(name, np.concatenate(pos), np.concatenate(loops), np.concatenate(sizes),
                       np.concatenate(pmat), {f"UV{k}": np.concatenate(uvs[k]) for k in uv_sets},
                       None, np.concatenate(normals), mats)
    ctx.stats["meshes"] += 1
    return me


def build_model(model: xbox_model.XModel, ctx: Context, collection, parent=None, label=None,
                lightmapped=False):
    """Objects for a model's node tree; returns (root object, {node name: object}, node objects)."""
    objs = {}
    by_index = {}
    skinned = model.version == 2

    def make(node, parent_obj, depth, hidden=False):
        name = node.name or f"node{node.index}"
        data = None
        if node.meshes and not skinned:
            data = build_mesh_data(f"{label or model.name}:{name}", node.meshes, ctx, lightmapped)
        if data is None:
            obj = bu.new_empty(name, collection, parent_obj, size=0.3)
        else:
            obj = bu.new_object(name, data, collection, parent_obj)
        obj.matrix_basis = d3d_matrix(node.matrix)
        obj["cs_kind"] = node.kind
        obj["cs_node_index"] = node.index
        if ctx.opts.hide_damage and depth > 0 and _HIDE.search(name):
            hidden = True
        if hidden:
            bu.hide(obj, True)
        ctx.stats["objects"] += 1
        objs.setdefault(name, obj)
        by_index[node.index] = obj
        for c in node.children:
            make(c, obj, depth + 1, hidden)
        return obj

    root = make(model.root, parent, 0)
    if label:
        root.name = label
    root["cs_model"] = model.name
    if skinned:
        _build_skin(model, ctx, collection, root, by_index, label or model.name)
    return root, objs, by_index


def _build_skin(model, ctx, collection, root, by_index, label):
    """Armature over every node, skinned meshes deformed by it."""
    bpy.context.view_layer.update()
    arm_data = bpy.data.armatures.new(f"{label}_armature")
    arm = bu.new_object(f"{label}_armature", arm_data, collection, root)
    arm.matrix_parent_inverse = Matrix.Identity(4)
    arm.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.objects.active = arm
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root_inv = root.matrix_world.inverted()
    edit = {}
    for n in model.nodes:
        if n.index == 0:
            continue
        eb = arm_data.edit_bones.new(n.name or f"node{n.index}")
        mw = root_inv @ by_index[n.index].matrix_world
        loc, rot, _sc = mw.decompose()
        eb.head = (0, 0, 0)
        eb.tail = (0, 0.1, 0)
        eb.matrix = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()
        edit[n.index] = eb
    for n in model.nodes:
        if n.index in edit and n.parent is not None and n.parent.index in edit:
            edit[n.index].parent = edit[n.parent.index]
    bpy.ops.object.mode_set(mode="OBJECT")
    names = {n.index: (n.name or f"node{n.index}") for n in model.nodes}
    for n in model.nodes:
        if not n.meshes:
            continue
        me = build_mesh_data(f"{label}:{n.name}", n.meshes, ctx, False)
        obj = bu.new_object(f"{n.name}_skin", me, collection, arm)
        if ctx.opts.hide_damage and _HIDE.search(n.name or ""):
            bu.hide(obj, True)
        offset = 0
        for m in n.meshes:
            if m.weights is not None and m.joints:
                groups = {}
                w = np.concatenate([m.weights, (1.0 - m.weights.sum(axis=1))[:, None]], axis=1)
                for vi in range(len(m.positions)):
                    for k in range(4):
                        wt = float(w[vi, k])
                        if wt <= 1e-4:
                            continue
                        slot = int(round((m.registers[vi, k] - 3) / 3))
                        if not (0 <= slot < len(m.joints)):
                            continue
                        jn = names.get(m.joints[slot][0])
                        if jn is None:
                            continue
                        g = groups.get(jn) or obj.vertex_groups.get(jn) or obj.vertex_groups.new(name=jn)
                        groups[jn] = g
                        g.add([offset + vi], wt, "ADD")
            offset += len(m.positions)
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
    root["cs_armature"] = arm.name


# ------------------------------------------------------------------------------- animation

def apply_animation(anim: xbox_anim.Animation, objs: dict, opts: XboxOptions, armature=None):
    """Keys every track whose target exists. Returns the number of tracks applied."""
    applied = 0
    for t in anim.tracks:
        pos = (t.pos.astype(np.float64) @ C3.T)
        scale = t.scale[:, [0, 2, 1]]
        q = t.quat.astype(np.float64)  # x, y, z, w in the D3D frame
        quats = np.stack([q[:, 3], -q[:, 0], -q[:, 2], -q[:, 1]], axis=1)
        frames = 1.0 + t.frames
        if armature is not None and t.target in armature.pose.bones:
            _key_pose_bone(anim, t, armature, frames, pos, quats, scale)
            applied += 1
            continue
        obj = objs.get(t.target)
        if obj is None:
            continue
        bu.make_action(f"{anim.name}|{t.target}"[:60], obj, frames, pos, quats, scale)
        applied += 1
    return applied


def _key_pose_bone(anim, t, arm, frames, pos, quats, scale):
    pb = arm.pose.bones[t.target]
    bone = pb.bone
    rest_local = bone.matrix_local if bone.parent is None else bone.parent.matrix_local.inverted() @ bone.matrix_local
    rest_inv = rest_local.inverted()
    locs, rots, scs = [], [], []
    for i in range(len(frames)):
        m = Matrix.LocRotScale(Vector(pos[i]), Quaternion(quats[i]), Vector(scale[i]))
        basis = rest_inv @ m
        l, r, s = basis.decompose()
        locs.append(tuple(l))
        rots.append(tuple(r))
        scs.append(tuple(s))
    act = arm.animation_data.action if arm.animation_data and arm.animation_data.action else None
    if act is None or act.get("cs_anim") != anim.name:
        act = bpy.data.actions.new(anim.name)
        act.use_fake_user = True
        act["cs_anim"] = anim.name
        slot = act.slots.new(id_type="OBJECT", name=arm.name)
        layer = act.layers.new("Layer")
        layer.strips.new(type="KEYFRAME")
        bu.attach_action(arm, act, slot, None)
    slot = act.slots[0]
    bag = act.layers[0].strips[0].channelbag(slot, ensure=True)
    base = f'pose.bones["{t.target}"]'
    pb.rotation_mode = "QUATERNION"
    for path, vals in (("location", locs), ("rotation_quaternion", rots), ("scale", scs)):
        v = np.asarray(vals, dtype=np.float32)
        for i in range(v.shape[1]):
            fc = bag.fcurves.new(f"{base}.{path}", index=i)
            fc.keyframe_points.add(len(frames))
            co = np.empty(len(frames) * 2, dtype=np.float32)
            co[0::2] = frames
            co[1::2] = v[:, i]
            fc.keyframe_points.foreach_set("co", co)
            fc.update()


# ---------------------------------------------------------------------------------- imports

def _models_in(pkg, instances: bool):
    out = []
    for e in pkg.of_type(xbox_package.TYPE_MODEL):
        if ("|" in e.name) == instances:
            out.append(e)
    return out


def _filter(entries, pattern):
    if not pattern:
        return entries
    pats = [p.strip().lower() for p in pattern.split(",") if p.strip()]
    return [e for e in entries if any(fnmatch.fnmatch(e.name.lower(), p) or fnmatch.fnmatch(e.base_name.lower(), p)
                                      or e.base_name.lower() == p or e.base_name.lower() == p + ".x" for p in pats)]


def import_level(pkg, ctx: Context, parent_col, report):
    t0 = time.time()
    label = os.path.splitext(os.path.basename(pkg.name))[0]
    col = bu.new_collection(f"CS_Xbox_{label}", parent_col)
    root = bu.new_empty(f"{label}_root", col, None, size=20.0, display="ARROWS")
    if ctx.opts.lightmaps:
        for k, e in ctx.textures.items():
            if k.endswith("$lmap.tga") and e.package is pkg:
                ctx.lightmap = ctx.image(e.name)
                break
    # placed, lightmapped unit instances: their root matrix is the world placement
    units_col = bu.new_collection(f"{label}_units", col)
    n_units = 0
    objs_by_model = {}
    for e in _models_in(pkg, True):
        try:
            model = xbox_model.read(e.data(), e.name)
        except Exception as exc:
            report(f"  {e.name}: {exc}")
            continue
        r, objs, _ = build_model(model, ctx, units_col, root, label=e.instance_name,
                                 lightmapped=bool(model.flags & 0x100))
        r["cs_model"] = e.base_name
        objs_by_model[e.instance_name] = objs
        n_units += 1
    # terrain and water chunks of the world section (world-space vertices)
    world_col = bu.new_collection(f"{label}_world", col)
    groups = {}
    for i, (name, mesh) in enumerate(xbox_package.world_meshes(pkg)):
        groups.setdefault(name, []).append(mesh)
    n_chunks = 0
    for name, meshes in groups.items():
        sub = bu.new_collection(f"{label}_{name}", world_col)
        for i, m in enumerate(meshes):
            me = build_mesh_data(f"{label}:{name}{i:03d}", [m], ctx, True)
            if me is None:
                continue
            bu.new_object(f"{name}{i:03d}", me, sub, root)
            ctx.stats["objects"] += 1
            n_chunks += 1
    props = pkg.world_props()
    if props:
        col["cs_scatter_props"] = ", ".join(props)
    # level animations (doors, turrets...) onto the units that have the targets
    n_anim = 0
    if ctx.opts.animations:
        for e in pkg.of_type(xbox_package.TYPE_ANIM):
            try:
                anim = xbox_anim.read(e.data())
            except Exception:
                continue
            targets = {t.target for t in anim.tracks}
            for inst, objs in objs_by_model.items():
                if targets & set(objs):
                    n_anim += apply_animation(anim, objs, ctx.opts)
    ctx.lightmap = None
    report(f"Crimson Skies Xbox level {label}: {n_units} units, {n_chunks} terrain/water chunks, "
           f"{n_anim} animation tracks, {len(ctx.images.images)} textures in {time.time() - t0:.1f}s")
    return col


def import_models(pkg, ctx: Context, parent_col, report, pattern="", with_anims=True):
    t0 = time.time()
    label = os.path.splitext(os.path.basename(pkg.name))[0]
    col = bu.new_collection(f"CS_Xbox_{label}", parent_col)
    entries = _filter(_models_in(pkg, False), pattern if pattern and not pattern.lower().endswith(".bin") else "")
    if not entries:
        entries = _filter(_models_in(pkg, True), pattern if not pattern.lower().endswith(".bin") else "")
    built = []
    for e in entries:
        try:
            model = xbox_model.read(e.data(), e.name)
        except Exception as exc:
            report(f"  {e.name}: {exc}")
            continue
        sub = bu.new_collection(os.path.splitext(e.base_name)[0], col)
        holder = bu.new_empty(os.path.splitext(e.base_name)[0], sub, None, size=1.0, display="ARROWS")
        r, objs, _ = build_model(model, ctx, sub, holder, lightmapped=False)
        if "|" in e.name:
            r.matrix_basis = Matrix.Identity(4)
        built.append((e, model, holder, objs))
    # lay out in a grid
    spacing = ctx.opts.arrange_spacing
    if spacing > 0:
        per_row = 8
        for _e, model, holder, _o in built:
            i = ctx.grid_index
            ctx.grid_index += 1
            holder.location = ((i % per_row) * spacing, -(i // per_row) * spacing, 0.0)
    n_anim = 0
    if with_anims and ctx.opts.animations:
        anims = []
        for e in pkg.of_type(xbox_package.TYPE_ANIM):
            try:
                anims.append(xbox_anim.read(e.data()))
            except Exception:
                pass
        for anim in anims:
            targets = {t.target for t in anim.tracks}
            best = max(built, key=lambda b: len(targets & set(b[3])), default=None)
            if best is None or not (targets & set(best[3])):
                continue
            arm = None
            an = best[2].children[0].get("cs_armature") if best[2].children else None
            if an:
                arm = bpy.data.objects.get(an)
            n_anim += apply_animation(anim, best[3], ctx.opts, arm)
        if n_anim:
            longest = max((a.frame_count for a in anims), default=0)
            bu.set_scene_range(longest + 1, ctx.opts.fps)
    report(f"Crimson Skies Xbox {label}: {len(built)} models, {ctx.stats['objects']} objects, "
           f"{n_anim} animation tracks, {len(ctx.images.images)} textures in {time.time() - t0:.1f}s")
    return col


def import_path(path: str, opts: XboxOptions, report=print):
    ctx = Context(opts)
    low = path.lower()
    if low.endswith(".x"):
        data = open(path, "rb").read()
        if data[:4] == b"xof ":
            raise RuntimeError("authoring-format (xof) .x files are not supported; use the compiled packages")
        folder = os.path.dirname(path)
        model = xbox_model.read(data, os.path.basename(path))
        col = bu.new_collection(f"CS_Xbox_{os.path.basename(path)}")
        build_model(model, ctx, col, None)
        return col
    src = xbox_package.Source(path)
    try:
        if low.endswith(".bin") and not os.path.isdir(path):
            names = [os.path.basename(path)]
            pkgs = [xbox_package.Package(names[0], open(path, "rb").read())]
        else:
            entry = opts.entry.strip()
            if entry:
                pkg_pattern, _, model_pattern = entry.partition(":")
                names = src.resolve(pkg_pattern) or src.resolve(f"*{pkg_pattern}*")
            else:
                names, model_pattern = [], ""
                report("No package given: listing packages. Set 'Package / Entry', e.g. SH_Tutorial.bin or PF_Bandito.bin")
                for n in src.package_names():
                    report(f"  {n}")
                return None
            if not names:
                raise RuntimeError(f"no package matches {entry!r}")
            pkgs = [src.package(n) for n in names]
        model_pattern = ""
        if ":" in opts.entry:
            model_pattern = opts.entry.split(":", 1)[1]
        for pkg in pkgs:
            ctx.add_package(pkg)
        # aircraft packages share effect textures with the default aircraft package
        if not low.endswith(".bin"):
            try:
                for extra in src.resolve("default_airplane.bin"):
                    ctx.add_package(src.package(extra))
            except Exception:
                pass
        cols = []
        for pkg in pkgs:
            mode = opts.mode
            if mode == "AUTO":
                mode = "LEVEL" if pkg.is_level and not model_pattern else "ANIMS"
            if mode == "LEVEL":
                cols.append(import_level(pkg, ctx, None, report))
            else:
                cols.append(import_models(pkg, ctx, None, report, model_pattern, with_anims=(mode != "MODELS")))
        return cols
    finally:
        src.close()
