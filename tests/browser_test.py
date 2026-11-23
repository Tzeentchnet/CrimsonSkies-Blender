"""The sidebar browser end to end, headless: enable the add-on, set the sources, scan, search,
import with the LOD / collision / damage preferences, check the livery panel follows the
imported aircraft, then wipe the preferences and check settings and scan come back.

    set BLENDER_USER_CONFIG=E:\\tests\\CrimsonSkies_Importer_<date>\\config
    blender -b --factory-startup --python tests/browser_test.py -- [pc|xbox|persist ...]

BLENDER_USER_CONFIG keeps the test's settings.json and scan cache out of your own config.
"""

import os
import sys

import addon_utils
import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blender_test import PC, XBOX_ISO, frame_and_render  # noqa: E402

ADDON = "crimson_skies_importer"
OUT = os.environ.get("CS_TEST_OUT", r"E:\tests\CrimsonSkies_Importer_20261123")
failures = []


def check(cond, what):
    print(("[ok]   " if cond else "[FAIL] ") + what)
    if not cond:
        failures.append(what)


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable(ADDON, default_set=True)
    from crimson_skies_importer import browser
    browser._startup()  # timers do not run in background mode
    return bpy.context.preferences.addons[ADDON].preferences


def reset():
    """An empty scene, as File > New does; the list comes back from the cache."""
    bpy.ops.wm.read_homefile(use_empty=True)
    from crimson_skies_importer import browser
    browser._startup()


def browser_state():
    return bpy.context.window_manager.cs_browser


def tick(names, kind=None):
    st = browser_state()
    for a in st.assets:
        a.checked = a.name in names and (kind is None or a.kind == kind)
    return sum(a.checked for a in st.assets)


def objs(pred):
    return [o for o in bpy.data.objects if pred(o)]


def layer_hidden(col):
    def walk(lc):
        if lc.collection == col:
            return lc
        for c in lc.children:
            r = walk(c)
            if r:
                return r
    lc = walk(bpy.context.view_layer.layer_collection)
    return lc is not None and lc.hide_viewport


def aux(kind):
    return [c for c in bpy.data.collections if c.get("cs_role") == kind]


def test_pc():
    p = fresh()
    p.platform = "PC"
    p.pc_path = PC
    check(bpy.ops.crimson_skies.browser_scan() == {"FINISHED"}, "PC scan runs")
    st = browser_state()
    check(len(st.assets) > 1000, f"PC index has {len(st.assets)} assets")
    from crimson_skies_importer import browser
    st.search = "bhawk"
    shown = [a.name for a, f in zip(st.assets, browser._visible_flags(st)) if f]
    check("player_bhawk" in shown and len(shown) < 10, f"search 'bhawk' shows {shown}")
    st.search = ""
    st.category = "Player Aircraft"
    shown = [a.name for a, f in zip(st.assets, browser._visible_flags(st)) if f]
    check(len(shown) == 11 and all(n.startswith("player") for n in shown), f"Player Aircraft shows {len(shown)}")
    st.category = "ALL"

    # defaults: no LODs, no damage, no collisions (settings.json may hold an earlier run's choice)
    rna = p.bl_rna.properties
    check(not rna["import_lods"].default and not rna["import_collisions"].default
          and rna["damage_states"].default == "OFF", "preference defaults")
    p.import_lods, p.import_collisions, p.damage_states = False, False, "OFF"
    tick({"player_bhawk"}, "pc_model")
    check(bpy.ops.crimson_skies.browser_import() == {"FINISHED"}, "import player_bhawk")
    names = {o.name.split(".")[0] for o in bpy.data.objects}
    check("pdp1" not in names and "destroyed" not in names and "piece1" not in names, "damage OFF skips damage nodes")
    check("fuse02" not in names and not aux("LODs"), "LODs OFF skips lower LODs")
    active = bpy.context.view_layer.objects.active
    s = bpy.context.scene.cs_paint
    check(active is not None and active.name.startswith("player_bhawk"), f"aircraft is active ({active and active.name})")
    check(s.synced.startswith("player_bhawk") and s.pattern == p.pc_livery,
          f"livery panel synced: pattern={s.pattern} synced={s.synced}")
    from crimson_skies_importer import pc_paint
    items = [i[0] for i in pc_paint._pattern_items(s, bpy.context)]
    check(set(items) == {"shipped", "blake", "fortune", "hughes"}, f"Bloodhawk livery list {items}")
    frame_and_render(os.path.join(OUT, "browser_pc_bhawk.png"), None, elevation=25, azimuth=-130)

    # LODs ON, damage HIDDEN, collisions ON
    reset()
    p.import_lods, p.damage_states, p.import_collisions = True, "HIDDEN", True
    tick({"player_bhawk", "player_fury"}, "pc_model")
    bpy.ops.crimson_skies.browser_import()
    lods, dmg = aux("LODs"), aux("Damage")
    check(len(lods) == 2 and all(layer_hidden(c) for c in lods), f"LOD collections hidden: {[c.name for c in lods]}")
    check(any(o.name.startswith("fuse02") for c in lods for o in c.all_objects), "Bloodhawk LOD meshes in LODs")
    check(len(dmg) == 2 and all(layer_hidden(c) for c in dmg), f"Damage collections hidden: {[c.name for c in dmg]}")
    dmg_objs = [o for c in dmg for o in c.all_objects]
    check(any(o.name.startswith("pdp1") for o in dmg_objs) and all(not o.hide_viewport for o in dmg_objs),
          f"damage objects visible inside the hidden collection ({len(dmg_objs)})")
    check(all(o.get("cs_collision") for o in objs(lambda o: o.type == "MESH" and o.get("cs_node_index") is not None)),
          "collision role tagged on PC meshes")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "browser_pc_options.blend"), compress=True)

    # damage ON: in place and visible
    reset()
    p.import_lods, p.damage_states, p.import_collisions = False, "ON", False
    tick({"player_bhawk"}, "pc_model")
    bpy.ops.crimson_skies.browser_import()
    pdp = objs(lambda o: o.name.startswith("pdp1"))
    check(pdp and not pdp[0].hide_viewport and not aux("Damage"), "damage ON: visible, no Damage collection")
    p.damage_states = "OFF"

    # a level object with bounding-box colliders, and a whole level
    reset()
    p.import_collisions = True
    from crimson_skies_importer.formats import pc_gamez
    gz = pc_gamez.read(open(os.path.join(PC, "ZBD", "C1", "gamez.zbd"), "rb").read())
    owners = set()
    for n in gz.nodes:
        if n.flags & 0x20:
            i = n.index
            while gz.nodes[i].parents:
                i = gz.nodes[i].parents[0]
            owners.add(gz.nodes[i].name)
    pick = sorted(owners)[0]
    tick({pick}, "pc_object")
    bpy.ops.crimson_skies.browser_import()
    boxes = objs(lambda o: o.get("cs_collision") == "bbox" and o.name.endswith("_bbox"))
    check(len(boxes) > 0 and all(o.display_type == "WIRE" for o in boxes), f"{pick}: {len(boxes)} bbox colliders")
    p.import_collisions = False
    reset()
    tick({"C1 World + M02"}, "pc_level")
    bpy.ops.crimson_skies.browser_import()
    check(len(bpy.data.objects) > 2000, f"C1 + M02 world: {len(bpy.data.objects)} objects")
    check(not objs(lambda o: "destroyed" in o.name.lower()), "C1 world without destroyed variants")


def test_xbox():
    p = fresh()
    p.platform = "XBOX"
    p.xbox_path = XBOX_ISO
    check(bpy.ops.crimson_skies.browser_scan() == {"FINISHED"}, "Xbox scan runs")
    st = browser_state()
    check(len(st.assets) > 300, f"Xbox index has {len(st.assets)} assets")

    tick({"PF_Bandito"}, "xbox_package")
    bpy.ops.crimson_skies.browser_import()
    names = [o.name for o in bpy.data.objects]
    check(not any("_dmg1" in n.lower() or n.lower().startswith("collide_") for n in names),
          "damage and collisions OFF: no _dmg1-4, no collide_")
    check(not any("debris" in c.name.lower() or "fusalage" in c.name.lower() for c in bpy.data.collections),
          "damage OFF: no debris / fuselage models")
    check(any(n.lower().endswith("_dmg0") for n in names), "healthy _dmg0 state kept")

    reset()
    p.import_collisions, p.damage_states = True, "HIDDEN"
    tick({"PF_Bandito"}, "xbox_package")
    bpy.ops.crimson_skies.browser_import()
    col = aux("Collision")
    cobjs = [o for c in col for o in c.all_objects]
    check(len(cobjs) == 5 and all(o.display_type == "WIRE" and not o.hide_viewport for o in cobjs),
          f"5 collide_ cards in a visible Collision collection ({len(cobjs)})")
    dmg = aux("Damage")
    check(dmg and all(layer_hidden(c) for c in dmg), f"damage collections hidden: {[c.name for c in dmg]}")
    check(any(o.name.lower().endswith("_dmg1") for c in dmg for o in c.all_objects), "_dmg1 in Damage")
    frame_and_render(os.path.join(OUT, "browser_xbox_bandito.png"), None, elevation=30, azimuth=-50)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "browser_xbox_bandito.blend"), compress=True)

    reset()
    p.import_collisions, p.damage_states = False, "OFF"
    tick({"Paratrooper", "Searchlight"}, "xbox_model")
    bpy.ops.crimson_skies.browser_import()
    check(len(bpy.data.objects) > 5, f"Xbox models import ({len(bpy.data.objects)} objects)")
    reset()
    n = tick({"debris_metal1"}, "xbox_model")
    bpy.ops.crimson_skies.browser_import()
    check(n == 1 and len(objs(lambda o: o.type == "MESH")) > 3, "a debris model picked by itself imports with damage OFF")


def test_persist():
    p = fresh()
    p.platform, p.pc_path, p.xbox_path = "XBOX", PC, XBOX_ISO
    p.import_lods, p.damage_states, p.pc_livery = True, "HIDDEN", "hughes"
    from crimson_skies_importer import prefs
    saved = prefs.load_settings()
    check(saved.get("damage_states") == "HIDDEN" and saved.get("pc_livery") == "hughes", f"settings.json: {saved}")
    bpy.ops.crimson_skies.browser_scan()
    n = len(browser_state().assets)
    # factory reset: the add-on is disabled and its preferences dropped; enabling it again restores them
    addon_utils.disable(ADDON, default_set=True)
    p = fresh()
    check(p.platform == "XBOX" and p.import_lods and p.damage_states == "HIDDEN" and p.pc_livery == "hughes",
          f"preferences restored after reset: {p.platform} lods={p.import_lods} dmg={p.damage_states}")
    check(len(browser_state().assets) == n, f"scan reloaded from cache ({len(browser_state().assets)} of {n})")
    # loading a file refills the list too
    reset()
    check(len(browser_state().assets) == n, "list refilled after loading a file")
    p.platform = "PC"
    check(len(browser_state().assets) != n, "switching game swaps the list")
    p.platform = "XBOX"
    for k, v in (("import_lods", False), ("damage_states", "OFF"), ("pc_livery", "fortune")):
        setattr(p, k, v)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    which = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["persist", "pc", "xbox"]
    for name in which:
        print(f"===== {name}")
        globals()[f"test_{name}"]()
    print(f"===== {len(failures)} failure(s)")
    for f in failures:
        print("  ", f)
    sys.exit(1 if failures else 0)
