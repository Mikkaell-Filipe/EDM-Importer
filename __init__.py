bl_info = {
    "name": "DCS World EDM format (.edm) [custom]",
    "author": "community reverse-engineering effort",
    "version": (0, 7, 2),
    "blender": (4, 0, 0),
    "location": "File > Import/Export > DCS EDM (.edm)",
    "description": (
        "Import/export DCS World .edm models with correct animation, "
        "orientation and hierarchy — no more scattered pieces."
    ),
    "warning": (
        "Export patches the source .edm it was imported from: it re-encodes "
        "geometry, ADDS new meshes (any collection; new textures become new "
        "EDM materials), NEW animations (EDM panel > Criar animação) and removes "
        "deleted parts. Animation and shaders of existing parts round-trip "
        "unchanged. Not yet verified inside DCS."
    ),
    "category": "Import-Export",
}

import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper


def _reload_submodules():
    """After installing a new zip over an old one, Blender reloads only this
    __init__ and keeps the OLD helper modules in memory (symptom: 'got an
    unexpected keyword argument'). Reload them, dependencies first."""
    import importlib
    import sys
    order = ('edm_math', 'edm_reader', 'edm_writer', 'edm_split', 'edm_newnodes',
             'edm_nodeplan', 'edm_report', 'edm_args', 'new_anim', 'edm_dds',
             'edm_textures', 'edm_check', 'edm_connectors', 'export_edm')
    for name in order:
        mod = sys.modules.get(__package__ + '.' + name)
        if mod is not None:
            try:
                importlib.reload(mod)
            except Exception as e:           # never block the export on this
                print("[EDM] could not reload %s: %s" % (name, e))


class IMPORT_OT_edm(bpy.types.Operator, ImportHelper):
    """Import a DCS World .edm model (geometry, hierarchy, animation)"""
    bl_idname = "import_scene.edm"
    bl_label = "Import EDM"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".edm"
    filter_glob: StringProperty(default="*.edm;*.EDM", options={'HIDDEN'})

    def execute(self, context):
        from . import import_edm
        try:
            collection = import_edm.import_edm(context, self.filepath)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"EDM import failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Imported {collection.name} ({len(collection.all_objects)} objects)")
        return {'FINISHED'}


class EXPORT_OT_edm(bpy.types.Operator, ExportHelper):
    """Export the active collection back to a DCS World .edm model.
    Only works on a collection originally imported with this addon —
    it patches mesh geometry into the source file's structure rather
    than building a new file from scratch."""
    bl_idname = "export_scene.edm"
    bl_label = "Export EDM"
    bl_options = {'PRESET'}

    filename_ext = ".edm"
    filter_glob: StringProperty(default="*.edm;*.EDM", options={'HIDDEN'})

    confirm_bulk_delete: BoolProperty(
        name="Confirm removing many parts",
        description=(
            "Export normally refuses if more than a handful of mesh parts "
            "would be removed at once (no matching object left in the "
            "collection) — that's usually a sign of exporting the wrong "
            "collection, not a deliberate edit. Enable this only after "
            "checking the reported list of parts and confirming that's "
            "really what you want to delete"),
        default=False,
    )

    write_node_pivots: BoolProperty(
        name="Write node pivots",
        description=(
            "Write the position of the node Empties (at frame 0) into the "
            "file, so moving an animated part's Empty in Blender moves its "
            "pivot in DCS too. Only translation is written"),
        default=True,
    )

    include_other_collections: BoolProperty(
        name="Include meshes from other collections",
        description=(
            "New visible meshes in ANY collection of the scene are exported "
            "(not only the imported collection). Hidden meshes and meshes "
            "with custom property 'edm_skip' are always left out. Everything "
            "that was added or ignored is listed in the Text 'EDM_Export_Report'"),
        default=True,
    )

    include_foreign: BoolProperty(
        name="Include meshes from other imported models",
        description=(
            "Meshes imported from ANOTHER .edm (e.g. a pilot copied from a "
            "different aircraft) are exported as NEW geometry. Their material "
            "indices/parent nodes from the other file are ignored; the "
            "texture image decides the material. Hidden meshes are still "
            "left out"),
        default=False,
    )

    texture_dir: StringProperty(
        name="Texture folder",
        description=(
            "Paste the path of the mod's textures folder here (the folder "
            "button cannot open inside this dialog). Optional: a custom "
            "property 'edm_texture_dir' on the imported collection works as "
            "default. New objects whose texture has "
            "<name>_NM and <name>_RoughMet files there get a 3-texture "
            "material (diffuse + normal + roughness/metal). Optional: the "
            "folder of the Blender image and of the .blend are searched too"),
        default="",
    )

    create_materials: BoolProperty(
        name="Create EDM materials for new textures",
        description=(
            "A new mesh whose Blender material uses an image that no EDM "
            "material has yet gets a NEW EDM material (a clone of a simple "
            "def_material) pointing to that image name. Put <image name>.dds "
            "in the mod's textures folder"),
        default=True,
    )

    fallback_to_root: BoolProperty(
        name="Attach loose objects to root",
        description=(
            "A new mesh that is not parented to any node empty is attached "
            "to the model's root node (static, always visible). Turn off to "
            "get an error instead and parent it yourself"),
        default=True,
    )

    def execute(self, context):
        from . import export_edm
        _reload_submodules()
        collection = context.collection
        # Prefer the active object's collection if it carries the source path
        if 'edm_source_path' not in collection:
            for coll in bpy.data.collections:
                if 'edm_source_path' in coll:
                    collection = coll
                    break
        try:
            edited, deleted, added = export_edm.export_edm(
                context, self.filepath, collection,
                confirm_bulk_delete=self.confirm_bulk_delete,
                write_node_pivots=self.write_node_pivots,
                include_other_collections=self.include_other_collections,
                create_materials=self.create_materials,
                fallback_to_root=self.fallback_to_root,
                include_foreign=self.include_foreign,
                texture_dir=(self.texture_dir or str(collection.get('edm_texture_dir', '') or '')
                             or bpy.path.abspath(getattr(context.scene, 'edm_texture_dir', '') or '')))
        except export_edm.ExportSafetyError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"EDM export failed: {e}")
            return {'CANCELLED'}
        rep = export_edm.last_report
        msg = f"Exported EDM — {edited} render node(s) updated"
        if added:
            msg += f", {added} NEW render node(s) added"
        if deleted:
            msg += f", {deleted} emptied/removed (no object left in Blender)"
        msg += " — see Text 'EDM_Export_Report'"
        self.report({'INFO'}, msg)
        if rep.get('anim'):
            self.report({'INFO'}, f"{len(rep['anim'])} NEW animation node(s) written")
        if rep.get('created_materials'):
            self.report({'WARNING'}, f"{len(rep['created_materials'])} new EDM material(s) created: "
                        "copy the textures (<name>.dds) into the mod's textures folder")
        if rep.get('rooted'):
            self.report({'WARNING'}, f"{len(rep['rooted'])} object(s) attached to the ROOT node "
                        "(static): " + ', '.join(rep['rooted'][:6]))
        if rep.get('ignored'):
            self.report({'WARNING'}, f"{len(rep['ignored'])} object(s) ignored: "
                        + ', '.join(list(rep['ignored'])[:6]))
        return {'FINISHED'}


class OBJECT_OT_edm_prepare_official_export(bpy.types.Operator):
    """Convert this addon's imported empty hierarchy into an Armature+bones
    structure, so the result can be exported through Eagle Dynamics' own
    official Blender-EDM-Exporter addon instead of this addon's built-in
    (reverse-engineered, unverified-by-the-game) exporter. Install the
    official addon separately from https://github.com/EagleDynamics/Blender-EDM-Exporter
    — this operator only restructures the scene, it does not export."""
    bl_idname = "object.edm_prepare_official_export"
    bl_label = "EDM: Convert to Armature for Official Exporter"
    bl_options = {'UNDO'}

    delete_old_empties: BoolProperty(
        name="Delete old empties",
        description=(
            "Remove the original transform-node empties after conversion "
            "instead of moving them to a side collection for reference"),
        default=False,
    )

    def execute(self, context):
        from . import prepare_official_export
        try:
            arm_obj, count = prepare_official_export.convert_to_armature(
                context, delete_old_empties=self.delete_old_empties)
        except Exception as e:
            self.report({'ERROR'}, f"Conversion failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'},
                     f"Built armature '{arm_obj.name}' — {count} object(s) "
                     "re-parented to bones. Now open the official EDM "
                     "Export panel (N-panel) to set Argument/Obj.Type per "
                     "object (see each object's 'edm_dcs_argument' custom "
                     "property for the numbers extracted from the source "
                     "file) before exporting.")
        return {'FINISHED'}


class OBJECT_OT_edm_fix_action_names(bpy.types.Operator):
    """One-click fix for files imported before this addon started naming
    Actions correctly: renames every animated object's Action to the
    '<arg>_<name>' pattern the official Eagle Dynamics exporter requires,
    using the edm_arg_rotation/edm_arg_position/edm_arg_visibility values
    this addon already stored on each object at import time. Fixes errors
    like "Could not extract arg from action" without needing to re-import
    or manually rename anything by hand."""
    bl_idname = "object.edm_fix_action_names"
    bl_label = "EDM: Fix Action Names for Official Exporter"
    bl_options = {'UNDO'}

    def execute(self, context):
        from . import prepare_official_export
        collection = context.collection
        if collection is None or 'edm_source_path' not in collection:
            for coll in bpy.data.collections:
                if 'edm_source_path' in coll:
                    collection = coll
                    break
        try:
            fixed, skipped = prepare_official_export.fix_action_names(context, collection)
        except Exception as e:
            self.report({'ERROR'}, f"Fix failed: {e}")
            return {'CANCELLED'}
        msg = f"Renamed {fixed} action(s)."
        if skipped:
            msg += f" {len(skipped)} object(s) need manual attention (see console)."
            print("EDM Fix Action Names — needs manual attention:")
            for name, reason in skipped:
                print(f"  {name}: {reason}")
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def menu_import(self, context):
    self.layout.operator(IMPORT_OT_edm.bl_idname, text="DCS EDM (.edm)")


def menu_export(self, context):
    self.layout.operator(EXPORT_OT_edm.bl_idname, text="DCS EDM (.edm)")


def menu_object(self, context):
    self.layout.operator(OBJECT_OT_edm_prepare_official_export.bl_idname)
    self.layout.operator(OBJECT_OT_edm_fix_action_names.bl_idname)


classes = (IMPORT_OT_edm, EXPORT_OT_edm, OBJECT_OT_edm_prepare_official_export,
           OBJECT_OT_edm_fix_action_names)


def register():
    from . import edm_ui
    edm_ui.register()
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)
    bpy.types.VIEW3D_MT_object.append(menu_object)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.VIEW3D_MT_object.remove(menu_object)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    from . import edm_ui
    edm_ui.unregister()


if __name__ == "__main__":
    register()