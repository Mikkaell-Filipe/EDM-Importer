"""
"EDM Fácil": sidebar panel (3D View > N > aba EDM) + operators.
Everything here is a thin layer over new_anim / edm_textures / edm_check / edm_split.
"""
import os

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)

from . import edm_check
from . import edm_textures as tex
from . import new_anim


def edm_collection(context):
    """The imported collection (has 'edm_source_path'): active one first."""
    c = context.collection
    if c is not None and 'edm_source_path' in c:
        return c
    obj = context.active_object
    if obj is not None:
        for c in obj.users_collection:
            if 'edm_source_path' in c:
                return c
    for c in bpy.data.collections:
        if 'edm_source_path' in c:
            return c
    return c if c is not None else context.scene.collection


def _selected_meshes(context):
    return [o for o in context.selected_objects if o.type == 'MESH']


def _popup(context, title, lines, icon='INFO'):
    def draw(self, _ctx):
        for ln in lines[:14]:
            self.layout.label(text=ln)
        if len(lines) > 14:
            self.layout.label(text='... (+%d) veja o Text "EDM_Check"' % (len(lines) - 14))
    context.window_manager.popup_menu(draw, title=title, icon=icon)


# --------------------------------------------------------------------------
# Animation
# --------------------------------------------------------------------------

class EDM_OT_anim_create(bpy.types.Operator):
    """Cria um pivô e já anima as peças selecionadas (alavanca, botão, trem de pouso, tampa...)"""
    bl_idname = 'edm.anim_create'
    bl_label = 'Criar animação'
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name='Tipo', items=[
        ('ROTATE', 'Girar', 'A peça gira em torno de um eixo (alavanca, porta, chave)'),
        ('MOVE', 'Mover', 'A peça desliza (botão que afunda, gaveta, trem de pouso telescópico)'),
        ('VISIBILITY', 'Mostrar / Esconder', 'A peça aparece ou some conforme o argumento')],
        default='ROTATE')
    anim_name: StringProperty(name='Nome', default='anim')
    arg: IntProperty(name='Argumento DCS', default=100, min=0, soft_max=999,
                     description='Número do argumento (o mesmo que o cockpit/aeronave usa)')
    axis: EnumProperty(name='Eixo', items=[('X', 'X', ''), ('Y', 'Y', ''), ('Z', 'Z', '')], default='X',
                       description='Eixo do pivô em volta do qual gira / ao longo do qual desliza')
    amount: FloatProperty(name='Quantidade', default=45.0,
                          description='Graus (Girar) ou unidades do Blender = metros (Mover). Negativo inverte')
    arg_from: FloatProperty(name='Argumento inicial', default=0.0, min=-1.0, max=1.0)
    arg_to: FloatProperty(name='Argumento final', default=1.0, min=-1.0, max=1.0)
    pivot_at: EnumProperty(name='Pivô em', items=[
        ('ACTIVE', 'Origem do objeto ativo', 'Use Set Origin antes para escolher o ponto'),
        ('CURSOR', 'Cursor 3D', 'Posicione o cursor 3D na dobradiça/eixo antes'),
        ('CENTER', 'Centro da seleção', '')], default='ACTIVE')
    orient: EnumProperty(name='Eixos', items=[
        ('WORLD', 'Do mundo', 'X/Y/Z do Blender'),
        ('ACTIVE', 'Do objeto ativo', 'Segue a rotação do objeto ativo')], default='WORLD')
    ranges: StringProperty(name='Visível quando', default='>0.5',
                           description="Faixas do argumento, ex.: '0.5:1' ou '>0.5' ou '0:0.3; 0.7:1'")

    @classmethod
    def poll(cls, context):
        return bool(_selected_meshes(context)) and context.mode == 'OBJECT'

    def invoke(self, context, event):
        act = context.active_object
        if act is not None and self.anim_name == 'anim':
            self.anim_name = act.name.replace('.', '_')
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, 'kind')
        col.prop(self, 'anim_name')
        col.prop(self, 'arg')
        if self.kind == 'VISIBILITY':
            col.prop(self, 'ranges')
        else:
            col.prop(self, 'axis')
            col.prop(self, 'amount')
            row = col.row(align=True)
            row.prop(self, 'arg_from')
            row.prop(self, 'arg_to')
            col.prop(self, 'orient')
        col.prop(self, 'pivot_at')

    def execute(self, context):
        objs = _selected_meshes(context)
        name = self.anim_name.strip() or 'anim'
        try:
            piv = new_anim.create_animation(
                context, objs, self.kind, name, self.arg, axis=self.axis, amount=self.amount,
                arg_from=self.arg_from, arg_to=self.arg_to, pivot_at=self.pivot_at,
                orient=self.orient, ranges=self.ranges)
        except Exception as e:
            self.report({'ERROR'}, 'Não foi possível criar a animação: %s' % e)
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        piv.select_set(True)
        context.view_layer.objects.active = piv
        if self.kind == 'VISIBILITY':
            self.report({'INFO'}, "Pivô '%s' criado (arg %d). Aparece quando o argumento estiver em: %s"
                        % (piv.name, self.arg, self.ranges))
        else:
            self.report({'INFO'}, "Pivô '%s' criado (arg %d). Use 'Testar argumento' ou arraste a "
                        "timeline (frame 100 = arg 0, 200 = arg 1)." % (piv.name, self.arg))
        return {'FINISHED'}


class EDM_OT_anim_remove(bpy.types.Operator):
    """Apaga o pivô selecionado e solta as peças onde estão (sem a animação)"""
    bl_idname = 'edm.anim_remove'
    bl_label = 'Remover animação'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return new_anim.is_pivot(context.active_object)

    def execute(self, context):
        piv = context.active_object
        n = new_anim.remove_animation(context, piv)
        self.report({'INFO'}, 'Pivô removido; %d peça(s) soltas.' % n)
        return {'FINISHED'}


def _update_preview(self, context):
    new_anim.preview_arg(context, self.edm_preview_arg)


class EDM_OT_split_parts(bpy.types.Operator):
    """Separa o objeto selecionado em peças soltas (para animar cada botão/alavanca)"""
    bl_idname = 'edm.split_parts'
    bl_label = 'Separar em peças soltas'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_selected_meshes(context)) and context.mode == 'OBJECT'

    def execute(self, context):
        from . import edm_split
        total = 0
        for o in _selected_meshes(context):
            try:
                total += len(edm_split.split_object(o))
            except Exception as e:
                self.report({'WARNING'}, "'%s': %s" % (o.name, e))
        self.report({'INFO'}, '%d peça(s) resultantes. Cada uma guarda o centro/tamanho em edm_part_info.' % total)
        return {'FINISHED'}


# --------------------------------------------------------------------------
# Check / textures
# --------------------------------------------------------------------------

class EDM_OT_check(bpy.types.Operator):
    """Verifica a cena antes de exportar (texturas, UV, animações) e explica como corrigir"""
    bl_idname = 'edm.check'
    bl_label = 'Verificar antes de exportar'

    def execute(self, context):
        coll = edm_collection(context)
        findings = edm_check.check(context, coll, tex.texture_dir(context, coll))
        lines = edm_check.to_lines(findings)
        edm_check.write_text(lines)
        n = edm_check.summary(findings)
        for line in lines:
            print('[EDM check]', line)
        icon = 'ERROR' if (n['ERRO'] or n['AVISO']) else 'INFO'
        _popup(context, lines[0], lines[2:], icon)
        self.report({'ERROR' if n['ERRO'] else 'INFO'},
                    "%d erro(s), %d aviso(s). Detalhes no Text 'EDM_Check'." % (n['ERRO'], n['AVISO']))
        return {'FINISHED'}


class EDM_OT_tex_convert(bpy.types.Operator):
    """Converte as texturas usadas pelas peças novas (e _NM/_RoughMet carregadas) para .dds na pasta de texturas"""
    bl_idname = 'edm.tex_convert'
    bl_label = 'Converter para DDS'
    bl_options = {'REGISTER'}

    fmt: EnumProperty(name='Formato', items=[
        ('AUTO', 'Automático', 'DXT1 se sem transparência, senão DXT5'),
        ('DXT1', 'DXT1', ''), ('DXT5', 'DXT5', ''), ('BGRA8', 'Sem compressão', 'Maior, sem perdas')],
        default='AUTO')
    mipmaps: BoolProperty(name='Mipmaps', default=True)
    all_images: BoolProperty(name='Todas as imagens do .blend', default=False,
                             description='Em vez de só as usadas pelos meshes novos visíveis')
    overwrite: BoolProperty(name='Sobrescrever .dds existentes', default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        coll = edm_collection(context)
        directory = tex.texture_dir(context, coll)
        if not directory:
            self.report({'ERROR'}, "Defina a 'Pasta de texturas' no painel EDM primeiro.")
            return {'CANCELLED'}
        if self.all_images:
            imgs = {tex.image_stem(i): i for i in bpy.data.images
                    if i.size[0] > 0 and i.type == 'IMAGE' and tex.image_stem(i)}
        else:
            meshes = [o for o in context.scene.objects if o.type == 'MESH' and not o.hide_get()
                      and not o.get('edm_skip')]
            imgs = dict(tex.used_images(meshes))
            for s, i in tex.companion_images().items():
                for base in list(imgs):
                    if s.startswith(base.lower()):
                        imgs[tex.image_stem(i)] = i
        done, fail = [], []
        for stem, img in sorted(imgs.items()):
            path = os.path.join(directory, stem + '.dds')
            if os.path.isfile(path) and not self.overwrite:
                continue
            try:
                p, used = tex.write_dds(img, directory, stem, self.fmt, self.mipmaps)
                done.append('%s (%s)' % (stem, used))
            except Exception as e:
                fail.append('%s: %s' % (stem, e))
        for f in fail:
            print('[EDM dds]', f)
        msg = '%d textura(s) gravada(s) em %s' % (len(done), directory)
        if fail:
            msg += ' | %d falha(s), veja o console' % len(fail)
        self.report({'WARNING' if fail else 'INFO'}, msg)
        return {'FINISHED'}


class EDM_OT_tex_auto(bpy.types.Operator):
    """Cria o material das peças selecionadas a partir do arquivo de textura com o mesmo nome do mesh/material"""
    bl_idname = 'edm.tex_auto'
    bl_label = 'Material pelo nome'
    bl_options = {'REGISTER', 'UNDO'}

    only_missing: BoolProperty(name='Só quem não tem textura', default=True)

    @classmethod
    def poll(cls, context):
        return bool(_selected_meshes(context))

    def execute(self, context):
        coll = edm_collection(context)
        directory = tex.texture_dir(context, coll)
        if not directory:
            self.report({'ERROR'}, "Defina a 'Pasta de texturas' no painel EDM primeiro.")
            return {'CANCELLED'}
        done, missing = tex.auto_assign(_selected_meshes(context), directory, self.only_missing)
        if missing:
            print('[EDM texturas] sem arquivo correspondente:', ', '.join(missing))
        self.report({'INFO' if done else 'WARNING'},
                    '%d mesh(es) com material novo; %d sem arquivo de textura de mesmo nome.'
                    % (len(done), len(missing)))
        return {'FINISHED'}


# --------------------------------------------------------------------------
# Panel
# --------------------------------------------------------------------------

class VIEW3D_PT_edm_easy(bpy.types.Panel):
    bl_label = 'EDM Fácil'
    bl_idname = 'VIEW3D_PT_edm_easy'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'EDM'

    def draw(self, context):
        lay = self.layout
        scene = context.scene
        obj = context.active_object

        box = lay.box()
        box.label(text='1. Animar uma peça', icon='ANIM')
        box.label(text='Selecione os meshes da peça:')
        for kind, label, icon in (('ROTATE', 'Girar', 'DRIVER_ROTATIONAL_DIFFERENCE'),
                                  ('MOVE', 'Mover', 'ORIENTATION_GLOBAL'),
                                  ('VISIBILITY', 'Mostrar / Esconder', 'HIDE_OFF')):
            op = box.operator('edm.anim_create', text=label, icon=icon)
            op.kind = kind
        box.prop(scene, 'edm_preview_arg', text='Testar argumento', slider=True)
        if new_anim.is_pivot(obj):
            col = box.column(align=True)
            col.label(text="Pivô '%s'" % obj.name, icon='EMPTY_ARROWS')
            if 'edm_anim_arg' in obj:
                col.prop(obj, '["edm_anim_arg"]', text='Argumento')
            if 'edm_vis_ranges' in obj:
                col.prop(obj, '["edm_vis_ranges"]', text='Visível quando')
            else:
                col.label(text='Mais passos: pose em outro frame e tecle I.')
            col.operator('edm.anim_remove', icon='X')
        else:
            box.label(text='frame 100 = argumento 0 | 200 = 1 | 0 = -1')

        box = lay.box()
        box.label(text='2. Peças', icon='MOD_BUILD')
        box.operator('edm.split_parts', icon='MOD_EXPLODE')

        box = lay.box()
        box.label(text='3. Texturas', icon='TEXTURE')
        box.prop(scene, 'edm_texture_dir', text='Pasta')
        box.operator('edm.tex_auto', icon='MATERIAL')
        box.operator('edm.tex_convert', icon='FILE_IMAGE')

        box = lay.box()
        box.label(text='4. Exportar', icon='EXPORT')
        box.operator('edm.check', icon='CHECKMARK')
        box.label(text='Arquivo > Exportar > DCS EDM')


classes = (EDM_OT_anim_create, EDM_OT_anim_remove, EDM_OT_split_parts, EDM_OT_check,
           EDM_OT_tex_convert, EDM_OT_tex_auto, VIEW3D_PT_edm_easy)


def register():
    bpy.types.Scene.edm_texture_dir = StringProperty(
        name='Pasta de texturas', subtype='DIR_PATH', default='',
        description='Pasta de texturas do mod (onde ficam os .dds)')
    bpy.types.Scene.edm_preview_arg = FloatProperty(
        name='Argumento', default=0.0, min=-1.0, max=1.0, update=_update_preview,
        description='Move a timeline para este argumento (frame = (arg+1)*100)')
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.edm_preview_arg
    del bpy.types.Scene.edm_texture_dir
