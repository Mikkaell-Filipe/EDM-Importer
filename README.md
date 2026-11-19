# DCS EDM para Blender (custom) 0.7.0

Alvo: Blender 4.2+ (testado em desenho para 5.x; **não foi executado dentro do Blender** nesta build, ver "Testado / não testado").

> **Atenção (estado real deste zip):** o `import_edm.py` e o `__init__.py` recebidos eram os da linha 0.3.x (um empty por nó, frame = arg x 100). Por isso as seções 0.4.0 abaixo sobre importador em cadeia (anti-esticamento), `EDM_Diagnostic`, Split/Join e Mapping Report **descrevem a 0.4.x e não estão neste zip**; o que a 0.6.0 acrescenta (animação fácil, texturas, verificação, DDS) funciona sobre o importador 0.3.x e sobre o exportador 0.5.8.

## Connectors (0.7.0)
Empties novos com nome A29N-*, PNT_*, PTR-* ou FP_* (ou propriedade `edm_connector`=1) sao gravados como Connector no export. Veja a secao "NEW CONNECTORS written" do Text `EDM_Export_Report`. Para uma tela: o trio CENTER/DOWN/RIGHT, cada um com o filho .001, como o script A29N_criar_conectores.py ja cria.

## Instalar
Edit > Preferences > Add-ons > seta no canto > **Install from Disk...** > escolha `io_scene_edm_custom.zip`. Ative "DCS World EDM format (.edm) [custom]". Desative/remova versões antigas deste addon antes.

## O que mudou na 0.4.0

### 1. Distorção / partes esticadas
Causa real encontrada: vários nós EDM têm matriz com **escala não uniforme em eixos rotacionados** (cisalhamento no vocabulário do Blender). Um Object do Blender só guarda loc/rot/scale, então o importador antigo, ao jogar essa matriz em UM empty, perdia o cisalhamento e toda a malha filha saía esticada. No Cockpit_A-29B há 80 TransformNodes e 4 nós de animação nessa situação, e escalas de até ~68:1 (ex.: Flaps, Throttle Plate).
Correção: cada nó vira uma **cadeia curta de empties** (decomposição exata por SVD, erro medido ~1e-13). Nó simples = 1 empty; alavanca animada = 3. O último empty da cadeia ("tip") é o pai das malhas e filhos. A animação também deixou de sobrescrever a pose de repouso com uma decomposição com perda.
Modo de diagnóstico no import: `Full`, `No animation`, `Legacy 0.3.x matrices` (A/B). O Text **EDM_Diagnostic** lista os piores nós por anisotropia, nós espelhados, malhas fundidas e avisos.

### 2. Malhas juntas (cadeira ejetora etc.)
* Na importação: opção **Split merged meshes into loose parts**, ou depois, com os objetos selecionados: painel lateral **EDM > Split Loose Parts**.
* Ilhas são detectadas com vértices soldados por posição (costura de UV não fatia peça).
* Cada parte guarda `edm_part_info` (centro e tamanho) para você achar a cadeira.
* Exportar **recombina** as partes de cada slot. Mover uma parte move só ela; apagar remove só ela.
* **EDM: Join Parts (same slot)** junta peças do mesmo nó/grupo (ex.: toda a cadeira) num objeto só, e recusa juntar slots diferentes.
* Cockpit_A-29B: 992 partes no total (o grupo "Cockpit Base" sozinho tem 114).

**Cadeira soldada ao casco:** no Cockpit_A-29B as cadeiras dividem vértices com o casco (uma única ilha de 220 faces, 0,9 x 3,38 x 1,19 m), então Split Loose Parts não separa. Use: selecione `Cockpit Base_mesh`, Tab (Edit Mode), ative X-ray (Alt+Z), selecione as faces da cadeira (box select ou L sobre a peça), clique **EDM > Separate Selection as Part** e dê um nome. A cadeira vira um objeto do mesmo slot; mova/apague/reparente à vontade e exporte.

### 3. Nomes aleatórios
Malhas agora se chamam `<nó pai>_mesh` (o nome do RenderNode original, quase sempre vazio, fica em `edm_render_name`). O vínculo malha->material nunca depende do nome: usa `edm_render_index`, `edm_parent_group` e `edm_material_id`. **EDM: Mapping Report** gera o Text `EDM_Mapping` (nó, objeto, render#, grupo, material EDM, shader, texturas).

### 4. Args e animação
* Um Action por argumento, nomeado `<arg>_<objeto>` (o que o exportador oficial lê).
* Convenção oficial: arg -1..1 = frames 0..200 (frame 100 = arg 0). Opção "Legacy" mantém arg x 100.
* Visibilidade: propriedade float `VISIBLE` (como o oficial) + drivers que escondem os filhos.
* Slider **EDM > Argument preview** move a timeline.

### 5. Importação
Material cacheado por índice EDM (dois materiais com o mesmo nome não se fundem), todas as texturas registradas (`edm_textures`), difuso e normal ligados, uniforms em `edm_uniforms`, luzes com todas as propriedades em `edm_light_props`.

### 6. Exportação
Continua **remendando o arquivo-fonte** (não gera EDM do zero). Novo: recombina partes, grava pivôs: mova o **primeiro** empty da cadeia de um nó (o que tem o nome do nó) e o pivô vai para o arquivo. Partes reparentadas para outro nó viram RenderNode novo.

## Adicionar geometria nova (estrutura, piloto, cadeira...) -- 0.5.0
1. Modele em qualquer colecao da cena. O mesh precisa estar **visivel** (olho e monitor) e sem a propriedade `edm_skip`.
2. **Parente ao empty do no** onde ele deve viver (ex.: `cadeira front`, `piloto frente`): assim ele herda animacao/visibilidade do cockpit. Sem pai ele vai para o no raiz (estatico, sempre visivel).
3. Material: (a) se a imagem do Material do Blender ja for textura de um material do .edm, ele e reaproveitado; (b) se for imagem nova, o export CRIA um material EDM novo com esse nome de imagem (copie `<nome>.dds` para a pasta de texturas do mod); (c) para forcar um material existente, ponha a propriedade `edm_new_material` = nome ou indice.
4. Transformacoes do objeto e modificadores sao aplicados nos vertices. O UV precisa estar desenhado para a textura escolhida.
5. Depois do export abra o Text **EDM_Export_Report**: lista o que foi gravado, o que foi ignorado (e por que) e o que foi REMOVIDO do original.
6. Limites: geometria nova e estatica (sem novos nos de animacao) e sem pesos de bone.

## EDM Fácil (0.6.0): animar sem saber de EDM

Painel: 3D View > tecla **N** > aba **EDM**.

**Animar uma alavanca, botão, tampa, porta...**
1. Posicione o **cursor 3D** na dobradiça/eixo (ou use Set Origin na peça) e selecione o(s) mesh(es) da peça.
2. Painel EDM > **Girar** (ou **Mover**). Escolha o **argumento DCS** (o número que o cockpit/aeronave usa), o eixo e a quantidade (graus para Girar, metros para Mover; negativo inverte) e, em *Pivô em*, onde fica o eixo (objeto ativo / cursor 3D / centro).
3. Teste com o slider **Testar argumento** (ou arraste a timeline: frame 100 = arg 0, 200 = arg 1, 0 = arg -1).
4. Quer mais passos (ex.: trem de pouso com 3 posições)? Com o pivô selecionado, vá a outro frame, gire/mova o pivô e tecle **I**.
5. Duas animações na mesma peça (ex.: gira e desliza): selecione o **mesh** de novo e crie a segunda; o novo pivô vira filho do primeiro (nós encadeados no arquivo), cada um com seu argumento.
6. **Mostrar / Esconder**: informe as faixas do argumento em que a peça aparece, ex. `>0.5`, `0:0.3; 0.7:1`.
7. Exporte normalmente (Arquivo > Exportar > DCS EDM). O Text `EDM_Export_Report` lista as "NEW animations written".

Regras que o addon segue por você: a pose do pivô no argumento mais próximo de 0 vira o repouso do nó; as chaves são gravadas como deslocamento/rotação relativos a ele; rotações grandes não "viram" entre chaves; escala no pivô é ignorada (avisa).
Faça o pivô com o cockpit/peça-pai na pose de repouso. O importador desta build (0.3.x) anima as peças importadas com frame = arg x 100; os pivôs novos usam a convenção oficial (frame = (arg+1) x 100), então ao arrastar a timeline peças importadas e pivôs novos não andam juntas na prévia - não afeta o arquivo exportado.

**Texturas (problema do cockpit do AMX e similares)**
* Preencha **Pasta de texturas** (pasta do mod onde ficam os `.dds`).
* **Material pelo nome**: nos meshes selecionados, procura na pasta um arquivo (png/tga/dds...) com o nome do mesh, do material ou do pai e monta o material. Convenção DCS: `nome.dds`, `nome_NM.dds`, `nome_RoughMet.dds`.
* **Converter para DDS**: grava `nome.dds` (DXT1 sem transparência, DXT5 com) das imagens usadas pelos meshes novos e dos `_NM`/`_RoughMet` abertos no .blend.
* **Verificar antes de exportar** diz o que ainda falta.

## Testes (0.6.0)
`python3 -m unittest discover -s tests`: 58 testes (13 precisam de `EDM_TEST_FILE` e são pulados sem ele). Novos: `test_args` (poses -> chaves -> nó, conferido com `edm_nodeplan`), `test_new_anim` (alocação dos pivôs com um Blender simulado em numpy), `test_dds` (decodificação com Pillow), `test_smoke`.

## Testes (0.4/0.5)
`EDM_TEST_FILE=/caminho/Cockpit_A-29B.EDM python3 -m unittest discover -s tests` (Python puro, bpy simulado).
Resultado desta build: 15 testes OK (matemática, ilhas, slots, round-trip byte a byte, importação completa do cockpit com bpy simulado, pivôs, dry-run de exportação com as 992 partes recombinando nos 59 RenderNodes).

## Como testar no Blender 5.x
1. Importe `Cockpit_A-29B.EDM` com defaults. Abra o Text `EDM_Diagnostic`. Compare com `Legacy 0.3.x matrices`: as peças esticadas devem corrigir.
2. Selecione `Cockpit Base_mesh` > EDM > Split Loose Parts. Ache a cadeira por `edm_part_info`, selecione as peças, Join Parts, mova, exporte.
3. Slider de arg 0..1 em Salvo, STICK, Throttle, Canopy.
4. Exporte e abra no DCS / compare com o plugin oficial.

## Testado / NÃO testado (honesto)
Testado: leitura, escrita idêntica, matemática, planejamento de cadeias, importação e exportação com bpy simulado.
**Não testado:** execução dentro do Blender 5.x (APIs de Action em camadas, normais customizadas, drivers), nem o arquivo exportado dentro do DCS.
**Fórmula não confirmada por texto oficial:** `rest = tf_matrix * T(tf_position) * R(tf_quat1) * R(q2) S(tf_scale) R(q2)^-1` (inclui `tf_quat2`). Difere da 0.3.x só em poucos nós; se algo ficar errado use o modo Legacy.

## Ainda NÃO feito
SkinNode/bones (importa como aviso; Bone/Lod/Billboard viram empty identidade), colisão (Shell/Segments), animação de escala (guardada em `edm_scale_data_unsupported`), exportação de materiais/uniforms editados, tabela de mapeamento editável pela UI, validação pré-export (nomes duplicados, escala aplicada...), cálculo do bbox no cabeçalho, suporte a EDM diferente da v10, múltiplos modelos importados ao mesmo tempo (`_node_empties` varre a cena inteira).
