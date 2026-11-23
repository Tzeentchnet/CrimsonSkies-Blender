"""Imports aircraft with a livery, repaints through the sidebar operator, renders each state."""
import os, sys, bpy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crimson_skies_importer as addon
from blender_test import frame_and_render
addon.register()
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register() if not hasattr(bpy.types.Scene, "cs_paint") else None
OUT = r"E:\tests\CrimsonSkies_Importer_20261123"
planes = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "player_bhawk"
steps = sys.argv[sys.argv.index("--") + 2:] if "--" in sys.argv else ["hughes"]
bpy.ops.import_scene.crimson_skies_pc_models(filepath=r"E:\CrimsonSkies\ZBD\planes.zbd", root_filter=planes,
                                             animations=False, arrange_spacing=18.0)
roots = [o for o in bpy.data.objects if o.get("cs_skin_prefix")]
print("[paint] aircraft:", [(r.name, r["cs_skin_prefix"], r.get("cs_paint_pattern"), r["cs_patterns"]) for r in roots])
tag = planes.replace("*", "all").replace(",", "+")
frame_and_render(os.path.join(OUT, f"paint_{tag}_import.png"), None, elevation=35, azimuth=-130, engine="BLENDER_EEVEE")
for step in steps:
    pattern, _, cols = step.partition(":")
    s = bpy.context.scene.cs_paint
    for r in roots:
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        r.select_set(True)
        bpy.context.view_layer.objects.active = r
        try:
            s.pattern = pattern
        except TypeError:
            print("[paint] pattern", pattern, "not offered for", r.name)
            continue
        s.custom_colours = bool(cols)
        if cols:
            c = [tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) for h in cols.split("/")]
            s.colour1, s.colour2, s.colour3 = c
        s.nose, s.tail, s.wing = "36", "-1", "-1"
        print("[paint]", r.name, pattern, bpy.ops.crimson_skies.apply_paint())
    frame_and_render(os.path.join(OUT, f"paint_{tag}_{pattern}{'_custom' if cols else ''}.png"), None,
                     elevation=35, azimuth=-130, engine="BLENDER_EEVEE")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, f"paint_{tag}.blend"), compress=True)
