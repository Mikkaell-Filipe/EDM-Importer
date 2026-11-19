"""
Pure-python helpers (no bpy) that build NEW EDM animation nodes.

Used by new_anim.py ("easy animation"): the user animates a pivot Empty in
Blender and, at export, the pivot becomes a model::ArgAnimationNode (or an
ArgVisibilityNode) appended to the node tree of the source .edm.

Math (same composition the rest of the add-on uses, see edm_math / edm_nodeplan):

    node(arg) = tf_matrix * T(tf_position + dpos(arg)) * R(tf_quat1 * dq(arg)) * S

For a new node tf_position = 0, tf_quat1 = identity, S = 1, and tf_matrix is the
pivot's pose at the reference argument. The keys are therefore pure deltas:

    M(arg) = M0 * T(d) * R(dq)     ->    T(d) R(dq) = M0^-1 * M(arg)

Frames: OFFICIAL convention (arg = frame / 100 - 1, frame 100 = arg 0).
"""
from . import edm_math as em

DBL_MAX = 1.7976931348623157e308
IDENTITY_Q = (0.0, 0.0, 0.0, 1.0)

ANIM = 'ANIM'
VISIBILITY = 'VISIBILITY'

_EPS_POS = 1e-7
_EPS_ROT = 1e-7


def q_norm(q):
    return em.quat_normalize(tuple(q))


def flat_from_matrix(m):
    return em.to_flat(m)


def flat_from_loc_quat(loc, quat):
    return em.to_flat(em.trs_to_matrix((tuple(loc), q_norm(quat), (1.0, 1.0, 1.0))))


def frame_to_arg(frame):
    return frame / 100.0 - 1.0


def arg_to_frame(arg):
    return (arg + 1.0) * 100.0


# --------------------------------------------------------------------------
# Rigid-pose math
# --------------------------------------------------------------------------

def invert_rigid(m):
    """Inverse of a 4x4 (rows) whose 3x3 part is a rotation times UNIFORM or
    per-axis scale is NOT assumed: general 3x3 inverse via adjugate."""
    a = [[m[r][c] for c in range(3)] for r in range(3)]
    det = em.det3(a)
    if abs(det) < 1e-300:
        raise ValueError('singular matrix')
    inv = [[0.0] * 3 for _ in range(3)]
    inv[0][0] = (a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det
    inv[0][1] = (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det
    inv[0][2] = (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det
    inv[1][0] = (a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det
    inv[1][1] = (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det
    inv[1][2] = (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det
    inv[2][0] = (a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det
    inv[2][1] = (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det
    inv[2][2] = (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det
    out = em.ident()
    for r in range(3):
        for c in range(3):
            out[r][c] = inv[r][c]
        out[r][3] = -sum(inv[r][c] * m[c][3] for c in range(3))
    return out


def continuous(items):
    """items [(arg, quat)] -> same, with the sign chosen so consecutive
    quaternions stay in one hemisphere (no flip between keys)."""
    out, prev = [], None
    for a, q in items:
        if prev is not None and sum(x * y for x, y in zip(prev, q)) < 0:
            q = tuple(-c for c in q)
        out.append((a, q))
        prev = q
    return out


def keys_from_poses(poses, ref_arg=None):
    """poses: [(arg, 4x4 rows)] = pivot pose (relative to its EDM parent node)
    at every keyed argument.

    Returns dict:
      rest      4x4 rows  pose at the reference argument (-> tf_matrix)
      ref_arg   the reference argument used
      pos_keys  [(arg, (dx,dy,dz))]  or [] if the part never moves
      rot_keys  [(arg, (x,y,z,w))]   or [] if it never rotates
      warnings  [str]
    ref_arg defaults to the key closest to argument 0 (so a lever keyed at
    arg 0 -> 1 has identity deltas at arg 0).
    """
    poses = sorted(((float(a), m) for a, m in poses), key=lambda p: p[0])
    warnings = []
    if not poses:
        return {'rest': em.ident(), 'ref_arg': 0.0, 'pos_keys': [], 'rot_keys': [],
                'warnings': ['no keys']}
    if ref_arg is None:
        ref_arg = min((a for a, _ in poses), key=lambda a: abs(a))
    ref = min(poses, key=lambda p: abs(p[0] - ref_arg))
    rest = ref[1]
    inv0 = invert_rigid(rest)
    pos_keys, rot_keys = [], []
    for a, m in poses:
        delta = em.mat_chain(inv0, m)
        pos_keys.append((a, (delta[0][3], delta[1][3], delta[2][3])))
        rot_keys.append((a, q_norm(em.quat_from_matrix3(delta))))
        sx, sy, sz, _ratio, sheared, det = em.anisotropy(delta)
        if sheared or det < 0 or max(abs(sx - 1), abs(sy - 1), abs(sz - 1)) > 1e-3:
            warnings.append('pivot has scale/shear at arg %.3f: only move/rotate is exported' % a)
            break
    rot_keys = continuous(rot_keys)
    if all(max(abs(c) for c in v) <= _EPS_POS for _, v in pos_keys):
        pos_keys = []
    if all(em.quat_is_identity(q, _EPS_ROT) for _, q in rot_keys):
        rot_keys = []
    return {'rest': rest, 'ref_arg': ref[0], 'pos_keys': pos_keys, 'rot_keys': rot_keys,
            'warnings': warnings}


# --------------------------------------------------------------------------
# Node construction
# --------------------------------------------------------------------------

def _base(edm, type_name, name):
    """{'name','version','props'} copied from an existing node of this type
    (version number / empty props), so the game sees a familiar node."""
    version = 0
    for t, n in edm.nodes:
        if t == type_name:
            version = n.get('version', 0)
            break
    return {'name': name, 'version': version, 'props': []}


def append_node(edm, type_name, node, parent_index):
    edm.nodes.append((type_name, node))
    edm.node_parents.append(parent_index if parent_index is not None else -1)
    return len(edm.nodes) - 1


def add_static_transform(edm, name, flat_matrix, parent_index):
    """Append a plain model::TransformNode (fixed placement under its parent)."""
    node = _base(edm, 'model::TransformNode', name)
    node['transform'] = tuple(flat_matrix)
    return append_node(edm, 'model::TransformNode', node, parent_index)


def new_anim_node(edm, name, rest, arg, pos_keys, rot_keys):
    node = _base(edm, 'model::ArgAnimationNode', name)
    node['tf_matrix'] = em.to_flat(rest)
    node['tf_position'] = (0.0, 0.0, 0.0)
    node['tf_quat1'] = IDENTITY_Q
    node['tf_quat2'] = IDENTITY_Q
    node['tf_scale'] = (1.0, 1.0, 1.0)
    node['position_data'] = ([{'argument': int(arg),
                               'keys': [(float(a), tuple(v)) for a, v in pos_keys]}]
                             if pos_keys else [])
    node['rotation_data'] = ([{'argument': int(arg),
                               'keys': [(float(a), tuple(q)) for a, q in rot_keys]}]
                             if rot_keys else [])
    node['scale_data'] = []
    return node


def new_visibility_node(edm, name, arg, ranges):
    node = _base(edm, 'model::ArgVisibilityNode', name)
    node['visibility_data'] = [{'argument': int(arg),
                                'ranges': [(float(s), float(e)) for s, e in ranges]}]
    return node


def parse_ranges(text):
    """'0.5:1; -1:-0.2' / '>0.5' / '<0.3' -> [(start, end)].
    Empty text = always visible. Raises ValueError on garbage."""
    text = (text or '').strip()
    if not text:
        return [(-DBL_MAX, DBL_MAX)]
    out = []
    for part in text.replace(',', ';').split(';'):
        p = part.strip()
        if not p:
            continue
        if p.startswith('>'):
            out.append((float(p[1:]), DBL_MAX))
        elif p.startswith('<'):
            out.append((-DBL_MAX, float(p[1:])))
        else:
            if ':' not in p:
                raise ValueError("bad range %r (use 'start:end', e.g. 0.5:1)" % p)
            a, b = p.split(':', 1)
            out.append((float(a), float(b)))
    if not out:
        return [(-DBL_MAX, DBL_MAX)]
    return out


def ranges_to_text(ranges):
    parts = []
    for s, e in ranges:
        if s <= -1e299 and e >= 1e299:
            parts.append('always')
        elif s <= -1e299:
            parts.append('<%g' % e)
        elif e >= 1e299:
            parts.append('>%g' % s)
        else:
            parts.append('%g:%g' % (s, e))
    return '; '.join(parts)


def add_animation(edm, spec, parent_index):
    """Append the node(s) for one pivot.

    spec = {'name', 'kind': ANIM|VISIBILITY, 'arg': int,
            'rest': 4x4 rows (pose under the parent),
            ANIM:       'pos_keys', 'rot_keys'   (from keys_from_poses)
            VISIBILITY: 'ranges'}
    Returns (tip_index or None, warnings). None = nothing was created (a pivot
    that never moves needs no node: its meshes are baked into the parent).
    """
    warnings = []
    name = spec['name']
    arg = int(spec['arg'])
    if arg < 0:
        raise ValueError("argument must be >= 0 (got %d)" % arg)
    kind = spec.get('kind', ANIM)
    rest = spec.get('rest') or em.ident()
    if kind == VISIBILITY:
        ranges = spec.get('ranges') or [(-DBL_MAX, DBL_MAX)]
        cur = parent_index
        if not em.is_identity(rest, 1e-6):
            # a visibility node has no transform of its own: keep the pivot's
            # placement with a fixed TransformNode in front of it
            cur = add_static_transform(edm, name + '_pos', em.to_flat(rest), cur)
        node = new_visibility_node(edm, name, arg, ranges)
        return append_node(edm, 'model::ArgVisibilityNode', node, cur), warnings
    pos_keys = spec.get('pos_keys') or []
    rot_keys = spec.get('rot_keys') or []
    if not pos_keys and not rot_keys:
        return None, ["'%s': no motion between the keys, no animation written" % name]
    for keys in (pos_keys, rot_keys):
        if keys and len(keys) < 2:
            warnings.append("'%s': only one key; add a second key at another frame" % name)
    node = new_anim_node(edm, name, rest, arg, pos_keys, rot_keys)
    return append_node(edm, 'model::ArgAnimationNode', node, parent_index), warnings
