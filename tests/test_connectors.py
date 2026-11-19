"""Connectors novos (0.7.0). Parte pura sempre roda; o round-trip precisa de EDM_TEST_FILE."""
import os, sys, types, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
from edm_addon import edm_connectors as C, edm_reader as R, edm_writer as W, edm_math as em

IDENT = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def fake_edm():
    e = types.SimpleNamespace()
    e.nodes = [('model::TransformNode', {'name': 'root', 'version': 0, 'props': [], 'transform': tuple(em.to_flat(IDENT))})]
    e.node_parents = [-1]
    e.render_items = {'CONNECTORS': [('model::Connector', {'name': 'FP_X', 'version': 3, 'props': [], 'parent': 0, 'unknown': 7})]}
    return e


class TestPure(unittest.TestCase):
    def test_base_name(self):
        self.assertEqual(C.base_name('A29N-UFCP-CENTER.001'), 'A29N-UFCP-CENTER')
        self.assertEqual(C.base_name('PNT_831'), 'PNT_831')

    def test_add_connector(self):
        e = fake_edm()
        rest = [[1, 0, 0, 0.5], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        tf = C.add_connector(e, 'A29N-UFCP-CENTER', rest, 0)
        self.assertEqual(e.nodes[tf][0], 'model::TransformNode')
        self.assertEqual(e.node_parents[tf], 0)
        t, c = e.render_items['CONNECTORS'][-1]
        self.assertEqual((t, c['name'], c['parent']), ('model::Connector', 'A29N-UFCP-CENTER', tf))
        self.assertEqual((c['version'], c['unknown']), (3, 7))      # herdou de um conector real
        self.assertEqual(C.existing_connector_names(e), {'FP_X', 'A29N-UFCP-CENTER'})

    def test_selection_rule(self):
        mk = lambda n, **p: (lambda o: (o.props.update(p), o)[1])(fakes.FakeObj(n))
        self.assertTrue(C.is_connector_object(mk('A29N-HUD-CENTER')))
        self.assertTrue(C.is_connector_object(mk('PNT_831')))
        self.assertFalse(C.is_connector_object(mk('Empty')))
        self.assertTrue(C.is_connector_object(mk('Empty', edm_connector=1)))
        self.assertFalse(C.is_connector_object(mk('PNT_1', edm_connector=0)))
        self.assertFalse(C.is_connector_object(mk('PNT_1', edm_node_index=4)))      # no importado
        self.assertFalse(C.is_connector_object(mk('PNT_1.001', edm_node_type='Connector')))
        self.assertFalse(C.is_connector_object(mk('PTR-HUD-CENTER_ERRADO')))
        self.assertFalse(C.is_connector_object(mk('PNT_2', edm_skip=1)))

    def test_sanitize_copied_props(self):
        e = fake_edm(); e.nodes[0][1]['name'] = 'PTR-RAD-CENTER001'
        cp = fakes.FakeObj('A29N-GPS-CENTER'); cp['edm_node_index'] = 0; cp['edm_node_type'] = 'TransformNode'
        mk = fakes.FakeObj('A29N-GPS-CENTER.001'); mk['edm_node_type'] = 'Connector'
        ok = fakes.FakeObj('PTR-RAD-CENTER001'); ok['edm_node_index'] = 0
        coll = fakes.FakeColl('c'); [coll.objects.link(o) for o in (cp, mk, ok)]
        ctx = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[]))
        self.assertEqual(sorted(C.sanitize(ctx, e, coll)), ['A29N-GPS-CENTER', 'A29N-GPS-CENTER.001'])
        self.assertIn('edm_node_index', ok)
        self.assertTrue(C.is_connector_object(cp))

    def test_allocate_pairs_and_dupes(self):
        e = fake_edm()
        par = fakes.FakeObj('A29N-CMFD-CENTER'); kid = fakes.FakeObj('A29N-CMFD-CENTER.001'); kid.parent = par
        pnt = fakes.FakeObj('PNT_5'); old = fakes.FakeObj('FP_X')
        coll = fakes.FakeColl('c'); [coll.objects.link(o) for o in (par, kid, pnt, old)]
        ctx = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[]))
        out = C.allocate(ctx, e, coll, 0, None, lambda o, inside: (None, None), lambda o, a, r: IDENT)
        names = sorted(n['name'] for _t, n in e.render_items['CONNECTORS'])
        self.assertEqual(names, ['A29N-CMFD-CENTER', 'FP_X', 'PNT_5'])    # 1 por par, FP_X nao duplica
        self.assertEqual(len(out['created']), 2)
        self.assertEqual(len(out['skipped']), 1)


PATH = os.environ.get('EDM_TEST_FILE', '')


@unittest.skipUnless(os.path.isfile(PATH), 'EDM_TEST_FILE not set')
class TestRoundTrip(unittest.TestCase):
    def test_new_connector_survives_write_and_reread(self):
        import tempfile
        e = R.EDMFile(PATH)
        C.add_connector(e, 'A29N-TESTE-CENTER', IDENT, 0)
        out = os.path.join(tempfile.mkdtemp(), 'x.edm')
        open(out, 'wb').write(W.EDMWriter(e).write())
        e2 = R.EDMFile(out)
        c = [n for _t, n in e2.render_items['CONNECTORS'] if n['name'] == 'A29N-TESTE-CENTER']
        self.assertEqual(len(c), 1)
        self.assertEqual(e2.nodes[c[0]['parent']][1]['name'], 'A29N-TESTE-CENTER')


if __name__ == '__main__':
    unittest.main()
