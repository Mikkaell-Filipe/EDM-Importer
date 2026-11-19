import os
import struct
import bpy
import mathutils

from . import edm_reader as edmr
from . import edm_writer as edmw
from . import edm_newnodes as nn
from . import edm_nodeplan as npl
from . import new_anim
from . import edm_connectors as conn

# Filled by every export_edm() call (also shown in the Text 'EDM_Export_Report').
last_report = {}


def _flat_from_matrix(m):
    """Inverse of import_edm._matrix_from_flat_d: mathutils.Matrix (rows) ->
    flat column-major tuple of 16 doubles, as EDM stores transforms."""
    mt = m.transposed()
    flat = []
    for col in mt:
        flat.extend(col)
    return tuple(flat)


# ---------------------------------------------------------------------------
# Object <-> transform-node helpers
# ---------------------------------------------------------------------------

# Collection being exported. Node empties are looked up ONLY inside it, so a
# second imported model in the same scene (its empties reuse the same
# edm_node_index numbers) can never be mistaken for this model's nodes.
_scope = None


def _scope_objects():
    return list(_scope.all_objects) if _scope is not None else list(bpy.data.objects)

def _node_empties(include_new=True):
    """{edm_node_index: empty object} -- the "tip" empty of every transform
    node (the one that parents the node's meshes and children). With
    include_new the easy-animation pivots of this export are included under
    the index of the node they were just given."""
    out = {}
    for o in _scope_objects():
        if o.type == 'EMPTY' and 'edm_node_index' in o:
            out.setdefault(int(o['edm_node_index']), o)
    if include_new:
        for name, idx in new_anim.NEW_NODE_INDEX.items():
            o = bpy.data.objects.get(name)
            if o is not None:
                out.setdefault(idx, o)
    return out


def _node_index(emp):
    """EDM node index a node-empty stands for (imported node or new pivot)."""
    if emp.name in new_anim.NEW_NODE_INDEX:
        return new_anim.NEW_NODE_INDEX[emp.name]
    return int(emp['edm_node_index'])


def _node_firsts():
    """{edm_node_index: FIRST empty of the node's chain} (carries the pivot).
    0.4.x importers mark it with 'edm_node_first_of'. The 0.3.x importer
    makes ONE empty per node, so there the tip is also the first."""
    out = {}
    for o in _scope_objects():
        if o.type == 'EMPTY' and 'edm_node_first_of' in o:
            out.setdefault(int(o['edm_node_first_of']), o)
    return out or _node_empties(include_new=False)


def _root_index(edm):
    """Index of the first root transform node (parent -1)."""
    for i, p in enumerate(edm.node_parents):
        if p in (-1, 0xffffffff):
            return i
    return None


def _ancestor_node_empty(obj):
    """Nearest parent node-empty OF THE EXPORTED MODEL (an empty that belongs
    to another imported model is skipped: its node index means nothing here)."""
    inside = None
    if _scope is not None:
        inside = {o.name for o in _scope.all_objects}
    p = obj.parent
    while p is not None:
        if p.type == 'EMPTY' and (p.name in new_anim.NEW_NODE_INDEX
                                  or ('edm_node_index' in p and (inside is None or p.name in inside))):
            return p
        p = p.parent
    return None


def _relative_matrix(obj, empty, require_descendant):
    """Matrix taking `obj` local coordinates into the local frame of the
    transform-node `empty` (= the space EDM vertex positions live in).

    For a descendant of `empty` it is built from the objects' own local
    transforms (parent-inverse @ basis, chained) — no matrix inversion, so an
    untouched imported object gives EXACTLY the identity, and it does not
    depend on the animation pose of `empty`. For a non-descendant it falls
    back to inverse(empty.world) @ obj.world (correct only if `empty` is at
    its rest pose) unless require_descendant is set."""
    if empty is None:
        return None
    chain = []
    p = obj
    while p is not None and p is not empty:
        chain.append(p)
        p = p.parent
    if p is empty:
        m = mathutils.Matrix.Identity(4)
        for o in reversed(chain):
            m = m @ (o.matrix_parent_inverse @ o.matrix_basis)
        return m
    if require_descendant:
        return None
    return empty.matrix_world.inverted_safe() @ obj.matrix_world


def _is_identity(m, tol=1e-4):
    ident = mathutils.Matrix.Identity(4)
    for r in range(4):
        for c in range(4):
            if abs(m[r][c] - ident[r][c]) > tol:
                return False
    return True



# ---------------------------------------------------------------------------
# Transform-node pivots (Empties moved in Blender -> written into the .edm)
# ---------------------------------------------------------------------------
# Mesh vertices are baked relative to their node Empty (see _relative_matrix),
# so moving an Empty in Blender while the .edm node stayed at its old place
# made the mesh show up displaced in DCS. _sync_node_pivots writes the
# Empty's *rest* translation back into the node: the node's tf_matrix /
# transform gets a LEFT translation (exact: rotation/scale/animation are
# untouched, the whole node just moves). Only TRANSLATION is synced.

_ARG_TYPES = ('model::ArgAnimationNode', 'model::ArgRotationNode',
              'model::ArgPositionNode', 'model::ArgScaleNode')


def _m3(flat):
    """3x3 part of an EDM column-major flat matrix as rows: M[r][c]."""
    return [[flat[4 * c + r] for c in range(3)] for r in range(3)]


def _mul3(m, v):
    return tuple(m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] for r in range(3))


def _expected_rest_translation(type_name, node):
    """0.3.x importer (ONE empty per node, full rest matrix): parent-local
    translation of that empty at argument 0, or None if it can't be told."""
    if type_name == 'model::TransformNode':
        t = node['transform']
        return (t[12], t[13], t[14])
    if type_name in _ARG_TYPES:
        tfm = node['tf_matrix']
        pos = tuple(node.get('tf_position', (0.0, 0.0, 0.0)))
        pdata = node.get('position_data', [])
        if len(pdata) > 1:
            return None
        if pdata and pdata[0]['keys']:
            first = min(pdata[0]['keys'], key=lambda k: k[0])
            if first[0] < 0:
                return None
            pos = tuple(pos[i] + first[1][i] for i in range(3))
        base = _mul3(_m3(tfm), pos)
        return (base[0] + tfm[12], base[1] + tfm[13], base[2] + tfm[14])
    return None


def _empty_local_translation(emp):
    m = emp.matrix_parent_inverse @ emp.matrix_basis
    t = m.translation
    return (t[0], t[1], t[2])


def _sync_node_pivots(edm, empties, scene, local_translation=_empty_local_translation,
                      expected_fn=None):
    """Returns (patched, skipped): patched = [(empty name, shift length)],
    skipped = [names whose pivot move could not be written].
    expected_fn(type_name, node) -> translation the importer gave the node's
    first empty (default: edm_nodeplan.first_link_translation, 0.4.x chains)."""
    expected_fn = expected_fn or npl.first_link_translation
    patched, skipped = [], []
    old_frame = scene.frame_current if scene is not None else None
    if scene is not None:
        scene.frame_set(0)          # argument 0 = rest pose
    try:
        for idx, emp in empties.items():
            if not (0 <= idx < len(edm.nodes)):
                continue
            type_name, node = edm.nodes[idx]
            exp = expected_fn(type_name, node)
            if exp is None:
                if type_name in _ARG_TYPES:
                    skipped.append(emp.name)
                continue
            cur = local_translation(emp)
            d = tuple(cur[i] - exp[i] for i in range(3))
            length = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
            mag = (exp[0] ** 2 + exp[1] ** 2 + exp[2] ** 2) ** 0.5
            if length <= 1e-5 + 1e-6 * mag:
                continue
            key = 'transform' if type_name == 'model::TransformNode' else 'tf_matrix'
            t = list(node[key])
            for i in range(3):
                t[12 + i] += d[i]
            node[key] = tuple(t)
            patched.append((emp.name, length))
    finally:
        if scene is not None and old_frame is not None:
            scene.frame_set(old_frame)
    return patched, skipped


# ---------------------------------------------------------------------------
# Mesh -> EDM vertices
# ---------------------------------------------------------------------------

def _needs_face_flip(obj, matrix):
    """True if the triangle winding must be reversed. A transform with a
    negative determinant (mirrored / negative-scale object, or a mirrored
    parent) turns every face inside out; the EDM has no per-object
    negative scale, so the winding has to be flipped here or the game
    back-face-culls the near side and shows the far side through it.
    Custom property 'edm_flip_faces': 1 = always flip, -1 = never flip,
    absent/0 = automatic."""
    forced = 0
    try:
        forced = int(obj.get('edm_flip_faces') or 0)
    except Exception:
        forced = 0
    if forced > 0:
        return True
    if forced < 0:
        return False
    return matrix is not None and matrix.to_3x3().determinant() < 0


def _mesh_to_edm_vertices(obj, mesh, vertex_format, group_index=0, matrix=None, lookup=None):
    """Build one dict per *loop* (not per shared vertex) matching decode_vertices'
    channel layout, since EDM vertices are per-loop (each triangle corner can
    carry its own UV/normal, same as glTF/most game formats).

    matrix: optional 4x4 taking object-local coordinates into the parent
      transform node's local frame (used when the object was moved/scaled at
      OBJECT level, and for brand-new objects).
    lookup: tangent lookup built from the ORIGINAL node (see
      edm_newnodes.build_tangent_lookup) so unchanged vertices keep their
      original tangent/bitangent.

    Returns (vertices, triangle_index_list, (n_tangents_reused, n_computed))."""
    mesh.calc_loop_triangles()
    corner_normals = None
    try:
        corner_normals = [tuple(n.vector) for n in mesh.corner_normals]   # Blender 4.1+
    except Exception:
        try:
            mesh.calc_normals_split()
        except Exception:
            pass

    uv_layer = mesh.uv_layers.active.data if mesh.uv_layers.active else None

    nmat = None
    if matrix is not None:
        nmat = matrix.to_3x3().inverted_safe().transposed()

    verts = []
    key_to_index = {}
    tri_indices = []

    for tri in mesh.loop_triangles:
        for li in tri.loops:
            loop = mesh.loops[li]
            vi = loop.vertex_index
            co = mesh.vertices[vi].co
            normal = corner_normals[li] if corner_normals is not None else tuple(loop.normal)
            if matrix is not None:
                co = matrix @ co
                nv = nmat @ mathutils.Vector(normal)
                if nv.length > 1e-12:
                    nv.normalize()
                normal = tuple(nv)
            uv = tuple(uv_layer[li].uv) if uv_layer else (0.0, 0.0)
            # flip V back (import flips 1.0 - v for Blender's convention)
            uv = (uv[0], 1.0 - uv[1])
            key = (round(co.x, 6), round(co.y, 6), round(co.z, 6),
                   round(normal[0], 4), round(normal[1], 4), round(normal[2], 4),
                   round(uv[0], 6), round(uv[1], 6))
            existing = key_to_index.get(key)
            if existing is None:
                vdict = {
                    'position': (co.x, co.y, co.z, float(group_index)),
                    'normal': normal,
                    'uv0': uv,
                }
                existing = len(verts)
                verts.append(vdict)
                key_to_index[key] = existing
            tri_indices.append(existing)

    if _needs_face_flip(obj, matrix):
        for t in range(0, len(tri_indices) - 2, 3):
            tri_indices[t + 1], tri_indices[t + 2] = tri_indices[t + 2], tri_indices[t + 1]
    stats = nn.fill_tangents(verts, tri_indices, vertex_format, lookup)
    return verts, tri_indices, stats


def _pick_index_data_type(entries_count):
    if entries_count <= 0xFF:
        return 0
    if entries_count <= 0xFFFF:
        return 1
    return 2


def _mesh_of(context, obj, cleanup):
    """Mesh to export for `obj`. Objects with modifiers (Mirror, Bevel, Array
    ...) use the EVALUATED mesh so what you see is what is written."""
    if getattr(obj, 'modifiers', None):
        try:
            ev = obj.evaluated_get(context.evaluated_depsgraph_get())
            mesh = ev.to_mesh()
            cleanup.append(ev)
            return mesh
        except Exception:
            pass
    return obj.data


class ExportSafetyError(RuntimeError):
    """Raised when export would silently drop far more geometry than a
    single deliberate deletion normally involves."""
    pass


# How many parts (whole RenderNodes PLUS individual parts inside merged
# RenderNodes) can go missing from the collection before we assume something
# is wrong rather than a deliberate removal.
DEFAULT_MAX_AUTO_DELETE = 5


# ---------------------------------------------------------------------------
# Object discovery
# ---------------------------------------------------------------------------

def _skip_reason(obj):
    """Why a NEW mesh object is not exported, or None."""
    if obj.get('edm_skip'):
        return "custom property 'edm_skip' is set"
    if getattr(obj, 'hide_viewport', False):
        return 'hidden in viewport (monitor icon)'
    try:
        if obj.hide_get():
            return 'hidden (eye icon)'
    except Exception:
        pass
    return None


def _gather(context, edm, collection, include_other, include_foreign=False, foreign=None):
    """Returns (entries, pulled, ignored, reparented).

    entries   = [(name, render_index|None, group|None, obj, is_part)] ready for
                nn.plan_slots_multi
    pulled    = names of NEW meshes found OUTSIDE `collection` (scene-wide)
    ignored   = {name: reason}
    reparented= names of slot objects that were moved under a DIFFERENT
                transform node (they become NEW RenderNodes there)
    """
    render_nodes = edm.render_items.get('RENDER_NODES', [])
    entries, pulled, ignored, reparented = [], [], {}, []
    own = set()
    for obj in collection.all_objects:
        if obj.type != 'MESH':
            continue
        own.add(obj.name)
        if 'edm_render_index' in obj:
            ridx = obj['edm_render_index']
            g = obj.get('edm_parent_group', 0)
            anc = _ancestor_node_empty(obj)
            if anc is not None and 0 <= ridx < len(render_nodes):
                pdata = render_nodes[ridx][1]['parent_data']
                if g < len(pdata) and _node_index(anc) != pdata[g]['parent']:
                    reparented.append(obj.name)
                    entries.append((obj.name, None, None, obj, False))
                    continue
            entries.append((obj.name, ridx, g, obj, bool(obj.get('edm_split_part'))))
        else:
            reason = _skip_reason(obj)
            if reason:
                ignored[obj.name] = reason
                continue
            entries.append((obj.name, None, None, obj, False))
    if include_other:
        for obj in context.scene.objects:
            if obj.name in own:
                continue
            if obj.type not in ('MESH', 'EMPTY', 'LIGHT', 'CAMERA', 'ARMATURE'):
                ignored[obj.name] = ("object type %s is not exported "
                                     "(convert it to a mesh: Object > Convert > Mesh)"
                                     % obj.type)
                continue
            if obj.type != 'MESH':
                continue
            is_foreign = 'edm_render_index' in obj
            if is_foreign and (include_foreign or obj.get('edm_force_new')):
                reason = _skip_reason(obj)
                if reason:
                    ignored[obj.name] = reason
                    continue
                if foreign is not None:
                    foreign.append(obj.name)
                entries.append((obj.name, None, None, obj, False))
                pulled.append(obj.name)
                continue
            if is_foreign:
                # Mesh imported from ANOTHER .edm (e.g. a pilot copied from
                # another aircraft). Never pulled in silently, never dropped
                # silently: it is reported, and 'edm_force_new' = 1 opts in.
                ignored[obj.name] = ("comes from another imported model "
                                     "(has edm_render_index); tick 'Include meshes from other "
                                     "imported models' or set custom property "
                                     "'edm_force_new' = 1 to export it as NEW geometry")
                continue
            reason = _skip_reason(obj)
            if reason:
                ignored[obj.name] = reason
                continue
            entries.append((obj.name, None, None, obj, False))
            pulled.append(obj.name)
    return entries, pulled, ignored, reparented


def _plan_render_nodes(edm, entries):
    """Dry-run: for every model::RenderNode find the Blender objects that
    own it. Returns (plan, extras); plan item = {'ridx','node','group_objs'
    ({group: [obj,...]} or None),'name'}."""
    render_nodes = edm.render_items.get('RENDER_NODES', [])
    by_render_index, extras = nn.plan_slots_multi(entries)
    plan = []
    for ridx, (typeName, node) in enumerate(render_nodes):
        if typeName != 'model::RenderNode':
            continue
        plan.append({
            'ridx': ridx,
            'node': node,
            'group_objs': by_render_index.get(ridx),
            'name': node.get('name') or f'RenderNode_{ridx}',
        })
    return plan, extras


# ---------------------------------------------------------------------------
# Materials / parents for NEW objects
# ---------------------------------------------------------------------------

def _material_names(obj):
    return [s.material.name for s in getattr(obj, 'material_slots', []) if s.material]


def _image_in_groups(tree, depth=0):
    """First image texture found inside node groups (nested), or None."""
    if depth > 4:
        return None
    for n in getattr(tree, 'nodes', []):
        if n.type == 'GROUP' and getattr(n, 'node_tree', None):
            img = next((m.image for m in n.node_tree.nodes
                        if m.type == 'TEX_IMAGE' and m.image), None)
            if img is not None:
                return img
            img = _image_in_groups(n.node_tree, depth + 1)
            if img is not None:
                return img
    return None


def _main_images(obj):
    """Blender images feeding the Base Color of each material of `obj`."""
    out = []
    for slot in getattr(obj, 'material_slots', []):
        mat = slot.material
        tree = getattr(mat, 'node_tree', None) if mat else None
        if not tree:
            continue
        img = None
        try:
            bsdf = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
            links = bsdf.inputs['Base Color'].links
            if links and links[0].from_node.type == 'TEX_IMAGE':
                img = links[0].from_node.image
        except Exception:
            pass
        if img is None:
            img = next((n.image for n in tree.nodes if n.type == 'TEX_IMAGE' and n.image), None)
        if img is None:
            img = _image_in_groups(tree)
        if img is not None:
            out.append(img)
    return out


def _image_stems(obj):
    """Texture stems used by the Blender materials of `obj` (image feeding
    Base Color first). 'C:/x/pilot.png' -> 'pilot'."""
    stems = []
    for img in _main_images(obj):
        stem = nn.image_stem(img.filepath) or nn.image_stem(img.name)
        if stem:
            stems.append(stem)
    return stems


def _texture_search_dirs(obj, extra_dir=''):
    """Folders where <stem>_NM / <stem>_RoughMet may live: the folder chosen
    in the export dialog, the folder of the Blender image, the .blend folder
    and its 'textures' sub-folder."""
    dirs = []
    def add(d):
        if d and d not in dirs and os.path.isdir(d):
            dirs.append(d)
    if extra_dir:
        resolved = None
        try:
            resolved = bpy.path.abspath(extra_dir)      # '//textures' -> absolute
        except Exception:
            pass
        add(resolved if isinstance(resolved, str) else extra_dir)
    for img in _main_images(obj):
        try:
            add(os.path.dirname(bpy.path.abspath(img.filepath)))
        except Exception:
            pass
    try:
        blend_dir = os.path.dirname(bpy.data.filepath)
        add(blend_dir)
        add(os.path.join(blend_dir, 'textures'))
    except Exception:
        pass
    return dirs


def _wants_pbr(obj, extra_dir=''):
    """(wanted, searched_dirs). True if <stem>_NM and <stem>_RoughMet exist in
    one of the search folders. Custom property 'edm_pbr' (0/1) overrides."""
    forced = obj.get('edm_pbr')
    if forced is not None and forced != '':
        return bool(int(forced)), []
    imgs = _main_images(obj)
    if not imgs:
        return False, []
    img = imgs[0]
    stem = nn.image_stem(img.filepath) or nn.image_stem(img.name)
    dirs = _texture_search_dirs(obj, extra_dir)
    if not stem:
        return False, dirs
    return any(nn.has_pbr_companions(d, stem) for d in dirs), dirs


def _resolve_new(edm, extras, empties, opts, rep):
    """Material + parent node + local matrix for every NEW object. Problems
    are collected and raised together, before anything is written."""
    specs, problems = [], []
    created = {}
    root_idx = _root_index(edm)
    for obj in extras:
        stems = _image_stems(obj)
        fallback_stem = False
        if not stems:
            mnames = _material_names(obj)
            if mnames and not obj.get('edm_new_material'):
                stems = [nn.image_stem(mnames[0])]
                fallback_stem = True
        mat_id = nn.resolve_material_id(
            edm,
            edm_material_id=(None if obj.name in rep.get('foreign', ()) else obj.get('edm_material_id')),
            blender_material_names=_material_names(obj),
            explicit=obj.get('edm_new_material'))
        if mat_id is None:
            mat_id = nn.find_material_by_texture(edm, stems)
        if mat_id is None and opts['create_materials'] and stems:
            key = stems[0].lower()
            if key not in created:
                pbr, searched = _wants_pbr(obj, opts.get('texture_dir', ''))
                if not pbr and not obj.get('edm_pbr') and obj.get('edm_pbr') != 0:
                    rep['warnings'].append(
                        f"'{stems[0]}': no {stems[0]}_NM / {stems[0]}_RoughMet found "
                        f"(searched: {'; '.join(searched) or 'no folder'}). Fill 'Texture "
                        "folder' in the export dialog, or set custom property 'edm_pbr' = 1")
                tpl = nn.pick_template_material(edm, prefer=obj.get('edm_template_material'))
                if pbr and not obj.get('edm_template_material'):
                    tpl_pbr = nn.pick_template_material(edm, pbr=True)
                    if tpl_pbr is not None:
                        tpl = tpl_pbr
                    else:
                        pbr = False
                        rows, total = nn.describe_pbr_candidates(edm)
                        rep['warnings'].append(
                            f"'{stems[0]}' has _NM/_RoughMet maps but the EDM has no usable "
                            "static def_material with diffuse+normal+roughmet to clone: only the "
                            "diffuse map will be used. 3-texture materials in the file: "
                            f"{total}" + (": " + "; ".join(rows) if rows else "")
                            + ". Set 'edm_template_material' (an EDM material NAME) to force one")
                if tpl is not None:
                    tex_idx = {t['index'] for t in edm.materials[tpl].get('TEXTURES', [])}
                    pbr = pbr and {0, 1, 2} <= tex_idx
                    mats = _material_names(obj)
                    base = nn._SUFFIX_RE.sub('', mats[0]) if mats else stems[0]
                    if pbr and not nn._simple_def_format(edm.materials[tpl]):
                        rep['warnings'].append(
                            f"'{stems[0]}': template '{edm.materials[tpl].get('NAME')}' has extra "
                            "vertex channels (uv1 etc.): written as zeros")
                    created[key] = nn.clone_material(edm, tpl, base, stems[0], pbr=pbr,
                                                     blending=nn.default_blending(obj, stems[0], base))
                    rep['created_materials'].append(
                        f"'{edm.materials[created[key]]['NAME']}' <- texture '{stems[0]}'"
                        f"{' + _NM + _RoughMet' if pbr else ''} "
                        f"(cloned from '{edm.materials[tpl].get('NAME')}', BLENDING={edm.materials[created[key]].get('BLENDING')})")
            mat_id = created.get(key)
            if fallback_stem and mat_id is not None:
                rep['warnings'].append(
                    f"'{obj.name}': material '{_material_names(obj)[0]}' has no image texture; "
                    f"EDM material created using the NAME '{stems[0]}' as texture "
                    f"(copy {stems[0]}.dds to the mod textures folder or add an image texture)")

        parent_empty = _ancestor_node_empty(obj)
        parent_idx = None
        matrix = None
        if parent_empty is not None:
            parent_idx = _node_index(parent_empty)
            matrix = _relative_matrix(obj, parent_empty, require_descendant=True)
        else:
            spec = None if obj.name in rep.get('foreign', ()) else obj.get('edm_parent_node')
            if spec is not None and spec != '':
                if isinstance(spec, (int, float)) or (isinstance(spec, str) and spec.isdigit()):
                    idx = int(spec)
                    parent_idx = idx if 0 <= idx < len(edm.nodes) else None
                else:
                    for i, (t, n) in enumerate(edm.nodes):
                        if n.get('name') == spec:
                            parent_idx = i
                            break
                if parent_idx is not None:
                    matrix = _relative_matrix(obj, empties.get(parent_idx), require_descendant=False)
            if parent_idx is None and opts['fallback_to_root'] and root_idx is not None:
                parent_idx = root_idx
                matrix = _relative_matrix(obj, empties.get(root_idx), require_descendant=False)
                rep['rooted'].append(obj.name)

        why = []
        if mat_id is None:
            mnames = _material_names(obj)
            if not mnames:
                cause = "object has NO material slot/material"
            elif not opts['create_materials']:
                cause = "'create materials' is off in the export dialog"
            elif nn.pick_template_material(edm, prefer=obj.get('edm_template_material')) is None:
                cause = "the .edm has no def_material to clone as a template (set 'edm_new_material' to an existing material name)"
            else:
                cause = "could not create a material from materials " + ', '.join(mnames)
            why.append("no EDM material: " + cause)
        if parent_idx is None:
            why.append("no transform-node parent (parent it to a node empty, set custom "
                       "property 'edm_parent_node', or enable 'Attach loose objects to root')")
        if why:
            problems.append((obj.name, '; '.join(why)))
        else:
            specs.append((obj, mat_id, parent_idx, matrix))
    if problems:
        names = ', '.join(n for n, _ in problems[:6]) + (' ...' if len(problems) > 6 else '')
        detail = ['EDM export: objects that need attention (' + str(len(problems)) + '):'] + \
                 [f"  - '{n}': {w}" for n, w in problems]
        detail += ['', "To leave a helper mesh out: hide it or set custom property 'edm_skip' = 1."]
        rep['warnings'].extend(f"'{n}': {w}" for n, w in problems)
        _write_report_text(detail)
        raise RuntimeError(f"{len(problems)} mesh(es) without material/parent: {names} "
                           "(details in Text 'EDM_Export_Report')")
    return specs


def _grow_bbox(edm, obj, root_empty):
    """Union the model bounding box (header) with `obj` in EDM space."""
    try:
        m = root_empty.matrix_world.inverted_safe() @ obj.matrix_world
        lo, hi = list(edm.bbox_min), list(edm.bbox_max)
        for c in obj.bound_box:
            p = m @ mathutils.Vector(c)
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
        edm.bbox_min, edm.bbox_max = tuple(lo), tuple(hi)
        return True
    except Exception:
        return False


def _report_lines(rep):
    try:
        from . import bl_info as _bi
        _ver = '.'.join(str(x) for x in _bi['version'])
    except Exception:
        _ver = '?'
    out = ['DCS EDM export report  (addon %s)' % _ver, '=====================', '']
    out.append(f"RenderNodes rewritten: {rep['edited']}   emptied: {rep['deleted']}   "
               f"NEW: {len(rep['new'])}   parts removed: {len(rep['removed'])}")
    out.append(f"CONNECTORS written: {len(rep.get('connectors', []))}")
    out.append('')
    sections = (
        ('NEW animations written (easy animation pivots)', rep.get('anim', [])),
        ('NEW CONNECTORS written to the .edm (DCS can now find them by name)', rep.get('connectors', [])),
        ('Connectors NOT written', rep.get('connectors_skipped', [])),
        ('NEW RenderNodes written', rep['new']),
        ('NEW EDM materials created (put <texture>.dds in the mod textures folder!)',
         rep['created_materials']),
        ('Objects found OUTSIDE the imported collection and exported anyway', rep['pulled']),
        ('Objects attached to the ROOT node (static, always visible)', rep['rooted']),
        ('Objects with MIRRORED transform (negative scale): face winding flipped on export', rep['flipped']),
        ('Parts moved under a different node (became NEW RenderNodes)', rep['reparented']),
        ('Objects from OTHER imported models exported as NEW geometry', list(rep.get('foreign', []))),
        ('IGNORED objects', [f"{k}: {v}" for k, v in rep['ignored'].items()]),
        ('REMOVED from the original (no object left in Blender)', rep['removed']),
        ('Node pivots written', rep['pivots']),
        ('Warnings', rep['warnings']),
    )
    for title, items in sections:
        if items:
            out.append(f"{title} ({len(items)}):")
            out += [f"  - {x}" for x in items[:40]]
            if len(items) > 40:
                out.append(f"  ... (+{len(items) - 40} more)")
            out.append('')
    return out


def _write_report_text(lines):
    try:
        txt = bpy.data.texts.get('EDM_Export_Report') or bpy.data.texts.new('EDM_Export_Report')
        txt.clear()
        txt.write('\n'.join(lines))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _export_edm_impl(context, filepath, collection=None, confirm_bulk_delete=False,
               max_auto_delete=DEFAULT_MAX_AUTO_DELETE, write_node_pivots=True,
               include_other_collections=True, create_materials=True,
               fallback_to_root=True, include_foreign=False, texture_dir=''):
    """Re-parses the source .edm this collection was imported from, replaces
    vertex/index data of every RenderNode that has Blender objects, ADDS new
    geometry, and writes the result to filepath.

    Returns (edited, deleted, added); details in `last_report` and in the
    Text 'EDM_Export_Report'.

    EXISTING GEOMETRY: objects keep their (edm_render_index, edm_parent_group)
    slot. Several loose parts may share a slot (edm_split_part=1); they are
    recombined. A slot with no object left is treated as an intentional
    deletion. A slot object moved under a DIFFERENT transform node becomes a
    NEW RenderNode on that node.

    NEW GEOMETRY: any visible mesh without a slot -- in ANY collection of the
    scene when include_other_collections -- is written as a new single-group
    RenderNode:
      material: 'edm_new_material' > 'edm_material_id' (copied by Shift+D) >
                Blender material name == EDM material NAME > diffuse image
                name == an EDM material's texture > NEW EDM material cloned
                from a template (create_materials) with that image as texture
      parent:   nearest ancestor node empty > 'edm_parent_node' > root node
                (fallback_to_root); object transforms are baked into vertices
    Objects with modifiers are exported evaluated. The model bounding box in
    the header grows to include new geometry.

    Limitations: only position/normal/uv0/tangent/bitangent are re-encoded;
    material definitions (other than created ones) and animation round-trip
    from the source file; new geometry is static (no new animation nodes);
    bone weights are not written.
    """
    global last_report
    if collection is None:
        collection = context.collection

    src_path = collection.get('edm_source_path')
    if not src_path or not os.path.isfile(src_path):
        raise RuntimeError(
            "Could not find the original .edm this collection was imported "
            "from (missing/broken 'edm_source_path'). Export patches that file, "
            "so it must still exist at the same path.")

    edm = edmr.EDMFile(src_path)
    opts = {'create_materials': create_materials, 'fallback_to_root': fallback_to_root,
            'texture_dir': texture_dir}
    rep = {'edited': 0, 'deleted': 0, 'new': [], 'created_materials': [], 'pulled': [],
           'rooted': [], 'flipped': [], 'reparented': [], 'ignored': {}, 'removed': [], 'pivots': [],
           'warnings': []}

    # Empties copiados de conectores do AMX herdam props de no: limpar ANTES de tudo
    cleaned = conn.sanitize(context, edm, collection)
    if cleaned:
        rep['warnings'].append("props de no herdadas por engano removidas de %d empty(s): %s"
                               % (len(cleaned), ', '.join(cleaned[:8]) + (' ...' if len(cleaned) > 8 else '')))

    # Easy-animation pivots -> NEW animation nodes (before meshes are planned,
    # so meshes parented to a pivot are written under its node)
    anim_rep = new_anim.allocate(context, edm, collection, _root_index(edm),
                                 _node_empties(include_new=False).get(_root_index(edm)))
    rep['anim'] = anim_rep['created']
    rep['warnings'].extend(anim_rep['warnings'])

    # Connectors NOVOS (A29N-*, PNT_*, PTR-*, FP_*): antes nunca chegavam ao .edm
    conn_rep = conn.allocate(context, edm, collection, _root_index(edm),
                             _node_empties(include_new=False).get(_root_index(edm)),
                             new_anim._find_parent_node, new_anim._relative_rows)
    rep['connectors'] = conn_rep['created']
    rep['connectors_skipped'] = conn_rep['skipped']
    rep['warnings'].extend(conn_rep['warnings'])

    foreign = []
    entries, pulled, ignored, reparented = _gather(context, edm, collection,
                                                   include_other_collections,
                                                   include_foreign, foreign)
    rep['foreign'] = foreign
    rep['pulled'], rep['ignored'], rep['reparented'] = pulled, ignored, reparented
    plan, extras = _plan_render_nodes(edm, entries)
    empties = _node_empties()

    # slots whose object was only MOVED under another node (it is exported as a
    # NEW RenderNode there) must not count as deletions
    moved_slots = set()
    for nm in reparented:
        ob = bpy.data.objects.get(nm)
        if ob is not None and 'edm_render_index' in ob:
            moved_slots.add((int(ob['edm_render_index']), int(ob.get('edm_parent_group', 0))))

    # -- Pre-pass: decode originals of merged nodes, count removals -------
    total = len(plan)
    removed_names = []
    whole_deleted = 0
    orig_decoded = {}
    for p in plan:
        node = p['node']
        mat_def = edm.materials[node['material_id']]
        fmt = list(mat_def.get('VERTEX_FORMAT'))
        pdata = node['parent_data']
        if len(pdata) > 1:
            decoded = edmr.decode_vertices(fmt, node['vertex_data'])
            orig_decoded[p['ridx']] = decoded
            present = {int(round(v['position'][3])) for v in decoded}
            for g in sorted(present):
                if g < len(pdata) and (p['ridx'], g) not in moved_slots \
                        and not (p['group_objs'] and g in p['group_objs']):
                    pn = edm.nodes[pdata[g]['parent']][1]['name'] if 0 <= pdata[g]['parent'] < len(edm.nodes) else f'group {g}'
                    removed_names.append(pn)
        if not p['group_objs']:
            whole_deleted += 1
            if len(pdata) <= 1:
                has_geo = node['index_data'].get('entries', 0) > 0
                if has_geo and (p['ridx'], 0) not in moved_slots:     # an already-empty / moved slot is not a removal
                    label = p['name']
                    if not node.get('name') and pdata and 0 <= pdata[0]['parent'] < len(edm.nodes):
                        label = edm.nodes[pdata[0]['parent']][1].get('name') or label
                    removed_names.append(label)

    if total and len(removed_names) > max_auto_delete and not confirm_bulk_delete:
        sample = ', '.join(str(x) for x in removed_names[:15])
        if len(removed_names) > 15:
            sample += f", … (+{len(removed_names) - 15} more)"
        raise ExportSafetyError(
            f"Refusing to export: {len(removed_names)} part(s) have no "
            f"matching object in collection '{collection.name}' and would "
            f"be permanently removed from the file. Parts that would be "
            f"removed: {sample}. If you deleted these on purpose, enable "
            "'Confirm removing many parts' in the export options and "
            "export again. Otherwise check that this is the right "
            "collection (the one with 'edm_source_path') and that a "
            "box-select/Delete didn't sweep up more than intended.")

    # -- Resolve NEW objects up front so problems abort before any write ---
    new_specs = _resolve_new(edm, extras, empties, opts, rep)

    def _empty_vertex_index_data(vertex_format, unknown):
        # 0.7.0: antes gravava 0 vertices / 0 indices -> o DCS loga
        # "Failed assert numIndices != 0 && size != 0 / E_INVALIDARG" para cada
        # peca removida. Agora vai UM triangulo degenerado (3 indices no mesmo
        # vertice zerado): nao desenha nada e o buffer e valido.
        empty = edmr.encode_vertices(vertex_format, [{}])
        return empty, {'data_type': 0, 'entries': 3, 'unknown': unknown, 'data': [0, 0, 0]}

    cleanup = []
    reused_total = 0
    computed_total = 0
    try:
        for p in plan:
            node = p['node']
            group_objs = p['group_objs']
            mat_def = edm.materials[node['material_id']]
            parent_data = node['parent_data']

            if not group_objs:
                vertex_format = list(mat_def.get('VERTEX_FORMAT'))
                node['vertex_data'], node['index_data'] = _empty_vertex_index_data(
                    vertex_format, node['index_data'].get('unknown', 0))
                if len(parent_data) > 1:
                    node['parent_data'] = [{**e, 'index_start': 0} for e in parent_data]
                rep['deleted'] += 1
                continue

            any_obj = next(iter(group_objs.values()))[0]
            vertex_format = list(any_obj.get('edm_vertex_format', mat_def.get('VERTEX_FORMAT')))
            has_tangents = len(vertex_format) > 3 and vertex_format[2] and vertex_format[3]

            def _matrix_for(obj, g):
                pi = parent_data[g]['parent'] if g < len(parent_data) else None
                m = _relative_matrix(obj, empties.get(pi), require_descendant=True) if pi is not None else None
                if m is None or _is_identity(m):
                    return None
                return m

            def _group_geometry(g, objs, decoded):
                """Concatenate the geometry of every object of one slot."""
                lookup = None
                if has_tangents and decoded is not None:
                    lookup = nn.build_tangent_lookup(decoded, group=g if len(parent_data) > 1 else None)
                gv, gt = [], []
                for obj in objs:
                    matrix = _matrix_for(obj, g)
                    v, t, st = _mesh_to_edm_vertices(
                        obj, _mesh_of(context, obj, cleanup), vertex_format, group_index=g,
                        matrix=matrix, lookup=lookup if matrix is None else None)
                    nonlocal_stats[0] += st[0]
                    nonlocal_stats[1] += st[1]
                    off = len(gv)
                    gv.extend(v)
                    gt.extend(i + off for i in t)
                return gv, gt

            nonlocal_stats = [0, 0]
            if len(parent_data) <= 1:
                decoded = None
                if has_tangents:
                    decoded = edmr.decode_vertices(list(mat_def.get('VERTEX_FORMAT')), node['vertex_data'])
                verts, tri_indices = _group_geometry(0, group_objs.get(0, [any_obj]), decoded)
                node['vertex_data'] = edmr.encode_vertices(vertex_format, verts)
                node['index_data'] = {
                    'data_type': _pick_index_data_type(len(verts)),
                    'entries': len(tri_indices),
                    'unknown': node['index_data'].get('unknown', 0),
                    'data': tri_indices,
                }
                rep['edited'] += 1
                reused_total += nonlocal_stats[0]
                computed_total += nonlocal_stats[1]
                continue

            # Multi-group: recombine every group that still has objects into
            # one shared vertex/index buffer. A group with none is deleted.
            decoded = orig_decoded.get(p['ridx'])
            all_verts, all_tris, new_index_starts = [], [], []
            for g in range(len(parent_data)):
                if g in group_objs:
                    g_verts, g_tris = _group_geometry(g, group_objs[g], decoded)
                else:
                    g_verts, g_tris = [], []
                offset = len(all_verts)
                all_verts.extend(g_verts)
                all_tris.extend(vi + offset for vi in g_tris)
                new_index_starts.append(len(all_tris))
            node['vertex_data'] = edmr.encode_vertices(vertex_format, all_verts)
            node['index_data'] = {
                'data_type': _pick_index_data_type(len(all_verts)),
                'entries': len(all_tris),
                'unknown': node['index_data'].get('unknown', 0),
                'data': all_tris,
            }
            node['parent_data'] = [{**entry, 'index_start': new_index_starts[g]}
                                   for g, entry in enumerate(parent_data)]
            rep['edited'] += 1
            reused_total += nonlocal_stats[0]
            computed_total += nonlocal_stats[1]

        # -- NEW RenderNodes ---------------------------------------------------
        render_list = edm.render_items.setdefault('RENDER_NODES', [])
        root_empty = empties.get(_root_index(edm))
        for obj, mat_id, parent_idx, matrix in new_specs:
            mat_fmt = list(edm.materials[mat_id].get('VERTEX_FORMAT'))
            verts, tri_indices, st = _mesh_to_edm_vertices(
                obj, _mesh_of(context, obj, cleanup), mat_fmt, group_index=0, matrix=matrix, lookup=None)
            if not tri_indices:
                rep['warnings'].append(f"new object '{obj.name}' has no faces, skipped")
                continue
            computed_total += st[1]
            if _needs_face_flip(obj, matrix):
                rep['flipped'].append(obj.name)
            render_list.append(('model::RenderNode',
                                nn.new_render_node(edm, obj.name, mat_id, parent_idx, verts, tri_indices)))
            rep['new'].append(
                f"'{obj.name}' -> material '{edm.materials[mat_id].get('NAME')}' "
                f"(texture '{next((t['filename'] for t in edm.materials[mat_id].get('TEXTURES', []) if t['index'] == 0), '?')}'), "
                f"parent node '{edm.nodes[parent_idx][1].get('name')}' [{parent_idx}], "
                f"{len(verts)} verts, {len(tri_indices) // 3} tris")
            if root_empty is not None:
                _grow_bbox(edm, obj, root_empty)
    finally:
        for ev in cleanup:
            try:
                ev.to_mesh_clear()
            except Exception:
                pass

    if write_node_pivots:
        firsts = _node_firsts()
        legacy = bool(firsts) and not any('edm_node_first_of' in e for e in firsts.values())
        patched, skipped = _sync_node_pivots(
            edm, firsts, context.scene,
            expected_fn=_expected_rest_translation if legacy else None)
        rep['pivots'] = [f"{name} ({dist:.4f} m)" for name, dist in patched]
        if skipped:
            rep['warnings'].append("pivot move NOT written (couldn't be determined): " + ', '.join(skipped))

    rep['removed'] = list(removed_names)
    rep['tangents'] = (reused_total, computed_total)

    writer = edmw.EDMWriter(edm)
    out_bytes = writer.write()
    with open(filepath, 'wb') as f:
        f.write(out_bytes)

    last_report = rep
    lines = _report_lines(rep)
    _write_report_text(lines)
    print('\n'.join(lines))
    print(f"EDM export: tangents: {reused_total} vertices kept original, {computed_total} computed.")

    return rep['edited'], rep['deleted'], len(rep['new'])


def export_edm(context, filepath, collection=None, **kwargs):
    """See _export_edm_impl. Sets the node-empty scope to the exported
    collection for the duration of the export."""
    global _scope
    if collection is None:
        collection = context.collection
    _scope = collection
    try:
        return _export_edm_impl(context, filepath, collection, **kwargs)
    finally:
        _scope = None
        new_anim.clear()
