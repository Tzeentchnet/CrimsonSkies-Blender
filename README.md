# Crimson Skies Importer for Blender

A Blender 5.2 extension (works from 4.4) that imports models, materials, animations and levels
from both games:

- **Crimson Skies** (PC, 2000): read from an install folder.
- **Crimson Skies: High Road to Revenge** (Xbox, 2003): read straight from the disc image
  (`.iso` / `.xiso`), with no extraction step, or from extracted `.bin` / `.zip` / `.x` files.

No game data is included. You need your own copies of the games.

## Install

1. Build the zip (or use `dist/crimson_skies_importer-1.1.0.zip`):
   `blender --command extension build --source-dir crimson_skies_importer --output-dir dist`
2. In Blender, go to *Edit > Preferences > Get Extensions*, open the drop-down at the top right,
   choose *Install from Disk…* and pick the zip.

Everything is under **File > Import > Crimson Skies**.

## PC (Crimson Skies)

| Menu entry | File to pick | What you get |
|---|---|---|
| PC Models (planes.zbd) | `ZBD\planes.zbd` | Every aircraft and its node tree, textured, laid out in a grid |
| PC Level (gamez.zbd) | `ZBD\<chapter>\gamez.zbd` (C1, C1B, C1C, C2, C2B, C3, C4, C5) | The chapter world: terrain, buildings, sky, clouds, vehicles |

Options:

- **Root Filter**: comma-separated names or wildcards, for example `player_bhawk` or `player_*`.
- **LOD**: *Highest only* (the default) or *All*.
- **Flight-Ready State**: hides the damage panels, destroyed pieces, shadow and propeller spin
  frames that the game starts hidden. **Cockpit Interior** also imports the separate cockpit model.
- **World Content Only** (levels): imports the world node and its partitioned scenery, and leaves
  out effect templates such as bullet holes and letterbox bars.
- **Mission** (levels): a mission folder such as `M02`, whose `mis_anim.zbd` animations are added
  to the chapter's `cam_anim.zbd` ones.
- **Vertex Colors / Unlit / Custom Normals**: material and shading choices. *Unlit* matches the
  original's fullbright world.

### Liveries (paint patterns from `crimson.rof`)

The skins in `texture.zbd` are unpainted key textures; the game paints each aircraft at load time.
The importer does the same from `GOSDATA\ASSETS\crimson.rof` (with `crimptch.rof` applied):

- **Livery** (import option): one of the 14 patterns (Fortune Hunters, Black Hats, Blake's
  Bandits, British, Black Swans, Red Army, Hollywood Knights, Hughes, Medusas, Sacred Trust,
  German, Studio Security, Broadway, ITS Taxi), or *Shipped (unpainted)*. The default is
  Fortune Hunters, the only pattern every aircraft has. An aircraft without the chosen pattern
  falls back to Fortune Hunters with a warning.
- **Custom Colours**: Body, Dark Trim and Light Trim override the pattern's defaults. The
  defaults are the game's own (its hangar pattern and swatch tables).
- **Nose / Tail / Wing Decal**: any of the 50 numbered decals (squadron logos 00–20, nose art
  21–49), or the pattern's default.
- **Repaint after import**: select an imported aircraft and open *View3D sidebar (N) >
  Crimson Skies > Crimson Skies Livery*. The pattern list shows only the patterns that aircraft
  ships (the Bloodhawk offers Blake, Fortune and Hughes; the Hoplite Black Hats, Fortune,
  Studio and ITS Taxi), and choosing one loads its default colours. *Apply Livery* repaints
  every selected aircraft. Materials shared with other aircraft are copied first, so a repaint
  never changes another plane.

Per texel this is `shading × (w1·c1 + w2·c2 + w3·c3) / 255` with the pattern's overlay
composited on top, from each `.BM` skin's shading map, three region weights and RGBA overlay
(rows bottom-up), as documented by CSVM (`docs/formats/rof.md`, `paint.md`).

How the data maps into Blender:

- Textures come from the chapter's full-quality `texture.zbd`, with the other chapters and
  `rimage.zbd` as fallbacks. They are packed into the .blend, or written as PNG if you set
  **Texture Folder**.
- Animations are the compiled SI scripts (`.zan`, per-node splines). Each becomes an action,
  sampled at the scene frame rate, on the node it drives: the C1 train, the fuel trucks, the
  pilot-eject rig, and so on. Cutscene camera scripts become animated Blender cameras. When a
  node has more than one script, the first is the active action and the rest are muted NLA tracks.
- Fog volumes and Danger Zone paths come in as wireframe helpers that are hidden in renders.
- The game is Y-up; the import root is turned 90° about X, so the scene stands up in Blender
  while every node keeps its original local transform.

## Xbox (High Road to Revenge)

Pick the disc image with **Xbox: High Road to Revenge**, then set **Package / Entry**:

| Package / Entry | Result |
|---|---|
| `SH_Tutorial.bin` (any level `.bin`) | The level: every placed unit at its world position, the terrain and water chunks, lightmaps, and door/turret animations |
| `PF_Bandito.bin` | That aircraft package's models (main airframe, debris, fuselage) |
| `PF_*_Upgrade.bin` | Several packages at once, laid out in one grid |
| `SH_Tutorial.bin:parachute.x` | One model from a package, with every `.anm` of that package that drives it |

**Team Skin** picks the No Team, Team 1 or Team 2 variant of multiplayer aircraft
(`PF_*_MP*.bin`), which ship `…_noteam`, `…_Team1` and `…_Team2` versions of their skin.

If you leave **Package / Entry** empty, the importer lists the disc's 108 packages in the info log.
The **Import** mode can force *Level World*, *Models*, or *Models + Animations*.

What is imported:

- **Models**: the full node tree, with packed normals, up to four UV sets (`UV0` to `UV3`) and
  materials. The base texture drives the colour, and `~n` maps are wired in as normal maps. The
  other slots (damage overlays, `~a` masks, detail maps) are added as image nodes, unconnected,
  on their own UV maps.
- **Skinned models** (the parachutes): an armature built over the node tree, with vertex groups
  from the per-mesh joint tables.
- **Animations** (`.anm`): keyed onto objects, or onto pose bones for skinned models, at 30 fps.
- **Levels**: each lightmapped unit instance (`model.x|UnitName`) carries its own placement. The
  terrain and water chunks come from the world section. Lightmaps are multiplied in at 2x on `UV3`.
- **Textures**: DXT1/3/5, swizzled 32-bit and R5G6B5, linear 16-bit masks, and the P8 lightmap
  atlases.

## Formats

The PC readers are ports of the CSVM project's C# readers. Its format documentation is in
`CrimsonSkies-csvm/docs/formats`.

The Xbox recomp publishes no asset formats, so the Xbox layouts here were reverse-engineered from
the disc and checked against all of it. Every one of the 6,734 models, 674 animations and
17,158 textures parses to its exact end, and every level's world section yields its declared
mesh count except `mp_CH1` (see Limitations). The layouts are written up in the docstrings of
`formats/xbox_package.py`, `xbox_model.py`, `xbox_anim.py` and `xbox_texture.py`. In brief:

- **Package `.bin`**: a header, either `{?, count}` for aircraft or `{?, 150, count, world mesh
  count}` for levels. Then `count` entries of `{u32 type, u32 name length, name, u32 size, data}`.
  Types: 1 model, 2 texture, 3 cube map, 4 `.anm`, 5 `.cin`, 6 sound, 7 light. Levels then
  have a world section.
- **Model**: `{version, bounds, flags}`, then a recursive node tree. Each node is
  `{name, 4x3 matrix, bounds, kind, meshes, children}`. Each mesh has a shader id, an ARGB colour,
  a texture-slot mask with one name per set bit, 32-byte vertices
  (`pos f32x3, NORMPACKED3 normal, 4 x s16x2 UV`), a per-set UV scale, optional tangent frames,
  a u16 triangle list, a strip/remap list, a bounding tree, and, in version 2 files only, skin
  weights. Coordinates are left-handed D3D. The triangle order is the back face relative to the
  stored normals.
- **`.anm`**: tracks of `{frame, pos, scale, shear, quat xyzw}` keys addressed by node name.

## Limitations

- Xbox: the world section's **scatter props** (palm trees, rocks, crates) are listed in each
  level collection's `cs_scatter_props` property but are not placed; their compressed placement
  grids are not decoded. `.cin` cinematics, cube maps and sounds are not imported. One chunk of
  `mp_CH1` is not found (268 of 269).
- Xbox materials follow inferred shader semantics: slot 0 is the base, `~n` is a normal map,
  `~a` is a mask, not transparency, and only DXT1's 1-bit alpha is used as cut-out. They are
  approximations of the game's shaders.
- PC: only the compiled SI-script animations are imported. Reader-driven motion (propeller spins,
  `OBJECT_MOTION_FROM_TO` cars, hangar doors) is not. Facade billboards keep their authored
  orientation; their mode is stored in the mesh's `cs_facade_mode` property. Overlay material
  passes are kept only as a second UV map called `Overlay`.

## Tests

`tests/blender_test.py` imports one case headless, saves a .blend and renders a preview, for
example `blender -b --factory-startup --python tests/blender_test.py -- pc_level_c1`.
`tests/operator_test.py` runs every import operator end to end. The default output folder is
`E:\tests\CrimsonSkies_Importer_20261123`.
