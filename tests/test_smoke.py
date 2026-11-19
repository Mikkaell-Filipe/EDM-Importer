"""Import-time smoke test of the Blender-facing modules (bpy is a mock)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()


class TestImports(unittest.TestCase):
    def test_modules_import(self):
        from edm_addon import edm_ui, edm_check, edm_textures, new_anim, edm_dds, edm_args
        self.assertTrue(hasattr(edm_ui, 'register'))

    def test_check_lines(self):
        from edm_addon import edm_check as C
        f = [C._f(C.ERROR, 'a', 'bad', 'fix it'), C._f(C.INFO, 'b', 'fyi')]
        lines = C.to_lines(f)
        self.assertIn('1 erro(s)', lines[0])
        self.assertEqual(C.summary(f)[C.ERROR], 1)
        self.assertIn('Tudo certo', C.to_lines([])[-1])

    def test_texture_name_helpers(self):
        from edm_addon import edm_textures as T
        self.assertEqual(T.clean('Panel_L_mesh.001'), 'panel_l')
        self.assertTrue(T.has_file({'a.dds': 'a.DDS'}, 'a', ('.dds',)))


if __name__ == '__main__':
    unittest.main()
