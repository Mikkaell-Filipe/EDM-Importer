"""
Mesh -> EDM node -> material -> textures table (Text 'EDM_Mapping').

Real EDMs often have render nodes with EMPTY names and mesh names unrelated to
their texture. The importer stores the real relations in custom properties, so
this report never depends on object names.
"""
import json

import bpy


def _textures_of(mat):
    try:
        return json.loads(mat.get('edm_textures', '[]'))
    except Exception:
        return []


def build_mapping_lines(objects):
    rows = []
    for obj in objects:
        if obj.type != 'MESH' or 'edm_render_index' not in obj:
            continue
        mat = obj.material_slots[0].material if obj.material_slots and obj.material_slots[0].material else None
        texs = ', '.join(f"{t['index']}:{t['file']}" for t in _textures_of(mat)) if mat else '-'
        rows.append((obj.get('edm_node_name', ''), obj.name, obj['edm_render_index'],
                     obj.get('edm_parent_group', 0), obj.get('edm_render_name', '') or '(empty)',
                     (mat.get('edm_name') if mat else '-') or '-',
                     (mat.get('edm_base_material') if mat else '-') or '-', texs))
    rows.sort(key=lambda r: (str(r[0]), r[1]))
    head = ("EDM node (parent)", "Blender object", "render#", "group",
            "original render name", "EDM material", "shader", "textures")
    widths = [max(len(str(x)) for x in col) for col in zip(head, *rows)] if rows else [len(h) for h in head]
    fmt = ' | '.join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*head), '-+-'.join('-' * w for w in widths)]
    out += [fmt.format(*[str(c) for c in r]) for r in rows]
    return out


def write_mapping_text(objects, name='EDM_Mapping'):
    lines = build_mapping_lines(objects)
    txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    txt.clear()
    txt.write('\n'.join(lines))
    return txt, len(lines) - 2
