"""
Easy animation (Blender side).

Workflow for the user (no EDM knowledge needed):
  1. select the mesh(es) of the moving part
  2. panel EDM > "Criar animação": pick Rotate / Move / Show-Hide, argument number,
     axis and amount  ->  create_animation() makes a PIVOT empty, puts the meshes
     under it and keys the pose at the two arguments.
  3. (optional) pose the pivot at other frames and key them: more keys = more
     steps. Frame 100 = argument 0, frame 200 = argument 1, frame 0 = argument -1.
  4. File > Export > DCS EDM. allocate() turns every pivot into a NEW
     model::ArgAnimationNode / ArgVisibilityNode of the .edm; meshes parented to
     the pivot are written under that node (export_edm does that part).

Pivots are ordinary Empties carrying custom properties:
    edm_new_anim   1                      marks the pivot
    edm_anim_kind  ROTATE | MOVE | VISIBILITY | CUSTOM
    edm_anim_arg   DCS argument number (also read from the Action name '<arg>_<name>')
    edm_anim_name  label
    edm_vis_ranges '0.5:1; >2'   (VISIBILITY only: visible while arg is inside a range)
"""
import math
import re

import bpy
import mathutils

from . import edm_args as A
from . import edm_math as em

FLAG = 'edm_new_anim'
ARG_RE = re.compile(r'^(\d+)(_.+)?$')

# pivot object name -> index of the EDM node that stands for it. Filled by
# allocate() during one export, emptied by clear().
NEW_NODE_INDEX = {}

_MOTION_PATHS = ('location', 'rotation_quaternion', 'rotation_euler', 'rotation_axis_angle')


def clear():
    NEW_NODE_INDEX.clear()


def is_pivot(obj):
    try:
        return obj is not None and obj.type == 'EMPTY' and bool(obj.get(FLAG))
    except Exception:
        return False


# --------------------------------------------------------------------------
# Version independent access to the keys of an Action
# --------------------------------------------------------------------------

def fcurves_of(action):
    """F-curves of an Action in Blender 3.x/4.x (action.fcurves) and 5.x
    (layers > strips > channelbags)."""
    if action is None:
        return []
    try:
        return list(action.fcurves)
    except Exception:
        pass
    out = []
    try:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    out.extend(bag.fcurves)
    except Exception:
        pass
    return out


def keyed_frames(obj):
    ad = getattr(obj, 'animation_data', None)
    action = getattr(ad, 'action', None) if ad else None
    frames = set()
    for fc in fcurves_of(action):
        if fc.data_path in _MOTION_PATHS:
            for kp in fc.keyframe_points:
                frames.add(round(float(kp.co[0]), 4))
    return sorted(frames)


def pivot_arg(obj):
    """DCS argument of a pivot (property first, then the Action name), or None."""
    v = obj.get('edm_anim_arg')
    if v is not None and v != '':
        try:
            return int(v)
        except Exception:
            pass
    ad = getattr(obj, 'animation_data', None)
    action = getattr(ad, 'action', None) if ad else None
    if action is not None:
        m = ARG_RE.match(action.name)
        if m:
            return int(m.group(1))
    return None


# --------------------------------------------------------------------------
# Matrices
# --------------------------------------------------------------------------

def _rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def _relative_rows(obj, anc, root_empty):
    """Pose of `obj` in the local frame of its EDM parent `anc` (rows).
    Built from the objects' own parent-inverse @ basis, like export_edm does,
    so it does not depend on the pose of `anc` itself."""
    if anc is None:
        if root_empty is None:
            return _rows(obj.matrix_world)
        return _rows(root_empty.matrix_world.inverted_safe() @ obj.matrix_world)
    chain, p = [], obj
    while p is not None and p is not anc:
        chain.append(p)
        p = p.parent
    if p is None:
        return _rows(anc.matrix_world.inverted_safe() @ obj.matrix_world)
    m = mathutils.Matrix.Identity(4)
    for o in reversed(chain):
        m = m @ (o.matrix_parent_inverse @ o.matrix_basis)
    return _rows(m)


def _find_parent_node(obj, inside):
    """(empty, edm node index) of the nearest ancestor that is an EDM node of
    this export (imported node empty inside the model, or an already allocated
    pivot). (None, None) when there is none."""
    p = obj.parent
    while p is not None:
        if p.name in NEW_NODE_INDEX:
            return p, NEW_NODE_INDEX[p.name]
        if p.type == 'EMPTY' and 'edm_node_index' in p and p.name in inside:
            return p, int(p['edm_node_index'])
        p = p.parent
    return None, None


def _depth(o):
    d, p = 0, o.parent
    while p is not None:
        d += 1
        p = p.parent
    return d


# --------------------------------------------------------------------------
# Export: pivots -> EDM nodes
# --------------------------------------------------------------------------

def find_pivots(context, collection):
    seen = {}
    for o in list(collection.all_objects) + list(context.scene.objects):
        if is_pivot(o) and not o.get('edm_skip'):
            seen[o.name] = o
    return sorted(seen.values(), key=lambda o: (_depth(o), o.name))


def allocate(context, edm, collection, root_index, root_empty):
    """Append one EDM animation node per pivot of the scene. Returns
    {'created': [...], 'warnings': [...]}. Sets NEW_NODE_INDEX."""
    clear()
    out = {'created': [], 'warnings': []}
    pivots = find_pivots(context, collection)
    if not pivots:
        return out
    scene = context.scene
    inside = {o.name for o in collection.all_objects}
    old_frame, old_sub = scene.frame_current, getattr(scene, 'frame_subframe', 0.0)
    try:
        for piv in pivots:
            try:
                _allocate_one(scene, edm, piv, inside, root_index, root_empty, out)
            except Exception as e:             # one bad pivot must not kill the export
                out['warnings'].append("pivot '%s': %s" % (piv.name, e))
    finally:
        scene.frame_set(old_frame, subframe=old_sub)
    return out


def _allocate_one(scene, edm, piv, inside, root_index, root_empty, out):
    anc, parent_idx = _find_parent_node(piv, inside)
    if parent_idx is None:
        if root_index is None:
            raise RuntimeError('the model has no root node to attach to')
        parent_idx = root_index
        anc = None
        out['warnings'].append("pivot '%s' is not under any node of the model: attached to the root "
                               "(it will move, but not with the part it belongs to)" % piv.name)
    kind = str(piv.get('edm_anim_kind') or 'CUSTOM').upper()
    name = piv.name
    arg = pivot_arg(piv)
    if arg is None or arg < 0:
        raise RuntimeError("no valid argument. Set custom property 'edm_anim_arg' (e.g. 8) or name "
                           "the Action like '8_lever'")

    if kind == 'VISIBILITY':
        try:
            ranges = A.parse_ranges(str(piv.get('edm_vis_ranges') or ''))
        except ValueError as e:
            raise RuntimeError("'edm_vis_ranges': %s" % e)
        spec = {'name': name, 'kind': A.VISIBILITY, 'arg': arg, 'ranges': ranges,
                'rest': _relative_rows(piv, anc, root_empty)}
        tip, warns = A.add_animation(edm, spec, parent_idx)
        text = 'show/hide, visible %s' % A.ranges_to_text(ranges)
    else:
        frames = keyed_frames(piv)
        if not frames:
            raise RuntimeError('no keyframes (key Location and/or Rotation at two or more frames)')
        poses = []
        for f in frames:
            fi = int(math.floor(f))
            scene.frame_set(fi, subframe=f - fi)
            poses.append((A.frame_to_arg(f), _relative_rows(piv, anc, root_empty)))
        k = A.keys_from_poses(poses)
        out['warnings'].extend("pivot '%s': %s" % (name, w) for w in k['warnings'])
        spec = {'name': name, 'kind': A.ANIM, 'arg': arg, 'rest': k['rest'],
                'pos_keys': k['pos_keys'], 'rot_keys': k['rot_keys']}
        tip, warns = A.add_animation(edm, spec, parent_idx)
        text = '%s, %d key(s)' % ('+'.join(x for x, ks in (('move', k['pos_keys']),
                                                         ('rotate', k['rot_keys'])) if ks) or 'static',
                                  len(frames))
    out['warnings'].extend(warns)
    if tip is not None:
        NEW_NODE_INDEX[piv.name] = tip
        parent_name = edm.nodes[parent_idx][1].get('name') or '#%d' % parent_idx
        out['created'].append("'%s' arg %d: %s (under node '%s')" % (name, arg, text, parent_name))


# --------------------------------------------------------------------------
# Creating pivots (used by the operators in edm_ui)
# --------------------------------------------------------------------------

AXES = {'X': (1.0, 0.0, 0.0), 'Y': (0.0, 1.0, 0.0), 'Z': (0.0, 0.0, 1.0)}


def _nearest_node_empty(obj):
    p = obj.parent
    while p is not None:
        if p.type == 'EMPTY' and ('edm_node_index' in p or is_pivot(p)):
            return p
        p = p.parent
    return None


def _center_world(objs):
    pts = []
    for o in objs:
        try:
            for c in o.bound_box:
                pts.append(o.matrix_world @ mathutils.Vector(c))
        except Exception:
            pts.append(o.matrix_world.translation.copy())
    lo = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2


def _key_linear(obj, data_path, frame):
    prefs = bpy.context.preferences.edit
    old = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = 'LINEAR'
    try:
        obj.keyframe_insert(data_path, frame=frame)
    finally:
        prefs.keyframe_new_interpolation_type = old


def create_animation(context, objs, kind, name, arg, axis='X', amount=45.0,
                     arg_from=0.0, arg_to=1.0, pivot_at='ACTIVE', orient='WORLD',
                     ranges='>0.5', collection=None):
    """Make a pivot for `objs` and key it. kind: ROTATE | MOVE | VISIBILITY.
    amount: degrees (ROTATE) or scene units (MOVE). Returns the pivot Empty."""
    if not objs:
        raise RuntimeError('select the mesh(es) of the moving part first')
    if arg < 0:
        raise RuntimeError('the argument must be 0 or higher')
    scene = context.scene
    active = context.active_object if context.active_object in objs else objs[0]
    if pivot_at == 'CURSOR':
        loc = scene.cursor.location.copy()
    elif pivot_at == 'CENTER':
        loc = _center_world(objs)
    else:
        loc = active.matrix_world.translation.copy()
    rot = (active.matrix_world.to_quaternion() if orient == 'ACTIVE'
           else mathutils.Quaternion((1.0, 0.0, 0.0, 0.0)))
    world = mathutils.Matrix.Translation(loc) @ rot.to_matrix().to_4x4()

    parent = _nearest_node_empty(active)
    pivot = bpy.data.objects.new(name, None)
    pivot.empty_display_type = 'ARROWS'
    pivot.empty_display_size = 0.12
    pivot.rotation_mode = 'QUATERNION'
    coll = collection or (active.users_collection[0] if active.users_collection else scene.collection)
    coll.objects.link(pivot)
    if parent is not None:
        pivot.parent = parent
        pivot.matrix_parent_inverse = parent.matrix_world.inverted_safe()
    pivot.matrix_world = world
    pivot[FLAG] = 1
    pivot['edm_anim_kind'] = kind
    pivot['edm_anim_arg'] = int(arg)
    pivot['edm_anim_name'] = name

    # meshes -> children of the pivot, staying exactly where they are
    for o in objs:
        mw = o.matrix_world.copy()
        o.parent = pivot
        o.matrix_parent_inverse = pivot.matrix_world.inverted_safe()
        o.matrix_world = mw

    if kind == 'VISIBILITY':
        pivot['edm_vis_ranges'] = ranges
        scene.frame_set(int(A.arg_to_frame(0.0)))
        return pivot

    f0, f1 = A.arg_to_frame(arg_from), A.arg_to_frame(arg_to)
    ad = pivot.animation_data_create()
    ad.action = bpy.data.actions.new('%d_%s' % (int(arg), name))
    ax = mathutils.Vector(AXES.get(axis, AXES['X']))
    q0 = pivot.rotation_quaternion.copy()
    l0 = pivot.location.copy()
    if kind == 'ROTATE':
        pivot.rotation_quaternion = q0
        _key_linear(pivot, 'rotation_quaternion', f0)
        pivot.rotation_quaternion = q0 @ mathutils.Quaternion(ax, math.radians(amount))
        _key_linear(pivot, 'rotation_quaternion', f1)
        pivot.rotation_quaternion = q0
    else:                                   # MOVE along the pivot's own axis
        pivot.location = l0
        _key_linear(pivot, 'location', f0)
        pivot.location = l0 + (q0.to_matrix() @ ax) * amount
        _key_linear(pivot, 'location', f1)
        pivot.location = l0
    scene.frame_set(int(f0))
    return pivot


def remove_animation(context, pivot):
    """Delete the pivot, keeping its meshes where they are (parent = the
    pivot's own parent). Returns the number of children released."""
    n = 0
    for ch in list(pivot.children):
        mw = ch.matrix_world.copy()
        ch.parent = pivot.parent
        if pivot.parent is not None:
            ch.matrix_parent_inverse = pivot.parent.matrix_world.inverted_safe()
        ch.matrix_world = mw
        n += 1
    ad = pivot.animation_data
    action = ad.action if ad else None
    bpy.data.objects.remove(pivot, do_unlink=True)
    if action is not None and action.users == 0:
        bpy.data.actions.remove(action)
    return n


def preview_arg(context, arg):
    """Move the timeline to argument `arg` (frame 100 = arg 0)."""
    frame = A.arg_to_frame(arg)
    fi = int(math.floor(frame))
    context.scene.frame_set(fi, subframe=frame - fi)
