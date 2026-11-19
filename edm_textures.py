"""
Texture helpers (Blender side).

DCS loads <stem>.dds (+ <stem>_NM.dds, <stem>_RoughMet.dds) from the mod's
textures folder. The EDM only stores the stem. These helpers:

  * resolve the textures folder (scene field / collection property / .blend folder)
  * list what is missing for every image the model uses
  * convert Blender images to .dds (DXT1/DXT5) straight into that folder
  * give a mesh a material from a texture FILE whose name matches the mesh /
    material name (fixes "mesh name differs from texture name" cases)
"""
import os
import re

import bpy

from . import edm_dds
from . import edm_newnodes as nn

_SUFFIX = re.compile(r'\.\d{3}$')
_TEX_EXT = ('.dds', '.png', '.tga', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff')
_NM, _RM = '_NM', '_RoughMet'


def clean(name):
    """'Panel_L_mesh.001' -> 'panel_l'  (for fuzzy name matching)."""
    n = _SUFFIX.sub('', str(name))
    n = re.sub(r'_mesh$', '', n, flags=re.I)
    return n.strip().lower()


def texture_dir(context, collection=None):
    """Absolute textures folder: collection property 'edm_texture_dir', then
    the scene field, then <blend folder>/textures, then <blend folder>."""
    cands = []
    if collection is not None:
        cands.append(collection.get('edm_texture_dir'))
    cands.append(getattr(context.scene, 'edm_texture_dir', ''))
    for c in cands:
        if c:
            p = bpy.path.abspath(str(c))
            if os.path.isdir(p):
                return p
    base = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ''
    if base:
        for sub in ('textures', 'TEXTURA', 'textura', ''):
            p = os.path.join(base, sub) if sub else base
            if os.path.isdir(p):
                return p
    return ''


def files_in(directory):
    try:
        return {f.lower(): f for f in os.listdir(directory)}
    except Exception:
        return {}


def has_file(files, stem, exts=None):
    exts = exts or _TEX_EXT
    return any((stem + e).lower() in files for e in exts)


def material_images(mat):
    """Images of a material: [(image, is_base_color)]. Base colour first."""
    out = []
    tree = getattr(mat, 'node_tree', None) if mat else None
    if not tree:
        return out
    base = None
    try:
        bsdf = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
        links = bsdf.inputs['Base Color'].links
        if links and links[0].from_node.type == 'TEX_IMAGE':
            base = links[0].from_node.image
    except Exception:
        pass
    if base is not None:
        out.append((base, True))
    for n in tree.nodes:
        if n.type == 'TEX_IMAGE' and n.image and n.image is not base:
            out.append((n.image, False))
    return out


def image_stem(img):
    return nn.image_stem(img.filepath) or nn.image_stem(img.name)


def used_images(objs):
    """{stem: image} for the base-colour image of every material of `objs`."""
    out = {}
    for o in objs:
        for slot in getattr(o, 'material_slots', []):
            for img, is_base in material_images(slot.material):
                if is_base:
                    stem = image_stem(img)
                    if stem:
                        out.setdefault(stem, img)
    return out


# --------------------------------------------------------------------------
# image -> DDS
# --------------------------------------------------------------------------

def image_to_rgba(img):
    """uint8 (h, w, 4), row 0 = top, from a Blender image."""
    import numpy as np
    w, h = img.size
    if w == 0 or h == 0:
        raise RuntimeError("image '%s' has no pixels (file missing? Image > Reload)" % img.name)
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    arr = buf.reshape(h, w, 4)[::-1]            # Blender stores bottom-up
    return np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)


def write_dds(img, directory, stem=None, fmt='AUTO', mipmaps=True):
    """Write <stem>.dds for `img` into `directory`. Returns (path, used_format)."""
    stem = stem or image_stem(img)
    data, used = edm_dds.encode_dds(image_to_rgba(img), fmt, mipmaps)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, stem + '.dds')
    with open(path, 'wb') as f:
        f.write(data)
    return path, used


def companion_images():
    """{stem_lower: image} for every Blender image named <x>_NM / <x>_RoughMet
    (loaded in the .blend), so they can be converted too."""
    out = {}
    for img in bpy.data.images:
        s = image_stem(img)
        if s and (s.endswith(_NM) or s.endswith(_RM)):
            out[s.lower()] = img
    return out


# --------------------------------------------------------------------------
# status of the textures of a set of objects
# --------------------------------------------------------------------------

def status(objs, directory):
    """[(stem, state, detail)] with state in OK / NO_DDS / NO_FILE / NO_PBR.
    OK     <stem>.dds exists
    NO_DDS only a png/tga/... exists (or the Blender image can be converted)
    NO_FILE nothing found anywhere
    NO_PBR  diffuse ok but _NM/_RoughMet missing (informational)"""
    files = files_in(directory) if directory else {}
    rows = []
    for stem, img in sorted(used_images(objs).items()):
        if has_file(files, stem, ('.dds',)):
            state, detail = 'OK', stem + '.dds'
        elif has_file(files, stem):
            state, detail = 'NO_DDS', 'found as image but not as .dds'
        elif img is not None and img.size[0] > 0:
            state, detail = 'NO_DDS', 'only inside Blender (convert to .dds)'
        else:
            state, detail = 'NO_FILE', 'no file with this name'
        rows.append((stem, state, detail))
        if state == 'OK':
            nm = has_file(files, stem + _NM, ('.dds',))
            rm = has_file(files, stem + _RM, ('.dds',))
            if nm != rm:
                rows.append((stem, 'NO_PBR', 'has only one of %s / %s' % (_NM, _RM)))
    return rows


# --------------------------------------------------------------------------
# material from a texture file
# --------------------------------------------------------------------------

def _find_image_file(files, directory, wanted):
    """Path of the best texture FILE for names in `wanted` (cleaned, lower)."""
    best = None
    for w in wanted:
        if not w:
            continue
        for ext in ('.png', '.tga', '.dds', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'):
            f = files.get((w + ext).lower())
            if f:
                return os.path.join(directory, f)
    for fname in files.values():
        stem, ext = os.path.splitext(fname)
        if ext.lower() not in _TEX_EXT or stem.endswith(_NM) or stem.endswith(_RM):
            continue
        if clean(stem) in wanted:
            best = os.path.join(directory, fname)
            break
    return best


def make_material(name, image):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = tree.nodes.new('ShaderNodeBsdfPrincipled')
    tex = next((n for n in tree.nodes if n.type == 'TEX_IMAGE'), None) or tree.nodes.new('ShaderNodeTexImage')
    tex.image = image
    try:
        tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    except Exception:
        pass
    return mat


def auto_assign(objs, directory, only_missing=True):
    """Give meshes a material built from the texture file whose name matches the
    mesh name, its material name or its parent's name. Returns (done, notfound)."""
    files = files_in(directory)
    done, notfound = [], []
    for o in objs:
        if o.type != 'MESH':
            continue
        if only_missing and used_images([o]):
            continue
        wanted = [clean(o.name)]
        wanted += [clean(s.material.name) for s in o.material_slots if s.material]
        if o.parent is not None:
            wanted.append(clean(o.parent.name))
        path = _find_image_file(files, directory, [w for w in wanted if w])
        if not path:
            notfound.append(o.name)
            continue
        img = bpy.data.images.load(path, check_existing=True)
        mat = make_material(os.path.splitext(os.path.basename(path))[0], img)
        if o.data.materials:
            o.data.materials[0] = mat
        else:
            o.data.materials.append(mat)
        done.append((o.name, os.path.basename(path)))
    return done, notfound
