"""
Pure-python helpers for export_edm.py (no bpy/mathutils imports, so they can
be unit-tested outside Blender).

What lives here
---------------
1. Tangent/bitangent handling.
   The old exporter re-encoded only position/normal/uv0 and left every other
   vertex channel at ZERO. In the A-29B cockpit, channels 2 and 3 are the
   tangent and bitangent (unit vectors perpendicular to the normal) used for
   normal mapping, and ~2/3 of all vertices use a format that has them — so
   every exported normal-mapped part lost its tangent frame.
   Fix: reuse the ORIGINAL tangents for every vertex that still matches an
   original vertex (same position + UV, similar normal), and only compute
   tangents for vertices that are new or edited.

2. Slot planning: which Blender mesh objects map onto existing RenderNode
   slots, and which are NEW geometry (no slot, or a duplicate of a slot).

3. Building a brand-new single-group model::RenderNode that reuses an
   existing material entry and an existing transform node as parent.
"""
import copy
import math
import os
import re

from . import edm_reader as edmr


# --------------------------------------------------------------------------
# 1. Tangents
# --------------------------------------------------------------------------

def compute_tangents(positions, normals, uvs, tri_indices):
    """Per-vertex tangent (~dP/dU) and bitangent (~dP/dV), each made
    perpendicular to the vertex normal, accumulated over the triangles that
    touch the vertex. Direction convention checked against a real A-29B file
    (channel 2 ~ dP/dU, channel 3 ~ dP/dV in EDM UV space). This is an
    approximation of what the original modelling tool wrote — good enough
    for NEW geometry, far better than the zero vectors written before."""
    nv = len(positions)
    tx = [[0.0, 0.0, 0.0] for _ in range(nv)]
    bx = [[0.0, 0.0, 0.0] for _ in range(nv)]
    for t0 in range(0, len(tri_indices) - 2, 3):
        i0, i1, i2 = tri_indices[t0], tri_indices[t0 + 1], tri_indices[t0 + 2]
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        u0, u1, u2 = uvs[i0], uvs[i1], uvs[i2]
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        du1, dv1 = u1[0] - u0[0], u1[1] - u0[1]
        du2, dv2 = u2[0] - u0[0], u2[1] - u0[1]
        det = du1 * dv2 - du2 * dv1
        if abs(det) < 1e-14:
            continue
        r = 1.0 / det
        dpdu = ((e1[0] * dv2 - e2[0] * dv1) * r,
                (e1[1] * dv2 - e2[1] * dv1) * r,
                (e1[2] * dv2 - e2[2] * dv1) * r)
        dpdv = ((e2[0] * du1 - e1[0] * du2) * r,
                (e2[1] * du1 - e1[1] * du2) * r,
                (e2[2] * du1 - e1[2] * du2) * r)
        lu = math.sqrt(dpdu[0] ** 2 + dpdu[1] ** 2 + dpdu[2] ** 2)
        lv = math.sqrt(dpdv[0] ** 2 + dpdv[1] ** 2 + dpdv[2] ** 2)
        if lu < 1e-20 or lv < 1e-20:
            continue
        dpdu = (dpdu[0] / lu, dpdu[1] / lu, dpdu[2] / lu)
        dpdv = (dpdv[0] / lv, dpdv[1] / lv, dpdv[2] / lv)
        for i in (i0, i1, i2):
            for k in range(3):
                tx[i][k] += dpdu[k]
                bx[i][k] += dpdv[k]

    out_t, out_b = [], []
    for i in range(nv):
        n = normals[i]
        res = []
        for acc in (tx[i], bx[i]):
            d = acc[0] * n[0] + acc[1] * n[1] + acc[2] * n[2]
            v = (acc[0] - n[0] * d, acc[1] - n[1] * d, acc[2] - n[2] * d)
            ln = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
            res.append((v[0] / ln, v[1] / ln, v[2] / ln) if ln > 1e-12 else None)
        t, b = res
        if t is None and b is None:
            a = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
            d = a[0] * n[0] + a[1] * n[1] + a[2] * n[2]
            v = (a[0] - n[0] * d, a[1] - n[1] * d, a[2] - n[2] * d)
            ln = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
            t = (v[0] / ln, v[1] / ln, v[2] / ln)
        if t is None:
            t = (b[1] * n[2] - b[2] * n[1], b[2] * n[0] - b[0] * n[2], b[0] * n[1] - b[1] * n[0])
        if b is None:
            b = (n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0])
        out_t.append(t)
        out_b.append(b)
    return out_t, out_b


def _pkey(pos):
    # Positions coming back from Blender are float32 values converted to
    # python floats — bit-identical to the originals for untouched vertices —
    # so an (almost) exact position key never misses. UV and normal are then
    # compared with a tolerance (V goes through 1-v twice and the custom
    # normals are stored with reduced precision in Blender).
    return (round(pos[0], 5), round(pos[1], 5), round(pos[2], 5))


_UV_TOL = 2e-5
_NORMAL_MIN_DOT = 0.98


def build_tangent_lookup(decoded_vertices, group=None):
    """decoded_vertices: output of edm_reader.decode_vertices for the ORIGINAL
    node. group: if given, only vertices whose position.w rounds to it.
    Returns {position key: [(uv, normal, tangent, bitangent), ...]}, or {}
    when the vertex format has no tangent channels."""
    lookup = {}
    if not decoded_vertices or 'unknown2' not in decoded_vertices[0] \
            or 'unknown3' not in decoded_vertices[0] or 'uv0' not in decoded_vertices[0]:
        return lookup
    for v in decoded_vertices:
        if group is not None and int(round(v['position'][3])) != group:
            continue
        lookup.setdefault(_pkey(v['position']), []).append(
            (v['uv0'], v['normal'], v['unknown2'], v['unknown3']))
    return lookup


def find_original_tangents(lookup, position, uv, normal):
    """Best original (tangent, bitangent) for a vertex, or None."""
    cands = lookup.get(_pkey(position))
    if not cands:
        return None
    best = None
    best_dot = _NORMAL_MIN_DOT
    for (cuv, cn, ct, cb) in cands:
        if abs(cuv[0] - uv[0]) > _UV_TOL or abs(cuv[1] - uv[1]) > _UV_TOL:
            continue
        d = cn[0] * normal[0] + cn[1] * normal[1] + cn[2] * normal[2]
        if d >= best_dot:
            best_dot = d
            best = (ct, cb)
    return best


def fill_tangents(vertices, tri_indices, vertex_format, lookup=None):
    """Sets v['unknown2'] (tangent) and v['unknown3'] (bitangent) on every
    vertex dict when the vertex format carries those channels. Vertices that
    match an original one (see build_tangent_lookup) get the ORIGINAL vectors
    unchanged; the rest get compute_tangents().
    Returns (n_reused, n_computed)."""
    if len(vertex_format) < 4 or not vertex_format[2] or not vertex_format[3]:
        return 0, 0
    lookup = lookup or {}
    missing = []
    reused = 0
    for i, v in enumerate(vertices):
        best = find_original_tangents(lookup, v['position'], v['uv0'], v['normal']) if lookup else None
        if best is not None:
            v['unknown2'] = tuple(best[0])
            v['unknown3'] = tuple(best[1])
            reused += 1
        else:
            missing.append(i)
    if missing:
        T, B = compute_tangents([v['position'][:3] for v in vertices],
                                [v['normal'] for v in vertices],
                                [v['uv0'] for v in vertices], tri_indices)
        for i in missing:
            vertices[i]['unknown2'] = T[i]
            vertices[i]['unknown3'] = B[i]
    return reused, len(missing)


# --------------------------------------------------------------------------
# 2. Slot planning
# --------------------------------------------------------------------------

def plan_slots(objects):
    """objects: iterable of (name, render_index_or_None, group_or_None, ref)
    where `ref` is whatever the caller wants back (the Blender object).

    Returns (slots, extras):
      slots  = {render_index: {group: ref}}   objects that own an existing slot
      extras = [ref, ...]                     objects with NO slot info, or that
                                              collide with another object on the
                                              same (render_index, group)
    Collisions are resolved by name order (first name keeps the slot). That is
    safe for the usual case — a Shift+D duplicate shares material and parent
    with its original, so it doesn't matter which of the two takes the slot;
    both end up in the file.
    """
    slots = {}
    extras = []
    for name, ridx, group, ref in sorted(objects, key=lambda o: o[0]):
        if ridx is None:
            extras.append(ref)
            continue
        g = 0 if group is None else group
        bucket = slots.setdefault(ridx, {})
        if g in bucket:
            extras.append(ref)
        else:
            bucket[g] = ref
    return slots, extras


_SUFFIX_RE = re.compile(r'\.\d{3}$')


def resolve_material_id(edm, edm_material_id=None, blender_material_names=(), explicit=None):
    """Find which entry of edm.materials a new object should use.
    Priority: explicit (str name or int index) -> edm_material_id (copied by
    Shift+D from an imported object) -> Blender material name that matches an
    EDM material NAME (a trailing '.001'-style suffix is ignored).
    Returns an int index, or None."""
    n = len(edm.materials)
    if explicit is not None and explicit != '':
        if isinstance(explicit, int) and 0 <= explicit < n:
            return explicit
        if isinstance(explicit, float) and int(explicit) == explicit and 0 <= int(explicit) < n:
            return int(explicit)
        if isinstance(explicit, str):
            for i, m in enumerate(edm.materials):
                if m.get('NAME') == explicit:
                    return i
            if explicit.isdigit() and int(explicit) < n:
                return int(explicit)
        return None
    if edm_material_id is not None and 0 <= int(edm_material_id) < n:
        return int(edm_material_id)
    by_name = {}
    for i, m in enumerate(edm.materials):
        by_name.setdefault(m.get('NAME'), i)
    for nm in blender_material_names:
        if nm in by_name:
            return by_name[nm]
        stripped = _SUFFIX_RE.sub('', nm)
        if stripped in by_name:
            return by_name[stripped]
    return None


# --------------------------------------------------------------------------
# 3. New RenderNode
# --------------------------------------------------------------------------

def _pick_index_data_type(n_vertices):
    if n_vertices <= 0xFF:
        return 0
    if n_vertices <= 0xFFFF:
        return 1
    return 2


def new_render_node(edm, name, material_id, parent_index, vertices, tri_indices):
    """Build a single-group model::RenderNode dict (same shape edm_reader
    produces) reusing materials[material_id] and transform node
    `parent_index`. The vertices' position.w must be 0.0 (single group)."""
    mat = edm.materials[material_id]
    vertex_format = list(mat.get('VERTEX_FORMAT'))
    template = None
    for typeName, n in edm.render_items.get('RENDER_NODES', []):
        if typeName == 'model::RenderNode' and n['material_id'] == material_id \
                and len(n['parent_data']) == 1:
            template = n
            break
    if template is None:
        for typeName, n in edm.render_items.get('RENDER_NODES', []):
            if typeName == 'model::RenderNode' and len(n['parent_data']) == 1:
                template = n
                break
    unknown = template['index_data'].get('unknown', 0) if template else 0
    version = template.get('version', 0) if template else 0
    props = list(template.get('props', [])) if template else []
    return {
        'name': name,
        'version': version,
        'props': props,
        'material_id': material_id,
        'parent_data': [{'parent': parent_index, 'damage_argument': -1}],
        'vertex_data': edmr.encode_vertices(vertex_format, vertices),
        'index_data': {
            'data_type': _pick_index_data_type(len(vertices)),
            'entries': len(tri_indices),
            'unknown': unknown,
            'data': list(tri_indices),
        },
    }


# --------------------------------------------------------------------------
# 4. Slots with several objects per (render node, group) -- split parts
# --------------------------------------------------------------------------

def plan_slots_multi(objects):
    """objects: iterable of (name, render_index_or_None, group_or_None, ref, is_part).

    Like plan_slots, but every object flagged `is_part` (a loose part made by
    'Split Loose Parts', edm_split_part=1) may share a slot with the other
    parts of that slot; they are recombined on export.

    Returns (slots, extras):
      slots  = {render_index: {group: [ref, ...]}}
      extras = [ref, ...]  objects with no slot info, or colliding with the
               slot's owner (typically a Shift+D duplicate) -> NEW RenderNodes
    """
    slots = {}
    owners = {}
    extras = []
    for name, ridx, group, ref, is_part in sorted(objects, key=lambda o: o[0]):
        if ridx is None:
            extras.append(ref)
            continue
        g = 0 if group is None else group
        kind = owners.get((ridx, g))
        if kind is None:
            owners[(ridx, g)] = 'part' if is_part else 'single'
            slots.setdefault(ridx, {}).setdefault(g, []).append(ref)
        elif kind == 'part' and is_part:
            slots[ridx][g].append(ref)
        else:
            extras.append(ref)
    return slots, extras


# --------------------------------------------------------------------------
# 5. Materials for NEW objects
# --------------------------------------------------------------------------

def image_stem(name_or_path):
    """'C:/x/pilot_new.001.png' -> 'pilot_new'. DCS looks the texture up by
    this stem (it ships as <stem>.dds next to the model/livery)."""
    base = os.path.basename(str(name_or_path).replace('\\', '/'))
    stem, _ext = os.path.splitext(base)
    return _SUFFIX_RE.sub('', stem)


def find_material_by_texture(edm, stems):
    """Index of an existing EDM material whose diffuse (index 0) texture is
    one of `stems` (case-insensitive), or None."""
    wanted = [s.lower() for s in stems if s]
    for stem in wanted:
        for i, m in enumerate(edm.materials):
            for t in m.get('TEXTURES', []):
                if t['index'] == 0 and str(t['filename']).lower() == stem:
                    return i
    return None


def _simple_def_format(m):
    """True if m is a def_material whose vertex format the exporter can fill
    (position/normal/uv0 + optional tangents, nothing else)."""
    if m.get('MATERIAL_NAME') != 'def_material':
        return False
    fmt = list(m.get('VERTEX_FORMAT') or [])
    if len(fmt) < 5 or fmt[0] != 4 or fmt[1] != 3 or fmt[4] != 2:
        return False
    if any(fmt[k] for k in range(5, len(fmt))):
        return False
    return not m.get('ANIMATED_UNIFORMS')


def pick_template_material(edm, prefer=None, pbr=False):
    """Material to clone when a new object needs a texture the file does not
    have yet. `prefer` = EDM material NAME or index. Otherwise the simplest
    def_material: position/normal/uv0 (+tangents), one diffuse texture.
    pbr=True: look for a def_material that has diffuse(0) + normal(1) +
    roughness/metal(2) textures instead; returns None if the file has none
    (the caller then falls back to pbr=False)."""
    n = len(edm.materials)
    if prefer is not None and prefer != '':
        idx = resolve_material_id(edm, explicit=prefer)
        if idx is not None:
            return idx
    if pbr:
        # tier 1: simple format, exactly diffuse+normal+roughmet
        for i, m in enumerate(edm.materials):
            if _simple_def_format(m) and \
                    {t['index'] for t in m.get('TEXTURES', [])} == {0, 1, 2}:
                return i
        # tier 2: any static def_material with at least those 3 textures whose
        # vertex layout starts position/normal/uv0. Channels the exporter does not
        # produce (uv1, ...) are written as zeros (encode_vertices). Fewest extras first.
        best = None
        for i, m in enumerate(edm.materials):
            if not _pbr_capable(m):
                continue
            fmt = list(m.get('VERTEX_FORMAT') or [])
            extras = sum(1 for k, v in enumerate(fmt) if v and k not in (0, 1, 2, 3, 4))
            key = (extras, len(m.get('TEXTURES', [])), i)
            if best is None or key < best[0]:
                best = (key, i)
        return best[1] if best else None
    best = None
    for i, m in enumerate(edm.materials):
        if not _simple_def_format(m):
            continue
        fmt = list(m.get('VERTEX_FORMAT') or [])
        if any(t['index'] != 0 for t in m.get('TEXTURES', [])):
            continue
        key = (1 if m.get('BLENDING', 0) else 0, len(m.get('TEXTURES', [])), 1 if (fmt[2] and fmt[3]) else 0, i)
        if best is None or key < best[0]:
            best = (key, i)
    if best is not None:
        return best[1]
    for i, m in enumerate(edm.materials):
        fmt = list(m.get('VERTEX_FORMAT') or [])
        if len(fmt) >= 5 and fmt[0] == 4 and fmt[1] == 3 and fmt[4] == 2:
            return i
    return None


PBR_SUFFIXES = {1: '_NM', 2: '_RoughMet'}

_TRANSPARENT_HINTS = ('glass', 'visor', 'viseira', 'vidro', 'canopy', 'window')


def default_blending(obj, stem, name):
    """BLENDING value for a NEW material. A clone inherits the template's
    blending; a29b_prop.dds is alpha-blended (BLENDING=1), which made every
    new part translucent and depth-sorted wrongly (parts vanishing by view
    angle). New materials are opaque (0) unless the name looks like glass
    or the object has custom property 'edm_blending' (0/1/2...)."""
    try:
        v = obj.get('edm_blending')
        if v is not None and v != '':
            return int(v)
    except Exception:
        pass
    text = ('%s %s' % (stem, name)).lower()
    return 1 if any(h in text for h in _TRANSPARENT_HINTS) else 0


def _pbr_capable(m):
    """def_material with diffuse(0)+normal(1)+roughmet(2) textures, static
    (no bones, no animated uniforms), vertex layout position/normal/uv0."""
    if m.get('MATERIAL_NAME') != 'def_material' or m.get('ANIMATED_UNIFORMS'):
        return False
    fmt = list(m.get('VERTEX_FORMAT') or [])
    if len(fmt) < 5 or fmt[0] != 4 or fmt[1] != 3 or fmt[4] != 2:
        return False
    if len(fmt) > 21 and fmt[21]:
        return False                     # skinned
    return {0, 1, 2} <= {t['index'] for t in m.get('TEXTURES', [])}


def describe_pbr_candidates(edm, limit=6):
    """Why the file has / has not a usable 3-texture material (for the report)."""
    rows = []
    for i, m in enumerate(edm.materials):
        idx = {t['index'] for t in m.get('TEXTURES', [])}
        if not ({0, 1, 2} <= idx):
            continue
        fmt = list(m.get('VERTEX_FORMAT') or [])
        chans = [k for k, v in enumerate(fmt) if v]
        why = []
        if m.get('MATERIAL_NAME') != 'def_material':
            why.append('shader=%s' % m.get('MATERIAL_NAME'))
        if m.get('ANIMATED_UNIFORMS'):
            why.append('animated uniforms')
        if not (len(fmt) >= 5 and fmt[0] == 4 and fmt[1] == 3 and fmt[4] == 2):
            why.append('layout')
        if len(fmt) > 21 and fmt[21]:
            why.append('skinned')
        rows.append("'%s' (#%d, channels %s%s)" % (m.get('NAME'), i, chans,
                                                  '; rejected: ' + ', '.join(why) if why else ''))
    total = len(rows)
    return rows[:limit], total


def _suffixes_from_template(mat):
    """{index: suffix} copied from the template's own texture names
    ('x', 'x_NM', 'x_RoughMet' -> {1:'_NM', 2:'_RoughMet'}); falls back to
    the DCS defaults."""
    names = {t['index']: str(t['filename']) for t in mat.get('TEXTURES', [])}
    base = names.get(0, '')
    out = dict(PBR_SUFFIXES)
    for k in (1, 2):
        n = names.get(k, '')
        if base and n.lower().startswith(base.lower()) and len(n) > len(base):
            out[k] = n[len(base):]
    return out


def has_pbr_companions(directory, stem):
    """True if <stem>_NM.* and <stem>_RoughMet.* both exist in `directory`
    (case-insensitive). DCS naming: name.dds, name_NM.dds, name_RoughMet.dds."""
    try:
        names = [f.lower() for f in os.listdir(directory)]
    except Exception:
        return False
    def has(suffix):
        pre = (stem + suffix).lower() + '.'
        return any(f.startswith(pre) for f in names)
    return has(PBR_SUFFIXES[1]) and has(PBR_SUFFIXES[2])


def clone_material(edm, template_id, name, stem, pbr=False, blending=None):
    """Append a copy of materials[template_id] with a unique NAME and its
    diffuse texture (index 0) replaced by `stem`. With pbr=True the normal
    map (index 1) becomes <stem>_NM and the roughness/metal map (index 2)
    <stem>_RoughMet. Returns the new index."""
    mat = copy.deepcopy(edm.materials[template_id])
    existing = {m.get('NAME') for m in edm.materials}
    base, k = name, 1
    while name in existing:
        name = f"{base}_{k}"
        k += 1
    mat['NAME'] = name
    if blending is not None:
        mat['BLENDING'] = int(blending)
    sufx = _suffixes_from_template(mat)
    for t in mat.get('TEXTURES', []):
        if t['index'] == 0:
            t['filename'] = stem
        elif pbr and t['index'] in PBR_SUFFIXES:
            t['filename'] = stem + sufx[t['index']]
    edm.materials.append(mat)
    return len(edm.materials) - 1
