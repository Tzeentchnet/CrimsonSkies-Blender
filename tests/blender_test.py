"""Headless test driver. Run:

    blender --background --factory-startup --python tests/blender_test.py -- <case> [out_dir]

Registers the add-on from this repository, runs one import case, saves the .blend and
renders a preview PNG framing what was imported.
"""

import math
import os
import sys
import time

import bpy
from mathutils import Vector

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import crimson_skies_importer as addon  # noqa: E402

PC = r"E:\CrimsonSkies"
XBOX_ISO = (r"F:\Recomp_GameSource\Crimson Skies - High Road to Revenge (USA) (En,Fr,De,Zh,Ko)"
            r"\Crimson Skies - High Road to Revenge (USA) (En,Fr,De,Zh,Ko).xiso.iso")


def argv():
    a = sys.argv
    return a[a.index("--") + 1:] if "--" in a else []


def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def frame_and_render(png, focus=None, elevation=35.0, azimuth=-60.0, engine="BLENDER_WORKBENCH",
                     res=(1280, 800), frame=None):
    sc = bpy.context.scene
    if frame is not None:
        sc.frame_set(frame)
    bpy.context.view_layer.update()
    pts = []
    objs = focus or [o for o in sc.objects if o.type == "MESH" and o.visible_get()]
    for o in objs:
        if o.type != "MESH" or not o.visible_get():
            continue
        for c in o.bound_box:
            pts.append(o.matrix_world @ Vector(c))
    if not pts:
        print("nothing visible to frame")
        return
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 0.5)
    cam_data = bpy.data.cameras.new("preview_cam")
    cam_data.lens = 40
    cam_data.clip_end = radius * 20
    cam_data.clip_start = radius / 1000
    cam = bpy.data.objects.new("preview_cam", cam_data)
    sc.collection.objects.link(cam)
    el, az = math.radians(elevation), math.radians(azimuth)
    d = radius * 2.4
    cam.location = center + Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))) * d
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam
    sc.render.engine = engine
    if engine == "BLENDER_WORKBENCH":
        sh = sc.display.shading
        sh.light = "STUDIO"
        sh.color_type = "TEXTURE"
        sh.show_backface_culling = False
    if engine != "BLENDER_WORKBENCH":
        if sc.world is None:
            sc.world = bpy.data.worlds.new("preview_world")
        sc.world.color = (0.6, 0.65, 0.75)
        if not any(o.type == "LIGHT" and o.data.type == "SUN" for o in sc.objects):
            sun = bpy.data.lights.new("preview_sun", "SUN")
            sun.energy = 3.0
            so = bpy.data.objects.new("preview_sun", sun)
            so.rotation_euler = (math.radians(40), 0, math.radians(30))
            sc.collection.objects.link(so)
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.filepath = png
    sc.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam)
    print("rendered", png)


def report(msg):
    print("[report]", msg)


def run(case, out_dir):
    from crimson_skies_importer import pc_import
    os.makedirs(out_dir, exist_ok=True)
    clear()
    t = time.time()
    focus = None
    kw = {}
    if case == "pc_planes":
        pc_import.import_planes(os.path.join(PC, "ZBD", "planes.zbd"),
                                pc_import.PcOptions(root_filter="player_*"), report)
        kw = dict(elevation=30, azimuth=-50)
    elif case == "pc_bloodhawk":
        pc_import.import_planes(os.path.join(PC, "ZBD", "planes.zbd"),
                                pc_import.PcOptions(root_filter="player_bhawk"), report)
        kw = dict(elevation=25, azimuth=-130)
    elif case.startswith("pc_level_"):
        ch = case.split("_", 2)[2].upper()
        pc_import.import_level(os.path.join(PC, "ZBD", ch, "gamez.zbd"), pc_import.PcOptions(), report)
        kw = dict(elevation=50, azimuth=-60)
    elif case.startswith("xbox_"):
        from crimson_skies_importer import xbox_import
        spec = case[5:]
        entry, _, mode = spec.partition("@")
        mode = mode or "AUTO"
        xbox_import.import_path(XBOX_ISO, xbox_import.XboxOptions(entry=entry, mode=mode), report)
        kw = dict(elevation=30, azimuth=-50)
    else:
        raise SystemExit(f"unknown case {case}")
    print(f"import took {time.time() - t:.1f}s")
    blend = os.path.join(out_dir, f"{case.replace(':', '_').replace('/', '_').replace('@', '_').replace('*', '')}.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
    print("saved", blend)
    frame_and_render(os.path.join(out_dir, f"{case.replace(':', '_').replace('/', '_').replace('@', '_').replace('*', '')}.png"), focus, **kw)


if __name__ == "__main__":
    a = argv()
    run(a[0], a[1] if len(a) > 1 else r"E:\tests\CrimsonSkies_Importer_20261123")
