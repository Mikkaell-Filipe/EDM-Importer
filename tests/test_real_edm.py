"""Tests against a real EDM. Set EDM_TEST_FILE=/path/Cockpit_A-29B.EDM (skipped otherwise)."""
import os, sys, collections, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
from edm_addon import edm_reader as R, edm_writer as W, edm_math as em, edm_nodeplan as npl

PATH = os.environ.get('EDM_TEST_FILE', '')


@unittest.skipUnless(os.path.isfile(PATH), 'EDM_TEST_FILE not set')
class TestRealEdm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.edm = R.EDMFile(PATH)

    def test_roundtrip_identical(self):
        self.assertEqual(W.EDMWriter(self.edm).write(), open(PATH, 'rb').read())

    def test_static_nodes_exact(self):
        for t, n in self.edm.nodes:
            if n.get('position_data') or n.get('rotation_data'):
                continue
            m = em.rest_matrix(t, n)
            err = em.max_abs_diff(npl.evaluate_at(npl.plan_node(t, n)), m)
            self.assertLess(err / (1 + max(abs(x) for r in m for x in r)), 1e-6, n['name'])

    def test_every_node_is_a_legal_chain(self):
        for t, n in self.edm.nodes:
            for l in npl.plan_node(t, n):
                if l['kind'] == 'static':
                    self.assertTrue(em.columns_orthogonal(l['matrix'], 1e-6))

    def test_import_smoke(self):
        import inspect
        from unittest import mock
        from edm_addon import import_edm
        if len(inspect.signature(import_edm.import_edm).parameters) < 3:
            self.skipTest('import_edm in this build predates the 0.4.x importer (no options argument)')
        ctx = mock.MagicMock()
        coll = import_edm.import_edm(ctx, PATH, {'diag_report': False})
        objs = coll.all_objects
        empties = [o for o in objs if o.type == 'EMPTY']
        tips = [o for o in empties if 'edm_node_index' in o]
        self.assertEqual(len(tips), len(self.edm.nodes))
        self.assertTrue(any(o.name == 'Salvo' for o in tips))
        self.assertTrue(any(o.get('VISIBLE') is not None for o in tips))   # visibility nodes
        self.assertGreater(len([o for o in objs if o.type == 'MESH']), 100)

    def test_pivot_sync(self):
        from edm_addon import export_edm
        edm = R.EDMFile(PATH)
        firsts = {}
        for i, (t, n) in enumerate(edm.nodes):
            exp = npl.first_link_translation(t, n)
            if exp is not None:
                firsts[i] = fakes.FakeObj(f'n{i}')
                firsts[i].loc = tuple(exp)
        # untouched -> nothing patched
        patched, _ = export_edm._sync_node_pivots(edm, firsts, None, local_translation=lambda e: e.loc)
        self.assertEqual(patched, [])
        # move one animated node's pivot by +0.1 in X
        idx = next(i for i, (t, n) in enumerate(edm.nodes) if t in em.ARG_NODE_TYPES)
        firsts[idx].loc = (firsts[idx].loc[0] + 0.1, firsts[idx].loc[1], firsts[idx].loc[2])
        before = edm.nodes[idx][1]['tf_matrix'][12]
        patched, _ = export_edm._sync_node_pivots(edm, firsts, None, local_translation=lambda e: e.loc)
        self.assertEqual(len(patched), 1)
        self.assertAlmostEqual(edm.nodes[idx][1]['tf_matrix'][12] - before, 0.1, places=6)


if __name__ == '__main__':
    unittest.main()
