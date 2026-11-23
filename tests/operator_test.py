"""Registers the add-on and runs each import operator once, the way the File > Import menu does."""
import os, sys, bpy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import crimson_skies_importer as addon
addon.register()
OUT = r"E:\tests\CrimsonSkies_Importer_20261123"
ISO = (r"F:\Recomp_GameSource\Crimson Skies - High Road to Revenge (USA) (En,Fr,De,Zh,Ko)"
       r"\Crimson Skies - High Road to Revenge (USA) (En,Fr,De,Zh,Ko).xiso.iso")
cases = [
    ("pc_c1_ops", lambda: bpy.ops.import_scene.crimson_skies_pc_level(filepath=r"E:\CrimsonSkies\ZBD\C1\gamez.zbd", mission="M02")),
    ("pc_planes_ops", lambda: bpy.ops.import_scene.crimson_skies_pc_models(filepath=r"E:\CrimsonSkies\ZBD\planes.zbd")),
    ("xbox_level_ops", lambda: bpy.ops.import_scene.crimson_skies_xbox(filepath=ISO, entry="mp_SS1.bin")),
    ("xbox_planes_ops", lambda: bpy.ops.import_scene.crimson_skies_xbox(filepath=ISO, entry="PF_*_Upgrade.bin", mode="MODELS")),
]
only = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
for name, run in cases:
    if only and name not in only:
        continue
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon.register() if not hasattr(bpy.types, "TOPBAR_MT_file_import_crimson_skies") else None
    res = run()
    n_obj = len(bpy.data.objects); n_act = len(bpy.data.actions); n_img = len(bpy.data.images)
    cams = [o.name for o in bpy.data.objects if o.type == "CAMERA"]
    print(f"[ops] {name}: {res} objects={n_obj} actions={n_act} images={n_img} cameras={len(cams)}")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, name + ".blend"), compress=True)
