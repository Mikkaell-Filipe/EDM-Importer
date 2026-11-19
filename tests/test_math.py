"""Run:  python3 -m unittest discover -s tests   (from the add-on folder)"""
import os, random, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
from edm_addon import edm_math as em


def rand_matrix(sheared, seed=7):
    r = random.Random(seed)
    q = em.quat_normalize(tuple(r.uniform(-1, 1) for _ in range(4)))
    q2 = em.quat_normalize(tuple(r.uniform(-1, 1) for _ in range(4)))
    s = (r.uniform(0.2, 3), r.uniform(0.2, 3), r.uniform(0.2, 3))
    body = em.mat_chain(em.rotation(q), em.scale_with_orientation(q2, s) if sheared else em.scale(s))
    return em.mat_chain(em.translation((1.5, -2.0, 0.25)), body)


class TestChain(unittest.TestCase):
    def test_exact_for_sheared_and_plain(self):
        for sheared in (False, True):
            m = rand_matrix(sheared)
            chain = em.matrix_chain(m)
            self.assertLess(em.max_abs_diff(em.mat_chain(*[em.trs_to_matrix(t) for t in chain]), m), 1e-9)
            self.assertEqual(len(chain), 2 if sheared else 1)

    def test_extreme_anisotropy_and_negative_det(self):
        r = random.Random(1)
        for k in range(200):
            q = em.quat_normalize(tuple(r.uniform(-1, 1) for _ in range(4)))
            q2 = em.quat_normalize(tuple(r.uniform(-1, 1) for _ in range(4)))
            s = tuple(r.uniform(-3, 3) if k % 5 == 0 else r.uniform(0.001, 30) for _ in range(3))
            m = em.mat_chain(em.translation((1, 2, 3)), em.rotation(q), em.scale_with_orientation(q2, s))
            ch = em.matrix_chain(m)
            err = em.max_abs_diff(em.mat_chain(*[em.trs_to_matrix(t) for t in ch]), m)
            self.assertLess(err / (1 + max(abs(x) for rr in m for x in rr)), 1e-9)

    def test_flat_roundtrip(self):
        m = rand_matrix(True)
        self.assertLess(em.max_abs_diff(em.from_flat(em.to_flat(m)), m), 1e-12)

    def test_quat_matrix_roundtrip(self):
        q = em.quat_normalize((0.3, -0.5, 0.2, 0.7))
        q2 = em.quat_from_matrix3(em.rotation(q))
        if q2[3] * q[3] < 0:
            q2 = tuple(-c for c in q2)
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(q, q2)))

    def test_spec_formula_vs_legacy(self):
        node = {'tf_matrix': em.to_flat(em.translation((1, 2, 3))), 'tf_position': (0.5, 0, 0),
                'tf_quat1': (0, 0, 0.3826834, 0.9238795), 'tf_quat2': (0, 0, 0.3826834, 0.9238795),
                'tf_scale': (2.0, 1.0, 1.0)}
        got = em.rest_matrix('model::ArgRotationNode', node)
        want = em.mat_chain(em.translation((1.5, 2, 3)), em.rotation(node['tf_quat1']),
                            em.rotation(node['tf_quat2']), em.scale(node['tf_scale']),
                            em.rotation(em.quat_conj(node['tf_quat2'])))
        self.assertLess(em.max_abs_diff(got, want), 1e-12)
        self.assertGreater(em.max_abs_diff(got, em.legacy_rest_matrix('model::ArgRotationNode', node)), 1e-3)


if __name__ == '__main__':
    unittest.main()
