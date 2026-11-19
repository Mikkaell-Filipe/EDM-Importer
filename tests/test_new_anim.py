"""new_anim.allocate() driven by a tiny numpy fake of the Blender scene
(real mathutils/bpy are not available outside Blender)."""
import math, os, sys, types, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
import numpy as np
from edm_addon import new_anim, edm_args as A, edm_math as em, edm_nodeplan as npl


class M:
    """numpy-backed stand-in for mathutils.Matrix (only what new_anim uses)."""
    def __init__(self, a): self.a = np.array(a, dtype=float)
    def __matmul__(self, o): return M(self.a @ o.a)
    def __getitem__(self, i): return self.a[i]
    def inverted_safe(self): return M(np.linalg.inv(self.a))
    @staticmethod
    def Identity(n): return M(np.eye(n))
    @property
    def translation(self): return self.a[:3, 3]


class Obj:
    def __init__(self, name, typ='EMPTY', parent=None):
        self.name, self.type, self.parent = name, typ, parent
        self.props = {}
        self.matrix_parent_inverse = M.Identity(4)
        self.matrix_basis = M.Identity(4)
        self.animation_data = None
        self.children = []
        if parent is not None:
            parent.children.append(self)
    def get(self, k, d=None): return self.props.get(k, d)
    def __contains__(self, k): return k in self.props
    def __getitem__(self, k): return self.props[k]
    def __setitem__(self, k, v): self.props[k] = v
    @property
    def matrix_world(self):
        pw = self.parent.matrix_world if self.parent is not None else M.Identity(4)
        return pw @ self.matrix_parent_inverse @ self.matrix_basis


class Scene:
    def __init__(self, objs, pivot_pose):
        self.objects = objs
        self.pivot_pose = pivot_pose
        self.frame_current = 100
        self.frame_subframe = 0.0
    def frame_set(self, f, subframe=0.0):
        self.frame_current = f
        for o in self.objects:
            if o.name in self.pivot_pose:
                o.matrix_basis = M(self.pivot_pose[o.name](f + subframe))


def pose(loc, quat):
    return np.array(em.mat_chain(em.translation(loc), em.rotation(quat)))


def fake_edm():
    e = types.SimpleNamespace()
    root = {'name': 'Root', 'version': 0, 'props': [], 'transform': em.to_flat(em.ident())}
    node = {'name': 'Stick', 'version': 0, 'props': [], 'transform': em.to_flat(em.translation((1, 0, 0)))}
    e.nodes = [('model::TransformNode', root), ('model::TransformNode', node)]
    e.node_parents = [-1, 0]
    return e


def fcurve(path, frames):
    return types.SimpleNamespace(data_path=path, array_index=0,
                                 keyframe_points=[types.SimpleNamespace(co=(f, 0.0)) for f in frames])


class TestAllocate(unittest.TestCase):
    def setUp(self):
        new_anim.mathutils = types.SimpleNamespace(Matrix=M)
        new_anim.clear()

    def _scene(self, pivot_fn, frames, name='lever'):
        root = Obj('Root'); root['edm_node_index'] = 0
        stick = Obj('Stick', parent=root); stick['edm_node_index'] = 1
        stick.matrix_basis = M(pose((1, 0, 0), A.IDENTITY_Q))
        piv = Obj(name, parent=stick)
        piv.matrix_parent_inverse = M(np.linalg.inv(pose((1, 0, 0), A.IDENTITY_Q)))
        piv['edm_new_anim'] = 1; piv['edm_anim_arg'] = 9; piv['edm_anim_kind'] = 'ROTATE'
        action = types.SimpleNamespace(name='9_lever', fcurves=[fcurve('rotation_quaternion', frames)])
        piv.animation_data = types.SimpleNamespace(action=action)
        scene = Scene([root, stick, piv], {name: pivot_fn})
        coll = types.SimpleNamespace(all_objects=[root, stick, piv], name='X')
        ctx = types.SimpleNamespace(scene=scene)
        return ctx, coll, root, stick, piv

    def test_rotating_pivot_under_node(self):
        def fn(f):
            ang = (f - 100) / 100.0 * 90.0
            h = math.radians(ang) / 2
            return pose((0.2, 0.1, 0), (0, 0, math.sin(h), math.cos(h)))
        ctx, coll, root, stick, piv = self._scene(fn, [100.0, 200.0])
        edm = fake_edm()
        out = new_anim.allocate(ctx, edm, coll, 0, root)
        self.assertEqual(out['warnings'], [])
        self.assertEqual(len(edm.nodes), 3)
        t, node = edm.nodes[2]
        self.assertEqual((t, edm.node_parents[2], new_anim.NEW_NODE_INDEX['lever']),
                         ('model::ArgAnimationNode', 1, 2))
        self.assertEqual(node['rotation_data'][0]['argument'], 9)
        links = npl.plan_node(t, node)
        for f in (100.0, 200.0):
            # pivot pose relative to 'Stick' = parent_inverse @ basis
            want = np.linalg.inv(pose((1, 0, 0), A.IDENTITY_Q)) @ fn(f)
            got = npl.evaluate_at(links, A.frame_to_arg(f))
            self.assertLess(np.abs(np.array(got) - want).max(), 1e-9)
        self.assertEqual(ctx.scene.frame_current, 100)       # frame restored
        self.assertTrue(out['created'])
        new_anim.clear()
        self.assertEqual(new_anim.NEW_NODE_INDEX, {})

    def test_pivot_without_keys_is_reported_not_fatal(self):
        ctx, coll, root, stick, piv = self._scene(lambda f: np.eye(4), [])
        edm = fake_edm()
        out = new_anim.allocate(ctx, edm, coll, 0, root)
        self.assertEqual(len(edm.nodes), 2)
        self.assertTrue(any('no keyframes' in w for w in out['warnings']))

    def test_pivot_without_arg_is_reported(self):
        ctx, coll, root, stick, piv = self._scene(lambda f: np.eye(4), [100.0, 200.0])
        del piv.props['edm_anim_arg']
        piv.animation_data.action.name = 'lever'
        out = new_anim.allocate(ctx, fake_edm(), coll, 0, root)
        self.assertTrue(any('argument' in w for w in out['warnings']))

    def test_visibility_pivot(self):
        ctx, coll, root, stick, piv = self._scene(lambda f: np.eye(4), [])
        piv['edm_anim_kind'] = 'VISIBILITY'; piv['edm_vis_ranges'] = '0.5:1'
        piv.animation_data = None
        edm = fake_edm()
        out = new_anim.allocate(ctx, edm, coll, 0, root)
        self.assertEqual(out['warnings'], [])
        self.assertEqual(edm.nodes[-1][0], 'model::ArgVisibilityNode')
        self.assertEqual(edm.nodes[-1][1]['visibility_data'][0]['ranges'], [(0.5, 1.0)])

    def test_nested_pivots_chain(self):
        def fn(f): return pose((0, 0, 0), A.IDENTITY_Q) + 0 if f < 150 else pose((0.1, 0, 0), A.IDENTITY_Q)
        ctx, coll, root, stick, piv = self._scene(fn, [100.0, 200.0])
        child = Obj('knob', parent=piv)
        child['edm_new_anim'] = 1; child['edm_anim_arg'] = 10; child['edm_anim_kind'] = 'MOVE'
        child.animation_data = types.SimpleNamespace(
            action=types.SimpleNamespace(name='10_knob', fcurves=[fcurve('location', [100.0, 200.0])]))
        ctx.scene.objects.append(child); coll.all_objects.append(child)
        ctx.scene.pivot_pose['knob'] = lambda f: pose((0, 0.05 * (f - 100) / 100.0, 0), A.IDENTITY_Q)
        edm = fake_edm()
        new_anim.allocate(ctx, edm, coll, 0, root)
        self.assertEqual(new_anim.NEW_NODE_INDEX['knob'], new_anim.NEW_NODE_INDEX['lever'] + 1)
        self.assertEqual(edm.node_parents[new_anim.NEW_NODE_INDEX['knob']], new_anim.NEW_NODE_INDEX['lever'])


if __name__ == '__main__':
    unittest.main()
