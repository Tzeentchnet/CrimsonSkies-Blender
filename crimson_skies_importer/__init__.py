"""Crimson Skies importer for Blender.

PC (Crimson Skies, 2000): planes.zbd models, chapter gamez.zbd levels, texture archives,
compiled SI-script animations (cam_anim.zbd / mis_anim.zbd).
Xbox (Crimson Skies: High Road to Revenge, 2003): read straight from the disc image (.iso /
.xiso) or from extracted files: resource packages (.bin, also inside airplanes.zip),
compiled .x models, textures, .anm animations and level worlds.

Two ways in: File > Import > Crimson Skies, or the asset browser in the 3D view's sidebar
(N > Crimson Skies), which scans a PC install folder or the Xbox disc image into a searchable,
categorised list. LODs, collisions and damage states are add-on preferences shared by both.
"""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

bl_info = {
    "name": "Crimson Skies Importer",
    "author": "CrimsonSkies-Blender",
    "version": (1, 2, 0),
    "blender": (4, 4, 0),
    "location": "File > Import > Crimson Skies; View3D > Sidebar > Crimson Skies",
    "description": "Import models, materials, animations and levels from Crimson Skies PC and Xbox",
    "category": "Import-Export",
}


def _livery_items():
    from .formats import pc_rof
    items = [("shipped", "Shipped (unpainted)", "The unpainted key textures from texture.zbd")]
    return items + [(p, pc_rof.PATTERN_LABELS[p], f"{pc_rof.PATTERN_LABELS[p]} livery") for p in pc_rof.PATTERNS]


def _decal_items():
    from .formats import pc_rof
    return [("-1", "Pattern default", "The pattern's own decal")] + [
        (str(i), n, n) for i, n in enumerate(pc_rof.DECALS)]


def _props(op, *mixins, **extra):
    keys = set()
    for m in mixins:
        for klass in m.__mro__:
            keys.update(getattr(klass, "__annotations__", {}).keys())
    kw = {k: getattr(op, k) for k in keys if hasattr(op, k)}
    kw.update(extra)
    return kw


def _shared(fn):
    """The LOD / collision / damage choices from the add-on preferences, for the menu operators."""
    from . import prefs
    p = prefs.get()
    return {k: v for k, v in fn(p).items() if k in ("import_lods", "import_collisions", "damage_states")}


def _report(op):
    def r(msg):
        print("[CrimsonSkies]", msg)
        op.report({"INFO"}, msg)
    return r


class _CommonProps:
    pack_images: BoolProperty(name="Pack Images", default=True,
                              description="Pack decoded textures into the .blend")
    image_dir: StringProperty(name="Texture Folder", subtype="DIR_PATH", default="",
                              description="If set, decoded textures are written here as PNG instead of packed")
    animations: BoolProperty(name="Animations", default=True, description="Import animations as actions")
    fps: IntProperty(name="Frame Rate", default=30, min=1, max=240)


# ----------------------------------------------------------------------------------------- PC

class _PcProps(_CommonProps):
    hide_inactive: BoolProperty(name="Hide Inactive Nodes", default=True,
                                description="Hide nodes the game starts inactive (damage states, destroyed parts)")
    import_normals: BoolProperty(name="Custom Normals", default=True)
    vertex_colors: BoolProperty(name="Vertex Colors", default=True,
                                description="Multiply the baked vertex colours into the materials")
    unlit: BoolProperty(name="Unlit (Emission)", default=False,
                        description="Fullbright materials, like the original's world rendering")
    root_filter: StringProperty(name="Root Filter", default="",
                                description="Comma-separated root node names or wildcards (empty = all)")


class CS_OT_import_pc_models(bpy.types.Operator, ImportHelper, _PcProps):
    """Import Crimson Skies (PC) aircraft and models from planes.zbd"""
    bl_idname = "import_scene.crimson_skies_pc_models"
    bl_label = "Import CS PC Models (planes.zbd)"
    bl_options = {"REGISTER", "UNDO", "PRESET"}
    filename_ext = ".zbd"
    filter_glob: StringProperty(default="*.zbd", options={"HIDDEN"})
    arrange_spacing: FloatProperty(name="Arrange Spacing", default=25.0, min=0.0,
                                   description="Lay several models out in a grid (0 = all at the origin)")
    display_state: BoolProperty(name="Flight-Ready State", default=True,
                                description="Hide the shadow and propeller spin frames the game starts hidden. "
                                            "Damage states follow the add-on preferences")
    cockpit: BoolProperty(name="Cockpit Interior", default=False,
                          description="Also import the separate cockpit interior model")
    paint_pattern: EnumProperty(name="Livery", default="fortune", items=_livery_items(),
                                description="Paint pattern from crimson.rof (falls back to Fortune Hunters "
                                            "on aircraft that do not ship the chosen one)")
    custom_colours: BoolProperty(name="Custom Colours", default=False)
    colour1: FloatVectorProperty(name="Body", subtype="COLOR_GAMMA", size=3, min=0, max=1,
                                 default=(223 / 255, 0, 41 / 255))
    colour2: FloatVectorProperty(name="Dark Trim", subtype="COLOR_GAMMA", size=3, min=0, max=1,
                                 default=(25 / 255,) * 3)
    colour3: FloatVectorProperty(name="Light Trim", subtype="COLOR_GAMMA", size=3, min=0, max=1,
                                 default=(1.0, 1.0, 1.0))
    nose_decal: EnumProperty(name="Nose Decal", items=_decal_items(), default="-1")
    tail_decal: EnumProperty(name="Tail Decal", items=_decal_items(), default="-1")
    wing_decal: EnumProperty(name="Wing Decal", items=_decal_items(), default="-1")

    def execute(self, context):
        from . import pc_import
        cols = None
        if self.custom_colours:
            cols = [tuple(c * 255.0 for c in v) for v in (self.colour1, self.colour2, self.colour3)]
        opts = pc_import.PcOptions(**_props(self, _PcProps, **_shared(pc_import.options_from_prefs),
                                            arrange_spacing=self.arrange_spacing,
                                            display_state=self.display_state, cockpit=self.cockpit,
                                            paint_pattern=self.paint_pattern, paint_colours=cols,
                                            paint_decals=[int(self.nose_decal), int(self.tail_decal),
                                                          int(self.wing_decal)]))
        pc_import.import_planes(self.filepath, opts, _report(self))
        return {"FINISHED"}


class CS_OT_import_pc_level(bpy.types.Operator, ImportHelper, _PcProps):
    """Import a Crimson Skies (PC) chapter world (ZBD/<chapter>/gamez.zbd)"""
    bl_idname = "import_scene.crimson_skies_pc_level"
    bl_label = "Import CS PC Level (gamez.zbd)"
    bl_options = {"REGISTER", "UNDO", "PRESET"}
    filename_ext = ".zbd"
    filter_glob: StringProperty(default="*.zbd", options={"HIDDEN"})
    world_only: BoolProperty(name="World Content Only", default=True,
                             description="Only the world node and its partitioned scenery, not effect templates")
    mission: StringProperty(name="Mission", default="",
                            description="Mission folder (e.g. M02) whose mis_anim.zbd animations are added")

    def execute(self, context):
        from . import pc_import
        kw = _props(self, _PcProps, world_only=self.world_only, mission=self.mission,
                    **_shared(pc_import.options_from_prefs))
        pc_import.import_level(self.filepath, pc_import.PcOptions(**kw), _report(self))
        return {"FINISHED"}


# --------------------------------------------------------------------------------------- Xbox

class _XboxProps(_CommonProps):
    hide_helpers: BoolProperty(name="Hide Helper Nodes", default=True,
                               description="Hide the propeller blur, effect cards and weapon/exhaust markers. "
                                           "Damage states and collisions follow the add-on preferences")
    normal_maps: BoolProperty(name="Normal Maps", default=True)
    vertex_colors: BoolProperty(name="Vertex Colors", default=True)


class CS_OT_import_xbox(bpy.types.Operator, ImportHelper, _XboxProps):
    """Import from Crimson Skies: High Road to Revenge (Xbox): a disc image, a resource package
    (.bin), airplanes.zip, or a loose .x model"""
    bl_idname = "import_scene.crimson_skies_xbox"
    bl_label = "Import CS Xbox (.iso/.bin/.zip/.x)"
    bl_options = {"REGISTER", "UNDO", "PRESET"}
    filter_glob: StringProperty(default="*.iso;*.xiso;*.bin;*.zip;*.x", options={"HIDDEN"})
    entry: StringProperty(name="Package / Entry", default="",
                          description="Inside a disc or zip: the package to import (e.g. SH_Tutorial.bin, "
                                      "ca/res/PF_Bandito.bin). Inside a package: a model name filter "
                                      "(wildcards, comma-separated). Empty = every package's main content")
    mode: EnumProperty(name="Import", items=(
        ("AUTO", "Auto", "Levels import their world; aircraft and model packages import their models"),
        ("LEVEL", "Level World", "Place every world instance of a level package"),
        ("MODELS", "Models", "Import each model of the package once, laid out in a grid"),
        ("ANIMS", "Models + Animations", "Models, with every .anm of the package applied where it fits"),
    ), default="AUTO")
    arrange_spacing: FloatProperty(name="Arrange Spacing", default=30.0, min=0.0)
    team_skin: EnumProperty(name="Team Skin", default="NOTEAM", items=(
        ("NOTEAM", "No Team", "The neutral multiplayer skin"),
        ("TEAM1", "Team 1", "Multiplayer team 1 skin"),
        ("TEAM2", "Team 2", "Multiplayer team 2 skin")),
        description="Skin variant for multiplayer aircraft packages (*_MP*.bin)")
    lightmaps: BoolProperty(name="Lightmaps", default=True,
                            description="Add the level's lightmaps as a second UV texture in level materials")

    def execute(self, context):
        from . import xbox_import
        kw = _props(self, _XboxProps, **_shared(xbox_import.options_from_prefs), entry=self.entry, mode=self.mode,
                    arrange_spacing=self.arrange_spacing, lightmaps=self.lightmaps, team_skin=self.team_skin)
        xbox_import.import_path(self.filepath, xbox_import.XboxOptions(**kw), _report(self))
        return {"FINISHED"}


class TOPBAR_MT_file_import_crimson_skies(bpy.types.Menu):
    bl_idname = "TOPBAR_MT_file_import_crimson_skies"
    bl_label = "Crimson Skies"

    def draw(self, context):
        lay = self.layout
        lay.operator(CS_OT_import_pc_models.bl_idname, text="PC Models (planes.zbd)")
        lay.operator(CS_OT_import_pc_level.bl_idname, text="PC Level (gamez.zbd)")
        lay.separator()
        lay.operator(CS_OT_import_xbox.bl_idname, text="Xbox: High Road to Revenge (.iso/.bin/.zip/.x)")


def _menu(self, context):
    self.layout.menu(TOPBAR_MT_file_import_crimson_skies.bl_idname)


CLASSES = (CS_OT_import_pc_models, CS_OT_import_pc_level, CS_OT_import_xbox, TOPBAR_MT_file_import_crimson_skies)


def register():
    from . import browser, pc_paint, prefs
    prefs.register()
    for c in CLASSES:
        bpy.utils.register_class(c)
    browser.register()
    pc_paint.register()
    bpy.types.TOPBAR_MT_file_import.append(_menu)


def unregister():
    from . import browser, pc_paint, prefs
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    pc_paint.unregister()
    browser.unregister()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    prefs.unregister()
