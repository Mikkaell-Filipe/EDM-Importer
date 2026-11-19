"""Pure tests for edm_args (new animation nodes), cross-checked with edm_nodeplan."""
import math, os, sys, types, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
from edm_addon import edm_args as A, edm_math as em, edm_nodeplan as npl


def fake_edm():
    e = types.SimpleNamespace()
    root = {'name': 'Root', 'version': 0, 'props': [], 'transform': em.to_flat(em.ident())}
    e.nodes = [('model::TransformNode', root)]
    e.node_parents = [-1]
    return e


def axis_angle(axis, deg):
    h = math.radians(deg) / 2
    s = math.sin(h)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def pose(loc, quat):
    return em.mat_chain(em.translation(loc), em.rotation(quat))


class TestFrames(unittest.TestCase):
    def test_official_convention(self):
        self.assertEqual(A.arg_to_frame(0.0), 100.0)
        self.assertEqual(A.arg_to_frame(-1.0), 0.0)
        self.assertEqual(A.frame_to_arg(200.0), 1.0)
        self.assertEqual(npl.arg_to_frame(0.5), A.arg_to_frame(0.5))


class TestRotation(unittest.TestCase):
    def _build(self, rest, quats):
        poses = [(a, em.mat_chain(rest, em.rotation(q))) for a, q in quats]
        k = A.keys_from_poses(poses)
        edm = fake_edm()
        tip, w = A.add_animation(edm, {'name': 'lever', 'kind': A.ANIM, 'arg': 7, 'rest': k['rest'],
                                       'pos_keys': k['pos_keys'], 'rot_keys': k['rot_keys']}, 0)
        return edm, tip, w, poses

    def test_rotation_matches_intended_pose(self):
        rest = pose((0.3, -0.2, 1.1), axis_angle((0, 1, 0), 33))
        edm, tip, w, poses = self._build(rest, [(0.0, A.IDENTITY_Q), (1.0, axis_angle((1, 0, 0), 40))])
        t, node = edm.nodes[tip]
        self.assertEqual(t, 'model::ArgAnimationNode')
        self.assertEqual(edm.node_parents[tip], 0)
        links = npl.plan_node(t, node)
        for a, m in poses:
            self.assertLess(em.max_abs_diff(npl.evaluate_at(links, a), m), 1e-9)
        self.assertEqual(node['rotation_data'][0]['argument'], 7)
        self.assertEqual(node['position_data'], [])

    def test_symmetric_stick_minus_one_to_one(self):
        rest = pose((0, 1, 0), A.IDENTITY_Q)
        qs = [(-1.0, axis_angle((0, 0, 1), -20)), (0.0, A.IDENTITY_Q), (1.0, axis_angle((0, 0, 1), 20))]
        edm, tip, w, poses = self._build(rest, qs)
        self.assertEqual(A.keys_from_poses(poses)['ref_arg'], 0.0)
        links = npl.plan_node(*edm.nodes[tip])
        for a, m in poses:
            self.assertLess(em.max_abs_diff(npl.evaluate_at(links, a), m), 1e-9)

    def test_big_rotation_keeps_hemisphere(self):
        rest = em.ident()
        qs = [(i / 3.0, axis_angle((0, 0, 1), 100 * i)) for i in range(4)]   # 0..300 deg
        k = A.keys_from_poses([(a, em.mat_chain(rest, em.rotation(q))) for a, q in qs])
        keys = [q for _, q in k['rot_keys']]
        for p, q in zip(keys, keys[1:]):
            self.assertGreater(sum(x * y for x, y in zip(p, q)), 0)

    def test_no_motion_is_reported(self):
        rest = pose((1, 2, 3), A.IDENTITY_Q)
        k = A.keys_from_poses([(0.0, rest), (1.0, rest)])
        self.assertEqual((k['pos_keys'], k['rot_keys']), ([], []))
        edm = fake_edm()
        tip, w = A.add_animation(edm, {'name': 'x', 'arg': 3, 'rest': k['rest']}, 0)
        self.assertIsNone(tip)
        self.assertEqual(len(edm.nodes), 1)
        self.assertTrue(w)

    def test_scaled_pivot_warns(self):
        s = em.scale((2, 2, 2))
        k = A.keys_from_poses([(0.0, em.ident()), (1.0, s)])
        self.assertTrue(any('scale' in x for x in k['warnings']))


class TestMove(unittest.TestCase):
    def test_move_in_rotated_rest_frame(self):
        rest = pose((0.5, 0.0, 0.0), axis_angle((0, 0, 1), 90))
        local = [(0.0, (0, 0, 0)), (1.0, (0.0, 0.25, 0.0))]
        poses = [(a, em.mat_chain(rest, em.translation(v))) for a, v in local]
        k = A.keys_from_poses(poses)
        edm = fake_edm()
        tip, _ = A.add_animation(edm, {'name': 'slide', 'arg': 12, 'rest': k['rest'],
                                       'pos_keys': k['pos_keys'], 'rot_keys': k['rot_keys']}, 0)
        t, node = edm.nodes[tip]
        links = npl.plan_node(t, node)
        for a, m in poses:
            self.assertLess(em.max_abs_diff(npl.evaluate_at(links, a), m), 1e-9)
        self.assertEqual(node['rotation_data'], [])

    def test_move_and_rotate_chain(self):
        rest = pose((0, 0, 0), A.IDENTITY_Q)
        poses = [(0.0, rest), (1.0, pose((0.1, 0.2, 0.3), axis_angle((0, 1, 0), 30)))]
        k = A.keys_from_poses(poses)
        edm = fake_edm()
        tip, _ = A.add_animation(edm, {'name': 'both', 'arg': 1, 'rest': k['rest'],
                                       'pos_keys': k['pos_keys'], 'rot_keys': k['rot_keys']}, 0)
        t, node = edm.nodes[tip]
        # the exported node is T(dpos) R(dq) in the node frame (position applied before rotation)
        links = npl.plan_node(t, node)
        got = npl.evaluate_at(links, 1.0)
        want = em.mat_chain(rest, em.translation(node['position_data'][0]['keys'][1][1]),
                            em.rotation(node['rotation_data'][0]['keys'][1][1]))
        self.assertLess(em.max_abs_diff(got, want), 1e-9)


class TestVisibility(unittest.TestCase):
    def test_visibility_node(self):
        edm = fake_edm()
        tip, _ = A.add_animation(edm, {'name': 'cover', 'kind': A.VISIBILITY, 'arg': 3,
                                       'ranges': [(0.5, A.DBL_MAX)]}, 0)
        t, n = edm.nodes[tip]
        self.assertEqual(t, 'model::ArgVisibilityNode')
        self.assertEqual(n['visibility_data'][0], {'argument': 3, 'ranges': [(0.5, A.DBL_MAX)]})

    def test_offset_pivot_gets_a_fixed_transform_in_front(self):
        edm = fake_edm()
        rest = pose((1.0, 2.0, 3.0), A.IDENTITY_Q)
        tip, _ = A.add_animation(edm, {'name': 'cover', 'kind': A.VISIBILITY, 'arg': 3,
                                       'ranges': [(0.5, 1.0)], 'rest': rest}, 0)
        self.assertEqual([t for t, _ in edm.nodes[-2:]], ['model::TransformNode', 'model::ArgVisibilityNode'])
        self.assertEqual(edm.node_parents[tip], tip - 1)
        self.assertEqual(tuple(edm.nodes[tip - 1][1]['transform'][12:15]), (1.0, 2.0, 3.0))

    def test_identity_pivot_needs_no_transform(self):
        edm = fake_edm()
        A.add_animation(edm, {'name': 'c', 'kind': A.VISIBILITY, 'arg': 3, 'ranges': [(0.5, 1.0)]}, 0)
        self.assertEqual(len(edm.nodes), 2)

    def test_ranges_text_roundtrip(self):
        r = A.parse_ranges('0.5:1; >2; <-1')
        self.assertEqual(A.parse_ranges(A.ranges_to_text(r)), r)
        self.assertEqual(A.parse_ranges(''), [(-A.DBL_MAX, A.DBL_MAX)])
        with self.assertRaises(ValueError):
            A.parse_ranges('banana')

    def test_negative_arg_rejected(self):
        with self.assertRaises(ValueError):
            A.add_animation(fake_edm(), {'name': 'x', 'arg': -1, 'kind': A.VISIBILITY}, 0)


class TestWriteBack(unittest.TestCase):
    def test_writer_reader_roundtrip(self):
        """A new node must serialise with the existing writer without errors and
        come back identical from the reader's field layout."""
        from edm_addon import edm_writer as W
        edm = fake_edm()
        k = A.keys_from_poses([(0.0, em.ident()), (1.0, pose((0, 0.1, 0), axis_angle((1, 0, 0), 25)))])
        tip, _ = A.add_animation(edm, {'name': 'n', 'arg': 5, 'rest': k['rest'],
                                       'pos_keys': k['pos_keys'], 'rot_keys': k['rot_keys']}, 0)
        w = W.EDMWriter(edm)
        w.table = W.StringTable(); w._collecting = True; w.w = None
        w._write_node_by_type(*[edm.nodes[tip][0], edm.nodes[tip][1]])
        w._collecting = False; w.w = W.Writer()
        w._write_node_by_type(edm.nodes[tip][0], edm.nodes[tip][1])
        self.assertGreater(len(w.w.buf), 100)


if __name__ == '__main__':
    unittest.main()
