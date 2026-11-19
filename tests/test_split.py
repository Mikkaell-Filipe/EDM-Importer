import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
from edm_addon import edm_split, edm_newnodes as nn


class TestIslands(unittest.TestCase):
    def test_welded_by_position(self):
        # two triangles joined only through DUPLICATED vertices (UV seam) + one far away: 2 islands
        pos = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (5, 5, 5), (6, 5, 5), (5, 6, 5)]
        labels = edm_split.island_labels(pos, [(0, 1, 2), (3, 5, 4), (6, 7, 8)])
        self.assertEqual(labels[0], labels[1])
        self.assertNotEqual(labels[0], labels[2])

    def test_split_geometry_renumbers(self):
        verts = [{'position': p + (0.0,)} for p in [(0, 0, 0), (1, 0, 0), (0, 1, 0), (5, 5, 5), (6, 5, 5), (5, 6, 5)]]
        parts = edm_split.split_geometry(verts, [(0, 1, 2), (3, 4, 5)])
        self.assertEqual(len(parts), 2)
        for v, f in parts:
            self.assertEqual(f, [(0, 1, 2)])
            self.assertEqual(len(v), 3)

    def test_part_name(self):
        self.assertEqual(edm_split.part_name('Seat_mesh', 3), 'Seat_mesh_part003')
        self.assertEqual(edm_split.part_name('Seat_mesh_part003', 4), 'Seat_mesh_part004')


class TestSlots(unittest.TestCase):
    def test_parts_share_a_slot_duplicates_do_not(self):
        objs = [('a_part000', 3, 2, 'A0', True), ('a_part001', 3, 2, 'A1', True),
                ('dup', 3, 2, 'D', False), ('new', None, None, 'N', False), ('solo', 4, 0, 'S', False)]
        slots, extras = nn.plan_slots_multi(objs)
        self.assertEqual(slots[3][2], ['A0', 'A1'])
        self.assertEqual(slots[4][0], ['S'])
        self.assertCountEqual(extras, ['D', 'N'])


if __name__ == '__main__':
    unittest.main()
