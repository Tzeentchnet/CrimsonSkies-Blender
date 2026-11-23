"""Crimson Skies (PC) aircraft liveries: the paint patterns of crimson.rof applied to imported
planes, at import time or afterwards from the sidebar (View3D > N panel > Crimson Skies).

The shipped skins in texture.zbd are unpainted key textures; the game paints each aircraft at
load time from a pattern's .BM layers and three colours, and swaps the chosen nose/tail/wing
decals onto 16x16 placeholder textures. This does the same into Blender images.
"""

from __future__ import annotations

import os
import re
from collections import Counter

import bpy

from . import blender_util as bu
from .formats import pc_rof, pc_texture

_PREFIX = re.compile(r"^(agyro|hel|bal|blo|bri|dev|fir|fur|kes|pea|war)_", re.I)
_DECAL_SLOT = {"noselogo": 0, "taillogo": 1, "winglogo": 2}
BASE_NODE = "CS Base Texture"

_paint_libs = {}
_texture_libs = {}


def paint_library(install):
    lib = _paint_libs.get(install)
    if lib is None:
        lib = _paint_libs[install] = pc_rof.PaintLibrary(install)
    return lib


def texture_library(install):
    lib = _texture_libs.get(install)
    if lib is None:
        lib = pc_texture.TextureLibrary()
        for ch in pc_texture.all_chapters(install):
            paths = pc_texture.chapter_archives(install, ch)
            if paths:
                lib.add(paths[0])
        _texture_libs[install] = lib
    return lib


def _stem(name):
    s = os.path.splitext(name or "")[0].lower()
    return s.split(".")[0]


def aircraft_root(obj):
    """The nearest object up the parent chain that carries an aircraft skin prefix."""
    while obj is not None:
        if obj.get("cs_skin_prefix"):
            return obj
        obj = obj.parent
    return None


def _mesh_objects(root):
    return [o for o in [root] + list(root.children_recursive) if o.type == "MESH"]


def detect_prefix(root):
    c = Counter()
    for o in _mesh_objects(root):
        for slot in o.material_slots:
            m = slot.material
            if m and m.get("cs_texture"):
                mt = _PREFIX.match(_stem(m["cs_texture"]))
                if mt:
                    c[mt.group(1).lower()] += 1
    return c.most_common(1)[0][0] if c else None


def tag_aircraft(root, install):
    """Marks an imported aircraft root for painting; returns its skin prefix or None."""
    prefix = detect_prefix(root)
    if not prefix:
        return None
    root["cs_skin_prefix"] = prefix
    root["cs_install"] = install
    try:
        root["cs_patterns"] = ",".join(paint_library(install).patterns_for(prefix))
    except FileNotFoundError:
        root["cs_patterns"] = ""
    return prefix


def _base_node(mat):
    if not mat or not mat.node_tree:
        return None
    n = mat.node_tree.nodes.get(BASE_NODE)
    if n is None:
        n = next((x for x in mat.node_tree.nodes if x.type == "TEX_IMAGE"), None)
    return n


def _own_materials(root):
    """The aircraft's paintable materials. A material (or mesh) also used outside this aircraft
    is copied first, so repainting one plane never repaints another."""
    inside = set(_mesh_objects(root))
    outside = set()
    for o in bpy.data.objects:
        if o.type == "MESH" and o not in inside:
            outside.update(s.material.name for s in o.material_slots if s.material)
    copies = {}
    out = []
    for o in inside:
        for i, slot in enumerate(o.material_slots):
            m = slot.material
            if not m or not m.get("cs_texture"):
                continue
            if m.name in outside:
                if any(u not in inside for u in bpy.data.objects if u.data is o.data):
                    o.data = o.data.copy()
                if m.name not in copies:
                    copies[m.name] = m.copy()
                o.data.materials[i] = copies[m.name]
                m = copies[m.name]
            out.append(m)
    return list(dict.fromkeys(out))


def _image(name, rgba, has_alpha, pack=True):
    img = bpy.data.images.get(name)
    if img is not None:
        return img
    return bu.make_image(name, rgba, has_alpha, pack=pack)


def paint_aircraft(root, pattern, colours=None, decals=None, report=print):
    """Paints an imported aircraft. pattern 'shipped' restores the unpainted textures.
    colours: three (r, g, b) 0..255 or None for the pattern's defaults.
    decals: three decal indices (nose, tail, wing), None entries for the pattern's default."""
    prefix = root.get("cs_skin_prefix")
    install = root.get("cs_install")
    if not prefix or not install:
        raise RuntimeError(f"{root.name} is not an imported Crimson Skies aircraft")
    mats = _own_materials(root)
    if pattern == "shipped":
        for m in mats:
            node = _base_node(m)
            orig = m.get("cs_original_image")
            if node is not None and orig and orig in bpy.data.images:
                node.image = bpy.data.images[orig]
        for k in ("cs_paint_pattern", "cs_paint_colours", "cs_paint_decals"):
            if k in root:
                del root[k]
        return 0
    lib = paint_library(install)
    available = lib.patterns_for(prefix)
    if pattern not in available:
        report(f"{root.name}: no '{pattern}' livery for the {pc_rof.AIRCRAFT.get(prefix, prefix)}; "
               f"available: {', '.join(available)}. Using fortune.")
        pattern = "fortune"
    cols = [tuple(int(round(v)) for v in c) for c in (colours or pc_rof.PATTERN_COLOURS[pattern])]
    default_decals = pc_rof.PATTERN_DECALS[pattern]
    dec = [d if d is not None and d >= 0 else default_decals[i]
           for i, d in enumerate(decals or (None, None, None))]
    tex_lib = texture_library(install)
    tag = f"{pattern}-" + "".join("%02x%02x%02x" % c for c in cols)
    painted = 0
    for m in mats:
        node = _base_node(m)
        if node is None:
            continue
        if "cs_original_image" not in m and node.image is not None:
            m["cs_original_image"] = node.image.name
        stem = _stem(m["cs_texture"])
        suffix = stem.split("_", 1)[1] if "_" in stem else ""
        if stem.startswith(prefix + "_") and suffix in _DECAL_SLOT:
            name = pc_rof.DECALS[dec[_DECAL_SLOT[suffix]]]
            t = tex_lib.find(name)
            if t is not None:
                node.image = _image(name.lower(), t.rgba, True)
                painted += 1
            continue
        skin = lib.skin(pattern, stem)
        if skin is None:
            orig = m.get("cs_original_image")
            if orig and orig in bpy.data.images:
                node.image = bpy.data.images[orig]
            continue
        base = tex_lib.find(stem)
        alpha = base.rgba[..., 3] if base is not None and base.has_alpha else None
        rgba = pc_rof.paint(skin, cols, alpha)
        node.image = _image(f"{stem}@{tag}", rgba, alpha is not None)
        painted += 1
    root["cs_paint_pattern"] = pattern
    root["cs_paint_colours"] = [v for c in cols for v in c]
    root["cs_paint_decals"] = dec
    return painted


# ------------------------------------------------------------------------------------------ UI

_pattern_items_cache = []


def _pattern_items(self, context):
    root = aircraft_root(context.active_object) if context and context.active_object else None
    pats = (root.get("cs_patterns", "") if root else "").split(",")
    pats = [p for p in pats if p] or list(pc_rof.PATTERNS)
    items = [("shipped", "Shipped (unpainted)", "The unpainted key textures from texture.zbd")]
    items += [(p, pc_rof.PATTERN_LABELS[p], f"{pc_rof.PATTERN_LABELS[p]} livery") for p in pats]
    _pattern_items_cache[:] = items
    return _pattern_items_cache


def decal_items(include_default=True):
    items = [("-1", "Pattern default", "The pattern's own decal")] if include_default else []
    items += [(str(i), n, n) for i, n in enumerate(pc_rof.DECALS)]
    return items


_DECAL_ITEMS = decal_items()


def _on_pattern(self, context):
    p = self.pattern
    if p in pc_rof.PATTERN_COLOURS:
        cols = pc_rof.PATTERN_COLOURS[p]
        self.colour1, self.colour2, self.colour3 = [tuple(v / 255.0 for v in c) for c in cols]


def sync_settings(scene, root):
    """Loads an aircraft's current livery (pattern, colours, decals) into the sidebar settings,
    so the panel opens on what the aircraft wears. The aircraft (or one of its parts) must be the
    active object: the pattern list is built from it."""
    s = scene.cs_paint
    pattern = root.get("cs_paint_pattern") or "shipped"
    offered = (root.get("cs_patterns") or "").split(",")
    try:
        s.pattern = pattern if pattern == "shipped" or pattern in offered or not offered[0] else "shipped"
    except TypeError:
        pass
    cols = list(root.get("cs_paint_colours") or [])
    if len(cols) == 9:
        mine = [tuple(cols[i:i + 3]) for i in (0, 3, 6)]
        s.colour1, s.colour2, s.colour3 = [tuple(v / 255.0 for v in c) for c in mine]
        defaults = [tuple(c) for c in pc_rof.PATTERN_COLOURS.get(pattern, ())]
        s.custom_colours = bool(defaults) and mine != defaults
    else:
        s.custom_colours = False
    decals = list(root.get("cs_paint_decals") or [])
    default_decals = list(pc_rof.PATTERN_DECALS.get(pattern, ()))
    for i, key in enumerate(("nose", "tail", "wing")):
        d = decals[i] if i < len(decals) else -1
        if default_decals and d == default_decals[i]:
            d = -1
        setattr(s, key, str(d))
    s.synced = root.name


def focus_aircraft(roots):
    """Selects imported aircraft, makes the first active and loads its livery into the sidebar."""
    vl = bpy.context.view_layer
    vl.update()  # new objects reach the view layer's bases lazily
    for o in vl.objects:
        if o.select_get():
            o.select_set(False)
    shown = []
    for r in roots:
        try:
            r.select_set(True, view_layer=vl)
            shown.append(r)
        except RuntimeError:  # not in this view layer (an excluded collection)
            pass
    if shown:
        vl.objects.active = shown[0]
        sync_settings(bpy.context.scene, shown[0])


_msgbus_owner = object()


def _on_active_changed():
    ctx = bpy.context
    obj = getattr(ctx, "active_object", None)
    scene = getattr(ctx, "scene", None)
    if obj is None or scene is None or not hasattr(scene, "cs_paint"):
        return
    root = aircraft_root(obj)
    if root is not None and scene.cs_paint.synced != root.name:
        sync_settings(scene, root)


def subscribe():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.LayerObjects, "active"), owner=_msgbus_owner, args=(),
                             notify=_on_active_changed)


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    subscribe()  # message-bus subscriptions do not survive loading a file


class CS_PaintSettings(bpy.types.PropertyGroup):
    pattern: bpy.props.EnumProperty(name="Livery", items=_pattern_items, update=_on_pattern)
    custom_colours: bpy.props.BoolProperty(name="Custom Colours", default=False)
    colour1: bpy.props.FloatVectorProperty(name="Body", subtype="COLOR_GAMMA", size=3, min=0, max=1,
                                           default=(223 / 255, 0, 41 / 255))
    colour2: bpy.props.FloatVectorProperty(name="Dark Trim", subtype="COLOR_GAMMA", size=3, min=0, max=1,
                                           default=(25 / 255,) * 3)
    colour3: bpy.props.FloatVectorProperty(name="Light Trim", subtype="COLOR_GAMMA", size=3, min=0, max=1,
                                           default=(1, 1, 1))
    nose: bpy.props.EnumProperty(name="Nose", items=_DECAL_ITEMS, default="-1")
    tail: bpy.props.EnumProperty(name="Tail", items=_DECAL_ITEMS, default="-1")
    wing: bpy.props.EnumProperty(name="Wing", items=_DECAL_ITEMS, default="-1")
    synced: bpy.props.StringProperty(options={"HIDDEN"},
                                     description="The aircraft whose livery these settings were loaded from")


class CS_OT_apply_paint(bpy.types.Operator):
    """Paint the selected Crimson Skies aircraft with the chosen livery"""
    bl_idname = "crimson_skies.apply_paint"
    bl_label = "Apply Livery"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and aircraft_root(context.active_object) is not None

    def execute(self, context):
        s = context.scene.cs_paint
        roots = {aircraft_root(o) for o in (context.selected_objects or [context.active_object])}
        roots.discard(None)
        cols = None
        if s.custom_colours:
            cols = [tuple(c * 255.0 for c in v) for v in (s.colour1, s.colour2, s.colour3)]
        decals = [int(s.nose), int(s.tail), int(s.wing)]
        n = 0
        for root in roots:
            try:
                n += paint_aircraft(root, s.pattern, cols, decals, lambda m: self.report({"WARNING"}, m))
            except (RuntimeError, FileNotFoundError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
        s.synced = aircraft_root(context.active_object).name if context.active_object else ""
        self.report({"INFO"}, f"Painted {len(roots)} aircraft ({n} textures)")
        return {"FINISHED"}


class CS_OT_load_paint(bpy.types.Operator):
    """Load the active aircraft's current livery into these settings"""
    bl_idname = "crimson_skies.load_paint"
    bl_label = "Load Aircraft's Livery"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and aircraft_root(context.active_object) is not None

    def execute(self, context):
        sync_settings(context.scene, aircraft_root(context.active_object))
        return {"FINISHED"}


class CS_PT_paint(bpy.types.Panel):
    bl_label = "Crimson Skies Livery"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Crimson Skies"
    bl_order = 1

    def draw(self, context):
        lay = self.layout
        root = aircraft_root(context.active_object) if context.active_object else None
        if root is None:
            lay.label(text="Select an imported PC aircraft")
            return
        lay.label(text=f"{root.name}: {pc_rof.AIRCRAFT.get(root['cs_skin_prefix'], '')}", icon="AUTO")
        current = root.get("cs_paint_pattern")
        lay.label(text=f"Wearing: {pc_rof.PATTERN_LABELS.get(current, '') if current else 'Shipped (unpainted)'}")
        s = context.scene.cs_paint
        if s.synced != root.name:
            lay.operator(CS_OT_load_paint.bl_idname, icon="IMPORT")
        offered = [p for p in (root.get("cs_patterns") or "").split(",") if p]
        box = lay.box()
        box.label(text=f"Liveries ({len(offered)})" if offered else "Liveries (crimson.rof not found)")
        box.column(align=True).prop(s, "pattern", expand=True)
        lay.prop(s, "custom_colours")
        col = lay.column()
        col.enabled = s.custom_colours
        col.prop(s, "colour1")
        col.prop(s, "colour2")
        col.prop(s, "colour3")
        box = lay.box()
        box.label(text="Decals")
        box.prop(s, "nose")
        box.prop(s, "tail")
        box.prop(s, "wing")
        lay.operator(CS_OT_apply_paint.bl_idname, icon="BRUSH_DATA")


CLASSES = (CS_PaintSettings, CS_OT_apply_paint, CS_OT_load_paint, CS_PT_paint)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.cs_paint = bpy.props.PointerProperty(type=CS_PaintSettings)
    subscribe()
    bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    del bpy.types.Scene.cs_paint
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
