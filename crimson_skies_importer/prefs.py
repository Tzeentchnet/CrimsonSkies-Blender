"""Add-on preferences: game sources and the import options every importer shares.

Blender keeps add-on preferences in userpref.blend, which only holds them while the add-on stays
enabled and the preferences get saved. To survive a factory reset, an uninstall and reinstall, or
a disable and enable, every change is also written to settings.json in the add-on's user folder
(the folder the scan cache lives in), and register() puts those values back.
"""


import json
import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

ADDON = __package__
SETTINGS_FILE = "settings.json"

DAMAGE_ITEMS = (
    ("OFF", "Off", "Do not import damage states, destroyed pieces or debris models"),
    ("ON", "On", "Import damage states in place, visible"),
    ("HIDDEN", "Hidden", "Import damage states into their own collection, with its visibility off"),
)

_loading = False


def user_dir(*sub, create=True) -> str:
    """The add-on's own user folder: the extension's user directory when installed as an
    extension, otherwise a folder in Blender's config directory."""
    base = None
    try:
        base = bpy.utils.extension_path_user(ADDON, path="", create=create)
    except Exception:  # loaded as a legacy add-on or from a script: not an extension package
        base = None
    if not base:
        base = os.path.join(bpy.utils.user_resource("CONFIG"), "crimson_skies_importer")
    path = os.path.join(base, *sub)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def cache_dir() -> str:
    return user_dir("cache")


def _livery_items():
    from .formats import pc_rof
    return [("shipped", "Shipped (unpainted)", "The unpainted key textures from texture.zbd")] + [
        (p, pc_rof.PATTERN_LABELS[p], f"{pc_rof.PATTERN_LABELS[p]} livery") for p in pc_rof.PATTERNS]


def _save(self, context):
    if not _loading:
        save_settings(self)


def _on_source(self, context):
    _save(self, context)
    from . import browser
    browser.reload_list(context)


class CS_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON

    platform: EnumProperty(name="Game", update=_on_source, items=(
        ("PC", "PC", "Crimson Skies (2000): an install folder"),
        ("XBOX", "Xbox", "Crimson Skies: High Road to Revenge (2003): the disc image")), default="PC")
    pc_path: StringProperty(name="PC Install Folder", subtype="DIR_PATH", update=_on_source,
                            description="The Crimson Skies install folder (the one holding ZBD and GOSDATA)")
    xbox_path: StringProperty(name="Xbox Disc Image", subtype="FILE_PATH", update=_on_source,
                              description="The High Road to Revenge disc image (.iso / .xiso), or a folder of "
                                          "extracted packages")
    import_lods: BoolProperty(name="Import LODs", default=False, update=_save,
                              description="Also import the lower levels of detail, into their own hidden "
                                          "collection (PC; the Xbox game ships no LOD models)")
    import_collisions: BoolProperty(name="Import Collisions", default=False, update=_save,
                                    description="Import collision volumes into their own collection, as wireframe "
                                                "(Xbox collide_/boundbox nodes; PC bounding-box colliders)")
    damage_states: EnumProperty(name="Damage States", items=DAMAGE_ITEMS, default="OFF", update=_save,
                                description="Damage panels, damage-state variants, destroyed pieces and debris")
    animations: BoolProperty(name="Animations", default=True, update=_save,
                             description="Import animations as actions")
    pack_images: BoolProperty(name="Pack Images", default=True, update=_save,
                              description="Pack decoded textures into the .blend")
    pc_livery: EnumProperty(name="Livery", items=_livery_items(), default="fortune", update=_save,
                            description="Livery painted on PC aircraft at import (repaint from the sidebar)")
    team_skin: EnumProperty(name="Team Skin", default="NOTEAM", update=_save, items=(
        ("NOTEAM", "No Team", "The neutral multiplayer skin"),
        ("TEAM1", "Team 1", "Multiplayer team 1 skin"),
        ("TEAM2", "Team 2", "Multiplayer team 2 skin")),
        description="Skin variant for Xbox multiplayer aircraft packages (*_MP*.bin)")

    def draw(self, context):
        lay = self.layout
        box = lay.box()
        box.label(text="Game Sources", icon="FILE_FOLDER")
        box.prop(self, "pc_path")
        box.prop(self, "xbox_path")
        box.prop(self, "platform", expand=True)
        box = lay.box()
        box.label(text="Import", icon="IMPORT")
        draw_import_options(box, self, browser=False)
        row = lay.row()
        row.label(text=f"Settings and scan cache: {user_dir(create=False)}")
        row.operator("crimson_skies.clear_cache", icon="TRASH")


def draw_import_options(lay, p, browser=True):
    col = lay.column(heading="Include")
    col.prop(p, "import_lods")
    col.prop(p, "import_collisions")
    lay.prop(p, "damage_states", expand=True)
    col = lay.column()
    col.prop(p, "animations")
    col.prop(p, "pack_images")
    if not browser or p.platform == "PC":
        lay.prop(p, "pc_livery")
    if not browser or p.platform == "XBOX":
        lay.prop(p, "team_skin")


_KEYS = ("platform", "pc_path", "xbox_path", "import_lods", "import_collisions", "damage_states", "animations",
         "pack_images", "pc_livery", "team_skin")


class _Defaults:
    """Stand-in for the preferences when the add-on is registered without an add-on entry
    (scripts that call register() directly): the defaults, overlaid with settings.json."""

    def __init__(self):
        for k in _KEYS:
            prop = CS_AddonPreferences.__annotations__[k]
            setattr(self, k, prop.keywords.get("default", ""))
        for k, v in load_settings().items():
            setattr(self, k, v)


def get():
    addon = bpy.context.preferences.addons.get(ADDON)
    if addon is not None and addon.preferences is not None:
        return addon.preferences
    return _Defaults()


def load_settings() -> dict:
    path = os.path.join(user_dir(create=False), SETTINGS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in doc.items() if k in _KEYS}


def save_settings(p):
    doc = {k: getattr(p, k) for k in _KEYS}
    path = os.path.join(user_dir(), SETTINGS_FILE)
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        os.replace(path + ".tmp", path)
    except OSError as exc:
        print(f"[CrimsonSkies] could not save settings: {exc}")


def restore_settings():
    """Applies settings.json to the registered preferences. Returns True if prefs exist."""
    global _loading
    addon = bpy.context.preferences.addons.get(ADDON)
    if addon is None or addon.preferences is None:
        return False
    p = addon.preferences
    saved = load_settings()
    if not saved:
        save_settings(p)  # first run: start the file from the current preferences
        return True
    _loading = True
    try:
        for k, v in saved.items():
            try:
                if getattr(p, k) != v:
                    setattr(p, k, v)
            except (TypeError, ValueError, AttributeError) as exc:
                print(f"[CrimsonSkies] settings: {k}: {exc}")
    finally:
        _loading = False
    return True


class CS_OT_clear_cache(bpy.types.Operator):
    """Delete the cached scans (the browser lists nothing until the next scan)"""
    bl_idname = "crimson_skies.clear_cache"
    bl_label = "Clear Scan Cache"

    def execute(self, context):
        from . import browser, catalog
        n = catalog.clear_cache(cache_dir())
        browser.reload_list(context)
        self.report({"INFO"}, f"Removed {n} cached scan(s)")
        return {"FINISHED"}


CLASSES = (CS_AddonPreferences, CS_OT_clear_cache)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
