"""
Bridge from this addon's own import (a tree of Empty objects, one per EDM
transform node) to the structure the OFFICIAL Eagle Dynamics Blender-EDM-
Exporter expects as its root: a single Armature, one Bone per transform
node, with every render/connector/light object parented to the armature
"relative to one bone" (their wording — i.e. Object > Bone parenting, not
vertex-group skinning, except for genuine SkinNode meshes).

WHY THIS EXISTS: the official exporter writes the binary .edm through a
compiled module (pyedm.pyd) we cannot read or reimplement — the byte-level
serialization is closed-source. Our own export_edm.py is a best-effort
reverse-engineered writer with no guarantee DCS's loader accepts every
field the way we produced it (this is very likely the cause of exports
"not updating in game" / the model showing as invisible / falling through
the world — a malformed or incompletely-understood field silently
rejected or mishandled by the game's loader instead of erroring loudly).

The reliable path is: use OUR reader/importer (pure Python, already
validated) to get geometry + hierarchy + animation into Blender, edit
there, run THIS converter once, then export through the official addon —
which is guaranteed byte-compatible because Eagle Dynamics wrote it.

WHAT THIS SCRIPT DOES NOT KNOW: the official addon's exact internal
custom-property / RNA field names for its EDM Properties panel (Argument,
Obj.Type, Two sided, Damage arg, etc.) — that panel's source wasn't
available to inspect. So this script does NOT attempt to auto-fill those
official fields. Instead, it copies the DCS argument numbers we already
extracted from the source file into a plainly-named custom property
(`edm_dcs_argument`) on each relevant bone/object, so you can read them
off in the N-panel and type them into the official addon's own fields by
hand — verified against the real file, not guessed.
"""
import bpy
import mathutils

from . import import_edm as _imp


def fix_action_names(context, collection=None):
    """One-click fix for .blend files imported BEFORE import_edm.py started
    auto-naming Actions correctly: walks every object in `collection` (or
    the whole scene if not given) that carries one of this addon's
    edm_arg_rotation / edm_arg_position / edm_arg_visibility custom
    properties, and renames its current Action to the '<arg>_<name>'
    pattern the official exporter requires — using
    import_edm._rename_action_for_arg, the exact same logic new imports
    use, so old and new files end up named the same way.

    Returns (fixed_count, skipped) where `skipped` lists (obj.name,
    reason) for objects that had more than one of those properties with
    DIFFERENT argument numbers sharing a single Action — those need a
    human to split them (one Action can only carry one arg name) and are
    left untouched rather than guessed at.
    """
    objects = collection.all_objects if collection is not None else context.scene.objects

    fixed = 0
    skipped = []
    for obj in objects:
        args_present = [obj[k] for k in
                         ('edm_arg_rotation', 'edm_arg_position', 'edm_arg_visibility')
                         if k in obj]
        if not args_present:
            continue
        distinct = set(args_present)
        if len(distinct) > 1:
            skipped.append((obj.name, f"multiple different args on one object: {sorted(distinct)}"))
            continue

        ad = getattr(obj, 'animation_data', None)
        action = getattr(ad, 'action', None) if ad else None
        if action is None:
            continue
        before = action.name
        _imp._rename_action_for_arg(obj, args_present[0])
        if action.name != before:
            fixed += 1

    return fixed, skipped


def _find_source_collection(context):
    collection = context.collection
    if 'edm_source_path' in collection:
        return collection
    for coll in bpy.data.collections:
        if 'edm_source_path' in coll:
            return coll
    return None


def _argument_summary(obj):
    """Collects whatever DCS argument numbers we stored on this object at
    import time into one human-readable string, or None if it has none."""
    parts = []
    for key, label in (('edm_arg_rotation', 'rot'),
                        ('edm_arg_position', 'pos'),
                        ('edm_arg_visibility', 'vis')):
        if key in obj:
            parts.append(f"{label}={obj[key]}")
    return ', '.join(parts) if parts else None


def convert_to_armature(context, collection=None, delete_old_empties=False):
    """Rebuilds the transform-node empties of `collection` as bones of a
    new Armature object, re-parents every mesh/connector/light onto the
    matching bone (Object > Bone parenting, preserving current world
    position), and returns the new armature object.

    Old empties are moved into a side collection ('<name> - old empties (ref)')
    rather than deleted by default, so you can cross-check argument numbers
    and names before cleaning up — pass delete_old_empties=True to remove
    them outright once you've verified the conversion looks right.
    """
    if collection is None:
        collection = _find_source_collection(context)
    if collection is None:
        raise RuntimeError(
            "No collection with 'edm_source_path' found — import an .edm "
            "with this addon first, then run this converter on that "
            "collection.")

    src_path = collection['edm_source_path']
    edm = _imp.edmr.EDMFile(src_path)

    # Map from EDM node index -> the empty object this addon's importer
    # created for it (matched by the 'edm_node_index' custom property, not
    # by name, since node names can repeat or be empty in the source file).
    empties_by_index = {}
    for obj in collection.all_objects:
        if obj.type == 'EMPTY' and 'edm_node_index' in obj:
            empties_by_index[obj['edm_node_index']] = obj

    n = len(edm.nodes)
    if len(empties_by_index) != n:
        raise RuntimeError(
            f"Expected {n} transform-node empties (one per EDM node) but "
            f"found {len(empties_by_index)} in collection '{collection.name}'. "
            "Run this on a collection that was imported by this addon and "
            "hasn't had its empties renamed/deleted.")

    # World-space rest matrix per node, using the addon's own (validated)
    # composition — same math the importer used to place the empties, so
    # bone rest pose matches exactly what you see in the viewport now.
    local = [_imp._local_rest_matrix(t, node) for t, node in edm.nodes]
    world = [None] * n

    def resolve(i, guard=None):
        if world[i] is not None:
            return world[i]
        guard = guard or set()
        if i in guard:
            return mathutils.Matrix.Identity(4)
        guard.add(i)
        p = edm.node_parents[i]
        if p == -1 or p < 0 or p >= n:
            world[i] = local[i]
        else:
            world[i] = resolve(p, guard) @ local[i]
        return world[i]

    for i in range(n):
        resolve(i)

    # --- build the armature -------------------------------------------
    arm_data = bpy.data.armatures.new(f"{collection.name}_armature")
    arm_obj = bpy.data.objects.new(f"{collection.name}_armature", arm_data)
    collection.objects.link(arm_obj)

    context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm_data.edit_bones

    bone_names = {}
    for i, (typeName, node) in enumerate(edm.nodes):
        raw_name = node['name'] or f'{typeName}_{i}'
        # Blender bone names must be unique and <=63 bytes; disambiguate
        # collisions the same way Blender itself would.
        base = raw_name[:55]
        name = base
        suffix = 1
        while name in edit_bones:
            suffix += 1
            name = f"{base}.{suffix:03d}"
        eb = edit_bones.new(name)
        eb.matrix = world[i]
        # Bones need nonzero length; matrix-assignment already gave it a
        # minimal one along local Y, which is fine — these bones only
        # exist to carry a transform, not to be posed/weight-painted
        # (except genuine SkinNode meshes, which this script doesn't
        # attempt to rig — see docstring).
        bone_names[i] = name

    for i, parent_idx in enumerate(edm.node_parents):
        if parent_idx != -1 and 0 <= parent_idx < n:
            edit_bones[bone_names[i]].parent = edit_bones[bone_names[parent_idx]]

    bpy.ops.object.mode_set(mode='OBJECT')

    # copy DCS argument numbers onto the matching pose bone for reference
    for i, (typeName, node) in enumerate(edm.nodes):
        pbone = arm_obj.pose.bones[bone_names[i]]
        empty = empties_by_index[i]
        summary = _argument_summary(empty)
        if summary:
            pbone['edm_dcs_argument'] = summary

    # --- re-parent every render/connector/light object onto its bone ---
    reparented = 0
    for obj in list(collection.all_objects):
        if obj.type == 'EMPTY':
            continue
        parent_empty = obj.parent
        if parent_empty is None or 'edm_node_index' not in parent_empty:
            continue
        node_idx = parent_empty['edm_node_index']
        bone_name = bone_names.get(node_idx)
        if bone_name is None:
            continue

        world_matrix = obj.matrix_world.copy()
        obj.parent = arm_obj
        obj.parent_type = 'BONE'
        obj.parent_bone = bone_name
        # Blender's BONE parenting offsets by the bone's tail, not head —
        # compensate via matrix_parent_inverse so the object doesn't jump,
        # keeping exactly the position it had a moment ago.
        obj.matrix_parent_inverse = (arm_obj.matrix_world @
                                      arm_obj.pose.bones[bone_name].matrix).inverted()
        obj.matrix_world = world_matrix
        summary = _argument_summary(obj)
        if summary:
            obj['edm_dcs_argument'] = summary
        reparented += 1

    # --- move the old empties out of the way ---------------------------
    old_empties = [empties_by_index[i] for i in range(n)]
    if delete_old_empties:
        for e in old_empties:
            bpy.data.objects.remove(e, do_unlink=True)
    else:
        side_name = f"{collection.name} - old empties (ref)"
        side_coll = bpy.data.collections.get(side_name)
        if side_coll is None:
            side_coll = bpy.data.collections.new(side_name)
            context.scene.collection.children.link(side_coll)
        for e in old_empties:
            for c in list(e.users_collection):
                c.objects.unlink(e)
            side_coll.objects.link(e)

    return arm_obj, reparented
