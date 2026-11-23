"""Blender-side helpers shared by the PC and Xbox importers."""

from __future__ import annotations

import math
import os

import bpy
import numpy as np
from mathutils import Matrix

# Game Y-up (x, y, z) -> Blender Z-up (x, -z, y): a +90 degree turn about X on the import root.
Y_UP_TO_Z_UP = Matrix.Rotation(math.radians(90.0), 4, "X")


# --------------------------------------------------------------------------- collections / objects

def new_collection(name: str, parent=None):
    col = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(col)
    return col


def new_object(name: str, data, collection, parent=None):
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    return obj


def new_empty(name: str, collection, parent=None, size=0.5, display="PLAIN_AXES"):
    obj = new_object(name, None, collection, parent)
    obj.empty_display_size = size
    obj.empty_display_type = display
    return obj


def hide(obj, hidden=True):
    obj.hide_viewport = hidden
    obj.hide_render = hidden


# --------------------------------------------------------------------------- images

class ImageCache:
    """bpy images keyed by texture name; created once, optionally packed or written to disk."""

    def __init__(self, prefix: str, pack: bool = True, save_dir: str | None = None):
        self.prefix = prefix
        self.pack = pack
        self.save_dir = save_dir
        self.images = {}

    def get(self, key: str, loader):
        """loader() -> (rgba uint8 (h, w, 4) top row first, has_alpha) or None."""
        k = key.lower()
        if k in self.images:
            return self.images[k]
        img = None
        try:
            res = loader()
        except Exception as exc:  # a broken texture must not stop a whole import
            print(f"[CrimsonSkies] texture {key}: {exc}")
            res = None
        if res is not None:
            rgba, has_alpha = res
            img = make_image(f"{self.prefix}{key}", rgba, has_alpha, self.pack, self.save_dir)
        self.images[k] = img
        return img


def make_image(name: str, rgba: np.ndarray, has_alpha: bool, pack=True, save_dir=None):
    h, w = rgba.shape[:2]
    img = bpy.data.images.new(name, width=w, height=h, alpha=has_alpha)
    flipped = np.ascontiguousarray(rgba[::-1])  # Blender rows run bottom-up
    img.pixels.foreach_set((flipped.astype(np.float32) / 255.0).ravel())
    img.alpha_mode = "STRAIGHT" if has_alpha else "NONE"
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        path = os.path.join(save_dir, safe + ".png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        img.source = "FILE"
    elif pack:
        img.pack()
    return img


# --------------------------------------------------------------------------- materials

class MaterialSpec:
    __slots__ = ("name", "image", "color", "alpha", "use_alpha", "blend", "double_sided",
                 "vertex_colors", "unlit", "extra_images", "uv_scroll", "normal_image", "base_uv",
                 "lightmap", "lightmap_uv")

    def __init__(self, name):
        self.name = name
        self.image = None
        self.color = (1.0, 1.0, 1.0)
        self.alpha = 1.0
        self.use_alpha = False
        self.blend = False  # alpha blend (vs. dithered/clip)
        self.double_sided = True
        self.vertex_colors = None  # color attribute name to multiply in, or None
        self.unlit = False
        self.extra_images = []  # (label, image, uv_map) shown unlinked for reference
        self.uv_scroll = None
        self.normal_image = None
        self.base_uv = None  # UV map name for the base texture (None = the active one)
        self.lightmap = None  # image multiplied in at 2x (Xbox lightmaps)
        self.lightmap_uv = None


def build_material(spec: MaterialSpec):
    mat = bpy.data.materials.new(spec.name)
    mat.use_backface_culling = not spec.double_sided
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (700, 0)
    if spec.unlit:
        shader = nodes.new("ShaderNodeEmission")
        color_in, alpha_in = shader.inputs["Color"], None
        mix = nodes.new("ShaderNodeMixShader")
        transp = nodes.new("ShaderNodeBsdfTransparent")
        mix.location, transp.location = (480, 0), (250, 150)
        shader.location = (250, -50)
        links.new(transp.outputs[0], mix.inputs[1])
        links.new(shader.outputs[0], mix.inputs[2])
        links.new(mix.outputs[0], out.inputs["Surface"])
        alpha_in = mix.inputs[0]
        alpha_in.default_value = spec.alpha
    else:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        shader.location = (300, 0)
        shader.inputs["Roughness"].default_value = 0.8
        links.new(shader.outputs[0], out.inputs["Surface"])
        color_in, alpha_in = shader.inputs["Base Color"], shader.inputs["Alpha"]
        alpha_in.default_value = spec.alpha
    base_rgb = (*spec.color, 1.0)
    color_src = None
    alpha_src = None
    base_tex = None
    if spec.image is not None:
        tex = base_tex = nodes.new("ShaderNodeTexImage")
        tex.name = tex.label = "CS Base Texture"
        tex.image = spec.image
        tex.location = (-400, 100)
        tex.interpolation = "Linear"
        color_src = tex.outputs["Color"]
        if spec.use_alpha:
            alpha_src = tex.outputs["Alpha"]
        if spec.uv_scroll and any(spec.uv_scroll):
            mapping = _scroll_mapping(tree, spec.uv_scroll)
            links.new(mapping.outputs[0], tex.inputs["Vector"])
        elif spec.base_uv:
            uvn = nodes.new("ShaderNodeUVMap")
            uvn.uv_map = spec.base_uv
            uvn.location = (-600, 100)
            links.new(uvn.outputs[0], tex.inputs["Vector"])
    if spec.image is None or spec.color != (1.0, 1.0, 1.0):
        if color_src is None:
            color_in.default_value = base_rgb
        else:
            mul = _mix_multiply(nodes, (-100, 150))
            _sock(mul.inputs, "B_Color").default_value = base_rgb
            links.new(color_src, _sock(mul.inputs, "A_Color"))
            color_src = _sock(mul.outputs, "Result_Color")
    if spec.vertex_colors:
        vc = nodes.new("ShaderNodeVertexColor")
        vc.layer_name = spec.vertex_colors
        vc.location = (-400, -200)
        mul = _mix_multiply(nodes, (50, 0))
        if color_src is None:
            _sock(mul.inputs, "A_Color").default_value = base_rgb
        else:
            links.new(color_src, _sock(mul.inputs, "A_Color"))
        links.new(vc.outputs["Color"], _sock(mul.inputs, "B_Color"))
        color_src = _sock(mul.outputs, "Result_Color")
    if spec.lightmap is not None:
        lt = nodes.new("ShaderNodeTexImage")
        lt.image = spec.lightmap
        lt.label = "lightmap"
        lt.location = (-400, -150)
        if spec.lightmap_uv:
            uvn = nodes.new("ShaderNodeUVMap")
            uvn.uv_map = spec.lightmap_uv
            uvn.location = (-600, -150)
            links.new(uvn.outputs[0], lt.inputs["Vector"])
        x2 = nodes.new("ShaderNodeVectorMath")
        x2.operation = "SCALE"
        _sock(x2.inputs, "Scale").default_value = 2.0
        x2.location = (-150, -150)
        links.new(lt.outputs["Color"], x2.inputs[0])
        mul = _mix_multiply(nodes, (100, -100))
        if color_src is None:
            _sock(mul.inputs, "A_Color").default_value = base_rgb
        else:
            links.new(color_src, _sock(mul.inputs, "A_Color"))
        links.new(x2.outputs["Vector"], _sock(mul.inputs, "B_Color"))
        color_src = _sock(mul.outputs, "Result_Color")
    if color_src is not None:
        links.new(color_src, color_in)
    if alpha_src is not None and alpha_in is not None:
        if spec.alpha < 1.0:
            m = nodes.new("ShaderNodeMath")
            m.operation = "MULTIPLY"
            m.inputs[1].default_value = spec.alpha
            links.new(alpha_src, m.inputs[0])
            alpha_src = m.outputs[0]
        links.new(alpha_src, alpha_in)
    if spec.normal_image is not None and not spec.unlit:
        nt = nodes.new("ShaderNodeTexImage")
        nt.image = spec.normal_image
        nt.image.colorspace_settings.name = "Non-Color"
        nt.location = (-400, -450)
        nm = nodes.new("ShaderNodeNormalMap")
        nm.location = (-100, -450)
        links.new(nt.outputs["Color"], nm.inputs["Color"])
        links.new(nm.outputs["Normal"], shader.inputs["Normal"])
    y = -700
    for label, img, uvmap in spec.extra_images:
        t = nodes.new("ShaderNodeTexImage")
        t.image = img
        t.label = label
        t.location = (-700, y)
        if uvmap:
            uvn = nodes.new("ShaderNodeUVMap")
            uvn.uv_map = uvmap
            uvn.location = (-900, y)
            links.new(uvn.outputs[0], t.inputs["Vector"])
        y -= 280
    if base_tex is not None:
        nodes.active = base_tex  # what Solid/Workbench texture mode displays
    transparent = (spec.use_alpha and spec.image is not None) or spec.alpha < 1.0
    if transparent:
        mat.surface_render_method = "BLENDED" if spec.blend else "DITHERED"
    return mat


def _sock(sockets, identifier):
    for s in sockets:
        if s.identifier == identifier:
            return s
    raise KeyError(identifier)


def _mix_multiply(nodes, location):
    mul = nodes.new("ShaderNodeMix")
    mul.data_type = "RGBA"
    mul.blend_type = "MULTIPLY"
    _sock(mul.inputs, "Factor_Float").default_value = 1.0
    mul.location = location
    return mul


def _scroll_mapping(tree, scroll):
    nodes, links = tree.nodes, tree.links
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-1000, 300)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-700, 300)
    links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    # Drive the offset with the frame: offset = scroll * frame / fps.
    for axis, rate in enumerate(scroll[:2]):
        if not rate:
            continue
        fc = mapping.inputs["Location"].driver_add("default_value", axis)
        fc.driver.expression = f"{rate} * frame / {bpy.context.scene.render.fps}"
    return mapping


# --------------------------------------------------------------------------- meshes

def build_mesh(name, positions, loop_verts, poly_sizes, poly_mats=None, uv_layers=None,
               color_layers=None, loop_normals=None, materials=None):
    """positions (V,3); loop_verts (L,); poly_sizes (P,); uv_layers {name: (L,2)};
    color_layers {name: (L,4)}; loop_normals (L,3) or None."""
    me = bpy.data.meshes.new(name)
    positions = np.asarray(positions, dtype=np.float32).reshape(-1, 3)
    loop_verts = np.asarray(loop_verts, dtype=np.int32)
    poly_sizes = np.asarray(poly_sizes, dtype=np.int32)
    me.vertices.add(len(positions))
    me.vertices.foreach_set("co", positions.ravel())
    me.loops.add(len(loop_verts))
    me.loops.foreach_set("vertex_index", loop_verts)
    me.polygons.add(len(poly_sizes))
    starts = np.zeros(len(poly_sizes), dtype=np.int32)
    if len(poly_sizes):
        starts[1:] = np.cumsum(poly_sizes)[:-1]
    me.polygons.foreach_set("loop_start", starts)
    if poly_mats is not None and len(poly_sizes):
        me.polygons.foreach_set("material_index", np.asarray(poly_mats, dtype=np.int32))
    for mat in materials or []:
        me.materials.append(mat)
    for uv_name, uvs in (uv_layers or {}).items():
        layer = me.uv_layers.new(name=uv_name)
        layer.data.foreach_set("uv", np.asarray(uvs, dtype=np.float32).ravel())
    for c_name, cols in (color_layers or {}).items():
        attr = me.color_attributes.new(c_name, "BYTE_COLOR", "CORNER")
        attr.data.foreach_set("color", np.asarray(cols, dtype=np.float32).ravel())
    me.update(calc_edges=True)
    if loop_normals is not None and len(loop_verts):
        ln = np.asarray(loop_normals, dtype=np.float32).reshape(-1, 3)
        lens = np.linalg.norm(ln, axis=1)
        ok = lens > 1e-8
        ln[ok] /= lens[ok][:, None]
        try:
            me.normals_split_custom_set(ln.tolist())
        except Exception as exc:
            print(f"[CrimsonSkies] {name}: custom normals skipped ({exc})")
    else:
        me.shade_smooth() if hasattr(me, "shade_smooth") else None
    return me


# --------------------------------------------------------------------------- animation

def make_action(name: str, obj, frames, locations=None, quaternions=None, scales=None,
                rotation_mode="QUATERNION", assign=True, euler=None):
    """Keys obj's transform channels with linear interpolation. Arrays are per frame."""
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    slot = act.slots.new(id_type="OBJECT", name=obj.name)
    layer = act.layers.new("Layer")
    strip = layer.strips.new(type="KEYFRAME")
    bag = strip.channelbag(slot, ensure=True)
    frames = np.asarray(frames, dtype=np.float32)

    def key(path, values, group):
        values = np.asarray(values, dtype=np.float32)
        for i in range(values.shape[1]):
            fc = bag.fcurves.new(path, index=i)
            try:
                fc.group = bag.groups.get(group) or bag.groups.new(group)
            except Exception:
                pass
            fc.keyframe_points.add(len(frames))
            co = np.empty(len(frames) * 2, dtype=np.float32)
            co[0::2] = frames
            co[1::2] = values[:, i]
            fc.keyframe_points.foreach_set("co", co)
            fc.keyframe_points.foreach_set("interpolation", [1] * len(frames))  # LINEAR
            fc.update()

    if locations is not None:
        key("location", locations, "Location")
    if quaternions is not None:
        q = np.asarray(quaternions, dtype=np.float64)
        # keep consecutive quaternions in one hemisphere so linear keys do not flip
        for i in range(1, len(q)):
            if np.dot(q[i], q[i - 1]) < 0.0:
                q[i] = -q[i]
        key("rotation_quaternion", q, "Rotation")
    if euler is not None:
        key("rotation_euler", euler, "Rotation")
    if scales is not None:
        key("scale", scales, "Scale")
    if assign:
        attach_action(obj, act, slot, rotation_mode)
    return act, slot


def attach_action(obj, act, slot, rotation_mode="QUATERNION"):
    """Active action if free, otherwise a muted NLA strip so every action stays reachable."""
    if rotation_mode:
        obj.rotation_mode = rotation_mode
    ad = obj.animation_data or obj.animation_data_create()
    if ad.action is None:
        ad.action = act
        try:
            ad.action_slot = slot
        except Exception:
            pass
    else:
        track = ad.nla_tracks.new()
        track.name = act.name
        start = int(act.frame_range[0])
        st = track.strips.new(act.name, start, act)
        try:
            st.action_slot = slot
        except Exception:
            pass
        track.mute = True


def set_scene_range(frame_end: int, fps: int = 30):
    sc = bpy.context.scene
    sc.render.fps = fps
    sc.frame_start = 1
    sc.frame_end = max(sc.frame_end, int(frame_end))
