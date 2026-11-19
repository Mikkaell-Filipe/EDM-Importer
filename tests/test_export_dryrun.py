"""Export dry-run on a real EDM: every group split into loose parts (fake Blender objects whose
geometry is patched in), recombined by export_edm, re-read and compared with the original.
Set EDM_TEST_FILE (skipped otherwise)."""
import os, sys, tempfile, types, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
from edm_addon import edm_reader as R, edm_split as sp

PATH = os.environ.get('EDM_TEST_FILE', '')


def _groups(edm, ridx, node):
    fmt = edm.materials[node['material_id']]['VERTEX_FORMAT']
    pd = node['parent_data']
    if len(pd) <= 1:
        v = R.decode_vertices(fmt, node['vertex_data'])
        d = node['index_data']['data']
        return [(0, [(v, [tuple(d[i:i + 3]) for i in range(0, len(d) - len(d) % 3, 3)])])]
    gs = R.split_by_vertex_group(fmt, node['vertex_data'], node['index_data'], len(pd))
    return [(g, [(v, f)]) for g, (v, f) in enumerate(gs)]


@unittest.skipUnless(os.path.isfile(PATH), 'EDM_TEST_FILE not set')
class TestExportDry(unittest.TestCase):
    def test_split_parts_recombine(self):
        from edm_addon import export_edm
        edm = R.EDMFile(PATH)
        coll = fakes.FakeColl('c')
        coll['edm_source_path'] = PATH
        tips = {}
        for i in range(len(edm.nodes)):
            t = fakes.FakeObj(f'tip{i}')
            t['edm_node_index'] = i
            t['edm_node_first_of'] = i
            t.loc = None
            tips[i] = t
        geo = {}
        expected_faces = {}
        for ridx, (tn, node) in enumerate(edm.render_items['RENDER_NODES']):
            pd = node['parent_data']
            for g, items in _groups(edm, ridx, node):
                for v, f in items:
                    if not f:
                        continue
                    parts = sp.split_geometry(v, f)
                    expected_faces[(ridx, g)] = len(f)
                    for pi, (pv, pf) in enumerate(parts):
                        o = fakes.FakeObj(f'r{ridx}g{g}p{pi}', data=object())
                        o['edm_render_index'] = ridx
                        o['edm_parent_group'] = g
                        o['edm_split_part'] = 1
                        o.hide_viewport = False
                        o.parent = tips[pd[g]['parent'] if pd else -1] if pd else None
                        geo[o.name] = (pv, [i for t3 in pf for i in t3])
                        coll._objs.append(o)

        def fake_mesh(obj, mesh, fmt, group_index=0, matrix=None, lookup=None):
            v, t = geo[obj.name]
            return v, t, (0, 0)

        out = os.path.join(tempfile.mkdtemp(), 'out.edm')
        with mock.patch.object(export_edm, '_mesh_to_edm_vertices', fake_mesh), \
             mock.patch.object(export_edm, '_relative_matrix', lambda *a, **k: None), \
             mock.patch.object(export_edm, '_node_empties', lambda: tips), \
             mock.patch.object(export_edm, '_node_firsts', lambda: {}):
            ctx = mock.MagicMock()
            export_edm.export_edm(ctx, out, coll, confirm_bulk_delete=True)
        e2 = R.EDMFile(out)
        n_checked = 0
        for ridx, (tn, node) in enumerate(e2.render_items['RENDER_NODES']):
            for g, items in _groups(e2, ridx, node):
                for v, f in items:
                    if f:
                        self.assertEqual(len(f), expected_faces[(ridx, g)], (ridx, g))
                        n_checked += 1
        self.assertEqual(n_checked, len(expected_faces))




def _tips(edm):
    tips = {}
    for i in range(len(edm.nodes)):
        t = fakes.FakeObj(f'tip{i}')
        t['edm_node_index'] = i
        tips[i] = t
    return tips


def _node_named(edm, name):
    return next(i for i, (t, n) in enumerate(edm.nodes) if n['name'] == name)


def _sample_geometry(edm, material_id=None):
    """Some real triangles (one loose part) to stand in for a Blender mesh."""
    best = None
    for ridx, (_, node) in enumerate(edm.render_items['RENDER_NODES']):
        if node['index_data']['entries'] == 0:
            continue
        for g, items in _groups(edm, ridx, node):
            for v, f in items:
                if f:
                    cand = sp.split_geometry(v, f)[0]
                    if node['material_id'] == material_id:
                        return cand[0], [i for t3 in cand[1] for i in t3], len(cand[1])
                    best = best or cand
    pv, pf = best
    return pv, [i for t3 in pf for i in t3], len(pf)


def _fake_material(name, image_path):
    img = types.SimpleNamespace(filepath=image_path, name=os.path.basename(image_path))
    tex = types.SimpleNamespace(type='TEX_IMAGE', image=img)
    tree = types.SimpleNamespace(nodes=[tex])
    return types.SimpleNamespace(name=name, node_tree=tree)


def _run_export(edm, coll, tips, new_objs, geo, **kw):
    from edm_addon import export_edm

    def fake_mesh(obj, mesh, fmt, group_index=0, matrix=None, lookup=None):
        if obj.name in geo:
            return geo[obj.name][0], geo[obj.name][1], (0, 0)
        raise AssertionError(obj.name)

    out = os.path.join(tempfile.mkdtemp(), 'out.edm')
    ctx = mock.MagicMock()
    ctx.scene.objects.__iter__.return_value = iter(new_objs)
    with mock.patch.object(export_edm, '_mesh_to_edm_vertices', fake_mesh), \
         mock.patch.object(export_edm, '_relative_matrix', lambda *a, **k: None), \
         mock.patch.object(export_edm, '_node_empties', lambda: tips), \
         mock.patch.object(export_edm, '_node_firsts', lambda: {}):
        res = export_edm.export_edm(ctx, out, coll, confirm_bulk_delete=True, **kw)
    return res, R.EDMFile(out), export_edm.last_report


@unittest.skipUnless(os.path.isfile(PATH), 'EDM_TEST_FILE not set')
class TestNewObjects(unittest.TestCase):
    """The user's case: seat + pilot removed, new seat/pilot modelled in the
    default 'Collection' (NOT the imported one)."""

    def setUp(self):
        self.edm = R.EDMFile(PATH)
        self.coll = fakes.FakeColl('c')
        self.coll['edm_source_path'] = PATH
        self.tips = _tips(self.edm)

    def _new(self, name, parent_name, **props):
        o = fakes.FakeObj(name, data=object())
        o.hide_viewport = False
        o.material_slots = []
        for k, v in props.items():
            o[k] = v
        if parent_name:
            o.parent = self.tips[_node_named(self.edm, parent_name)]
        return o

    def test_seat_in_other_collection_existing_material(self):
        mid = next(i for i, m in enumerate(self.edm.materials) if m['NAME'] == 'a29b_internal')
        pv, pt, nf = _sample_geometry(self.edm, mid)
        seat = self._new('NewSeat', 'cadeira front', edm_new_material='a29b_internal')
        (e, d, a), e2, rep = _run_export(self.edm, self.coll, self.tips, [seat], {'NewSeat': (pv, pt)})
        self.assertEqual(a, 1)
        self.assertIn('NewSeat', rep['pulled'])
        n = next(n for _, n in e2.render_items['RENDER_NODES'] if n['name'] == 'NewSeat')
        self.assertEqual(n['material_id'], mid)
        self.assertEqual(n['parent_data'][0]['parent'], _node_named(self.edm, 'cadeira front'))
        self.assertEqual(len(n['index_data']['data']) // 3, nf)

    def test_pilot_with_new_texture_creates_material(self):
        mid = next(i for i, m in enumerate(self.edm.materials) if m['NAME'] == 'A-29B Pilot.dds')
        pv, pt, nf = _sample_geometry(self.edm, mid)
        pilot = self._new('NewPilot', 'piloto frente')
        pilot.material_slots = [types.SimpleNamespace(material=_fake_material('PilotMat', 'C:/tex/pilot_novo.png'))]
        n_mats = len(self.edm.materials)
        (e, d, a), e2, rep = _run_export(self.edm, self.coll, self.tips, [pilot], {'NewPilot': (pv, pt)})
        self.assertEqual(a, 1)
        self.assertEqual(len(e2.materials), n_mats + 1)
        new_mat = e2.materials[-1]
        self.assertEqual(new_mat['NAME'], 'PilotMat')
        self.assertEqual(new_mat['MATERIAL_NAME'], 'def_material')
        self.assertEqual([t['filename'] for t in new_mat['TEXTURES'] if t['index'] == 0], ['pilot_novo'])
        n = next(n for _, n in e2.render_items['RENDER_NODES'] if n['name'] == 'NewPilot')
        self.assertEqual(n['material_id'], n_mats)
        self.assertTrue(rep['created_materials'])

    def test_existing_texture_reuses_material(self):
        mid = next(i for i, m in enumerate(self.edm.materials) if m['NAME'] == 'A-29B Pilot.dds')
        pv, pt, nf = _sample_geometry(self.edm, mid)
        pilot = self._new('NewPilot', 'piloto frente')
        pilot.material_slots = [types.SimpleNamespace(material=_fake_material('X', 'a29b_pilot.dds'))]
        n_mats = len(self.edm.materials)
        (e, d, a), e2, rep = _run_export(self.edm, self.coll, self.tips, [pilot], {'NewPilot': (pv, pt)})
        self.assertEqual(len(e2.materials), n_mats)
        n = next(n for _, n in e2.render_items['RENDER_NODES'] if n['name'] == 'NewPilot')
        self.assertEqual(n['material_id'], mid)

    def test_loose_object_attached_to_root(self):
        mid = next(i for i, m in enumerate(self.edm.materials) if m['NAME'] == 'a29b_internal')
        pv, pt, nf = _sample_geometry(self.edm, mid)
        loose = self._new('Loose', None, edm_new_material='a29b_internal')
        (e, d, a), e2, rep = _run_export(self.edm, self.coll, self.tips, [loose], {'Loose': (pv, pt)})
        self.assertEqual(a, 1)
        self.assertIn('Loose', rep['rooted'])
        n = next(n for _, n in e2.render_items['RENDER_NODES'] if n['name'] == 'Loose')
        self.assertEqual(n['parent_data'][0]['parent'], 0)

    def test_loose_object_without_fallback_errors(self):
        loose = self._new('Loose', None, edm_new_material='a29b_internal')
        with self.assertRaises(RuntimeError):
            _run_export(self.edm, self.coll, self.tips, [loose], {}, fallback_to_root=False)

    def test_hidden_and_skip_are_reported_not_exported(self):
        hid = self._new('HiddenRef', 'cadeira front', edm_new_material='a29b_internal')
        hid.hide_viewport = True
        skp = self._new('SkipRef', 'cadeira front', edm_new_material='a29b_internal', edm_skip=1)
        (e, d, a), e2, rep = _run_export(self.edm, self.coll, self.tips, [hid, skp], {})
        self.assertEqual(a, 0)
        self.assertIn('HiddenRef', rep['ignored'])
        self.assertIn('SkipRef', rep['ignored'])

    def test_other_collections_can_be_switched_off(self):
        seat = self._new('NewSeat', 'cadeira front', edm_new_material='a29b_internal')
        (e, d, a), e2, rep = _run_export(self.edm, self.coll, self.tips, [seat], {},
                                         include_other_collections=False)
        self.assertEqual(a, 0)
        self.assertEqual(rep['pulled'], [])


class TestScope(unittest.TestCase):
    def test_other_models_empties_are_ignored(self):
        from edm_addon import export_edm
        mine = fakes.FakeObj('mine'); mine['edm_node_index'] = 5
        foreign = fakes.FakeObj('foreign'); foreign['edm_node_index'] = 5
        coll = fakes.FakeColl('c'); coll._objs.append(mine)
        export_edm.bpy.data.objects.__iter__.return_value = iter([foreign, mine])
        export_edm._scope = coll
        try:
            out = export_edm._node_empties()
        finally:
            export_edm._scope = None
        self.assertEqual(out, {5: mine})


class TestStem(unittest.TestCase):
    def test_windows_path(self):
        from edm_addon import edm_newnodes as nn
        self.assertEqual(nn.image_stem(r'C:\tex\pilot_new.001.png'), 'pilot_new')


class TestForeignMeshes(unittest.TestCase):
    def _setup(self):
        from edm_addon import export_edm
        edm = types.SimpleNamespace(render_items={'RENDER_NODES': []})
        coll = fakes.FakeColl('own')
        pilot = fakes.FakeObj('pilot_colet', data=object())
        pilot['edm_render_index'] = 3
        pilot['edm_material_id'] = 7
        ctx = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[pilot]))
        return export_edm, edm, coll, pilot, ctx

    def test_foreign_ignored_by_default(self):
        ex, edm, coll, pilot, ctx = self._setup()
        entries, pulled, ignored, _ = ex._gather(ctx, edm, coll, True)
        self.assertEqual(entries, [])
        self.assertIn('pilot_colet', ignored)

    def test_foreign_included_on_request(self):
        ex, edm, coll, pilot, ctx = self._setup()
        foreign = []
        entries, pulled, ignored, _ = ex._gather(ctx, edm, coll, True, True, foreign)
        self.assertEqual([e[0] for e in entries], ['pilot_colet'])
        self.assertEqual(foreign, ['pilot_colet'])
        self.assertEqual(ignored, {})


class TestPbrCompanions(unittest.TestCase):
    def _edm(self, with_pbr=True):
        fmt = [4, 3, 1, 1, 2, 0, 0]
        mats = [{'NAME': 'simple', 'MATERIAL_NAME': 'def_material', 'VERTEX_FORMAT': fmt,
                 'TEXTURES': [{'index': 0, 'filename': 'a29b_prop'}]}]
        if with_pbr:
            mats.append({'NAME': 'full', 'MATERIAL_NAME': 'def_material', 'VERTEX_FORMAT': fmt,
                         'TEXTURES': [{'index': 0, 'filename': 'x'}, {'index': 1, 'filename': 'x_NM'},
                                      {'index': 2, 'filename': 'x_RoughMet'}]})
        return types.SimpleNamespace(materials=mats)

    def test_companions_detected_case_insensitive(self):
        from edm_addon import edm_newnodes as nn
        d = tempfile.mkdtemp()
        for f in ('Pilot_AL.dds', 'Pilot_AL_NM.dds', 'Pilot_AL_RoughMet.dds', 'Carenagem_A29.png'):
            open(os.path.join(d, f), 'w').close()
        self.assertTrue(nn.has_pbr_companions(d, 'pilot_al'))
        self.assertFalse(nn.has_pbr_companions(d, 'Carenagem_A29'))

    def test_pbr_template_and_clone(self):
        from edm_addon import edm_newnodes as nn
        edm = self._edm()
        self.assertEqual(nn.pick_template_material(edm), 0)
        tpl = nn.pick_template_material(edm, pbr=True)
        self.assertEqual(tpl, 1)
        i = nn.clone_material(edm, tpl, 'AT27_PILOT', 'Pilot_AL', pbr=True)
        names = {t['index']: t['filename'] for t in edm.materials[i]['TEXTURES']}
        self.assertEqual(names, {0: 'Pilot_AL', 1: 'Pilot_AL_NM', 2: 'Pilot_AL_RoughMet'})
        j = nn.clone_material(edm, 1, 'Plain', 'Foo')           # pbr off: only diffuse
        names = {t['index']: t['filename'] for t in edm.materials[j]['TEXTURES']}
        self.assertEqual(names[1], 'x_NM')

    def test_no_pbr_template_returns_none(self):
        from edm_addon import edm_newnodes as nn
        self.assertIsNone(nn.pick_template_material(self._edm(False), pbr=True))


class TestPbrSearch(unittest.TestCase):
    def _obj(self, **props):
        o = fakes.FakeObj('x', data=object())
        for k, v in props.items():
            o[k] = v
        return o

    def test_texture_folder_option_is_searched(self):
        from edm_addon import export_edm
        d = tempfile.mkdtemp()
        for f in ('Pilot_AL.dds', 'Pilot_AL_NM.dds', 'Pilot_AL_RoughMet.dds'):
            open(os.path.join(d, f), 'w').close()
        img = types.SimpleNamespace(filepath='C:/elsewhere/pilot_al.png', name='pilot_al.png')
        obj = self._obj()
        with mock.patch.object(export_edm, '_main_images', lambda o: [img]):
            ok, dirs = export_edm._wants_pbr(obj, d)
            self.assertTrue(ok)
            self.assertIn(d, dirs)
            ok, dirs = export_edm._wants_pbr(obj, '')       # nothing to search
            self.assertFalse(ok)

    def test_edm_pbr_property_overrides(self):
        from edm_addon import export_edm
        self.assertEqual(export_edm._wants_pbr(self._obj(edm_pbr=1)), (True, []))
        self.assertEqual(export_edm._wants_pbr(self._obj(edm_pbr=0)), (False, []))


class TestOperatorMatchesExporter(unittest.TestCase):
    def test_every_kwarg_the_operator_passes_is_accepted(self):
        import ast, inspect
        from edm_addon import export_edm
        src = open(os.path.join(fakes.ADDON_DIR, '__init__.py'), encoding='utf-8').read()
        passed = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == 'export_edm':
                passed |= {k.arg for k in node.keywords if k.arg}
        self.assertTrue(passed)
        accepted = set(inspect.signature(export_edm._export_edm_impl).parameters)
        self.assertEqual(passed - accepted, set())


if __name__ == '__main__':
    unittest.main()
