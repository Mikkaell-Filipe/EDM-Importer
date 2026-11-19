"""Connectors NOVOS (0.7.0).

Por que existe
--------------
O exportador so remendava o .edm de origem: os Connectors que ja existiam no
arquivo eram reescritos como estavam, mas um empty novo criado no Blender
(A29N-UFCP-CENTER, PNT_831, ...) NUNCA chegava ao .edm. O DCS entao nao achava
o conector ("Wrong connector name PNT_xxx") e jogava o display na mira / deixava
a tela preta, mesmo com o empty visivel no .blend.

Como o DCS enxerga um conector
------------------------------
Um model::Connector nao tem matriz propria: a posicao vem do no pai. Os
conectores do AMX sao assim: TransformNode "FP_LMFD_Center" (a matriz) + um
Connector de mesmo nome filho dele. O importador mostra isso como o empty
"FP_LMFD_Center" e o filho "FP_LMFD_Center.001" (o .001 e so o Blender
desduplicando o nome). O script A29N_criar_conectores.py cria exatamente esse
par; aqui cada par vira (TransformNode + Connector).

Regra de selecao (empty novo = sem 'edm_node_index' / 'edm_node_type'):
  * propriedade 'edm_connector' = 1  -> sempre conector; = 0 -> nunca;
  * senao, o nome (sem sufixo .001) comeca com um dos PREFIXOS abaixo.
  * 'edm_skip' ou nome com 'ERRADO' -> ignorado.
O filho ".001" do par e o marcador e nao gera um segundo conector.

Parte pura (sem bpy) para poder testar fora do Blender.
"""
import re

from . import edm_args as A
from . import edm_math as em

PREFIXOS = ('A29N-', 'PNT_', 'PTR-', 'FP_')
_SUFIXO = re.compile(r'\.\d{3}$')


def base_name(name):
    return _SUFIXO.sub('', name)


def existing_connector_names(edm):
    return {n.get('name') for _t, n in edm.render_items.get('CONNECTORS', [])}


def add_connector(edm, name, rest_rows, parent_index):
    """Anexa TransformNode(name, rest) sob parent_index e um Connector(name)
    filho dele. Devolve o indice do TransformNode."""
    tf = A.add_static_transform(edm, name, em.to_flat(rest_rows), parent_index)
    items = edm.render_items.setdefault('CONNECTORS', [])
    node = None
    for t, n in items:                      # copia version/unknown de um conector real
        if t == 'model::Connector':
            node = {'name': name, 'version': n.get('version', 0), 'props': [],
                    'parent': tf, 'unknown': n.get('unknown', 0)}
            break
    if node is None:
        node = A._base(edm, 'model::Connector', name)
        node['parent'] = tf
        node['unknown'] = 0
    items.append(('model::Connector', node))
    return tf


# --------------------------------------------------------------------------
# Lado Blender
# --------------------------------------------------------------------------

def is_connector_object(obj):
    if obj.type != 'EMPTY':
        return False
    if obj.get('edm_skip') or 'edm_node_index' in obj or 'edm_node_type' in obj:
        return False
    if 'edm_anim_arg' in obj or 'edm_anim_kind' in obj:
        return False
    flag = obj.get('edm_connector')
    if flag is not None:
        return bool(int(flag))
    b = base_name(obj.name)
    return b.startswith(PREFIXOS) and 'ERRADO' not in b.upper()


def sanitize(context, edm, collection):
    """Empties COPIADOS de um conector/no importado (ex.: o script que cria
    A29N-* clona PTR-RAD-CENTER001) herdam 'edm_node_index'/'edm_node_type' do
    original e eram tomados por nos ja importados (nunca gravados; pior: podiam
    mover o pivo do no do AMX). Se o nome tem prefixo de conector mas o indice
    aponta para um no de OUTRO nome, as propriedades sao herdadas por engano e
    sao removidas. Devolve os nomes limpos."""
    node_names = [n.get('name') for _t, n in edm.nodes]
    conn_names = existing_connector_names(edm)
    cleaned = []
    seen = {o.name: o for o in list(collection.all_objects) + list(context.scene.objects)}
    for o in seen.values():
        if o.type != 'EMPTY':
            continue
        b = base_name(o.name)
        if not b.startswith(PREFIXOS) or o.get('edm_connector') == 0:
            continue
        stale = False
        if 'edm_node_index' in o:
            try:
                stale = node_names[int(o['edm_node_index'])] != b
            except Exception:
                stale = True
        elif o.get('edm_node_type') == 'Connector' and b not in conn_names:
            stale = True
        if stale:
            for k in ('edm_node_index', 'edm_node_first_of', 'edm_node_type', 'edm_node_name'):
                if k in o:
                    del o[k]
            cleaned.append(o.name)
    return cleaned


def allocate(context, edm, collection, root_index, root_empty, find_parent, relative_rows):
    """Cria os conectores novos. find_parent(obj, inside) -> (empty, idx);
    relative_rows(obj, anc, root_empty) -> 4x4 em linhas (ambos de new_anim).
    Devolve {'created': [...], 'skipped': [...], 'warnings': [...]}"""
    out = {'created': [], 'skipped': [], 'warnings': []}
    inside = {o.name for o in collection.all_objects}
    seen = {o.name: o for o in list(collection.all_objects) + list(context.scene.objects)}
    cands = [o for o in seen.values() if is_connector_object(o)]
    names = {o.name for o in cands}
    have = existing_connector_names(edm)
    done = set()
    for obj in sorted(cands, key=lambda o: o.name):
        b = base_name(obj.name)
        par = obj.parent
        if par is not None and par.name in names and base_name(par.name) == b:
            continue                         # filho .001 = marcador do par
        if b in have:
            out['skipped'].append("%s (ja existe no .edm)" % b)
            continue
        if b in done:
            out['warnings'].append("conector '%s' duplicado na cena: so o primeiro foi gravado" % b)
            continue
        try:
            anc, pidx = find_parent(obj, inside)
            if pidx is None:
                if root_index is None:
                    raise RuntimeError('o modelo nao tem no raiz')
                pidx, anc = root_index, None
            rows = relative_rows(obj, anc, root_empty)
            add_connector(edm, b, rows, pidx)
            done.add(b)
            pn = edm.nodes[pidx][1].get('name') or '#%d' % pidx
            out['created'].append("%s (sob o no '%s')" % (b, pn))
        except Exception as e:               # um conector ruim nao derruba o export
            out['warnings'].append("conector '%s': %s" % (obj.name, e))
    return out
