"""Opens Blender with a window, shows the Crimson Skies sidebar after a scan and an import, and
saves screenshots of the browser and the livery panel, then quits. Needs a display:

    set BLENDER_USER_CONFIG=E:\\tests\\CrimsonSkies_Importer_<date>\\config
    blender --factory-startup --python tests/ui_screenshot.py -- [pc|xbox]
"""

import os
import sys

import addon_utils
import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_test import PC, XBOX_ISO  # noqa: E402

OUT = os.environ.get("CS_TEST_OUT", r"E:\tests\CrimsonSkies_Importer_20261123")
PLATFORM = (sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "pc").upper()
steps = []


def view3d():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == "VIEW_3D":
            region = next(r for r in area.regions if r.type == "WINDOW")
            return win, area, region
    raise RuntimeError("no 3D view")


def step_setup():
    addon_utils.enable("crimson_skies_importer", default_set=True)
    from crimson_skies_importer import browser
    p = bpy.context.preferences.addons["crimson_skies_importer"].preferences
    p.platform = PLATFORM
    if PLATFORM == "PC":
        p.pc_path = PC
    else:
        p.xbox_path = XBOX_ISO
    browser._startup()
    win, area, region = view3d()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    area.spaces.active.show_region_ui = True
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.crimson_skies.browser_scan()
    st = bpy.context.window_manager.cs_browser
    st.category = "Player Aircraft"
    want = "player_bhawk" if PLATFORM == "PC" else "PF_Bandito"
    for i, a in enumerate(st.assets):
        if a.name == want:
            st.index = i
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.crimson_skies.browser_import()
        bpy.ops.view3d.view_all()
    # the sidebar's active tab is read-only from Python: show the panels on the default tab instead
    from crimson_skies_importer import browser, pc_paint
    for i, cls in enumerate((browser.CS_PT_browser, browser.CS_PT_browser_options, pc_paint.CS_PT_paint)):
        bpy.utils.unregister_class(cls)
        cls.bl_category = "Item"
        if not getattr(cls, "bl_parent_id", ""):
            cls.bl_order = -10 + i if cls is not pc_paint.CS_PT_paint else -20
    for cls in (browser.CS_PT_browser, browser.CS_PT_browser_options, pc_paint.CS_PT_paint):
        bpy.utils.register_class(cls)
    for r in area.regions:
        r.tag_redraw()


def step_shot():
    win, area, region = view3d()
    path = os.path.join(OUT, f"ui_sidebar_{PLATFORM.lower()}.png")
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=path)
    print("screenshot", path)


def step_quit():
    bpy.ops.wm.quit_blender()


def run():
    if not steps:
        return None
    fn = steps.pop(0)
    try:
        fn()
    except Exception:
        import traceback
        traceback.print_exc()
        bpy.ops.wm.quit_blender()
    return 1.5


steps[:] = [step_setup, step_shot, step_quit]
bpy.app.timers.register(run, first_interval=2.0)
