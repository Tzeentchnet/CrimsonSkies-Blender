"""Crimson Skies (PC, 2000): build Blender scenes from planes.zbd / gamez.zbd and the
compiled animation archives."""

from __future__ import annotations

import os
import re
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import blender_util as bu
from .formats import pc_anim, pc_gamez, pc_texture


class PcOptions:
    def __init__(self, **kw):
        # LODs past the nearest one: off, or into their own hidden collection. The old
        # lod="ALL" keyword still turns them on.
        self.import_lods = kw.get("import_lods", kw.get("lod", "HIGHEST") == "ALL")
        self.import_collisions = kw.get("import_collisions", False)
        self.damage_states = kw.get("damage_states", "OFF")  # OFF | ON | HIDDEN
        self.node_indices = kw.get("node_indices")  # import only these nodes (None = roots by root_filter)
        self.hide_inactive = kw.get("hide_inactive", True)
        self.import_normals = kw.get("import_normals", True)
        self.vertex_colors = kw.get("vertex_colors", True)
        self.unlit = kw.get("unlit", False)
        self.root_filter = kw.get("root_filter", "")
        self.world_only = kw.get("world_only", True)
        self.animations = kw.get("animations", True)
        self.mission = kw.get("mission", "")
        self.arrange_spacing = kw.get("arrange_spacing", 25.0)
        self.pack_images = kw.get("pack_images", True)
        self.image_dir = kw.get("image_dir", "")
        self.fps = kw.get("fps", 30)
        self.display_state = kw.get("display_state", True)
        self.cockpit = kw.get("cockpit", False)
        self.paint_pattern = kw.get("paint_pattern", "fortune")  # 'shipped' = unpainted
        self.paint_colours = kw.get("paint_colours")  # None = the pattern's defaults
        self.paint_decals = kw.get("paint_decals")  # (nose, tail, wing), -1 = pattern default


# Damage states (docs/formats/gamez.md, "player-plane damage states", and destructibles.md): the
# torn damage panels (pdp*, pcdp* in the cockpit) and their on/off groups, the destroyed pieces,
# the cockpit bullet holes, and the destroyed variants of world destructibles. The cockpit's
# damage gauge (damageindicator, nosedamage...) is instrumentation, not damage.
_DAMAGE = re.compile(r"^(pc?dp\d+\w*|destroyed\w*|player_damage_(on|off)|bullet\d+|partial_damage\w*)$|"
                     r"destroy|dstry|_damaged?\b|broken|wreck", re.I)
# Other parts the game starts hidden on an aircraft: the blob shadow and the propeller spin frames
# (staticprop1 is the parked propeller).
_HIDDEN_PARTS = re.compile(r"^(shadow|prop\d+b?|prop2\w*|nitroprop\d*|staticrotor\w*|piece\d+)$", re.I)
# Node flags (docs/formats/gamez.md): polygons tested for collision, or a bounding box instead.
FLAG_INTERSECT_SURFACE = 0x10
FLAG_INTERSECT_BBOX = 0x20
# Invisible helper volumes in the world: fog volumes and Danger Zone paths.
_HELPERS = re.compile(r"^(fvol\d*|dzpaths?\d*|fogvol\w*)$", re.I)
_COCKPIT = re.compile(r"^cockpit\d*$", re.I)


def find_install(path: str) -> str:
    """The install folder (the one holding ZBD/) for a path anywhere inside it."""
    p = os.path.abspath(path)
    if os.path.isfile(p):
        p = os.path.dirname(p)
    while True:
        if os.path.isdir(os.path.join(p, "ZBD")):
            return p
        if os.path.basename(p).upper() == "ZBD":
            return os.path.dirname(p)
        parent = os.path.dirname(p)
        if parent == p:
            return ""
        p = parent


class PcScene:
    """Builds Blender objects for one GameZ archive."""

    def __init__(self, gz: pc_gamez.GameZ, textures: pc_texture.TextureLibrary, opts: PcOptions, label: str):
        self.gz = gz
        self.textures = textures
        self.opts = opts
        self.label = label
        self.images = bu.ImageCache("", pack=opts.pack_images, save_dir=opts.image_dir or None)
        self.mesh_cache = {}
        self.materials = {}
        self.objects_by_name = {}
        self.node_objects = {}
        self.stats = {"objects": 0, "meshes": 0, "lods": 0, "damage": 0, "colliders": 0}
        self.root = None
        self.collection = None
        self.aux = {}
        self.bbox_nodes = []  # (node object, base collection) of bounding-box colliders

    def aux_collection(self, kind: str, base):
        """The LODs / Damage / Collision collection next to an import collection."""
        key = (kind, base.name)
        col = self.aux.get(key)
        if col is None:
            col = bu.new_collection(f"{base.name} {kind}", base)
            col["cs_role"] = kind
            if kind == "LODs" or (kind == "Damage" and self.opts.damage_states == "HIDDEN"):
                bu.set_collection_hidden(col, True)
            self.aux[key] = col
        return col

    # -- materials -----------------------------------------------------------
    def _image(self, name):
        def load():
            t = self.textures.find(name)
            if t is None:
                return None
            return t.rgba, t.has_alpha
        return self.images.get(name, load)

    def material(self, index: int, double_sided: bool, scroll):
        key = (index, double_sided, tuple(scroll) if scroll else None)
        mat = self.materials.get(key)
        if mat is not None:
            return mat
        spec = bu.MaterialSpec("")
        if 0 <= index < len(self.gz.materials):
            m = self.gz.materials[index]
            if m.texture:
                spec.image = self._image(m.texture)
                tex = self.textures.find(m.texture)
                spec.use_alpha = bool(tex and tex.has_alpha)
                spec.blend = bool(tex and tex.alpha_class == "Full")
                spec.name = os.path.splitext(m.texture)[0]
                if spec.image is None:
                    spec.color = (1.0, 0.0, 1.0)
            else:
                spec.color = m.color
                spec.alpha = m.alpha
                spec.name = "color_%02x%02x%02x" % tuple(int(c * 255) for c in m.color)
            if m.cycle_textures:
                for i, t in enumerate(m.cycle_textures[1:], 1):
                    img = self._image(t) if t else None
                    if img is not None:
                        spec.extra_images.append((f"cycle frame {i}", img, None))
        else:
            spec.name = "missing_material"
            spec.color = (1.0, 0.0, 1.0)
        spec.double_sided = double_sided
        spec.unlit = self.opts.unlit
        spec.uv_scroll = scroll
        spec.vertex_colors = "Col" if self.opts.vertex_colors else None
        if not double_sided:
            spec.name += "_1s"
        mat = bu.build_material(spec)
        if 0 <= index < len(self.gz.materials):
            mat["cs_material_index"] = index
            if self.gz.materials[index].texture:
                mat["cs_texture"] = self.gz.materials[index].texture
            mat["cs_soil"] = self.gz.materials[index].soil
        self.materials[key] = mat
        return mat

    # -- meshes --------------------------------------------------------------
    def mesh(self, index: int):
        me = self.mesh_cache.get(index)
        if me is not None:
            return me
        src = self.gz.meshes[index]
        positions = np.asarray(src.vertices, dtype=np.float32).reshape(-1, 3)
        nverts = len(positions)
        normals = np.asarray(src.normals, dtype=np.float32).reshape(-1, 3) if src.normals else None
        loop_v, sizes, pmat, uvs, cols, lnorm, overlay_uv = [], [], [], [], [], [], []
        mat_slots = {}
        mats = []
        scroll = src.texture_scroll if any(src.texture_scroll) else None

        def slot_for(material, two_sided):
            key = (material, two_sided)
            if key not in mat_slots:
                mat_slots[key] = len(mats)
                mats.append(self.material(material, two_sided, scroll))
            return mat_slots[key]

        def emit(poly, corners):
            vs = [poly.verts[c] for c in corners]
            if len(set(vs)) < 3 or any(v < 0 or v >= nverts for v in vs):
                return
            loop_v.extend(vs)
            sizes.append(len(corners))
            pmat.append(slot_for(poly.material, poly.show_backface))
            for c in corners:
                uv = poly.uvs[c] if poly.uvs and c < len(poly.uvs) else (0.0, 0.0)
                uvs.append((uv[0], 1.0 - uv[1]))
                if poly.overlays and poly.overlays[0][1]:
                    ou = poly.overlays[0][1][c]
                    overlay_uv.append((ou[0], 1.0 - ou[1]))
                else:
                    overlay_uv.append(uvs[-1])
                col = poly.colors[c] if c < len(poly.colors) else (1.0, 1.0, 1.0)
                cols.append((col[0], col[1], col[2], 1.0))
                n = None
                if normals is not None and poly.normals and c < len(poly.normals):
                    ni = poly.normals[c]
                    if 0 <= ni < len(normals):
                        n = normals[ni]
                lnorm.append(n)

        has_overlay = False
        for poly in src.polygons:
            n = len(poly.verts)
            if n < 3:
                continue
            if poly.overlays:
                has_overlay = True
            if poly.strip:
                for i in range(n - 2):
                    emit(poly, (i, i + 1, i + 2) if i % 2 == 0 else (i, i + 2, i + 1))
            else:
                emit(poly, range(n))
        name = f"{self.label}_mesh{index:04d}"
        loop_normals = None
        if self.opts.import_normals and lnorm and all(n is not None for n in lnorm):
            loop_normals = np.asarray(lnorm, dtype=np.float32)
        uv_layers = {"UVMap": uvs}
        if has_overlay:
            uv_layers["Overlay"] = overlay_uv
        me = bu.build_mesh(name, positions, loop_v, sizes, pmat, uv_layers,
                           {"Col": cols} if self.opts.vertex_colors else None, loop_normals, mats)
        me["cs_model_type"] = src.model_type
        if src.model_type == "Facade":
            me["cs_facade_mode"] = src.facade_mode
        self.mesh_cache[index] = me
        self.stats["meshes"] += 1
        return me

    # -- nodes ---------------------------------------------------------------
    @staticmethod
    def _is_lower_lod(node):
        return node.kind == "Lod" and (node.lod_range or 0.0) > 0.0

    def build_node(self, index: int, collection, parent, depth=0, visited=None, hidden=False, base=None,
                   in_damage=False):
        if visited is None:
            visited = set()
        if index in visited or not (0 <= index < len(self.gz.nodes)) or depth > 64:
            return None
        visited.add(index)
        node = self.gz.nodes[index]
        base = base or collection
        if node.kind in ("Camera", "Window", "Display"):
            return None
        if self._is_lower_lod(node):
            if not self.opts.import_lods:
                return None
            collection = self.aux_collection("LODs", base)
            self.stats["lods"] += 1
        if depth > 0 and not self.opts.cockpit and _COCKPIT.match(node.name or "") and self.opts.display_state:
            return None
        # a damage root the user picked by itself (node_indices) is imported whatever the setting
        if (depth > 0 or self.opts.node_indices is None) and not in_damage and _DAMAGE.search(node.name or ""):
            if self.opts.damage_states == "OFF":
                return None
            in_damage, hidden = True, False
            self.stats["damage"] += 1
            if self.opts.damage_states == "HIDDEN":
                collection = self.aux_collection("Damage", base)
        data = self.mesh(node.mesh) if node.mesh >= 0 else None
        if data is None:
            obj = bu.new_empty(node.name or f"node{index}", collection, parent, size=0.25)
        else:
            obj = bu.new_object(node.name or f"node{index}", data, collection, parent)
        if node.matrix is not None:
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = Matrix(node.matrix)
        obj["cs_node_index"] = index
        obj["cs_kind"] = node.kind
        if node.lod_range is not None:
            obj["cs_lod_range"] = node.lod_range
        if node.kind == "Light":
            obj.empty_display_type = "SPHERE"
        if in_damage:
            obj["cs_damage_state"] = True
        elif self.opts.hide_inactive and not node.active and depth > 0:
            hidden = True
        if self.opts.display_state and depth > 0 and not in_damage and _HIDDEN_PARTS.match(node.name or ""):
            hidden = True
        if self.opts.import_collisions:
            obj["cs_collision"] = ("bbox" if node.flags & FLAG_INTERSECT_BBOX else
                                   "surface" if node.flags & FLAG_INTERSECT_SURFACE else "none")
            if node.flags & FLAG_INTERSECT_BBOX:
                self.bbox_nodes.append((obj, base))
        if hidden:
            bu.hide(obj, True)  # Blender does not hide children with their parent
        if _HELPERS.match(node.name or "") or (parent is not None and parent.get("cs_helper")):
            obj["cs_helper"] = True
            obj.display_type = "WIRE"
            obj.hide_render = True
        self.stats["objects"] += 1
        self.objects_by_name.setdefault(node.name, []).append(obj)
        self.node_objects[index] = obj
        if node.mesh >= 0 and self.gz.meshes[node.mesh].lights:
            for li, L in enumerate(self.gz.meshes[node.mesh].lights):
                ld = bpy.data.lights.new(f"{node.name}_light{li}", "POINT")
                ld.color = L.color
                ld.energy = 50.0
                lo = bu.new_object(ld.name, ld, collection, obj)
                lo.location = L.position
                if L.blink_period:
                    lo["cs_blink_period"] = L.blink_period
        for c in node.children:
            self.build_node(c, collection, obj, depth + 1, visited, hidden, base, in_damage)
        return obj

    def build_colliders(self):
        """A wireframe box over each bounding-box collider node's meshes, in the Collision
        collection. Every other solid node collides with its own polygons (cs_collision)."""
        if not self.bbox_nodes:
            return
        bpy.context.view_layer.update()
        for obj, base in self.bbox_nodes:
            inv = obj.matrix_world.inverted()
            pts = []
            for o in [obj] + list(obj.children_recursive):
                if o.type == "MESH":
                    m = inv @ o.matrix_world
                    pts.extend(m @ Vector(c) for c in o.bound_box)
            if not pts:
                continue
            lo = [min(p[i] for p in pts) for i in range(3)]
            hi = [max(p[i] for p in pts) for i in range(3)]
            box = bu.box_object(f"{obj.name}_bbox", lo, hi, self.aux_collection("Collision", base), obj)
            box["cs_collision"] = "bbox"
            self.stats["colliders"] += 1


def _texture_library(install: str, chapter: str | None, extra=()):
    lib = pc_texture.TextureLibrary()
    chapters = pc_texture.all_chapters(install)
    order = ([chapter] if chapter else []) + [c for c in chapters if c != chapter]
    for ch in order:
        paths = pc_texture.chapter_archives(install, ch)
        if paths:
            lib.add(paths[0])  # texture.zbd: the full-quality set
    for p in extra:
        lib.add(p)
    lib.add(os.path.join(install, "ZBD", "rimage.zbd"))
    return lib


def _matches(name: str, filt: str) -> bool:
    if not filt.strip():
        return True
    import fnmatch
    pats = [p.strip().lower() for p in filt.split(",") if p.strip()]
    n = name.lower()
    return any(fnmatch.fnmatch(n, p) or p == n for p in pats)


def options_from_prefs(p) -> dict:
    """PcOptions keywords from the add-on preferences."""
    return dict(import_lods=p.import_lods, import_collisions=p.import_collisions, damage_states=p.damage_states,
                animations=p.animations, pack_images=p.pack_images, paint_pattern=p.pc_livery)


def _wanted_roots(gz, opts: PcOptions):
    if opts.node_indices is not None:
        idx = set(opts.node_indices)
        return [n for n in gz.nodes if n.index in idx]
    return [n for n in gz.roots() if _matches(n.name, opts.root_filter)]


def _summary(scene: PcScene):
    s = scene.stats
    extra = [f"{s[k]} {k}" for k in ("lods", "damage", "colliders") if s[k]]
    return (", " + ", ".join(extra)) if extra else ""


def import_planes(path: str, opts: PcOptions, report=print):
    """planes.zbd (or any gamez-format archive treated as a model library)."""
    t0 = time.time()
    install = find_install(path)
    with open(path, "rb") as f:
        gz = pc_gamez.read(f.read())
    lib = _texture_library(install, None) if install else pc_texture.TextureLibrary()
    label = os.path.splitext(os.path.basename(path))[0]
    col = bu.new_collection(f"CS_PC_{label}")
    root = bu.new_empty(f"{label}_root", col, None, size=1.0, display="ARROWS")
    root.matrix_world = bu.Y_UP_TO_Z_UP
    scene = PcScene(gz, lib, opts, label)
    scene.root, scene.collection = root, col
    roots = [n for n in _wanted_roots(gz, opts) if n.kind not in ("Camera", "Window", "Display")]
    per_row = max(1, int(len(roots) ** 0.5 + 0.999))
    for i, n in enumerate(roots):
        sub = bu.new_collection(n.name, col)
        obj = scene.build_node(n.index, sub, root)
        if obj is not None and opts.arrange_spacing > 0 and len(roots) > 1:
            # arrange in the game frame: x across, -z (forward) down the rows
            obj.location = (obj.location[0] + (i % per_row) * opts.arrange_spacing,
                            obj.location[1],
                            obj.location[2] + (i // per_row) * opts.arrange_spacing)
    scene.build_colliders()
    painted = 0
    aircraft = []
    if install:
        from . import pc_paint
        for obj in list(root.children):
            try:
                if pc_paint.tag_aircraft(obj, install):
                    aircraft.append(obj)
                    if opts.paint_pattern != "shipped":
                        pc_paint.paint_aircraft(obj, opts.paint_pattern, opts.paint_colours, opts.paint_decals,
                                                report)
                        painted += 1
            except FileNotFoundError as exc:
                report(f"  liveries unavailable: {exc}")
                break
        if aircraft:
            # select the aircraft so the sidebar's livery panel lists the first one's liveries
            pc_paint.focus_aircraft(aircraft)
    if opts.animations and install:
        _apply_animations(scene, install, None, opts, report)
    report(f"Crimson Skies PC: {label}: {painted} painted, {len(roots)} models, {scene.stats['objects']} objects, "
           f"{scene.stats['meshes']} meshes{_summary(scene)}, {len(scene.images.images)} textures "
           f"in {time.time() - t0:.1f}s")
    return col


def import_level(path: str, opts: PcOptions, report=print, label=None):
    """A chapter's gamez.zbd: the world, its textures and (optionally) its animations. With
    opts.node_indices, only those root objects (label names the collection)."""
    t0 = time.time()
    install = find_install(path)
    chapter = os.path.basename(os.path.dirname(os.path.abspath(path)))
    with open(path, "rb") as f:
        gz = pc_gamez.read(f.read())
    lib = _texture_library(install, chapter) if install else pc_texture.TextureLibrary()
    if opts.node_indices is not None:
        name = f"{chapter}_{label}" if label else f"{chapter}_objects"
    else:
        name = f"{chapter}_{opts.mission}" if opts.mission else chapter
    col = bu.new_collection(f"CS_PC_{name}")
    root = bu.new_empty(f"{name}_root", col, None, size=10.0, display="ARROWS")
    root.matrix_world = bu.Y_UP_TO_Z_UP
    scene = PcScene(gz, lib, opts, chapter)
    scene.root, scene.collection = root, col
    roots = gz.roots()
    wanted = []
    if opts.node_indices is not None:
        wanted = [n.index for n in _wanted_roots(gz, opts)]
    elif opts.world_only:
        worlds = [n for n in gz.nodes if n.kind == "World"]
        seen = set()
        for w in worlds:
            for idx in [w.index] + list(w.partition_nodes or []):
                if idx not in seen:
                    seen.add(idx)
                    wanted.append(idx)
    else:
        wanted = [n.index for n in roots]
    if opts.node_indices is None:
        wanted = [i for i in wanted if _matches(gz.nodes[i].name, opts.root_filter)]
    visited = set()
    # objects picked out of a chapter keep their world placement, so a few far-apart ones are
    # still where the level puts them
    for idx in wanted:
        if idx in visited:
            continue
        scene.build_node(idx, col, root, 0, visited)
    scene.build_colliders()
    if opts.animations and install:
        _apply_animations(scene, install, chapter, opts, report)
    report(f"Crimson Skies PC: {name}: {scene.stats['objects']} objects, {scene.stats['meshes']} meshes"
           f"{_summary(scene)}, {len(scene.images.images)} textures in {time.time() - t0:.1f}s")
    return col


def _anim_paths(install, chapter, mission):
    zbd = os.path.join(install, "ZBD")
    out = []
    if chapter:
        p = os.path.join(zbd, chapter, "cam_anim.zbd")
        if os.path.isfile(p):
            out.append(p)
        if mission:
            p = os.path.join(zbd, chapter, mission, "mis_anim.zbd")
            if os.path.isfile(p):
                out.append(p)
    else:
        for ch in pc_texture.all_chapters(install):
            p = os.path.join(zbd, ch, "cam_anim.zbd")
            if os.path.isfile(p):
                out.append(p)
    return out


def _subtree_find(scene: PcScene, root_obj, name):
    stack = [root_obj]
    while stack:
        o = stack.pop()
        if o.get("cs_node_index") is not None and scene.gz.nodes[o["cs_node_index"]].name == name:
            return o
        stack.extend(o.children)
    return None


def _apply_animations(scene: PcScene, install, chapter, opts: PcOptions, report):
    count = 0
    longest = 0.0
    done = set()
    for path in _anim_paths(install, chapter, opts.mission):
        try:
            with open(path, "rb") as f:
                arch = pc_anim.read(f.read())
        except Exception as exc:
            report(f"animations {path}: {exc}")
            continue
        tag = os.path.relpath(path, os.path.join(install, "ZBD")).replace("\\", "/")
        owners = {}
        for d in arch.defs:
            for sid in d.script_ids:
                owners.setdefault(sid, d)
        for sid, script in enumerate(arch.scripts):
            if not script.frames:
                continue
            targets = scene.objects_by_name.get(script.object_name)
            if (not targets and chapter and script.object_name.lower().startswith("camera")
                    and scene.root is not None and opts.node_indices is None):
                # cutscene camera scripts: one animated Blender camera per script
                cam = bpy.data.cameras.new(script.name)
                cam.lens_unit = "FOV"
                cam.angle = 1.2
                cam.clip_end = 20000.0
                cam_obj = bu.new_object(script.name, cam, scene.collection, scene.root)
                targets = [cam_obj]
            if not targets:
                continue
            target = targets[0]
            d = owners.get(sid)
            if d is not None and len(targets) > 1:
                roots = scene.objects_by_name.get(d.root_name) or scene.objects_by_name.get(d.name) or []
                for r in roots:
                    hit = _subtree_find(scene, r, script.object_name)
                    if hit is not None:
                        target = hit
                        break
            key = (tag, script.name, target.name)
            if key in done:
                continue
            done.add(key)
            _script_action(script, target, opts, f"{d.name if d else 'script'}|{script.name}")
            count += 1
            longest = max(longest, script.duration)
    if count:
        bu.set_scene_range(1 + longest * opts.fps, opts.fps)
    report(f"  animations: {count} SI scripts applied")


def _script_action(script: pc_anim.Script, obj, opts: PcOptions, name: str):
    fps = opts.fps
    dur = script.duration
    n = max(2, int(dur * fps) + 1)
    times = [min(dur, i / fps) for i in range(n)]
    # include the exact frame boundaries so piecewise motion keeps its corners
    frames = []
    locs, rots, scs = [], [], []
    has_t = any(f.translate for f in script.frames)
    has_r = any(f.rotate for f in script.frames)
    has_s = any(f.scale for f in script.frames)
    rest_loc, rest_rot, rest_scale = obj.matrix_basis.decompose()
    for t in times:
        tr, rq, sc = script.sample(t)
        frames.append(1.0 + t * fps)
        locs.append(tr if tr is not None else tuple(rest_loc))
        rots.append(rq if rq is not None else tuple(rest_rot))
        scs.append(sc if sc is not None else tuple(rest_scale))
    obj.rotation_mode = "QUATERNION"
    obj.location, obj.rotation_quaternion, obj.scale = rest_loc, rest_rot, rest_scale
    act, slot = bu.make_action(name[:60], obj, frames,
                               locs if has_t else None,
                               rots if has_r else None,
                               scs if has_s else None)
    act["cs_script"] = script.script_name
    act["cs_spline"] = script.spline
    return act
