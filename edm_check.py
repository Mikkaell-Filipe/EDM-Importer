"""
Pre-export checker. Looks at the scene the way the exporter will and says, in
plain language, what is going to fail or look wrong in DCS - before exporting.

Each finding: {'level': 'ERRO'|'AVISO'|'INFO', 'who': object name, 'msg': ..., 'fix': ...}
"""
import os

import bpy

from . import edm_args as A
from . import edm_textures as tex
from . import new_anim

ERROR, WARN, INFO = 'ERRO', 'AVISO', 'INFO'


def _f(level, who, msg, fix=''):
    return {'level': level, 'who': who, 'msg': msg, 'fix': fix}


def _will_export(obj, own):
    """Mirror of the exporter's discovery for NEW geometry."""
    if obj.type != 'MESH' or obj.get('edm_skip'):
        return False
    if getattr(obj, 'hide_viewport', False):
        return False
    try:
        if obj.hide_get():
            return False
    except Exception:
        pass
    if 'edm_render_index' in obj and obj.name not in own and not obj.get('edm_force_new'):
        return False
    return True


def _is_new(obj):
    return 'edm_render_index' not in obj or bool(obj.get('edm_force_new'))


def check(context, collection, texture_dir=''):
    out = []
    scene = context.scene
    own = {o.name for o in collection.all_objects} if collection is not None else set()

    # -- source file ------------------------------------------------------
    src = collection.get('edm_source_path') if collection is not None else None
    if not src:
        out.append(_f(ERROR, collection.name if collection else '-',
                      "this collection was not imported with this add-on (no 'edm_source_path')",
                      'Import the original .edm with File > Import > DCS EDM, then export that collection'))
    elif not os.path.isfile(src):
        out.append(_f(ERROR, collection.name, "the original .edm is missing: %s" % src,
                      "Put the .edm back at that path (export patches that file) or fix the collection property 'edm_source_path'"))

    # -- meshes -------------------------------------------------------------
    new_meshes = [o for o in scene.objects if _will_export(o, own) and _is_new(o)]
    for o in new_meshes:
        if not o.data.polygons:
            out.append(_f(WARN, o.name, 'mesh has no faces and will be skipped',
                          'Add faces or hide it / set edm_skip = 1'))
            continue
        if not o.data.uv_layers:
            out.append(_f(ERROR, o.name, 'no UV map: the texture would be a single smear',
                          'Edit Mode > U > Unwrap (or Smart UV Project)'))
        slots = [s.material for s in o.material_slots if s.material]
        has_forced = o.get('edm_new_material') not in (None, '') or o.get('edm_material_id') is not None
        if not slots and not has_forced:
            out.append(_f(ERROR, o.name, 'no material',
                          'EDM > "Texturas > Material pelo nome" or assign a material with an image texture'))
        elif slots and not has_forced and not tex.used_images([o]):
            out.append(_f(ERROR, o.name, "material '%s' has no image texture, so no EDM material can be made"
                          % slots[0].name,
                          'Plug an Image Texture into Base Color (or use EDM > "Material pelo nome")'))
        anc = o.parent
        under_node = False
        while anc is not None:
            if anc.type == 'EMPTY' and ('edm_node_index' in anc or new_anim.is_pivot(anc)):
                under_node = True
                break
            anc = anc.parent
        if not under_node and o.get('edm_parent_node') in (None, ''):
            out.append(_f(INFO, o.name, 'not parented to any node: it goes to the ROOT (static, always visible)',
                          'To make it move/hide with a part, parent it to that part\'s empty or to an animation pivot'))
        if any(c < 0 for c in o.scale) and not o.get('edm_flip_faces'):
            out.append(_f(INFO, o.name, 'mirrored (negative scale): faces will be flipped automatically', ''))

    # -- textures -------------------------------------------------------------
    if new_meshes:
        if not texture_dir:
            out.append(_f(WARN, '-', 'no textures folder set, so .dds files cannot be checked',
                          'Fill the "Pasta de texturas" field in the EDM panel'))
        for stem, state, detail in tex.status(new_meshes, texture_dir):
            if state == 'NO_DDS':
                out.append(_f(ERROR, stem, 'DCS needs %s.dds in the textures folder (%s)' % (stem, detail),
                              'EDM > "Texturas > Converter para DDS"'))
            elif state == 'NO_FILE':
                out.append(_f(ERROR, stem, 'texture %s not found anywhere' % stem,
                              'Put the file in the textures folder or fix the image in the material'))
            elif state == 'NO_PBR':
                out.append(_f(WARN, stem, detail, 'Provide both _NM and _RoughMet (or none)'))

    # -- animation pivots -------------------------------------------------------
    args = {}
    for p in new_anim.find_pivots(context, collection):
        kind = str(p.get('edm_anim_kind') or 'CUSTOM').upper()
        arg = new_anim.pivot_arg(p)
        if arg is None or arg < 0:
            out.append(_f(ERROR, p.name, 'no valid DCS argument',
                          "Set custom property 'edm_anim_arg' (a number like 8) or use EDM > Criar animação"))
        else:
            args.setdefault(arg, []).append(p.name)
        if not p.children:
            out.append(_f(WARN, p.name, 'the pivot has no meshes under it (nothing will move)',
                          'Parent the part\'s meshes to this pivot'))
        if kind == 'VISIBILITY':
            try:
                A.parse_ranges(str(p.get('edm_vis_ranges') or ''))
            except ValueError as e:
                out.append(_f(ERROR, p.name, "visibility ranges: %s" % e, "Example: 0.5:1  or  >0.5"))
            continue
        frames = new_anim.keyed_frames(p)
        if len(frames) < 2:
            out.append(_f(ERROR, p.name, 'needs keyframes at 2 or more frames',
                          'Pose the pivot at another frame (frame 100 = arg 0, 200 = arg 1) and press I'))
        if any(abs(s - 1.0) > 1e-4 for s in p.scale):
            out.append(_f(WARN, p.name, 'pivot has scale: only move/rotate is exported',
                          'Reset the pivot scale to 1 (Alt+S); scale the meshes instead'))
        ad = p.animation_data
        if ad and ad.action:
            m = new_anim.ARG_RE.match(ad.action.name)
            if m and arg is not None and int(m.group(1)) != arg:
                out.append(_f(WARN, p.name, "Action name says arg %s but the pivot says arg %s"
                              % (m.group(1), arg), 'Rename the Action to "<arg>_<name>"'))
    for arg, names in args.items():
        if len(names) > 1:
            out.append(_f(INFO, ', '.join(names[:3]), 'share argument %d: they will move together' % arg, ''))
    return out


def summary(findings):
    n = {ERROR: 0, WARN: 0, INFO: 0}
    for f in findings:
        n[f['level']] += 1
    return n


def to_lines(findings):
    n = summary(findings)
    lines = ['EDM check: %d erro(s), %d aviso(s), %d info' % (n[ERROR], n[WARN], n[INFO]), '']
    for lvl in (ERROR, WARN, INFO):
        for f in findings:
            if f['level'] == lvl:
                lines.append('[%s] %s: %s' % (lvl, f['who'], f['msg']))
                if f['fix']:
                    lines.append('        -> %s' % f['fix'])
    if not findings:
        lines.append('Tudo certo para exportar.')
    return lines


def write_text(lines):
    try:
        txt = bpy.data.texts.get('EDM_Check') or bpy.data.texts.new('EDM_Check')
        txt.clear()
        txt.write('\n'.join(lines))
    except Exception:
        pass
