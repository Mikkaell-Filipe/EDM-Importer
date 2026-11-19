"""
EDM (DCS World) binary model format reader — v10.
Based on the Blender_ioEDM spec (ndevenish, 2016) + corrections found by
inspecting a modern (__VERSION__=3) A-29B file, Aug 2026.

Known corrections vs. the original spec:
- RootNode: no leading `unknownA` uchar before the bounding box in
  __VERSION__=3 files (was present, or mis-documented, for __VERSION__=2).
- Quaternions are stored as 4 doubles (osg::Quaterniond), not 4 floats as
  the 2016 spec documents.
- model::Property<const char*> exists (used e.g. by connectors).
"""
import struct


class EDMParseError(Exception):
    pass


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def bytes(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def u8(self):
        v = self.d[self.p]; self.p += 1; return v

    def i32(self):
        v = struct.unpack_from('<i', self.d, self.p)[0]; self.p += 4; return v

    def u32(self):
        v = struct.unpack_from('<I', self.d, self.p)[0]; self.p += 4; return v

    def f32(self):
        v = struct.unpack_from('<f', self.d, self.p)[0]; self.p += 4; return v

    def f64(self):
        v = struct.unpack_from('<d', self.d, self.p)[0]; self.p += 8; return v

    def raw_vec3f(self):
        v = struct.unpack_from('<3f', self.d, self.p); self.p += 12; return v

    def raw_vec3d(self):
        v = struct.unpack_from('<3d', self.d, self.p); self.p += 24; return v

    def raw_vec4d(self):
        v = struct.unpack_from('<4d', self.d, self.p); self.p += 32; return v

    def raw_vec4f(self):
        v = struct.unpack_from('<4f', self.d, self.p); self.p += 16; return v

    def raw_quat(self):
        v = struct.unpack_from('<4d', self.d, self.p); self.p += 32; return v

    def raw_matrixd(self):
        v = struct.unpack_from('<16d', self.d, self.p); self.p += 128; return v

    def uint_string(self):
        n = self.u32()
        s = self.bytes(n)
        return s.decode('windows-1251', errors='replace')


class EDMFile:
    def __init__(self, path):
        with open(path, 'rb') as f:
            self.data = f.read()
        self.r = Reader(self.data)
        self.lookup = []
        self.materials = []
        self.nodes = []          # list of (typeName, nodeDict)
        self.node_parents = []
        self.render_items = {}
        self._ctx = {}
        self._trail = []
        try:
            self._parse()
        except (struct.error, IndexError, NotImplementedError) as e:
            raise EDMParseError(self._where(str(e))) from e

    def _note(self, **kw):
        """Registra o item que vai ser lido (contexto + trilha dos ultimos itens)."""
        self._ctx.update(kw)
        self._ctx['node'] = None
        self._ctx['at'] = self.r.p
        self._trail.append((kw.get('section'), kw.get('key'), kw.get('type'),
                            kw.get('item'), self.r.p))
        del self._trail[:-12]

    @staticmethod
    def _clean(text, limit=30):
        if text is None:
            return None
        t = ''.join(ch if ch.isprintable() and ord(ch) < 127 else '?' for ch in str(text))
        return t[:limit]

    def _where(self, msg):
        """Mensagem CURTA com o contexto primeiro (a barra do Blender trunca o fim)."""
        c = self._ctx
        short = str(msg)[:80]
        full = (f"[{c.get('section')}/{c.get('key')}/{c.get('type')} #{c.get('item')} "
                f"start@{c.get('at')} now@{self.r.p}/{len(self.data)}] "
                f"node={self._clean(c.get('node'))!r} :: {short}")
        try:
            print("[EDM import] FALHOU:", full)
            print("[EDM import] ultimos itens lidos (secao, chave, tipo, item, offset):")
            for t in self._trail:
                print("   ", t)
        except Exception:
            pass
        return full

    # -- lookup helpers --------------------------------------------------
    def lstr(self):
        i = self.r.u32()
        if i >= len(self.lookup):
            raise IndexError(f"lookup idx={i} (0x{i:X}) >= len(lookup)={len(self.lookup)}; primeiras={self.lookup[:5]}")
        return self.lookup[i]

    def read_map(self):
        n = self.r.u32()
        return {self.lstr(): self.r.u32() for _ in range(n)}

    # -- top level ---------------------------------------------------------
    def _parse(self):
        r = self.r
        assert r.bytes(3) == b'EDM'
        self.version = struct.unpack_from('<H', self.data, r.p)[0]; r.p += 2
        assert self.version == 10, f"only v10 supported, got {self.version}"

        lookup_size = r.u32()
        lookup_raw = r.bytes(lookup_size)
        self.lookup = [p.decode('windows-1251', errors='replace')
                        for p in lookup_raw.split(b'\x00')]

        self.indexA = self.read_map()
        self.indexB = self.read_map()

        self._parse_root_node()
        self._parse_transform_nodes()
        self._parse_render_items()

    # -- RootNode ------------------------------------------------------
    def _parse_root_node(self):
        r = self.r
        typeName = self.lstr()
        assert typeName == 'model::RootNode', typeName
        self.root_name = r.uint_string()
        self.root_version = r.u32()
        self.root_props = self._read_properties_set()

        save = r.p
        maybe_bbox_x = struct.unpack_from('<d', self.data, r.p)[0]
        FLT_MAX = 3.4028234663852886e+38   # bbox vazio/invertido gravado como double
        if not (abs(maybe_bbox_x) < 100000 or abs(maybe_bbox_x) == FLT_MAX):
            r.p = save + 1
        self.bbox_min = r.raw_vec3d()
        self.bbox_max = r.raw_vec3d()
        self.unknownB = [r.raw_vec3d() for _ in range(4)]

        matcount = r.u32()
        self.materials = [self._read_material() for _ in range(matcount)]
        self.root_unknownC = (r.u32(), r.u32())

    def _read_properties_set(self):
        r = self.r
        n = r.u32()
        out = []
        for _ in range(n):
            t = self.lstr()
            name, val = self._read_property_value(t)
            out.append((t, name, val))
        return out

    def _read_property_value(self, t):
        r = self.r
        name = self.lstr()
        if t.endswith('unsigned int>'):
            return name, r.u32()
        if t == 'model::Property<float>':
            return name, r.f32()
        if t == 'model::Property<osg::Vec2f>':
            v = struct.unpack_from('<2f', self.data, r.p); r.p += 8
            return name, v
        if t == 'model::Property<osg::Vec3f>':
            v = r.raw_vec3f()
            return name, v
        if t == 'model::Property<const char*>':
            return name, self.lstr()
        if t.startswith('model::AnimatedProperty<'):
            argument = r.u32()
            count = r.u32()
            inner = t[len('model::AnimatedProperty<'):-1]
            keys = []
            for _ in range(count):
                frame = r.f64()
                if inner == 'float':
                    val = r.f32()
                elif inner == 'osg::Vec2f':
                    val = struct.unpack_from('<2f', self.data, r.p); r.p += 8
                elif inner == 'osg::Vec3f':
                    val = r.raw_vec3f()
                else:
                    raise NotImplementedError(f"AnimatedProperty<{inner}>")
                keys.append((frame, val))
            return name, {'argument': argument, 'keys': keys}
        raise NotImplementedError(f"property type {t}")

    def _read_material(self):
        r = self.r
        mcount = r.u32()
        mat = {}
        for _ in range(mcount):
            key = self.lstr()
            if key == 'BLENDING':
                mat[key] = r.u8()
            elif key == 'CULLING':
                mat[key] = r.u8()
            elif key == 'DEPTH_BIAS':
                mat[key] = r.u32()
            elif key in ('MATERIAL_NAME', 'NAME'):
                mat[key] = self.lstr()
            elif key == 'SHADOWS':
                mat[key] = r.u8()
            elif key == 'TEXTURES':
                tc = r.u32()
                texs = []
                for _ in range(tc):
                    idx = r.i32(); unk = r.i32(); fname = self.lstr()
                    unk2 = struct.unpack_from('<4I', self.data, r.p); r.p += 16
                    mtx = struct.unpack_from('<16f', self.data, r.p); r.p += 64
                    texs.append({'index': idx, 'filename': fname, 'unknown2': unk2, 'matrix': mtx})
                mat[key] = texs
            elif key == 'TEXTURE_COORDINATES_CHANNELS':
                cc = r.u32()
                chans = struct.unpack_from(f'<{cc}i', self.data, r.p); r.p += 4 * cc
                mat[key] = chans
            elif key == 'VERTEX_FORMAT':
                vc = r.u32()
                mat[key] = list(r.bytes(vc))
            elif key in ('UNIFORMS', 'ANIMATED_UNIFORMS'):
                mat[key] = self._read_properties_set()
            else:
                raise NotImplementedError(f"material key {key!r} @ {r.p}")
        return mat

    # -- Transform node tree --------------------------------------------
    def _read_node_base(self):
        r = self.r
        name = r.uint_string()
        self._ctx['node'] = name
        version = r.u32()
        props = self._read_properties_set()
        return {'name': name, 'version': version, 'props': props}

    def _parse_transform_nodes(self):
        r = self.r
        count = r.u32()
        self.nodes = []
        for i in range(count):
            typeName = self.lstr()
            self._note(section='transform', key=None, type=typeName, item=i)
            node = self._read_node_by_type(typeName)
            self.nodes.append((typeName, node))
        self.node_parents = [r.i32() for _ in range(count)]

    def _read_node_by_type(self, typeName):
        r = self.r
        base = self._read_node_base()
        if typeName == 'model::Node':
            return base
        if typeName == 'model::TransformNode':
            base['transform'] = r.raw_matrixd()
            return base
        if typeName == 'model::LodNode':
            n = r.u32()
            base['levels'] = [(r.f64(), r.f64()) for _ in range(n)]
            return base
        if typeName == 'model::Bone':
            base['m1'] = r.raw_matrixd()
            base['m2'] = r.raw_matrixd()
            return base
        if typeName == 'model::BillboardNode':
            base['unknown'] = r.bytes(154)
            return base
        if typeName in ('model::ArgAnimationNode', 'model::ArgRotationNode',
                         'model::ArgPositionNode', 'model::ArgScaleNode',
                         'model::ArgAnimatedBone'):
            base['tf_matrix'] = r.raw_matrixd()
            base['tf_position'] = r.raw_vec3d()
            base['tf_quat1'] = r.raw_quat()
            base['tf_quat2'] = r.raw_quat()
            base['tf_scale'] = r.raw_vec3d()
            pcount = r.u32()
            positions = []
            for _ in range(pcount):
                arg = r.u32(); kc = r.u32()
                keys = [(r.f64(), r.raw_vec3d()) for _ in range(kc)]
                positions.append({'argument': arg, 'keys': keys})
            base['position_data'] = positions
            rcount = r.u32()
            rotations = []
            for _ in range(rcount):
                arg = r.u32(); kc = r.u32()
                keys = [(r.f64(), r.raw_quat()) for _ in range(kc)]
                rotations.append({'argument': arg, 'keys': keys})
            base['rotation_data'] = rotations
            scount = r.u32()
            scales = []
            for _ in range(scount):
                arg = r.u32()
                ac = r.u32()
                keysA = [(r.f64(), r.raw_vec4d()) for _ in range(ac)]  # spec: ScaleKey = f64 + doubles
                bc = r.u32()
                keysB = [(r.f64(), r.raw_vec3d()) for _ in range(bc)]
                scales.append({'argument': arg, 'keysA': keysA, 'keysB': keysB})
            base['scale_data'] = scales
            if typeName == 'model::ArgAnimatedBone':
                base['xf_bone'] = r.raw_matrixd()
            return base
        if typeName == 'model::ArgVisibilityNode':
            n = r.u32()
            args = []
            for _ in range(n):
                arg = r.u32()
                kc = r.u32()
                ranges = [(r.f64(), r.f64()) for _ in range(kc)]
                args.append({'argument': arg, 'ranges': ranges})
            base['visibility_data'] = args
            return base
        raise NotImplementedError(f"transform node type {typeName!r} @ {r.p}")

    # -- Render items ----------------------------------------------------
    def _read_vertex_data(self):
        r = self.r
        count = r.u32(); stride = r.u32()
        data = struct.unpack_from(f'<{count * stride}f', self.data, r.p)
        r.p += 4 * count * stride
        return {'count': count, 'stride': stride, 'data': data}

    def _read_index_data(self):
        r = self.r
        data_type = r.u8()
        entries = r.u32()
        unknown = r.u32()
        if data_type == 0:
            fmt, size = 'B', 1
        elif data_type == 1:
            fmt, size = 'H', 2
        elif data_type == 2:
            fmt, size = 'I', 4
        else:
            raise NotImplementedError(f"index data_type {data_type}")
        data = struct.unpack_from(f'<{entries}{fmt}', self.data, r.p)
        r.p += size * entries
        return {'data_type': data_type, 'entries': entries, 'unknown': unknown, 'data': data}

    def _read_parent_data(self):
        r = self.r
        count = r.u32()
        if count == 1:
            parent = r.u32(); dmg = r.i32()
            return [{'parent': parent, 'damage_argument': dmg}]
        else:
            out = []
            for _ in range(count):
                parent = r.u32(); idx_start = r.i32(); dmg = r.i32()
                out.append({'parent': parent, 'index_start': idx_start, 'damage_argument': dmg})
            return out

    def _read_render_node(self):
        r = self.r
        base = self._read_node_base()
        unknown = r.u32()
        base['material_id'] = r.u32()
        base['parent_data'] = self._read_parent_data()
        base['vertex_data'] = self._read_vertex_data()
        base['index_data'] = self._read_index_data()
        return base

    def _read_connector(self):
        r = self.r
        base = self._read_node_base()
        base['parent'] = r.u32()
        base['unknown'] = r.u32()
        return base

    def _read_shell_node(self):
        r = self.r
        base = self._read_node_base()
        base['unknown'] = r.u32()
        vc = r.u32()
        base['vertex_format'] = list(r.bytes(vc))
        base['vertex_data'] = self._read_vertex_data()
        base['index_data'] = self._read_index_data()
        return base

    def _read_segments_node(self):
        r = self.r
        base = self._read_node_base()
        base['unknown'] = r.u32()
        n = r.u32()
        if r.p + 24 * n > len(self.data):
            raise EDMParseError(self._where(f"SegmentsNode wants {n} segments "
                                            f"({24 * n} bytes) but only {len(self.data) - r.p} bytes remain"))
        base['segments'] = [struct.unpack_from('<6f', self.data, r.p + 24 * i) for i in range(n)]
        r.p += 24 * n
        return base

    def _read_light_node(self):
        r = self.r
        base = self._read_node_base()
        base['parent'] = r.u32()
        base['unknownB'] = r.u8()
        base['light_properties'] = self._read_properties_set()
        base['unknownC'] = r.u8()
        return base

    def _read_skin_node(self):
        r = self.r
        base = self._read_node_base()
        base['unknown_start'] = r.u32()   # spec: SkinNode starts like RenderNode (unknown, material)
        base['material'] = r.u32()
        bc = r.u32()
        base['bones'] = [r.u32() for _ in range(bc)]
        base['unknown'] = r.u32()
        base['vertex_data'] = self._read_vertex_data()
        base['index_data'] = self._read_index_data()
        return base

    def _parse_render_items(self):
        r = self.r
        n = r.u32()  # map<string, list<named_type>>
        self.render_items = {}
        for _ in range(n):
            key = self.lstr()
            listlen = r.u32()
            items = []
            for li in range(listlen):
                typeName = self.lstr()
                self._note(section='render_items', key=key, type=typeName, item=f"{li}/{listlen}")
                if key == 'CONNECTORS' and typeName == 'model::Connector':
                    items.append((typeName, self._read_connector()))
                elif key == 'RENDER_NODES' and typeName == 'model::RenderNode':
                    items.append((typeName, self._read_render_node()))
                elif key == 'RENDER_NODES' and typeName == 'model::SkinNode':
                    items.append((typeName, self._read_skin_node()))
                elif key == 'SHELL_NODES' and typeName == 'model::ShellNode':
                    items.append((typeName, self._read_shell_node()))
                elif key == 'SHELL_NODES' and typeName == 'model::SegmentsNode':
                    items.append((typeName, self._read_segments_node()))
                elif key == 'LIGHT_NODES' and typeName == 'model::LightNode':
                    items.append((typeName, self._read_light_node()))
                else:
                    raise NotImplementedError(f"render item {key}/{typeName} @ {r.p}")
            self.render_items[key] = items
        self.trailing_bytes = len(self.data) - r.p


# -- Vertex channel semantics --------------------------------------------
VERTEX_CHANNEL_INFO = {
    0: ('position', 4),
    1: ('normal', 3),
    2: ('unknown2', 3),
    3: ('unknown3', 3),
    4: ('uv0', 2),
    5: ('uv1', 2),
    6: ('uv2', 2),
    7: ('uv3', 2),
    8: ('uv4', 2),
    20: ('unknown20', 3),
    21: ('bone', 4),
    24: ('unknown24', 3),
    25: ('unknown25', 3),
}


def decode_vertices(vertex_format, vertex_data):
    """Split flat vertex float data into named channels.

    vertex_format: list[int] of length 26 (bytes from material VERTEX_FORMAT)
    vertex_data: {'count', 'stride', 'data': flat tuple of floats}
    Returns: list of dicts, one per vertex, keyed by channel name.
    """
    layout = []
    offset = 0
    for ch, length in enumerate(vertex_format):
        if length:
            name, _ = VERTEX_CHANNEL_INFO.get(ch, (f'ch{ch}', length))
            layout.append((name, length, offset))
            offset += length
    stride = vertex_data['stride']
    assert offset == stride, f"channel lengths sum to {offset}, stride is {stride}"
    data = vertex_data['data']
    count = vertex_data['count']
    out = []
    for i in range(count):
        base = i * stride
        vtx = {name: data[base + off: base + off + length] for name, length, off in layout}
        out.append(vtx)
    return out


def split_by_vertex_group(vertex_format, vertex_data, index_data, num_groups):
    """Split a multi-parent RenderNode's shared vertex/index buffers into
    per-group vertex+triangle lists.

    IMPORTANT: grouping is done using the group id embedded in the 4th
    component of each vertex's position channel (position.w) — this was
    confirmed against a real file to be the authoritative per-vertex group
    index into parent_data (distinct counts of vertices per w-value matched
    each parent_data entry's group size exactly).

    `parent_data[g]['index_start']` is NOT a reliable contiguous triangle
    range boundary for this purpose: real authored files commonly have
    index_start == 0 for most/all entries in a multi-parent RenderNode, so
    treating it as "triangles from index_start[g-1] to index_start[g]"
    silently collapses almost every group into group 0 (or leaves later
    groups empty) — this was the root cause of large static meshes (e.g. a
    fuselage/wing skin) coming in with pieces scattered/overlapping near
    the origin.

    Returns: list of length num_groups; each element is
    (local_verts: list[dict], local_tri_indices: list[int]) with vertex
    indices renumbered to start at 0 within that group. A group with no
    triangles gets ([], []).
    """
    verts = decode_vertices(vertex_format, vertex_data)
    idx = index_data['data']
    groups = [{'remap': {}, 'verts': [], 'faces': []} for _ in range(max(num_groups, 1))]
    for tri_start in range(0, len(idx) - len(idx) % 3, 3):
        tri = idx[tri_start:tri_start + 3]
        gids = [int(round(verts[vi]['position'][3])) for vi in tri]
        if gids[0] == gids[1] == gids[2]:
            g = gids[0]
        else:
            # Triangle straddles a group boundary (rare) — assign it to
            # whichever group most of its vertices agree on.
            g = max(set(gids), key=gids.count)
        g = max(0, min(g, len(groups) - 1))
        bucket = groups[g]
        face = []
        for vi in tri:
            if vi not in bucket['remap']:
                bucket['remap'][vi] = len(bucket['verts'])
                bucket['verts'].append(verts[vi])
            face.append(bucket['remap'][vi])
        bucket['faces'].append(tuple(face))
    return [(g['verts'], g['faces']) for g in groups]


def encode_vertices(vertex_format, vertices):
    """Inverse of decode_vertices: pack a list of per-vertex channel dicts
    back into a flat float tuple, following the layout implied by
    vertex_format. Used by the exporter after editing geometry in Blender."""
    layout = []
    offset = 0
    for ch, length in enumerate(vertex_format):
        if length:
            name, _ = VERTEX_CHANNEL_INFO.get(ch, (f'ch{ch}', length))
            layout.append((name, length, offset))
            offset += length
    stride = offset
    flat = []
    for vtx in vertices:
        row = [0.0] * stride
        for name, length, off in layout:
            vals = vtx.get(name)
            if vals is not None:
                for i in range(length):
                    row[off + i] = vals[i] if i < len(vals) else 0.0
        flat.extend(row)
    return {'count': len(vertices), 'stride': stride, 'data': flat}