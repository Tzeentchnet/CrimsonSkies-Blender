"""Asset catalog: scans a Crimson Skies source for importable assets and caches the result.

No bpy here, so the scan can run (and be tested) outside Blender. An asset is a plain dict:

    name      display name
    category  browser category
    kind      pc_model | pc_level | pc_object | xbox_package | xbox_model
    source    PC: path relative to the install (ZBD/planes.zbd, ZBD/C1/gamez.zbd)
              Xbox: package name inside the source (SH_Tutorial.bin, airplanes.zip/ca/res/PF_Bandito.bin)
    ref       PC model/object: node index; PC level: mission folder or ''; Xbox model: model entry name
    info      one line shown next to the name

The cache is one JSON file per platform. It records the source path and a stamp (sizes and
modification times of what was scanned), so a cache for another source, or for files that
changed, is reported as stale rather than silently used.
"""

from __future__ import annotations

import json
import os
import re
import time

CACHE_VERSION = 1

# ------------------------------------------------------------------------------------------- PC

_PC_SKIP_KINDS = ("Camera", "Window", "Display", "World")
_PC_EFFECT = re.compile(
    r"fire(?!truck)|smoke|spark|splash|flak|muzzle|trail|explo|puff|flash|bullethole|dust|ripple|burst|impact|"
    r"flare|steam|sonic|_effect|letterbox|spyglass|sgwin|flame|gunhit|touchdown|cloud|^genx|ring\d|"
    r"_control|flake", re.I)
_PC_DAMAGE = re.compile(r"destroy|dstry|damage|broken|wreck", re.I)


def _pc_files(install: str):
    """(relative path, absolute path) of every archive the PC scan reads."""
    zbd = os.path.join(install, "ZBD")
    out = []
    planes = os.path.join(zbd, "planes.zbd")
    if os.path.isfile(planes):
        out.append(("ZBD/planes.zbd", planes))
    if os.path.isdir(zbd):
        for ch in sorted(os.listdir(zbd)):
            p = os.path.join(zbd, ch, "gamez.zbd")
            if os.path.isfile(p):
                out.append((f"ZBD/{ch}/gamez.zbd", p))
    return out


def _stamp(paths):
    parts = []
    for p in paths:
        try:
            st = os.stat(p)
            parts.append(f"{os.path.basename(p)}:{st.st_size}:{int(st.st_mtime)}")
        except OSError:
            parts.append(f"{os.path.basename(p)}:missing")
    return "|".join(parts)


def pc_stamp(install: str) -> str:
    return _stamp([a for _r, a in _pc_files(install)])


def _subtree_names(gz, index):
    names, stack, seen = set(), [index], set()
    while stack:
        i = stack.pop()
        if i in seen or not (0 <= i < len(gz.nodes)):
            continue
        seen.add(i)
        n = gz.nodes[i]
        names.add((n.name or "").lower())
        stack.extend(n.children)
    return names, len(seen)


def scan_pc(install: str, progress=None):
    """Every importable asset of a PC install: the aircraft and models of planes.zbd, each
    chapter world (and each mission's animation set), and each chapter's other root objects."""
    from .formats import pc_gamez

    install = os.path.abspath(install)
    files = _pc_files(install)
    if not files:
        raise FileNotFoundError(f"{install}: no ZBD/planes.zbd or ZBD/<chapter>/gamez.zbd; "
                                f"pick the Crimson Skies install folder")
    assets = []
    templates_seen = set()
    for i, (rel, path) in enumerate(files):
        if progress:
            progress(i, len(files), rel)
        with open(path, "rb") as f:
            gz = pc_gamez.read(f.read())
        if rel == "ZBD/planes.zbd":
            for n in gz.roots():
                if n.kind in _PC_SKIP_KINDS:
                    continue
                names, count = _subtree_names(gz, n.index)
                if n.name.lower().startswith("player"):
                    cat = "Player Aircraft"
                elif "healthy" in names:
                    cat = "AI Aircraft"
                else:
                    cat = "Characters & Props"
                assets.append(dict(name=n.name, category=cat, kind="pc_model", source=rel,
                                   ref=str(n.index), info=f"planes.zbd, {count} nodes"))
            continue
        chapter = rel.split("/")[1]
        chapter_dir = os.path.dirname(path)
        assets.append(dict(name=f"{chapter} World", category="Levels", kind="pc_level", source=rel, ref="",
                           info=f"chapter {chapter}, cam_anim.zbd animations"))
        for m in sorted(os.listdir(chapter_dir)):
            if os.path.isfile(os.path.join(chapter_dir, m, "mis_anim.zbd")):
                assets.append(dict(name=f"{chapter} World + {m}", category="Levels", kind="pc_level",
                                   source=rel, ref=m, info=f"chapter {chapter}, mission {m} animations"))
        for n in gz.roots():
            if n.kind in _PC_SKIP_KINDS or not n.name:
                continue
            _names, count = _subtree_names(gz, n.index)
            # effect and weapon templates repeat in every chapter: the same name with the same
            # node count is listed once, from the first chapter that has it
            key = (n.name.lower(), count)
            if key in templates_seen:
                continue
            templates_seen.add(key)
            if _PC_EFFECT.search(n.name):
                cat = "Effects"
            elif _PC_DAMAGE.search(n.name):
                cat = "Destroyed States"
            else:
                cat = "Level Objects"
            assets.append(dict(name=n.name, category=cat, kind="pc_object", source=rel, ref=str(n.index),
                               info=f"{chapter}, {count} nodes"))
    if progress:
        progress(len(files), len(files), "done")
    return _sorted(assets), pc_stamp(install)


# ----------------------------------------------------------------------------------------- Xbox

_XB_MODEL_RULES = (
    (re.compile(r"debris|_dmg\d|collapse|collaspe|_fusalage", re.I), "Debris & Damage"),
    (re.compile(r"boss", re.I), "Bosses"),
    (re.compile(r"^zep", re.I), "Zeppelins"),
    (re.compile(r"^airplane", re.I), "Aircraft Models"),
    (re.compile(r"^aagun|searchlight|pandoragun|turret|_gun", re.I), "Turrets & Defenses"),
    (re.compile(r"^char_|paratrooper|parachute(?!_health)|pilot", re.I), "Characters"),
    (re.compile(r"powerup|_health|^ctf_|^target|^mp_target", re.I), "Pickups & Targets"),
    (re.compile(r"car_|truck|train|tug|gunboat|boat|bus|_cab", re.I), "Vehicles"),
    (re.compile(r"sky|night|twilight|storm", re.I), "Skies"),
    (re.compile(r"fireball|emitter|lightcone|volume|^fx|rocket|^single_", re.I), "Effects"),
)
_XB_SKIP_MODEL = re.compile(r"^(camera\d*|emptymodel)\.x$", re.I)


def xbox_stamp(path: str) -> str:
    if os.path.isdir(path):
        return f"dir:{int(os.stat(path).st_mtime)}"
    return _stamp([path])


def _xbox_package_category(name: str, pkg) -> str:
    base = name.rsplit("/", 1)[-1]
    low = base.lower()
    if pkg.is_level:
        return "Multiplayer Levels" if low.startswith("mp_") else "Levels"
    if low.startswith("pf_"):
        return "Player Aircraft"
    if low.startswith("ai_"):
        return "AI Aircraft"
    if low.startswith("amb_"):
        return "Ambient Aircraft"
    return "Other Packages"


def _xbox_model_category(base_name: str) -> str:
    for rx, cat in _XB_MODEL_RULES:
        if rx.search(base_name):
            return cat
    return "Props"


def scan_xbox(path: str, progress=None):
    """Every importable asset of the Xbox game: each package (levels, aircraft) and each distinct
    model of the level and shared packages (aircraft packages are listed whole)."""
    from .formats import xbox_package

    src = xbox_package.Source(path)
    assets = []
    models = {}  # lower base name -> asset
    try:
        names = src.package_names()
        if not names:
            raise FileNotFoundError(f"{path}: no Crimson Skies packages found; pick the disc image (.iso/.xiso)")
        for i, n in enumerate(names):
            if progress:
                progress(i, len(names), n)
            try:
                pkg = src.package(n)
            except Exception as exc:  # one unreadable package must not stop the scan
                print(f"[CrimsonSkies] scan {n}: {exc}")
                continue
            base = n.rsplit("/", 1)[-1]
            cat = _xbox_package_category(n, pkg)
            counts = {}
            for e in pkg.entries:
                counts[e.type] = counts.get(e.type, 0) + 1
            n_models = sum(1 for e in pkg.of_type(xbox_package.TYPE_MODEL) if "|" not in e.name)
            n_units = counts.get(xbox_package.TYPE_MODEL, 0) - n_models
            bits = []
            if pkg.is_level:
                bits.append(f"{n_units} placed units")
            bits.append(f"{n_models} models")
            if counts.get(xbox_package.TYPE_ANIM):
                bits.append(f"{counts[xbox_package.TYPE_ANIM]} anims")
            assets.append(dict(name=os.path.splitext(base)[0], category=cat, kind="xbox_package", source=n, ref="",
                               info=", ".join(bits)))
            if cat in ("Player Aircraft", "AI Aircraft", "Ambient Aircraft"):
                continue
            for e in pkg.of_type(xbox_package.TYPE_MODEL):
                if "|" in e.name or not e.base_name or _XB_SKIP_MODEL.match(e.base_name):
                    continue
                key = e.base_name.lower()
                a = models.get(key)
                if a is None:
                    models[key] = dict(name=os.path.splitext(e.base_name)[0], category=_xbox_model_category(e.base_name),
                                       kind="xbox_model", source=n, ref=e.base_name, info=base, _n=1)
                else:
                    a["_n"] += 1
    finally:
        src.close()
    for a in models.values():
        extra = a.pop("_n") - 1
        if extra:
            a["info"] += f" (+{extra} more)"
        assets.append(a)
    if progress:
        progress(1, 1, "done")
    return _sorted(assets), xbox_stamp(path)


# ---------------------------------------------------------------------------------------- cache

def _sorted(assets):
    return sorted(assets, key=lambda a: (a["category"].lower(), a["name"].lower(), a["source"].lower()))


def scan(platform: str, path: str, progress=None):
    if platform == "PC":
        return scan_pc(path, progress)
    return scan_xbox(path, progress)


def stamp(platform: str, path: str) -> str:
    try:
        return pc_stamp(path) if platform == "PC" else xbox_stamp(path)
    except OSError:
        return ""


def cache_file(cache_dir: str, platform: str) -> str:
    return os.path.join(cache_dir, f"index_{platform.lower()}.json")


def save_cache(cache_dir: str, platform: str, path: str, assets, stamp_: str):
    os.makedirs(cache_dir, exist_ok=True)
    doc = dict(version=CACHE_VERSION, platform=platform, source=os.path.normcase(os.path.abspath(path)),
               stamp=stamp_, scanned=time.strftime("%Y-%m-%d %H:%M"), assets=assets)
    tmp = cache_file(cache_dir, platform) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, cache_file(cache_dir, platform))
    return doc


def load_cache(cache_dir: str, platform: str, path: str):
    """The cached scan for this platform and source, or None. Adds 'stale': True when the
    source's files changed since the scan."""
    p = cache_file(cache_dir, platform)
    if not path or not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if doc.get("version") != CACHE_VERSION or doc.get("source") != os.path.normcase(os.path.abspath(path)):
        return None
    doc["stale"] = stamp(platform, path) != doc.get("stamp")
    return doc


def clear_cache(cache_dir: str):
    n = 0
    for platform in ("PC", "XBOX"):
        p = cache_file(cache_dir, platform)
        if os.path.isfile(p):
            os.remove(p)
            n += 1
    return n


def matches(asset, terms, category="ALL") -> bool:
    """Search: every whitespace-separated term must appear in the name, category or info."""
    if category != "ALL" and asset["category"] != category:
        return False
    if not terms:
        return True
    hay = f"{asset['name']} {asset['category']} {asset['info']} {asset['source']}".lower()
    return all(t in hay for t in terms)
