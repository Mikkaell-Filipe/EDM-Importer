"""Minimal fake bpy/mathutils so the add-on's own logic (node chains, naming, export
planning) runs outside Blender. NOT a Blender emulator: mesh data is a MagicMock."""
import os
import sys
import types
from unittest import mock

ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeMatrix:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]


class FakeObj:
    def __init__(self, name, data=None):
        self.name = name
        self.data = data
        self.type = 'EMPTY' if data is None else 'MESH'
        self.parent = None
        self.props = {}
        self.location = (0.0, 0.0, 0.0)
        self.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        self.scale = (1.0, 1.0, 1.0)
        self.animation_data = types.SimpleNamespace(action=None)
        self.keys_inserted = []
        self.drivers = []

    def __setitem__(self, k, v): self.props[k] = v
    def __getitem__(self, k): return self.props[k]
    def __delitem__(self, k): del self.props[k]
    def __contains__(self, k): return k in self.props
    def get(self, k, d=None): return self.props.get(k, d)
    def keys(self): return list(self.props.keys())
    def keyframe_insert(self, data_path, frame): self.keys_inserted.append((data_path, frame))
    def driver_add(self, prop):
        fc = mock.MagicMock()
        self.drivers.append((prop, fc))
        return fc


class FakeColl:
    def __init__(self, name):
        self.name = name
        self.props = {}
        self._objs = []
        self.objects = types.SimpleNamespace(link=self._objs.append)

    @property
    def all_objects(self): return list(self._objs)
    def __setitem__(self, k, v): self.props[k] = v
    def __getitem__(self, k): return self.props[k]
    def __contains__(self, k): return k in self.props
    def get(self, k, d=None): return self.props.get(k, d)


def install():
    """Returns the package name under which the add-on modules can be imported."""
    bpy = mock.MagicMock(name='bpy')
    bpy.data.objects.new = FakeObj
    bpy.data.collections.new = FakeColl
    bpy.data.texts.get = lambda n: None
    bpy.context.preferences = types.SimpleNamespace(
        edit=types.SimpleNamespace(keyframe_new_interpolation_type='BEZIER'))
    bpy.types.Operator = object
    bpy.types.Panel = object
    mu = types.ModuleType('mathutils')
    mu.Matrix = FakeMatrix
    sys.modules.update({'bpy': bpy, 'mathutils': mu, 'bpy.props': mock.MagicMock(),
                        'bpy_extras': mock.MagicMock(), 'bpy_extras.io_utils': mock.MagicMock()})
    pkg = types.ModuleType('edm_addon')
    pkg.__path__ = [ADDON_DIR]
    sys.modules['edm_addon'] = pkg
    return 'edm_addon'
