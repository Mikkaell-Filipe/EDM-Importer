"""
Pure-python plan of the Blender empty chain that represents ONE EDM node.

An EDM arg node is  rest = tfm * T(pos) * R(q1) * Sc  with animation on the
pos / rot parts. Every Blender link must be a plain loc*rot*scale and each DCS
argument needs its own Action ('<arg>_<name>', what the official exporter
reads), so one node becomes a short chain:

    [static link(s)] -> [pos link per arg] -> [rot link per arg] -> [static scale link(s)]

Adjacent static links are multiplied first, so a plain node is 1 empty and a
typical animated lever is 3. The FIRST link carries the node's base
translation (pivot, written back on export); the LAST ("tip") parents the
node's children and meshes.
"""
from . import edm_math as em

IDENTITY_Q = (0.0, 0.0, 0.0, 1.0)


def arg_to_frame(arg, convention='OFFICIAL'):
    """OFFICIAL: arg -1..1 = frames 0..200 (frame 100 = arg 0), as in the
    official exporter (arg = frame/100 - 1). LEGACY: frame = arg*100."""
    return (arg + 1.0) * 100.0 if convention == 'OFFICIAL' else arg * 100.0


def _continuous_quats(items):
    out, prev = [], None
    for f, q in items:
        if prev is not None and sum(a * b for a, b in zip(prev, q)) < 0:
            q = tuple(-c for c in q)
        out.append((f, q))
        prev = q
    return out


def _static_chain(m):
    return [{'kind': 'static', 'trs': t, 'matrix': em.trs_to_matrix(t)} for t in em.matrix_chain(m)]


def plan_node(type_name, node, mode='FULL', frames='OFFICIAL'):
    """List of links, outermost first:
      {'kind':'static','trs':(loc,quat,scale),'matrix':4x4}
      {'kind':'pos','arg':n,'keys':[(frame,(x,y,z))]}
      {'kind':'rot','arg':n,'keys':[(frame,(x,y,z,w))]}"""
    if mode == 'LEGACY_MATRIX':
        m = em.legacy_rest_matrix(type_name, node)
        return [{'kind': 'static', 'trs': em._trs_from_orthogonal(m), 'matrix': m}]

    animated = (mode != 'NO_ANIM') and type_name in em.ARG_NODE_TYPES
    if not animated or not (node.get('position_data') or node.get('rotation_data')):
        return _static_chain(em.rest_matrix(type_name, node))

    pieces = em.node_pieces(type_name, node)
    raw = [('S', pieces['tfm'])]
    pos0 = node.get('tf_position', (0.0, 0.0, 0.0))
    pdata = node.get('position_data') or []
    if pdata:
        for k, entry in enumerate(pdata):
            base = pos0 if k == 0 else (0.0, 0.0, 0.0)
            keys = [(arg_to_frame(a, frames), tuple(base[i] + v[i] for i in range(3)))
                    for a, v in entry['keys']]
            raw.append(('P', entry['argument'], keys))
    else:
        raw.append(('S', pieces['pos']))
    q1 = node.get('tf_quat1', IDENTITY_Q)
    rdata = node.get('rotation_data') or []
    if rdata:
        for k, entry in enumerate(rdata):
            base = q1 if k == 0 else IDENTITY_Q
            keys = [(arg_to_frame(a, frames), em.quat_normalize(em.quat_mul(base, em.quat_normalize(d))))
                    for a, d in entry['keys']]
            raw.append(('R', entry['argument'], _continuous_quats(keys)))
    else:
        raw.append(('S', pieces['rot']))
    raw.append(('S', pieces['scale']))

    links, pending = [], None
    for item in raw:
        if item[0] == 'S':
            pending = item[1] if pending is None else em.mat_mul(pending, item[1])
        else:
            if pending is not None:
                links.extend(_static_chain(pending))
                pending = None
            links.append({'kind': 'pos' if item[0] == 'P' else 'rot', 'arg': item[1], 'keys': item[2]})
    if pending is not None and not em.is_identity(pending, 1e-12):
        links.extend(_static_chain(pending))
    return links


def first_link_translation(type_name, node):
    """Translation the node's FIRST empty gets (parent-local): translation of
    tfm (when position keys exist, tf_position lives in a later link) or of
    tfm*T(tf_position). Exporter compares it with the moved empty."""
    if type_name == 'model::TransformNode':
        m = em.from_flat(node['transform'])
    elif type_name in em.ARG_NODE_TYPES:
        m = em.from_flat(node['tf_matrix'])
        if not node.get('position_data'):
            m = em.mat_mul(m, em.translation(node.get('tf_position', (0.0, 0.0, 0.0))))
    else:
        return None
    return (m[0][3], m[1][3], m[2][3])


def evaluate_at(links, arg=0.0, frames='OFFICIAL'):
    """Product of all links with animated ones sampled linearly at `arg`."""
    f = arg_to_frame(arg, frames)
    ms = []
    for l in links:
        if l['kind'] == 'static':
            ms.append(l['matrix'])
            continue
        keys = sorted(l['keys'], key=lambda k: k[0])
        if not keys:
            continue
        if f <= keys[0][0]:
            v = keys[0][1]
        elif f >= keys[-1][0]:
            v = keys[-1][1]
        else:
            for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
                if f0 <= f <= f1:
                    t = (f - f0) / (f1 - f0) if f1 != f0 else 0.0
                    v = tuple(a + (b - a) * t for a, b in zip(v0, v1))
                    break
        ms.append(em.translation(v) if l['kind'] == 'pos' else em.rotation(v))
    return em.mat_chain(*ms) if ms else em.ident()
