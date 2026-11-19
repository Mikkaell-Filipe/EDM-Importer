"""
EDM (DCS World) binary model writer — v10, mirrors edm_reader.EDMFile.

Strategy: walk the SAME parsed python structures the reader produces
(materials, nodes, render_items, ...) twice:
  pass 1 ("collect"): record every string that must live in the lookup
                       table, in first-use order.
  pass 2 ("emit"):    write real bytes, using the lookup table built in
                       pass 1 to resolve string -> index.

This keeps the writer's traversal order guaranteed identical to the
reader's (same functions, same order of field access), which is the
easiest way to avoid subtle desyncs between read and write.

Validated byte-exact (read -> write -> reread, full deep compare) against
a real 5.5MB A-29B .edm file, Aug 2026.
"""
import struct


class StringTable:
    def __init__(self):
        self.strings = []
        self.index = {}

    def add(self, s):
        if s not in self.index:
            self.index[s] = len(self.strings)
            self.strings.append(s)
        return self.index[s]

    def to_bytes(self):
        return b''.join(s.encode('windows-1251', errors='replace') + b'\x00' for s in self.strings)


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def bytes(self, b):
        self.buf += b

    def u8(self, v):
        self.buf += struct.pack('<B', v)

    def i32(self, v):
        self.buf += struct.pack('<i', v)

    def u32(self, v):
        self.buf += struct.pack('<I', v)

    def f32(self, v):
        self.buf += struct.pack('<f', v)

    def f64(self, v):
        self.buf += struct.pack('<d', v)

    def raw_vec3f(self, v):
        self.buf += struct.pack('<3f', *v)

    def raw_vec3d(self, v):
        self.buf += struct.pack('<3d', *v)

    def raw_vec4d(self, v):
        self.buf += struct.pack('<4d', *v)

    def raw_vec4f(self, v):
        self.buf += struct.pack('<4f', *v)

    def raw_quat(self, v):
        self.buf += struct.pack('<4d', *v)

    def raw_matrixd(self, v):
        self.buf += struct.pack('<16d', *v)

    def uint_string(self, s):
        enc = s.encode('windows-1251', errors='replace')
        self.u32(len(enc))
        self.buf += enc


class EDMWriter:
    """Serializes an edm_reader.EDMFile back to bytes.

    Only the render-node/shell-node/skin-node vertex & index data are meant
    to be replaced (e.g. from an edited Blender mesh) before calling
    write(). Everything else round-trips from the structures the reader
    already parsed.
    """

    def __init__(self, edm):
        self.edm = edm

    def write(self):
        self.table = StringTable()
        self.w = None
        self._collecting = True
        self._walk_all()

        self._collecting = False
        self.w = Writer()
        header = bytearray()
        header += b'EDM'
        header += struct.pack('<H', 10)
        lookup_bytes = self.table.to_bytes()
        header += struct.pack('<I', len(lookup_bytes))
        header += lookup_bytes
        self.w.buf = bytearray()
        self._walk_all()
        return bytes(header) + bytes(self.w.buf)

    def lstr(self, s):
        if self._collecting:
            self.table.add(s)
        else:
            self.w.u32(self.table.index[s])

    def _walk_all(self):
        edm = self.edm
        self._write_map(edm.indexA)
        self._write_map(edm.indexB)
        self._write_root_node()
        self._write_transform_nodes()
        self._write_render_items()

    def _write_map(self, d):
        if not self._collecting:
            self.w.u32(len(d))
        for k, v in d.items():
            self.lstr(k)
            if not self._collecting:
                self.w.u32(v)

    def _write_root_node(self):
        edm = self.edm
        self.lstr('model::RootNode')
        if not self._collecting:
            self.w.uint_string(edm.root_name)
            self.w.u32(edm.root_version)
        self._write_properties_set(edm.root_props)
        if not self._collecting:
            self.w.raw_vec3d(edm.bbox_min)
            self.w.raw_vec3d(edm.bbox_max)
            for v in edm.unknownB:
                self.w.raw_vec3d(v)
            self.w.u32(len(edm.materials))
        for mat in edm.materials:
            self._write_material(mat)
        if not self._collecting:
            self.w.u32(edm.root_unknownC[0])
            self.w.u32(edm.root_unknownC[1])

    def _write_properties_set(self, props):
        if not self._collecting:
            self.w.u32(len(props))
        for t, name, val in props:
            self.lstr(t)
            self._write_property_value(t, name, val)

    def _write_property_value(self, t, name, val):
        self.lstr(name)
        if t == 'model::Property<const char*>':
            self.lstr(val)
            return
        if self._collecting:
            return
        if t.endswith('unsigned int>'):
            self.w.u32(val)
        elif t == 'model::Property<float>':
            self.w.f32(val)
        elif t == 'model::Property<osg::Vec2f>':
            self.w.buf += struct.pack('<2f', *val)
        elif t == 'model::Property<osg::Vec3f>':
            self.w.raw_vec3f(val)
        elif t.startswith('model::AnimatedProperty<'):
            inner = t[len('model::AnimatedProperty<'):-1]
            self.w.u32(val['argument'])
            self.w.u32(len(val['keys']))
            for frame, v in val['keys']:
                self.w.f64(frame)
                if inner == 'float':
                    self.w.f32(v)
                elif inner == 'osg::Vec2f':
                    self.w.buf += struct.pack('<2f', *v)
                elif inner == 'osg::Vec3f':
                    self.w.raw_vec3f(v)
                else:
                    raise NotImplementedError(inner)
        else:
            raise NotImplementedError(t)

    def _write_material(self, mat):
        if not self._collecting:
            self.w.u32(len(mat))
        for key, val in mat.items():
            self.lstr(key)
            if key == 'BLENDING' or key == 'CULLING' or key == 'SHADOWS':
                if not self._collecting:
                    self.w.u8(val)
            elif key == 'DEPTH_BIAS':
                if not self._collecting:
                    self.w.u32(val)
            elif key in ('MATERIAL_NAME', 'NAME'):
                self.lstr(val)
            elif key == 'TEXTURES':
                if not self._collecting:
                    self.w.u32(len(val))
                for tex in val:
                    if not self._collecting:
                        self.w.i32(tex['index'])
                        self.w.i32(-1)
                    self.lstr(tex['filename'])
                    if not self._collecting:
                        # preserve the exact bytes read, rather than
                        # hardcoding defaults, so untouched textures
                        # round-trip byte-for-byte
                        self.w.buf += struct.pack('<4I', *tex['unknown2'])
                        self.w.buf += struct.pack('<16f', *tex['matrix'])
            elif key == 'TEXTURE_COORDINATES_CHANNELS':
                if not self._collecting:
                    self.w.u32(len(val))
                    self.w.buf += struct.pack(f'<{len(val)}i', *val)
            elif key == 'VERTEX_FORMAT':
                if not self._collecting:
                    self.w.u32(len(val))
                    self.w.buf += bytes(val)
            elif key in ('UNIFORMS', 'ANIMATED_UNIFORMS'):
                self._write_properties_set(val)
            else:
                raise NotImplementedError(f"material key {key!r}")

    def _write_node_base(self, node):
        if not self._collecting:
            self.w.uint_string(node['name'])
            self.w.u32(node['version'])
        self._write_properties_set(node['props'])

    def _write_transform_nodes(self):
        edm = self.edm
        if not self._collecting:
            self.w.u32(len(edm.nodes))
        for typeName, node in edm.nodes:
            self.lstr(typeName)
            self._write_node_by_type(typeName, node)
        if not self._collecting:
            for p in edm.node_parents:
                self.w.i32(p)

    def _write_node_by_type(self, typeName, node):
        self._write_node_base(node)
        if typeName == 'model::Node':
            return
        if typeName == 'model::TransformNode':
            if not self._collecting:
                self.w.raw_matrixd(node['transform'])
            return
        if typeName == 'model::LodNode':
            if not self._collecting:
                self.w.u32(len(node['levels']))
                for a, b in node['levels']:
                    self.w.f64(a); self.w.f64(b)
            return
        if typeName == 'model::Bone':
            if not self._collecting:
                self.w.raw_matrixd(node['m1'])
                self.w.raw_matrixd(node['m2'])
            return
        if typeName == 'model::BillboardNode':
            if not self._collecting:
                self.w.bytes(node['unknown'])
            return
        if typeName in ('model::ArgAnimationNode', 'model::ArgRotationNode',
                         'model::ArgPositionNode', 'model::ArgScaleNode',
                         'model::ArgAnimatedBone'):
            if not self._collecting:
                self.w.raw_matrixd(node['tf_matrix'])
                self.w.raw_vec3d(node['tf_position'])
                self.w.raw_quat(node['tf_quat1'])
                self.w.raw_quat(node['tf_quat2'])
                self.w.raw_vec3d(node['tf_scale'])
                self.w.u32(len(node['position_data']))
                for p in node['position_data']:
                    self.w.u32(p['argument']); self.w.u32(len(p['keys']))
                    for frame, v in p['keys']:
                        self.w.f64(frame); self.w.raw_vec3d(v)
                self.w.u32(len(node['rotation_data']))
                for rot in node['rotation_data']:
                    self.w.u32(rot['argument']); self.w.u32(len(rot['keys']))
                    for frame, v in rot['keys']:
                        self.w.f64(frame); self.w.raw_quat(v)
                self.w.u32(len(node['scale_data']))
                for sc in node['scale_data']:
                    self.w.u32(sc['argument'])
                    self.w.u32(len(sc['keysA']))
                    for frame, v in sc['keysA']:
                        self.w.f64(frame); self.w.raw_vec4d(v)
                    self.w.u32(len(sc['keysB']))
                    for frame, v in sc['keysB']:
                        self.w.f64(frame); self.w.raw_vec3d(v)
                if typeName == 'model::ArgAnimatedBone':
                    self.w.raw_matrixd(node['xf_bone'])
            return
        if typeName == 'model::ArgVisibilityNode':
            if not self._collecting:
                self.w.u32(len(node['visibility_data']))
                for a in node['visibility_data']:
                    self.w.u32(a['argument'])
                    self.w.u32(len(a['ranges']))
                    for s, e in a['ranges']:
                        self.w.f64(s); self.w.f64(e)
            return
        raise NotImplementedError(typeName)

    def _write_vertex_data(self, vd):
        if not self._collecting:
            self.w.u32(vd['count'])
            self.w.u32(vd['stride'])
            self.w.buf += struct.pack(f"<{vd['count'] * vd['stride']}f", *vd['data'])

    def _write_index_data(self, idx):
        if not self._collecting:
            self.w.u8(idx['data_type'])
            self.w.u32(idx['entries'])
            # preserve the exact 'unknown' field read from the file
            self.w.u32(idx['unknown'])
            fmt = {0: 'B', 1: 'H', 2: 'I'}[idx['data_type']]
            self.w.buf += struct.pack(f"<{idx['entries']}{fmt}", *idx['data'])

    def _write_parent_data(self, pd):
        if not self._collecting:
            self.w.u32(len(pd))
        if len(pd) == 1:
            if not self._collecting:
                self.w.u32(pd[0]['parent'])
                self.w.i32(pd[0]['damage_argument'])
        else:
            for entry in pd:
                if not self._collecting:
                    self.w.u32(entry['parent'])
                    self.w.i32(entry['index_start'])
                    self.w.i32(entry['damage_argument'])

    def _write_render_node(self, node):
        self._write_node_base(node)
        if not self._collecting:
            self.w.u32(0)
            self.w.u32(node['material_id'])
        self._write_parent_data(node['parent_data'])
        self._write_vertex_data(node['vertex_data'])
        self._write_index_data(node['index_data'])

    def _write_skin_node(self, node):
        self._write_node_base(node)
        if not self._collecting:
            self.w.u32(node.get('unknown_start', 0))
            self.w.u32(node['material'])
            self.w.u32(len(node['bones']))
            for b in node['bones']:
                self.w.u32(b)
            self.w.u32(node['unknown'])
        self._write_vertex_data(node['vertex_data'])
        self._write_index_data(node['index_data'])

    def _write_shell_node(self, node):
        self._write_node_base(node)
        if not self._collecting:
            self.w.u32(node['unknown'])
            self.w.u32(len(node['vertex_format']))
            self.w.bytes(bytes(node['vertex_format']))
        self._write_vertex_data(node['vertex_data'])
        self._write_index_data(node['index_data'])

    def _write_segments_node(self, node):
        self._write_node_base(node)
        if not self._collecting:
            self.w.u32(node['unknown'])
            self.w.u32(len(node['segments']))
            for seg in node['segments']:
                self.w.buf += struct.pack('<6f', *seg)

    def _write_connector(self, node):
        self._write_node_base(node)
        if not self._collecting:
            self.w.u32(node['parent'])
            self.w.u32(node['unknown'])

    def _write_light_node(self, node):
        self._write_node_base(node)
        if not self._collecting:
            self.w.u32(node['parent'])
            self.w.u8(node['unknownB'])
        self._write_properties_set(node['light_properties'])
        if not self._collecting:
            self.w.u8(node['unknownC'])

    def _write_render_items(self):
        edm = self.edm
        if not self._collecting:
            self.w.u32(len(edm.render_items))
        for key, items in edm.render_items.items():
            self.lstr(key)
            if not self._collecting:
                self.w.u32(len(items))
            for typeName, node in items:
                self.lstr(typeName)
                if typeName == 'model::Connector':
                    self._write_connector(node)
                elif typeName == 'model::RenderNode':
                    self._write_render_node(node)
                elif typeName == 'model::SkinNode':
                    self._write_skin_node(node)
                elif typeName == 'model::ShellNode':
                    self._write_shell_node(node)
                elif typeName == 'model::SegmentsNode':
                    self._write_segments_node(node)
                elif typeName == 'model::LightNode':
                    self._write_light_node(node)
                else:
                    raise NotImplementedError(typeName)