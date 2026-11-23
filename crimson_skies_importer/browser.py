"""The asset browser: View3D sidebar (N) > Crimson Skies.

Pick the game (PC install folder or Xbox disc image), Scan, then search and filter the index by
category and import what is ticked (or the highlighted row). Scans are cached on disk
(catalog.py), so the list comes back after a restart without scanning again; the game, the
source paths and the import options are add-on preferences (prefs.py).

The list itself lives on the window manager (never saved in a .blend) and is refilled from the
cache at startup, after loading a file, and whenever the game or its path changes.
"""


import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty

from . import catalog, prefs

_CATEGORY_ICONS = {
    "Player Aircraft": "AUTO", "AI Aircraft": "AUTO", "Ambient Aircraft": "AUTO", "Aircraft Models": "AUTO",
    "Levels": "WORLD", "Multiplayer Levels": "COMMUNITY", "Level Objects": "OBJECT_DATA",
    "Characters": "ARMATURE_DATA", "Characters & Props": "ARMATURE_DATA", "Effects": "OUTLINER_OB_FORCE_FIELD",
    "Destroyed States": "MOD_EXPLODE", "Debris & Damage": "MOD_EXPLODE", "Zeppelins": "MESH_CAPSULE",
    "Turrets & Defenses": "LIGHT_SPOT", "Vehicles": "MESH_CYLINDER", "Bosses": "GHOST_ENABLED",
    "Pickups & Targets": "PIVOT_CURSOR", "Skies": "LIGHT_SUN", "Props": "MESH_CUBE",
    "Other Packages": "PACKAGE",
}

# runtime state for the loaded index: per-row search text and category, the cache document, and
# the last filter result (filter_items runs on every redraw)
_state = {"hay": [], "cats": [], "doc": None, "filter_key": None, "filter_flags": None, "error": ""}
_category_items_cache = []


def _source_path(p):
    path = p.pc_path if p.platform == "PC" else p.xbox_path
    return bpy.path.abspath(path).rstrip("\\/") if path else ""


def _category_items(self, context):
    doc = _state["doc"]
    counts = {}
    for c in _state["cats"]:
        counts[c] = counts.get(c, 0) + 1
    items = [("ALL", f"All Categories ({len(_state['cats'])})", "Every category", "NONE", 0)]
    for i, c in enumerate(sorted(counts), 1):
        items.append((c, f"{c} ({counts[c]})", f"Only {c}", _CATEGORY_ICONS.get(c, "DOT"), i))
    if doc is None:
        items = items[:1]
    _category_items_cache[:] = items
    return _category_items_cache


def _on_filter(self, context):
    _state["filter_key"] = None


class CS_AssetItem(bpy.types.PropertyGroup):
    # name: the display name (PropertyGroup's built-in name)
    category: StringProperty()
    kind: StringProperty()
    source: StringProperty()
    ref: StringProperty()
    info: StringProperty()
    checked: BoolProperty(name="Import", description="Tick to import with the others")


class CS_BrowserState(bpy.types.PropertyGroup):
    assets: CollectionProperty(type=CS_AssetItem)
    index: IntProperty(name="Active Asset", default=0)
    search: StringProperty(name="Search", options={"TEXTEDIT_UPDATE"}, update=_on_filter,
                           description="Words to look for in the name, category and source (all must match)")
    category: EnumProperty(name="Category", items=_category_items, update=_on_filter)


def reload_list(context=None):
    """Refills the list from the cached scan for the current game and source."""
    context = context or bpy.context
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "cs_browser"):
        return
    st = wm.cs_browser
    p = prefs.get()
    path = _source_path(p)
    doc = catalog.load_cache(prefs.cache_dir(), p.platform, path) if path else None
    st.assets.clear()
    _state["doc"] = doc
    _state["hay"], _state["cats"] = [], []
    _state["filter_key"] = None
    if doc:
        for a in doc["assets"]:
            it = st.assets.add()
            it.name, it.category, it.kind = a["name"], a["category"], a["kind"]
            it.source, it.ref, it.info = a["source"], a["ref"], a["info"]
            _state["hay"].append(f"{a['name']} {a['category']} {a['info']} {a['source']}".lower())
            _state["cats"].append(a["category"])
    try:
        st.category = "ALL"
    except TypeError:
        pass
    st.index = 0
    _tag_redraw(context)


def _tag_redraw(context):
    screen = getattr(context, "screen", None)
    for area in (screen.areas if screen else []):
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _visible_flags(st):
    key = (st.search, st.category, len(_state["hay"]))
    if _state["filter_key"] != key:
        terms = st.search.lower().split()
        cat = st.category
        flags = []
        for hay, c in zip(_state["hay"], _state["cats"]):
            ok = (cat == "ALL" or c == cat) and all(t in hay for t in terms)
            flags.append(ok)
        _state["filter_key"], _state["filter_flags"] = key, flags
    return _state["filter_flags"]


class CS_UL_assets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "checked", text="")
        split = row.split(factor=0.6, align=True)
        split.label(text=item.name, icon=_CATEGORY_ICONS.get(item.category, "DOT"))
        sub = split.row()
        sub.enabled = False
        sub.label(text=item.info)

    def draw_filter(self, context, layout):
        pass  # search and category live in the panel, where they stay visible

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = _visible_flags(context.window_manager.cs_browser)
        if len(flags) != len(items):
            return [self.bitflag_filter_item] * len(items), []
        bit = self.bitflag_filter_item
        return [bit if f else 0 for f in flags], []


# ---------------------------------------------------------------------------------- operators

class CS_OT_browser_scan(bpy.types.Operator):
    """Index the game's importable assets (the result is cached and survives restarts)"""
    bl_idname = "crimson_skies.browser_scan"
    bl_label = "Scan"

    def execute(self, context):
        p = prefs.get()
        path = _source_path(p)
        if not path:
            self.report({"ERROR"}, "Set the PC install folder or the Xbox disc image first")
            return {"CANCELLED"}
        if not os.path.exists(path):
            self.report({"ERROR"}, f"Not found: {path}")
            return {"CANCELLED"}
        wm = context.window_manager
        wm.progress_begin(0, 1000)

        def progress(i, n, what):
            wm.progress_update(int(1000 * i / max(1, n)))

        try:
            assets, stamp = catalog.scan(p.platform, path, progress)
        except Exception as exc:
            self.report({"ERROR"}, f"Scan failed: {exc}")
            return {"CANCELLED"}
        finally:
            wm.progress_end()
        catalog.save_cache(prefs.cache_dir(), p.platform, path, assets, stamp)
        reload_list(context)
        self.report({"INFO"}, f"Crimson Skies {p.platform}: {len(assets)} assets indexed")
        return {"FINISHED"}


class CS_OT_browser_tick(bpy.types.Operator):
    """Tick or untick every asset the search and category show"""
    bl_idname = "crimson_skies.browser_tick"
    bl_label = "Tick Assets"
    bl_options = {"INTERNAL"}

    action: EnumProperty(items=(("SELECT", "Tick Shown", ""), ("DESELECT", "Untick All", "")))

    def execute(self, context):
        st = context.window_manager.cs_browser
        flags = _visible_flags(st)
        for it, shown in zip(st.assets, flags):
            if self.action == "DESELECT":
                it.checked = False
            elif shown:
                it.checked = True
        return {"FINISHED"}


def _targets(st):
    ticked = [a for a in st.assets if a.checked]
    if ticked:
        return ticked
    if 0 <= st.index < len(st.assets):
        flags = _visible_flags(st)
        if st.index < len(flags) and flags[st.index]:
            return [st.assets[st.index]]
    return []


class CS_OT_browser_import(bpy.types.Operator):
    """Import the ticked assets, or the highlighted one when none is ticked"""
    bl_idname = "crimson_skies.browser_import"
    bl_label = "Import"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.window_manager.cs_browser.assets)

    def execute(self, context):
        st = context.window_manager.cs_browser
        targets = _targets(st)
        if not targets:
            self.report({"WARNING"}, "Nothing to import: highlight an asset or tick some")
            return {"CANCELLED"}
        assets = [dict(name=a.name, category=a.category, kind=a.kind, source=a.source, ref=a.ref) for a in targets]
        p = prefs.get()
        path = _source_path(p)

        def report(msg):
            print("[CrimsonSkies]", msg)
            self.report({"INFO"}, msg)

        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            import_assets(p.platform, path, assets, p, report)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"Import failed: {exc}")
            return {"CANCELLED"}
        for a in st.assets:
            a.checked = False
        return {"FINISHED"}


def import_assets(platform, path, assets, p, report=print):
    """Imports catalog assets with the preferences' options. Groups what can share one import:
    every planes.zbd model in one grid, objects of one chapter together, models of one Xbox
    package together."""
    if platform == "PC":
        from . import pc_import
        common = pc_import.options_from_prefs(p)
        models = [a for a in assets if a["kind"] == "pc_model"]
        if models:
            opts = pc_import.PcOptions(**common, node_indices=[int(a["ref"]) for a in models])
            pc_import.import_planes(os.path.join(path, "ZBD", "planes.zbd"), opts, report)
        for a in assets:
            if a["kind"] == "pc_level":
                opts = pc_import.PcOptions(**common, world_only=True, mission=a["ref"])
                pc_import.import_level(os.path.join(path, *a["source"].split("/")), opts, report)
        by_source = {}
        for a in assets:
            if a["kind"] == "pc_object":
                by_source.setdefault(a["source"], []).append(a)
        for src, group in by_source.items():
            label = group[0]["name"] if len(group) == 1 else None
            opts = pc_import.PcOptions(**common, world_only=False, node_indices=[int(a["ref"]) for a in group])
            pc_import.import_level(os.path.join(path, *src.split("/")), opts, report, label=label)
        return
    from . import xbox_import
    common = xbox_import.options_from_prefs(p)
    packages = [a["source"] for a in assets if a["kind"] == "xbox_package"]
    if packages:
        xbox_import.import_path(path, xbox_import.XboxOptions(**common, entry=",".join(packages)), report)
    by_pkg = {}
    for a in assets:
        if a["kind"] == "xbox_model":
            by_pkg.setdefault(a["source"], []).append(a["ref"])
    for pkg, names in by_pkg.items():
        entry = f"{pkg}:{','.join(names)}"
        xbox_import.import_path(path, xbox_import.XboxOptions(**common, entry=entry, mode="ANIMS"), report)


# ------------------------------------------------------------------------------------- panels

class CS_PT_browser(bpy.types.Panel):
    bl_label = "Crimson Skies Browser"
    bl_idname = "CS_PT_browser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Crimson Skies"
    bl_order = 0

    def draw(self, context):
        lay = self.layout
        p = prefs.get()
        st = context.window_manager.cs_browser
        is_prefs = isinstance(p, bpy.types.AddonPreferences)
        if is_prefs:
            lay.row().prop(p, "platform", expand=True)
            lay.prop(p, "pc_path" if p.platform == "PC" else "xbox_path", text="")
        else:
            lay.label(text=f"{p.platform}: {_source_path(p) or '(no source set)'}")
        row = lay.row(align=True)
        row.scale_y = 1.2
        row.operator(CS_OT_browser_scan.bl_idname, icon="FILE_REFRESH",
                     text="Rescan" if _state["doc"] else "Scan")
        doc = _state["doc"]
        if doc is None:
            lay.label(text="Not scanned yet" if _source_path(p) else
                      ("Pick the install folder" if p.platform == "PC" else "Pick the disc image"), icon="INFO")
            return
        sub = lay.row()
        sub.label(text=f"{len(st.assets)} assets, scanned {doc.get('scanned', '?')}",
                  icon="ERROR" if doc.get("stale") else "CHECKMARK")
        if doc.get("stale"):
            lay.label(text="Source changed since the scan: rescan", icon="ERROR")
        lay.prop(st, "search", text="", icon="VIEWZOOM")
        lay.prop(st, "category", text="")
        lay.template_list("CS_UL_assets", "", st, "assets", st, "index", rows=12)
        flags = _visible_flags(st)
        shown = sum(flags)
        ticked = sum(1 for a in st.assets if a.checked)
        row = lay.row(align=True)
        row.label(text=f"{shown} shown, {ticked} ticked")
        row.operator(CS_OT_browser_tick.bl_idname, text="", icon="CHECKBOX_HLT").action = "SELECT"
        row.operator(CS_OT_browser_tick.bl_idname, text="", icon="CHECKBOX_DEHLT").action = "DESELECT"
        if 0 <= st.index < len(st.assets):
            a = st.assets[st.index]
            box = lay.box()
            col = box.column(align=True)
            col.label(text=a.name, icon=_CATEGORY_ICONS.get(a.category, "DOT"))
            col.label(text=f"{a.category}: {a.info}")
            col.label(text=a.source + (f" : {a.ref}" if a.kind == "xbox_model" else ""))
        row = lay.row()
        row.scale_y = 1.4
        n = ticked or (1 if _targets(st) else 0)
        row.operator(CS_OT_browser_import.bl_idname, icon="IMPORT",
                     text=f"Import {ticked} Ticked" if ticked else "Import Highlighted")
        row.enabled = n > 0


class CS_PT_browser_options(bpy.types.Panel):
    bl_label = "Import Options"
    bl_parent_id = "CS_PT_browser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Crimson Skies"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        p = prefs.get()
        if not isinstance(p, bpy.types.AddonPreferences):
            self.layout.label(text="Enable the add-on in Preferences to edit these")
            return
        lay = self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False
        prefs.draw_import_options(lay, p)


# --------------------------------------------------------------------------- startup / handlers

def _startup():
    try:
        prefs.restore_settings()
        reload_list(bpy.context)
    except Exception as exc:
        print(f"[CrimsonSkies] browser startup: {exc}")
    return None


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    bpy.app.timers.register(_startup, first_interval=0.0)


CLASSES = (CS_AssetItem, CS_BrowserState, CS_UL_assets, CS_OT_browser_scan, CS_OT_browser_tick,
           CS_OT_browser_import, CS_PT_browser, CS_PT_browser_options)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.cs_browser = PointerProperty(type=CS_BrowserState)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.timers.register(_startup, first_interval=0.0)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    del bpy.types.WindowManager.cs_browser
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
