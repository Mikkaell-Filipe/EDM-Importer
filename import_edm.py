import os
import re
import bpy
import mathutils

from . import edm_reader as edmr

_ARG_NAME_RE = re.compile(r'^\d+(_.+)?$')


def _rename_action_for_arg(obj, argument):
    """Rename obj's current Action to the '<arg>_<description>' pattern the
    official Eagle Dynamics exporter requires (it extracts the DCS argument
    number straight from the Action's name — see utils.extract_arg_number
    in the official addon). Without this, Blender's auto-generated name
    (e.g. 'Circle12025Action', from keyframe_insert on object 'Circle12025')
    is left in place, and the official exporter aborts with 'Could not
    extract arg from action' for every animated object imported here.
    Safe to call repeatedly; does nothing once the name already matches.
    """
    ad = getattr(obj, 'animation_data', None)
    action = getattr(ad, 'action', None) if ad else None
    if action is None or _ARG_NAME_RE.match(action.name):
        return
    action.name = f"{int(round(argument))}_{obj.name}"


def _matrix_from_flat_d(flat16):
    # EDM matrices are column-major (OpenGL convention); mathutils.Matrix
    # wants rows, so build column-major then transpose.
    cols = [flat16[i * 4:(i + 1) * 4] for i in range(4)]
    m = mathutils.Matrix(cols)
    m.transpose()
    return m


def _find_or_load_texture(edm_path, filename):
    """Best-effort texture lookup: same folder, then a 'textures' subfolder,
    trying the extensions actually shipped with DCS content (.dds primarily,
    sometimes .bmp)."""
    base_dir = os.path.dirname(edm_path)
    candidates = []
    for folder in (base_dir, os.path.join(base_dir, 'textures'),
                   os.path.join(os.path.dirname(base_dir), 'textures')):
        for ext in ('.dds', '.DDS', '.bmp', '.BMP', '.png', '.tga'):
            candidates.append(os.path.join(folder, filename + ext))
            candidates.append(os.path.join(folder, filename))
    for path in candidates:
        if os.path.isfile(path):
            try:
                return bpy.data.images.load(path, check_existing=True)
            except RuntimeError:
                continue
    return None


def _build_material(edm_path, mat_def, cache):
    name = mat_def.get('NAME') or mat_def.get('MATERIAL_NAME') or 'edm_material'
    if name in cache:
        return cache[name]
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat['edm_base_material'] = mat_def.get('MATERIAL_NAME', '')
    mat['edm_blending'] = mat_def.get('BLENDING', 0)
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    textures = mat_def.get('TEXTURES', [])
    diffuse_tex = next((t for t in textures if t['index'] == 0), None)
    if diffuse_tex is not None and bsdf is not None:
        img = _find_or_load_texture(edm_path, diffuse_tex['filename'])
        if img is not None:
            tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
            tex_node.image = img
            mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
        else:
            mat['edm_diffuse_texture_missing'] = diffuse_tex['filename']
    if mat_def.get('BLENDING') == 1:
        mat.blend_method = 'BLEND'
    cache[name] = mat
    return mat


def _trs_matrix(pos, quat_xyzw, scale):
    qx, qy, qz, qw = quat_xyzw
    t = mathutils.Matrix.Translation(pos)
    q = mathutils.Quaternion((qw, qx, qy, qz)).to_matrix().to_4x4()
    s = mathutils.Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0))
    return t @ q @ s


def _local_rest_matrix(typeName, node):
    """Rest-pose local matrix for a transform node — used to set the
    matrix_basis of the empties that represent the transform-node tree
    (see import_edm).

    CORRECTED AGAIN (2nd pass). 1st pass used tf_matrix alone (dropping
    tf_position/tf_quat1/tf_scale entirely), which fixed the transform-node
    *pivot* hierarchy (414/416 node pivots landed inside the model bbox)
    but NOT all attached mesh geometry: some RenderNode groups (e.g.
    Cockpit_A-29B.EDM 'Box019' parent=356, a switch on the throttle grip)
    are authored in a LOCAL space that's still ~100x too big/off after
    tf_matrix alone — decoded raw vertex Z reached 109 units for a cockpit
    whose whole bbox is ~3.4x1.7x1m. That node's tf_scale
    (0.00227, 0.00227, 0.0000819) shrinks it to a plausible ~0.01 units,
    and several sibling switches under the same parent ('Throttle', node
    347) all share the IDENTICAL tf_matrix but have DIFFERENT small
    tf_position values (~0.003-0.05) that are exactly what separates them
    spatially — meaning tf_matrix alone collapses distinct siblings onto
    the same point.

    Verified with a full standalone walk of Cockpit_A-29B.EDM's hierarchy
    AND all 29 RenderNodes' decoded vertex groups (231 groups total,
    accounting for multi-parent splits): using
        local = tf_matrix @ TRS(tf_position, tf_quat1, tf_scale)
    gives 416/416 node pivots AND 231/231 mesh groups landing inside the
    model bbox (+1m margin) — vs 414/416 and 220/231 with tf_matrix alone.
    So tf_matrix is a base/joint matrix, and tf_position/tf_quat1/tf_scale
    is a further local offset+orientation+size applied on top of it (not
    an alternative encoding of the same thing, and not decorative/unused
    the way the 1st-pass fix assumed).
    """
    if typeName == 'model::TransformNode':
        return _matrix_from_flat_d(node['transform'])
    if typeName in ('model::ArgAnimationNode', 'model::ArgRotationNode',
                     'model::ArgPositionNode', 'model::ArgScaleNode',
                     'model::ArgAnimatedBone'):
        tfm = _matrix_from_flat_d(node['tf_matrix'])
        trs = _trs_matrix(node.get('tf_position', (0.0, 0.0, 0.0)),
                           node.get('tf_quat1', (0.0, 0.0, 0.0, 1.0)),
                           node.get('tf_scale', (1.0, 1.0, 1.0)))
        return tfm @ trs
    return mathutils.Matrix.Identity(4)


def _build_mesh_obj(name, edm_path, verts, faces, material_def, mat_index, materials_cache):
    """Build one Blender mesh object from an already-local list of decoded
    vertices and faces (face indices local to `verts`)."""
    positions = [(v['position'][0], v['position'][1], v['position'][2]) for v in verts]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    mesh.validate()
    mesh.update()

    if verts and 'normal' in verts[0]:
        normals = [v['normal'] for v in verts]
        try:
            mesh.normals_split_custom_set_from_vertices(normals)
        except Exception:
            pass

    if verts and 'uv0' in verts[0]:
        uv_layer = mesh.uv_layers.new(name='UVMap')
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                vi = mesh.loops[li].vertex_index
                u, v = verts[vi]['uv0']
                uv_layer.data[li].uv = (u, 1.0 - v)

    obj = bpy.data.objects.new(name, mesh)
    mat = _build_material(edm_path, material_def, materials_cache)
    mesh.materials.append(mat)
    return obj


def _make_render_node_objects(name, edm_path, render_node, material_def, mat_index,
                               render_index, materials_cache):
    """Build one or more Blender objects for a RenderNode.

    Most RenderNodes have a single entry in parent_data — one object,
    parented normally to its transform node.

    Some (confirmed on a real cockpit file: e.g. 'MDP_ECS' with 94 entries,
    'Object011' with 49) combine MANY unrelated moving parts — switches,
    levers, gauges — into a single vertex/index buffer, one parent_data
    entry per part. Each entry's `index_start` is the END offset (in the
    shared index buffer) of that part's triangles, with the first part
    implicitly starting at 0 (validated against real data: this exactly
    matches each vertex's 4th position component, which is the group id).

    These are split into one Blender object per group here, each using
    ONLY that group's vertices (renumbered locally) — no world-space
    baking. Each sub-object is then parented normally to its own
    transform-node empty, so Blender's normal parenting makes it move
    correctly when that node is animated (baking to world space, which
    the previous version of this addon did, silently froze these parts
    and broke their animation — exactly the "peças não se movem" bug).

    CORRECTED: groups are now built from each vertex's position.w group id
    (via edmr.split_by_vertex_group), not from parent_data's index_start
    field. index_start is commonly 0 for most/all entries in a real
    multi-parent RenderNode, so treating it as a contiguous triangle-range
    boundary silently dumped almost everything into group 0 — this was the
    actual cause of large static meshes (fuselage/wing skin) importing with
    pieces scattered near the origin, even after the transform-node rest
    pose fix below.

    Returns a list of (obj, parent_node_index) tuples.
    """
    fmt = material_def.get('VERTEX_FORMAT')
    parent_data = render_node['parent_data']

    if len(parent_data) <= 1:
        verts = edmr.decode_vertices(fmt, render_node['vertex_data'])
        idx_data = render_node['index_data']['data']
        faces = [tuple(idx_data[i:i + 3]) for i in range(0, len(idx_data) - len(idx_data) % 3, 3)]
        obj = _build_mesh_obj(name, edm_path, verts, faces, material_def, mat_index, materials_cache)
        obj['edm_node_type'] = 'RenderNode'
        obj['edm_material_id'] = mat_index
        obj['edm_render_index'] = render_index
        obj['edm_vertex_format'] = list(fmt)
        obj['edm_parent_group'] = 0
        obj['edm_group_count'] = 1
        parent_idx = parent_data[0]['parent'] if parent_data else -1
        return [(obj, parent_idx)]

    groups = edmr.split_by_vertex_group(fmt, render_node['vertex_data'],
                                         render_node['index_data'], len(parent_data))
    objects = []
    for g, (entry, (local_verts, local_faces)) in enumerate(zip(parent_data, groups)):
        if not local_verts:
            continue
        sub_name = f"{name}.{g:03d}"
        obj = _build_mesh_obj(sub_name, edm_path, local_verts, local_faces,
                               material_def, mat_index, materials_cache)
        obj['edm_node_type'] = 'RenderNode'
        obj['edm_material_id'] = mat_index
        obj['edm_render_index'] = render_index
        obj['edm_vertex_format'] = list(fmt)
        obj['edm_parent_group'] = g
        obj['edm_group_count'] = len(parent_data)
        objects.append((obj, entry['parent']))
    return objects


def _make_empty(name, node_type, extra=None):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = 'PLAIN_AXES'
    obj.empty_display_size = 0.15
    obj['edm_node_type'] = node_type
    if extra:
        for k, v in extra.items():
            obj[k] = v
    return obj


def _apply_animation(obj, node, typeName):
    """Animation import.

    Key insight (confirmed against real data): the recorded keyframes in
    position_data/rotation_data are DELTAS relative to the node's rest pose
    (tf_position / tf_quat1) — at argument value 0.0 they are (0,0,0) /
    identity — not absolute replacements. So:
        local_position(arg) = tf_position + delta_position(arg)
        local_rotation(arg) = tf_quat1 * delta_rotation(arg)   (quat mult)

    CORRECTED (2nd pass): the node's full local matrix is now
        tf_matrix @ Translation(pos) @ Rotation(rot) @ Scale(tf_scale)
    (see _local_rest_matrix), not Translation(pos) @ Rotation(rot) @
    Scale(tf_scale) alone. Because tf_matrix only pre-multiplies (and
    Translation only ever touches the last column, Rotation/Scale only
    the 3x3 part), position and rotation stay cleanly decoupled through
    that extra multiplication:
      - a position delta changes ONLY the resulting local translation:
            new_translation = tfm_3x3 @ (tf_position + delta_pos) + tfm_translation
        (rotation/scale of the empty are untouched by position curves)
      - a rotation delta changes ONLY the resulting local 3x3 (rotation
        + scale) part:
            m3 = tfm_3x3 @ (tf_quat1 * delta_rot).to_matrix() @ diag(tf_scale)
            new_rotation, new_scale = m3.to_quaternion(), m3.to_scale()
        (translation of the empty is untouched by rotation curves)
    This avoids having to rebuild+redecompose the full 4x4 at every single
    keyframe while still being exact (no shear/approximation error), since
    the two families of curves never touch each other's channels.

    Also, the first element of each keyframe tuple is the DCS *argument
    value* (typically 0.0-1.0 — e.g. how far the stick is pushed), not a
    time-based animation frame. We map argument value -> Blender frame via
    `arg * 100` (so frame 0-100 previews the full 0.0-1.0 range) purely so
    the timeline is scrubbable; it is NOT gameplay-accurate driving logic
    (that would need drivers tied to the actual cockpit argument system).
    """
    if typeName in ('model::ArgAnimationNode', 'model::ArgRotationNode',
                     'model::ArgPositionNode', 'model::ArgScaleNode',
                     'model::ArgAnimatedBone'):
        obj.rotation_mode = 'QUATERNION'
        tfm = _matrix_from_flat_d(node['tf_matrix'])
        tfm3 = tfm.to_3x3()
        tfm_translation = tfm.translation

        base_pos = mathutils.Vector(node.get('tf_position', (0.0, 0.0, 0.0)))
        qx, qy, qz, qw = node.get('tf_quat1', (0.0, 0.0, 0.0, 1.0))
        base_rot = mathutils.Quaternion((qw, qx, qy, qz))
        base_scale = node.get('tf_scale', (1.0, 1.0, 1.0))
        scale_diag = mathutils.Matrix.Diagonal(base_scale).to_3x3()

        for rot in node.get('rotation_data', []):
            obj['edm_arg_rotation'] = rot['argument']
            for arg_value, quat in rot['keys']:
                x, y, z, w = quat
                delta = mathutils.Quaternion((w, x, y, z))
                rot_now = base_rot @ delta
                m3 = tfm3 @ rot_now.to_matrix() @ scale_diag
                obj.rotation_quaternion = m3.to_quaternion()
                obj.scale = m3.to_scale()
                obj.keyframe_insert('rotation_quaternion', frame=arg_value * 100)
                obj.keyframe_insert('scale', frame=arg_value * 100)
            _rename_action_for_arg(obj, rot['argument'])
        for pos in node.get('position_data', []):
            obj['edm_arg_position'] = pos['argument']
            for arg_value, v in pos['keys']:
                pos_now = base_pos + mathutils.Vector(v)
                obj.location = tfm3 @ pos_now + tfm_translation
                obj.keyframe_insert('location', frame=arg_value * 100)
            _rename_action_for_arg(obj, pos['argument'])
    elif typeName == 'model::ArgVisibilityNode':
        for a in node.get('visibility_data', []):
            obj['edm_arg_visibility'] = a['argument']
            for start, end in a['ranges']:
                if start > -1e299:
                    obj.hide_viewport = False
                    obj.keyframe_insert('hide_viewport', frame=start * 100)
                if end < 1e299:
                    obj.hide_viewport = True
                    obj.keyframe_insert('hide_viewport', frame=end * 100)
            _rename_action_for_arg(obj, a['argument'])


def import_edm(context, filepath):
    edm = edmr.EDMFile(filepath)
    materials_cache = {}

    collection = bpy.data.collections.new(os.path.splitext(os.path.basename(filepath))[0])
    context.scene.collection.children.link(collection)
    collection['edm_source_path'] = filepath

    # AXIS CONVERSION: EDM/OSG stores coordinates as X=forward (nose
    # direction), Y=up, Z=right/left (wingspan) — confirmed against a real
    # A-29B file: the Y-axis bounding-box span (~3.96) matches the aircraft's
    # real height (3.97 m), while X (~11.14, asymmetric about 0 — nose vs.
    # tail) and Z (~11.23, symmetric about 0 — left vs. right wing) match its
    # length/wingspan (both ~11.1-11.4 m, too close to tell apart by size
    # alone; the symmetry pattern is what disambiguates them).
    #
    # Blender is Z-up. Importing raw coordinates with no conversion (as
    # every previous version of this addon did) puts the model's real "up"
    # axis (Y) along Blender's forward/depth axis, and its wingspan (Z)
    # along Blender's up axis — the model ends up lying on its side/belly
    # ("deitado de bruço").
    #
    # Fix: wrap the whole import under one root empty applying the basis
    # change (xb, yb, zb) = (zo, xo, yo) — i.e. old Z (wingspan) -> new X
    # (left/right), old X (nose) -> new Y (forward/depth), old Y (up) ->
    # new Z (up). This is a pure axis permutation (determinant +1, no
    # mirroring), so geometry, normals and animation all stay correct —
    # only reparented under this one extra empty. Verified against the
    # real A-29B bbox: after conversion, the up-axis (Z) range becomes
    # -2.14..1.82 (landing gear below origin, canopy above), which is
    # exactly what "up" should look like for a parked aircraft.
    axis_root = bpy.data.objects.new('EDM_AXIS_FIX', None)
    axis_root.empty_display_type = 'ARROWS'
    axis_root.empty_display_size = 0.5
    axis_root.matrix_basis = mathutils.Matrix((
        (0.0, 0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))
    collection.objects.link(axis_root)

    # 1. Transform-node tree -> empties (parented by node_parents afterwards)
    tf_objects = []
    for i, (typeName, node) in enumerate(edm.nodes):
        name = node['name'] or f'{typeName}_{i}'
        obj = _make_empty(name, typeName, {'edm_node_index': i})
        obj.rotation_mode = 'QUATERNION'
        # THE FIX: apply the node's rest-pose local transform to the actual
        # empty, not just internally for baking multi-parent meshes. Without
        # this, every Arg*Node (rotation/position/scale — i.e. every
        # animated part: stick, pedals, levers, gear...) sits at identity
        # (world origin) until an animation keyframe happens to touch it,
        # which is exactly what produced the scattered pieces converging
        # near one point in the screenshot.
        obj.matrix_basis = _local_rest_matrix(typeName, node)
        _apply_animation(obj, node, typeName)
        collection.objects.link(obj)
        tf_objects.append(obj)

    for i, parent_idx in enumerate(edm.node_parents):
        if parent_idx != -1 and parent_idx != 0xffffffff:
            tf_objects[i].parent = tf_objects[parent_idx]

    # 2. Render nodes -> real meshes, parented to their transform node.
    #    Multi-parent RenderNodes are split into one sub-object per group
    #    (see _make_render_node_objects) so each part inherits animation
    #    from its own transform node correctly.
    render_nodes = edm.render_items.get('RENDER_NODES', [])
    mesh_i = 0
    for typeName, node in render_nodes:
        if typeName != 'model::RenderNode':
            mesh_i += 1  # keep index aligned with position in RENDER_NODES list
            continue  # SkinNode handled separately below (TODO: bone weights)
        mat_def = edm.materials[node['material_id']]
        name = node['name'] or f'RenderNode_{mesh_i}'
        for obj, parent_idx in _make_render_node_objects(
                name, filepath, node, mat_def, node['material_id'], mesh_i, materials_cache):
            if 0 <= parent_idx < len(tf_objects):
                obj.parent = tf_objects[parent_idx]
            collection.objects.link(obj)
        mesh_i += 1

    # 3. Connectors -> empties parented to their transform node
    for typeName, node in edm.render_items.get('CONNECTORS', []):
        obj = _make_empty(node['name'], 'Connector')
        obj.empty_display_type = 'ARROWS'
        parent_idx = node['parent']
        if 0 <= parent_idx < len(tf_objects):
            obj.parent = tf_objects[parent_idx]
        collection.objects.link(obj)

    # 4. Lights
    for typeName, node in edm.render_items.get('LIGHT_NODES', []):
        light_data = bpy.data.lights.new(name=node['name'] or 'edm_light', type='POINT')
        obj = bpy.data.objects.new(node['name'] or 'edm_light', light_data)
        obj['edm_node_type'] = 'LightNode'
        parent_idx = node['parent']
        if 0 <= parent_idx < len(tf_objects):
            obj.parent = tf_objects[parent_idx]
        collection.objects.link(obj)

    # Any object that ended up without a parent (root transform nodes,
    # and defensively any connector/light whose parent index was invalid)
    # goes under the axis-fix root, so the conversion applies to the whole
    # model via normal parenting inheritance.
    for obj in list(collection.objects):
        if obj is not axis_root and obj.parent is None:
            obj.parent = axis_root

    collection['edm_source_path'] = filepath
    return collection
