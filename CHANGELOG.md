# Changelog
## 0.7.2
- Fix: erro "Export needs a little more info for new mesh object(s)" aparecia cortado na barra e sem dizer QUAL objeto. Agora a mensagem comeca com os nomes dos objetos e o motivo exato (sem material, sem molde de material no .edm, 'create materials' desligado, sem pai de no) fica no Text `EDM_Export_Report`.
- Novo: mesh cujo material nao tem imagem (so cor) usa o NOME do material como textura (aviso no relatorio; copie <nome>.dds para a pasta de texturas).
- Novo: imagem de textura dentro de Node Group tambem e encontrada.
## 0.7.1
- Fix: empties COPIADOS de conectores do AMX (script que cria A29N-*/PNT_*) herdavam 'edm_node_index'/'edm_node_type' e eram tratados como nos ja importados: nao eram gravados. Agora `edm_connectors.sanitize` remove essas props herdadas por engano (nome com prefixo de conector mas indice de no com outro nome) antes do export; aviso no relatorio.
## 0.7.0 - Connectors novos + fim dos buffers vazios
- Fix (principal): empties NOVOS criados no Blender (A29N-*, PNT_*, PTR-*, FP_*) nunca eram gravados no .edm. O exportador so reescrevia os Connectors que ja existiam no arquivo de origem; por isso o DCS dizia "Wrong connector name PNT_xxx" e os displays (bindings.lua -> A29N-*) ficavam pretos / caiam na mira, mesmo com o empty no .blend. Agora cada par (empty + filho .001) vira um TransformNode (com a pose do empty sob o no pai) + um model::Connector de mesmo nome. Regra: sem 'edm_node_index'/'edm_node_type'; nome (sem .001) com prefixo A29N-/PNT_/PTR-/FP_, ou propriedade `edm_connector`=1 (0 = nunca). `edm_skip` e nomes com ERRADO sao ignorados. Nomes que ja existem no .edm nao sao duplicados. Relatorio: secao "NEW CONNECTORS written".
- Fix: peca removida/zerada era gravada com 0 vertices e 0 indices -> o DCS logava "Failed assert numIndices != 0 && size != 0" e "E_INVALIDARG" centenas de vezes. Agora vai um triangulo degenerado invisivel.
- Novo: tests/test_connectors.py (regra de selecao, par .001, duplicatas; round-trip com EDM_TEST_FILE).
- Aviso honesto: nao executado dentro do Blender nem do DCS.
## 0.6.0 - "EDM Fácil"
- Novo: **animação fácil**. Painel lateral (3D View > N > aba **EDM**): selecione os meshes da peça e clique **Girar**, **Mover** ou **Mostrar / Esconder**. O addon cria um *pivô* (empty), coloca os meshes embaixo dele e já grava as chaves. No export cada pivô vira um **nó de animação NOVO** (`ArgAnimationNode` / `ArgVisibilityNode`) no .edm; meshes sob o pivô são gravados sob esse nó (inclusive peças importadas que você reparentou). Pivôs aninhados (um sobre o outro) viram nós encadeados. Pivô sem movimento não gera nó (a peça fica fixa no pai).
- Convenção de frames dos pivôs novos = a oficial: arg = frame/100 - 1 (frame 100 = arg 0, 200 = arg 1, 0 = arg -1). Mais passos = mais chaves (pose em outro frame + tecla I). Action nomeada `<arg>_<nome>` (o que o exportador oficial lê).
- Novo: **Verificar antes de exportar** (Text `EDM_Check`): falta de UV/material/textura, `.dds` ausente, `_NM`/`_RoughMet` pela metade, pivô sem argumento / sem chaves / com escala, peça sem pai (vai para o root), pasta do .edm original sumida - cada item com a correção em português.
- Novo: **Texturas**. *Converter para DDS* (DXT1/DXT5/sem compressão, com mipmaps; escrito em numpy, sem programas externos) grava `<nome>.dds` na pasta de texturas; *Material pelo nome* cria o material do mesh a partir do arquivo de textura com o mesmo nome do mesh/material/pai (resolve "nome do mesh diferente do nome da textura"). Campo **Pasta de texturas** na cena (também usado pelo export).
- Novo: botão **Separar em peças soltas** no painel.
- Export: o contador de "partes removidas" não conta mais peças apenas MOVIDAS para outro nó (reparentadas), e o relatório lista "NEW animations written".
- Aviso: nada disto foi executado dentro do Blender nem dentro do DCS nesta build; a matemática (poses -> chaves -> nó) foi testada contra a fórmula do próprio addon (`edm_nodeplan`) com Python puro.
## 0.5.8
- Fix: materiais NOVOS herdavam BLENDING=1 (mistura alfa) do molde a29b_prop.dds: pecas novas ficavam translucidas (carenagem mostrando o outro lado) e o piloto sumia/aparecia conforme o angulo (ordenacao de transparencia). Agora o molde preferido e um material opaco (BLENDING 0) e todo material novo sai opaco, exceto nomes com glass/visor/viseira/vidro/canopy/window (BLENDING 1). Propriedade `edm_blending` no objeto (0/1/2...) forca o valor.
## 0.5.7
- Fix: meshes novos com transformacao ESPELHADA (escala negativa, ex.: copias .001 espelhadas) saiam com as faces invertidas: no DCS o lado de perto some (backface culling) e aparece o de tras, "translucido". Agora o export inverte a ordem dos triangulos quando o determinante da matriz objeto->no e negativo, e o relatorio lista esses objetos. Propriedade `edm_flip_faces` no objeto: 1 = sempre inverter, -1 = nunca, ausente = automatico.
## 0.5.6
- Fix: texturas PBR (_NM/_RoughMet) agora tambem funcionam quando o EDM nao tem um def_material simples de 3 texturas. Busca em 2 niveis: (1) simples; (2) qualquer def_material estatico com difusa+normal+roughmet e layout posicao/normal/uv0 (canais extras como uv1 saem zerados). Sufixos copiados do material-modelo. O aviso do relatorio agora lista os candidatos e por que foram rejeitados.
## 0.5.5
- Fix: 'got an unexpected keyword argument texture_dir' ao instalar o zip por cima de outro sem reiniciar o Blender (so o __init__ era recarregado). O export agora recarrega os modulos auxiliares.
- Fix: 'Texture folder' e campo de texto simples (o botao de pasta nao abre dentro do dialogo de export: 'Cannot activate a file selector dialog'). Cole o caminho; ou use a propriedade `edm_texture_dir` na colecao importada.
## 0.5.4
- Novo: campo "Texture folder" no dialogo de export (pasta de texturas do mod). A busca de `_NM`/`_RoughMet` agora olha essa pasta, a pasta da imagem do Blender, a pasta do .blend e `textures/`. Quando nao acha, o relatorio (Warnings) diz onde procurou.
## 0.5.3
- Novo: texturas PBR. Se ao lado da imagem existirem `<nome>_NM` e `<nome>_RoughMet` (convencao DCS), o material novo e clonado de um def_material do proprio EDM que tenha difusa+normal+roughmet (indices 0/1/2) e recebe `<nome>`, `<nome>_NM`, `<nome>_RoughMet`. Se o EDM nao tiver esse material, cai no simples e avisa no relatorio (Warnings). Propriedade `edm_pbr` (0/1) forca/desliga; `edm_template_material` continua mandando.
## 0.5.2
- Novo: opcao de export "Include meshes from other imported models" (desligada por padrao). Exporta como geometria NOVA os meshes vindos de outro .edm (ex.: piloto de outra aeronave). `edm_material_id` e `edm_parent_node` do outro arquivo sao ignorados; a textura (imagem) define o material. O relatorio lista esses objetos numa secao propria.
## 0.5.1
- Fix: mesh de OUTRO modelo importado (ex.: piloto copiado de outro EDM, com edm_render_index) era ignorado em silencio. Agora aparece em IGNORED com o motivo; a propriedade `edm_force_new` = 1 manda exporta-lo como geometria NOVA. Curvas/textos etc. tambem sao listados como nao exportados.
- Fix: um mesh novo parentado a empty de outro modelo importado nao herda mais o no errado (so empties da colecao exportada contam).
- Fix: pivos/nos corrompidos quando a cena tem OUTRO modelo importado (os empties dele reusam os mesmos edm_node_index). A busca de empties de no agora e limitada a colecao exportada (ex.: o pivo de hide_cockpit_114.001 saia deslocado 5,69 m).
- Fix: imagem sem nome utilizavel nao cria mais material EDM com textura vazia.
## 0.5.0
- Fix: o export so removia (ex.: cadeiras e pilotos apagados ficavam zerados no .edm) e nao gravava a estrutura/piloto novos. O `export_edm.py` deste zip era o da 0.3.x (so olhava a colecao importada); agora ele inclui os meshes novos visiveis de QUALQUER colecao da cena (opcao "Include meshes from other collections").
- Novo: textura nova vira MATERIAL EDM novo (clone de um def_material simples com o nome da imagem do Blender); se a imagem ja for textura de um material do arquivo, reaproveita esse material. Propriedades manuais: `edm_new_material`, `edm_template_material`.
- Novo: mesh solto, sem pai de no, vai para o no raiz (estatico); opcao "Attach loose objects to root". Para mover/esconder junto com o cockpit, parente ao empty do no (ex.: `cadeira front`, `piloto frente`).
- Novo: modificadores (Mirror, Bevel, Array...) sao aplicados no export dos meshes (mesh avaliado).
- Novo: o bbox do cabecalho cresce para incluir a geometria nova (evita sumir por culling).
- Novo: pecas movidas para outro no viram RenderNode novo la; varias pecas por slot sao recombinadas (`edm_split_part`).
- Novo: Text 'EDM_Export_Report' (novos, materiais criados, objetos de outras colecoes, ignorados com motivo, removidos, pivos) + avisos na barra de status.
- Pivos: o deslocamento do empty agora e gravado como translacao a esquerda em tf_matrix/transform (exato, nao mexe na animacao). Funciona com o importador 0.3.x (1 empty por no) e com o 0.4.x (cadeia).
- Aviso: este zip veio MISTURADO (arquivos de 17/10 e 21/10). `import_edm.py`/`__init__.py` ainda sao da 0.3.x; os testes que dependem do importador 0.4.x (cadeia de empties, opcoes de import) ficam ignorados.
## 0.4.6
- Novo: EDM > Convert Materials for Official Exporter (Principled -> EDM_Default_Material, liga Base Color e Normal). Sem material EDM o exportador oficial pula o mesh e gera ~2 KB.
- Export custom: erro explica que so remenda arquivo importado; menu renomeado.
## 0.4.5
- Fix: export ignorava em silencio meshes novos fora da colecao importada (ex.: cadeira/aro modelados na 'Collection' padrao). Agora inclui meshes visiveis de qualquer colecao da cena.
- Novo: Text 'EDM_Export_Report' (novas geometrias gravadas, objetos incluidos de outras colecoes, ocultos ignorados, partes do original REMOVIDAS) + avisos na barra de status.
- Novo: EDM > Set Material for New Objects (escolhe o material EDM existente de um mesh novo).
## 0.4.4
- Novo: EDM > Separate Selection as Part (Edit Mode) para pecas soldadas por vertices (cadeira no casco do cockpit).
## 0.4.3
- Fix Visibility Steps agora RECRIA as chaves a partir do EDM (a troca de interpolacao falhava no Blender 5.x). Versao mostrada no painel EDM.
## 0.4.2
- Novo: EDM > Fix Visibility Steps (corrige cena ja importada).
## 0.4.1
- Fix: visibilidade (ex.: Canopy) usava rampa linear e escondia cockpit/cadeira por volta do frame 145; agora é degrau (visivel 0..0.91 = frames 100..191). Também corrige faixas que começam em -infinito.
## 0.4.0
- Fix: partes esticadas (cadeia exata de empties, SVD) em vez de um empty por nó.
- Novo: split/join de partes soltas; export recombina; nomes `<nó>_mesh`; Mapping Report; Diagnostic Report.
- Novo: args oficiais (frames 0..200, VISIBLE, um Action por arg), slider de arg, painel EDM.
- Novo: materiais por índice, texturas normal, uniforms guardados; luzes com propriedades.
- Export: pivôs de nós, partes múltiplas por slot, partes reparentadas viram RenderNode novo.
- Reader/Writer: preserva campos unknown (round-trip idêntico também em arquivos antigos).
## Plano (próximas etapas)
2. Materiais DCS completos (def/glass/self-illum/decal/mirror) + export de uniforms.
3. Skin/bones, LOD, billboard, colisão.
4. Exportador que gera o modelo da cena (sem depender do arquivo-fonte) + validação.
5. UI: tabela de mapeamento editável, editor de args.
