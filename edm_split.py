"""
Split merged meshes into loose parts (the exporter puts them back).

DCS cockpits bake dozens of pieces (ejection seat, consoles, rails, screws...)
into ONE vertex buffer per (render node, group), so moving only the seat is
impossible. Islands are found on vertices WELDED BY POSITION, so UV seams /
hard edges (which duplicate vertices in EDM) don't over-split a piece.

Every part keeps the EDM slot properties (edm_render_index, edm_parent_group,
...) plus edm_split_part=1; export recombines all parts of a slot.
"""
import re


def island_labels(positions, faces, digits=5):
    """One island label per face (0 = largest island)."""
    weld, vid = {}, []
    for p in positions:
        vid.append(weld.setdefault((round(p[0], digits), round(p[1], digits), round(p[2], digits)), len(weld)))
    parent = list(range(len(weld)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for f in faces:
        base = find(vid[f[0]])
        for i in f[1:]:
            r = find(vid[i])
            if r != base:
                parent[r] = base
    roots = [find(vid[f[0]]) for f in faces]
    counts = {}
    for r in roots:
        counts[r] = counts.get(r, 0) + 1
    order = {r: i for i, (r, _) in enumerate(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))}
    return [order[r] for r in roots]


def split_geometry(verts, faces, min_faces=1):
    """verts: decode_vertices dicts; faces: tuples. -> [(verts_i, faces_i)] largest first."""
    if not faces:
        return []
    labels = island_labels([v['position'][:3] for v in verts], faces)
    buckets = {}
    for f, lab in zip(faces, labels):
        buckets.setdefault(lab, []).append(f)
    out = []
    for lab in sorted(buckets):
        fl = buckets[lab]
        if len(fl) < min_faces:
            continue
        remap, vl, nf = {}, [], []
        for f in fl:
            t = []
            for i in f:
                if i not in remap:
                    remap[i] = len(vl)
                    vl.append(verts[i])
                t.append(remap[i])
            nf.append(tuple(t))
        out.append((vl, nf))
    return out


def part_name(base, index):
    return f"{re.sub(r'_part\d{3}$', '', base)}_part{index:03d}"


def describe_part(positions):
    if not positions:
        return ''
    lo = [min(p[k] for p in positions) for k in range(3)]
    hi = [max(p[k] for p in positions) for k in range(3)]
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    d = [hi[k] - lo[k] for k in range(3)]
    return "centro (%.3f, %.3f, %.3f) tamanho (%.3f, %.3f, %.3f)" % (*c, *d)


def split_object(obj, min_faces=1):
    """Replace mesh object `obj` by one object per loose part (Blender)."""
    import bpy
    mesh = obj.data
    if not mesh.polygons:
        return [obj]
    faces = [tuple(p.vertices) for p in mesh.polygons]
    positions = [tuple(v.co) for v in mesh.vertices]
    labels = island_labels(positions, faces)
    if max(labels) + 1 <= 1:
        return [obj]

    uv_layers = [(l.name, [tuple(d.uv) for d in l.data]) for l in mesh.uv_layers]
    try:
        normals = [tuple(n.vector) for n in mesh.corner_normals]      # Blender 4.1+
    except Exception:
        try:
            mesh.calc_normals_split()
        except Exception:
            pass
        normals = [tuple(l.normal) for l in mesh.loops]
    mat_idx = [p.material_index for p in mesh.polygons]
    by_label = {}
    for pi, lab in enumerate(labels):
        by_label.setdefault(lab, []).append(pi)

    new_objs = []
    parent, mpi, mbasis = obj.parent, obj.matrix_parent_inverse.copy(), obj.matrix_basis.copy()
    colls = list(obj.users_collection)
    props = {k: obj[k] for k in obj.keys()}
    idx = 0
    for lab in sorted(by_label):
        plist = by_label[lab]
        if len(plist) < min_faces:
            continue
        remap, vco, pfaces, loop_src = {}, [], [], []
        for pi in plist:
            poly = mesh.polygons[pi]
            nv = []
            for vi in poly.vertices:
                if vi not in remap:
                    remap[vi] = len(vco)
                    vco.append(positions[vi])
                nv.append(remap[vi])
            pfaces.append(tuple(nv))
            loop_src.extend(range(poly.loop_start, poly.loop_start + poly.loop_total))
        name = part_name(obj.name, idx)
        m = bpy.data.meshes.new(name)
        m.from_pydata(vco, [], pfaces)
        for mat in mesh.materials:
            m.materials.append(mat)
        for pi_new, pi_old in enumerate(plist):
            m.polygons[pi_new].material_index = mat_idx[pi_old]
        for uname, uvs in uv_layers:
            layer = m.uv_layers.new(name=uname)
            for li_new, li_old in enumerate(loop_src):
                layer.data[li_new].uv = uvs[li_old]
        try:
            m.normals_split_custom_set([normals[i] for i in loop_src])
        except Exception:
            pass
        m.update()
        o = bpy.data.objects.new(name, m)
        for k, v in props.items():
            o[k] = v
        o['edm_split_part'] = 1
        o['edm_part_info'] = describe_part(vco)
        o.parent = parent
        o.matrix_parent_inverse = mpi
        o.matrix_basis = mbasis
        for c in colls:
            c.objects.link(o)
        new_objs.append(o)
        idx += 1
    old_mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    return new_objs
