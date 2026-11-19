"""
Pure-python matrix math for the EDM add-on (no bpy / mathutils: unit-testable).

Conventions: 4x4 matrices are row-major lists M[row][col] acting on column
vectors. EDM stores matrices as 16 doubles column-major (from_flat/to_flat).
Quaternions are (x, y, z, w) as in the file.

Why this exists (the "stretched / distorted parts" bug)
-------------------------------------------------------
Many EDM nodes carry a matrix with NON-UNIFORM scale in a ROTATED frame (shear
in Blender terms). A Blender Object stores only loc/rot/scale, so assigning
such a matrix to ONE empty silently drops the shear and every child mesh comes
out stretched. matrix_chain() splits any matrix into a short chain of empties,
each a legal loc*rot*scale, whose product is EXACT.
"""
import math

IDENT = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))
ARG_NODE_TYPES = ('model::ArgAnimationNode', 'model::ArgRotationNode',
                  'model::ArgPositionNode', 'model::ArgScaleNode')


def ident():
    return [list(r) for r in IDENT]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def mat_chain(*ms):
    out = ident()
    for m in ms:
        out = mat_mul(out, m)
    return out


def from_flat(flat):
    return [[flat[4 * c + r] for c in range(4)] for r in range(4)]


def to_flat(m):
    return tuple(m[r][c] for c in range(4) for r in range(4))


def translation(t):
    m = ident()
    m[0][3], m[1][3], m[2][3] = t[0], t[1], t[2]
    return m


def scale(s):
    m = ident()
    m[0][0], m[1][1], m[2][2] = s[0], s[1], s[2]
    return m


def quat_normalize(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def quat_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def quat_is_identity(q, tol=1e-6):
    return abs(q[0]) < tol and abs(q[1]) < tol and abs(q[2]) < tol and abs(abs(q[3]) - 1.0) < tol


def rotation(q):
    x, y, z, w = quat_normalize(q)
    m = ident()
    m[0][:3] = [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)]
    m[1][:3] = [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)]
    m[2][:3] = [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]
    return m


def quat_from_matrix3(m):
    """Rotation matrix (3x3 or 4x4 rows) -> (x,y,z,w), w >= 0."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = (0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = ((m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s)
    q = quat_normalize(q)
    return q if q[3] >= 0 else tuple(-c for c in q)


def scale_with_orientation(q2, s):
    """R(q2) * S * R(q2)^-1 : scale along axes rotated by q2."""
    return mat_chain(rotation(q2), scale(s), rotation(quat_conj(q2)))


def max_abs_diff(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def det3(a):
    return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
            - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
            + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))


def is_identity(m, tol=1e-9):
    return max_abs_diff(m, IDENT) < tol


# ------------------------------------------------------------ node rest pose
def node_pieces(type_name, node):
    """Factors of a node's rest matrix (4x4): rest = tfm * pos * rot * scale.
    TransformNode only has 'tfm'. Arg nodes (formula NOT confirmed by an
    official text, see README):
        rest = tf_matrix * T(tf_position) * R(tf_quat1) * [R(q2) S(tf_scale) R(q2)^-1]
    """
    if type_name == 'model::TransformNode':
        return {'tfm': from_flat(node['transform'])}
    if type_name in ARG_NODE_TYPES:
        q2 = node.get('tf_quat2', (0.0, 0.0, 0.0, 1.0))
        return {'tfm': from_flat(node['tf_matrix']),
                'pos': translation(node.get('tf_position', (0.0, 0.0, 0.0))),
                'rot': rotation(node.get('tf_quat1', (0.0, 0.0, 0.0, 1.0))),
                'scale': scale_with_orientation(q2, node.get('tf_scale', (1.0, 1.0, 1.0)))}
    return {'tfm': ident()}


def rest_matrix(type_name, node):
    p = node_pieces(type_name, node)
    return mat_chain(*[p[k] for k in ('tfm', 'pos', 'rot', 'scale') if k in p])


def legacy_rest_matrix(type_name, node):
    """Add-on 0.3.x composition: tf_matrix * TRS(pos, quat1, scale) (no quat2)."""
    if type_name == 'model::TransformNode':
        return from_flat(node['transform'])
    if type_name in ARG_NODE_TYPES:
        return mat_chain(from_flat(node['tf_matrix']),
                         translation(node.get('tf_position', (0.0, 0.0, 0.0))),
                         rotation(node.get('tf_quat1', (0.0, 0.0, 0.0, 1.0))),
                         scale(node.get('tf_scale', (1.0, 1.0, 1.0))))
    return ident()


# ------------------------------------------------------- exact decomposition
def _cols(m):
    return [[m[r][c] for r in range(3)] for c in range(3)]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def columns_orthogonal(m, tol=1e-6):
    c = _cols(m)
    n = [_norm(x) for x in c]
    if min(n) < 1e-300:
        return True
    return all(abs(_dot(c[i], c[j])) <= tol * n[i] * n[j] for i in range(3) for j in range(i + 1, 3))


def anisotropy(m):
    """(sx, sy, sz, max/min ratio, sheared?, determinant) of the 3x3 part."""
    c = _cols(m)
    n = [_norm(x) for x in c]
    mn = min(n)
    ratio = (max(n) / mn) if mn > 1e-300 else 1e300
    return n[0], n[1], n[2], ratio, (not columns_orthogonal(m, 1e-4)), det3(m)


def _jacobi_eig(a):
    a = [row[:] for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(60):
        p, q, big = 0, 1, 0.0
        for i in range(3):
            for j in range(i + 1, 3):
                if abs(a[i][j]) > big:
                    big, p, q = abs(a[i][j]), i, j
        if big < 1e-300:
            break
        th = 0.5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
        c, s = math.cos(th), math.sin(th)
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    return [a[0][0], a[1][1], a[2][2]], v


def _svd3(a):
    """A = U diag(d) Vt, U and V proper rotations (sign goes to d). None if singular."""
    at_a = [[sum(a[k][i] * a[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    ev, v = _jacobi_eig(at_a)
    order = sorted(range(3), key=lambda i: -ev[i])
    ev = [ev[i] for i in order]
    v = [[v[r][i] for i in order] for r in range(3)]
    sig = [math.sqrt(max(e, 0.0)) for e in ev]
    if sig[2] < 1e-12 * max(sig[0], 1e-300):
        return None
    u = [[sum(a[r][c] * v[c][k] for c in range(3)) / sig[k] for k in range(3)] for r in range(3)]
    d = list(sig)
    if det3(v) < 0:
        for r in range(3):
            v[r][2] = -v[r][2]
        d[2] = -d[2]
    if det3(u) < 0:
        for r in range(3):
            u[r][2] = -u[r][2]
        d[2] = -d[2]
    vt = [[v[c][r] for c in range(3)] for r in range(3)]
    return u, d, vt


def _trs_from_orthogonal(m):
    """4x4 with orthogonal 3x3 columns -> (loc, quat_xyzw, scale)."""
    c = _cols(m)
    s = [_norm(x) for x in c]
    if det3(m) < 0:
        s[0] = -s[0]
    r = [[(c[k][i] / s[k]) if abs(s[k]) > 1e-300 else (1.0 if i == k else 0.0)
          for k in range(3)] for i in range(3)]
    return (m[0][3], m[1][3], m[2][3]), quat_from_matrix3(r), tuple(s)


def trs_to_matrix(trs):
    loc, q, s = trs
    return mat_chain(translation(loc), rotation(q), scale(s))


def matrix_chain(m, tol=1e-6):
    """Split m into a list of (loc, quat_xyzw, scale) whose product (first
    entry outermost) EQUALS m: 1 entry for the normal case (orthogonal
    columns), 2 when an SVD is needed (shear). Singular -> best single entry."""
    if columns_orthogonal(m, tol):
        return [_trs_from_orthogonal(m)]
    res = _svd3([[m[r][c] for c in range(3)] for r in range(3)])
    if res is None:
        return [_trs_from_orthogonal(m)]
    u, d, vt = res
    first, second = ident(), ident()
    for i in range(3):
        for j in range(3):
            first[i][j] = u[i][j] * d[j]
            second[i][j] = vt[i][j]
        first[i][3] = m[i][3]
    chain = [_trs_from_orthogonal(first), _trs_from_orthogonal(second)]
    big = 1 + max(abs(x) for r in m for x in r)
    if max_abs_diff(mat_chain(*[trs_to_matrix(t) for t in chain]), m) > 1e-5 * big:
        return [_trs_from_orthogonal(m)]
    return chain
