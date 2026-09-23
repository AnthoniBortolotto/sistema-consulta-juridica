# Consulta Jurídica com RAG

Busca e resposta fundamentada sobre legislação brasileira, com **citação verificável** — a
resposta traz o trecho literal de cada citação e o dispositivo de onde ele saiu, até o
inciso.

> **Status:** implementado de ponta a ponta — ingestão, busca, geração, API, front e
> avaliação de recuperação, com números medidos. Três coisas ainda não:
>
> - **a geração com o Claude nunca foi executada contra a API.** O contrato está coberto
>   por testes com dublês e o mapeamento de citação foi conferido contra o corpus real,
>   mas a primeira chamada exige uma `ANTHROPIC_API_KEY`;
> - por isso, os números de resposta e citação estão "a medir";
> - a jurisprudência do STJ ficou fora do corpus.
>
> Sem chave, o sistema responde por um modelo local — ver
> [Sem chave](#sem-chave-geração-local-com-ollama).

---

## O problema

Um LLM genérico perguntado sobre direito brasileiro inventa citações com fluência: cita
artigo que não existe, atribui texto à norma errada ou responde com dispositivo já
revogado. Em pesquisa jurídica isso não é um erro cosmético — é o modo de falha que
inviabiliza a ferramenta, porque verificar a citação custa o mesmo que ter pesquisado à mão.

Este projeto responde apenas a partir de texto legal recuperado, e devolve junto o caminho
de volta até a fonte: o trecho literal, a norma, o dispositivo e a data de vigência
considerada.

---

## Como funciona

```
pergunta + data de referência
   │
   ├─► busca densa   (bge-m3) ─┐
   │                            ├─► RRF ─► rerank ─────► expansão ──────► 8 trechos
   └─► busca esparsa (BM25)   ─┘   50     (opcional)    inciso → artigo      │
                                   cand.  bge-reranker   no SQLite            │
                                                                              │
       filtro de vigência dentro de cada busca, e reaplicado na expansão      │
                                                                              │
   Claude, trechos como blocos `document` + citations  ◄──────────────────────┘
   (sem chave: modelo local via Ollama, sem citations nativas)
            │
            └─► resposta + citações rastreáveis até o dispositivo
```

---

## Decisões de arquitetura

A parte interessante do projeto não é chamar um LLM — é o que vem antes.

### Chunking estrutural, não janela fixa

Texto jurídico tem hierarquia explícita: Título → Capítulo → Seção → Artigo → Parágrafo →
Inciso → Alínea. Partir por janela de N tokens corta artigos no meio e separa o inciso do
caput que lhe dá sentido.

Aqui a unidade de indexação é o **dispositivo**, e cada chunk carrega a hierarquia inteira,
a norma de origem e o intervalo de vigência. O texto indexado leva junto o texto dos
ancestrais — "VI - defesa da paz;" sozinho é um vetor sem informação; com o caput, não.

Um inciso recuperado é expandido para o artigo antes de ir ao modelo, porque inciso isolado
frequentemente é ininteligível. Artigo longo demais — o art. 5º da CF tem 79 incisos — é
cortado em fronteira de dispositivo, sempre mantendo o que a busca achou, com a omissão
marcada por `[…]`.

### Busca híbrida, não só vetorial

As duas consultas abaixo são igualmente comuns e exigem mecanismos diferentes:

| Consulta | Natureza |
|---|---|
| `art. 5º, LXXVIII` | léxica pura — embeddings erram em numeração |
| `responsabilidade objetiva do Estado` | semântica |

Busca densa e esparsa rodam juntas e são fundidas por Reciprocal Rank Fusion, no próprio
Qdrant. RRF usa a *posição* de cada candidato, não o score bruto, porque similaridade de
cosseno e score BM25 não compartilham escala calibrada. Um reranker cross-encoder reordena
os 50 candidatos da fusão — e é **opcional** (`CJ_USAR_RERANK`): custa ~11 s por consulta na
CPU e, medido, melhora a ordem mas não o recall — o recall@5 até cai (ver
[Avaliação](#avaliação)).

### Vigência como campo de primeira classe

Responder com norma revogada como se fosse vigente é o pior defeito possível no domínio.
Todo chunk carrega o início de vigência e a data de revogação, e toda consulta filtra por
uma data de referência — que é parâmetro obrigatório da API, não `now()` implícito, para
permitir consulta sobre o direito vigente à época de um fato.

Redação superada vira dispositivo próprio: perguntar sobre 2010 traz o texto de 2010. E o
filtro é **reaplicado** quando o inciso é expandido para o artigo — o Qdrant exclui o
inciso revogado, mas a expansão lê o artigo inteiro do SQLite, e sem o segundo filtro os
irmãos revogados voltariam por ali.

### Fonte da verdade relacional, índice derivado

O corpus canônico — árvore de dispositivos, vigências e remissões — vive em SQLite. O Qdrant
é **índice derivado e reconstruível**, não banco primário.

A razão é concreta: bancos vetoriais não têm join, então expansão inciso→artigo, hierarquia
e remissões precisariam ser desnormalizadas no payload. Quando uma emenda altera um artigo,
a desnormalização exige reescrever todos os chunks filhos sem transação. Com índice
derivado, reingestão é rebuild — não edição de estado mutável.

*Tradeoff honesto:* isso custa um processo de build — reindexar o corpus leva ~52 min na
CPU — e uma leitura a mais no SQLite por consulta, porque o texto não vai para o payload do
Qdrant. Em troca, a correção da consistência é estrutural em vez de disciplinar.

### Citação garantida pela API, não pedida por prompt

Os trechos recuperados são enviados como blocos `document` de texto puro, com citations
habilitadas na Messages API. A resposta volta particionada, e os blocos citados carregam o
`cited_text` e os índices de caractere no documento de origem. O sistema traduz esses
índices de volta ao dispositivo — a citação cai no inciso ou no parágrafo único, não no
artigo inteiro — e confere que o texto citado está mesmo no trecho enviado.

A alternativa comum — instruir o modelo a citar e confiar na obediência — falha
silenciosamente e de forma difícil de detectar em escala. É o que os backends sem chave
fazem, e por isso eles não entram na avaliação de citação.

### Sem framework de orquestração na v1

O pipeline é curto e determinístico: recuperar, reordenar, montar o prompt, chamar, ler
citações. Uma camada de abstração sobre isso esconderia justamente o que precisa ser
inspecionado durante a depuração — o prompt exato que foi enviado.

---

## Stack

| Camada | Escolha |
|---|---|
| Linguagem | Python |
| API | FastAPI |
| Fonte da verdade | SQLite |
| Índice vetorial | Qdrant — vetores nomeados denso + esparso |
| Embeddings | `BAAI/bge-m3` (local) |
| Busca esparsa | BM25 (`Qdrant/bm25` via fastembed, com stemmer em português) |
| Reranker | `BAAI/bge-reranker-v2-m3` (local, opcional) |
| Geração | Claude (`claude-opus-5`) via SDK `anthropic`, com citations nativas · sem chave: `qwen3.5:4b` via Ollama, ou o Claude Code CLI |
| Front de teste | Vue 3 por CDN, servido como estático |

Embeddings e reranking rodam localmente, na CPU — indexar o corpus inteiro não custa nada
em API.

---

## Corpus

| Fonte | O que entra | Volume |
|---|---|---|
| [Planalto](https://www.planalto.gov.br/) | CF/88 (com ADCT), Código Civil, CDC | ~4 MB de HTML · 8.894 dispositivos · 8.345 chunks |

**Particularidades do Planalto**, descobertas testando as fontes: o servidor bloqueia
requisições sem `User-Agent` de navegador; as páginas são `cp1252` sem declarar `charset`;
não há tags semânticas, e as âncoras nomeadas não servem de guia — quando um artigo tem
várias redações, a âncora principal aponta para uma já superada. A hierarquia vem do padrão
textual (cabeçalho em linha própria, "§ 1º", inciso romano com travessão), e a âncora só
corrobora.

E o mais importante: **`<strike>` marca redação superada, não revogação** — o art. 6º da CF
aparece quatro vezes na página, três riscadas e a vigente. Cada redação superada vira um
dispositivo próprio, com a vigência de quando valia, e é isso que viabiliza a consulta
retroativa. Um parser que ignore o `<strike>` indexa versões conflitantes do mesmo artigo
como direito vigente.

**Sobre o LexML:** a API SRU está atrás de desafio anti-bot do Senado e devolve HTML de
interstício em vez de XML. Descartado como fonte automatizável.

**Sobre jurisprudência:** não há nenhuma no corpus hoje. Não existe API pública de busca de
inteiro teor do STF ou do STJ; a [API do DataJud](https://www.cnj.jus.br/sistemas/datajud/api-publica/)
(CNJ) expõe metadados de processos, não o texto das decisões. O candidato escolhido são os
**precedentes qualificados** do [STJ](https://dadosabertos.web.stj.jus.br/) — 4.728 registros
de teses vinculantes, curtas, canônicas, com um campo `referenciaLegislativa` que liga cada tese ao
dispositivo que ela interpreta, o que permitiria cruzar os dois corpora. Ficou para depois:
da máquina de desenvolvimento, o portal do STJ não respondeu. Inteiro teor de acórdãos é
problema de aquisição de dados, não de recuperação, e fica fora do escopo.

---

## Avaliação

Um golden set de 13 perguntas com o dispositivo correto anotado à mão, medindo:

- **`recall@k`** da recuperação — o dispositivo certo está entre os k recuperados?
- **acurácia de citação** — as citações da resposta apontam para o dispositivo correto?
- **taxa de abstenção** — o sistema diz "não encontrei" quando deveria?

A última importa tanto quanto as outras: um sistema jurídico que sempre responde é pior
que um que admite lacuna.

### Recuperação — medido em 2026-09-22

11 perguntas (as 2 de abstenção ficam fora da média — não há dispositivo a recuperar),
corpus de 8345 chunks, `k_busca=50`:

| configuração | recall@5 | recall@10 | MRR |
|---|---|---|---|
| fusão RRF, sem expansão | 0,879 | 0,879 | 0,705 |
| **+ expansão até o artigo** | **0,970** | **1,000** | 0,803 |
| + rerank cross-encoder | 0,879 | 1,000 | **0,879** |

Como ler: a expansão inciso→artigo vale +0,09 de recall@5; o cross-encoder leva 8 das 11
perguntas para a primeira posição (MRR 0,88), mas **derruba** o recall@5 num caso — a
consulta retroativa, onde a resposta certa é justamente o texto que ainda NÃO mencionava o
termo perguntado. O reranker pontua por conteúdo e é cego à data.

Com 11 perguntas, uma única mudança de posição move o agregado em 9 pontos. Os números
servem para comparar configurações entre si, não como medida absoluta de qualidade — e
cada linha acima reproduz idêntica entre execuções, o que não era verdade antes de o
desempate da fusão virar determinístico.

Reproduzir:

```bash
uv run python -m consulta_juridica.eval validar              # confere o golden contra o corpus
uv run python -m consulta_juridica.eval recuperacao --sem-rerank
uv run python -m consulta_juridica.eval recuperacao
```

### Resposta e citação — a medir

Implementado e **ainda não executado**: medir exige chamar o modelo, e os números abaixo
só existem depois da primeira rodada paga. Estimativa, calculada sem gastar nada:
13 perguntas, ~52 mil tokens de entrada no Claude Opus 5, **US$ 0,42 a 2,92** — a faixa é
larga porque a saída depende de quanto o modelo pensa. Reexecutar sai de graça: as
respostas ficam em cache pelo hash do pedido.

| métrica | o que mede | valor |
|---|---|---|
| acurácia de citação | das citações feitas, quantas caem no dispositivo esperado ou dentro dele | *a medir* |
| abstenção correta | das perguntas sem resposta no corpus, em quantas o sistema admite a lacuna | *a medir* |
| abstenção indevida | das perguntas respondíveis, em quantas o sistema se recusa à toa | *a medir* |

As duas taxas de abstenção andam juntas porque cada uma sozinha se ganha trapaceando: um
sistema que sempre se abstém acerta todas as perguntas sem resposta. A acurácia de citação
conta só as perguntas que o sistema respondeu e devia responder — a abstenção indevida já é
falha, medida à parte, e somá-la ali contaria a mesma falha duas vezes.

```bash
uv run python -m consulta_juridica.eval e2e              # estima o custo e sai
uv run python -m consulta_juridica.eval e2e --confirmar  # executa (requer ANTHROPIC_API_KEY)
```

O eval recusa os backends sem citations nativas (`CJ_BACKEND_LLM=ollama` e `cli`): as
citações deles são âncoras que o modelo pode ou não emitir, e um número medido assim não
significa nada.

---

## Rodando localmente

```bash
uv sync
cp .env.example .env     # preencha ANTHROPIC_API_KEY se for gerar respostas com o Claude
docker compose up -d     # Qdrant local (e o Ollama, para gerar sem chave)
```

Ingestão, em três estágios independentes — baixar depende de rede, parsear não, indexar
carrega modelos:

```bash
uv run python -m consulta_juridica.ingest baixar
uv run python -m consulta_juridica.ingest ingerir     # bruto -> SQLite
uv run python -m consulta_juridica.ingest reindexar   # SQLite -> Qdrant, ~52 min na CPU
uv run python -m consulta_juridica.ingest status      # o que já está no SQLite e no Qdrant
```

Na primeira execução, os modelos de embedding e de rerank descem do Hugging Face: ~6,5 GB.

Consulta:

```bash
# inspeciona a recuperação sem gastar token
uv run python -m consulta_juridica.retrieval "prazo para contestação" --data 2026-09-20

# mede recall@k e MRR contra o golden set, também sem gastar token
uv run python -m consulta_juridica.eval recuperacao

# API + front em http://localhost:8000
uv run uvicorn consulta_juridica.api.app:criar_app --factory --reload
```

A recuperação leva ~13 s por consulta com o rerank e ~0,5 s sem ele, medido na CPU.

`ANTHROPIC_API_KEY` só é necessária para a etapa de geração com o Claude. Ingestão,
indexação e avaliação de recuperação rodam sem chave — os modelos de embedding e de rerank
são locais. O cliente da Anthropic é construído sem validar a chave, então a falta dela só
aparece na primeira chamada, e a API responde 503 dizendo o que fazer.

### Sem chave: geração local com Ollama

O `docker-compose.yml` também sobe um Ollama com a GPU passada ao container. Com ele, o
sistema responde de ponta a ponta sem chave e sem custo:

```bash
docker compose up -d                                  # Qdrant + Ollama
docker compose exec ollama ollama pull qwen3.5:4b     # ~3,4 GB, uma vez
docker compose exec ollama ollama ps                  # PROCESSOR tem de dizer "100% GPU"

# no .env: CJ_BACKEND_LLM=ollama e CJ_USAR_RERANK=false
uv run uvicorn consulta_juridica.api.app:criar_app --factory
```

Medido numa RTX 3060 Laptop (6 GB), com recuperação sem rerank:

| | raciocínio desligado | raciocínio ligado (padrão) |
|---|---|---|
| tempo por resposta, pela API | 4–9 s | 47–96 s |
| `vig-02`: transporte era direito social em 2010? | ❌ afirma que o art. 6º de 2010 lista o transporte — não lista | ✅ nota a ausência na lista de 2010 (mas sob a marca de abstenção, e isso é uma resposta: "não") |
| `abs-01`: prazo de recurso no processo civil (não está no corpus) | recusa, mas sem a marca: o sistema não detecta | ✅ recusa, detectada |
| `sem-02`: devolver compra feita pela internet | ✅ art. 49 do CDC | ❌ recusa: lê o "especialmente" do art. 49 como taxativo |

**Nenhum dos dois modos é confiável com um modelo de 4B**, e quatro perguntas são amostra
pequena. O padrão é o raciocínio ligado porque a falha dele é recusar demais; a do modo
rápido é apresentar texto posterior como direito vigente na data — o pior defeito possível
neste domínio. Um modelo maior não cabe inteiro nos 6 GB (o `qwen3.5:9b` tem 6,6 GB).

O backend local não tem citations nativas: as citações são âncoras `[D1]` que apontam o
trecho, não um intervalo dentro dele, e o eval ponta a ponta o recusa. Serve para ver o
sistema responder, não para medir citação. (`CJ_BACKEND_LLM=cli` faz o mesmo pelo Claude
Code, `claude -p`, consumindo a assinatura.)

---

## Limitações

- **Não é consulta jurídica.** É ferramenta de pesquisa. As respostas precisam ser
  conferidas contra a fonte oficial antes de qualquer uso profissional.
- **Não há jurisprudência no corpus**, só legislação federal: CF/88, Código Civil e CDC.
- Direito sumulado e entendimento consolidado mudam; o corpus é um retrato datado.
- Sem cobertura de legislação estadual ou municipal.

### A letra da lei não é o direito vigente — o caso da prisão civil

A pergunta `lim-01` do golden set existe para deixar este limite à vista. "Prisão civil por
dívida é permitida no Brasil?" O art. 5º, LXVII, da Constituição responde que não, **salvo**
a do devedor de pensão alimentícia e a do depositário infiel. É o texto vigente, e é o que
o sistema recupera e cita — corretamente.

Mas a Súmula Vinculante 25 do STF declarou ilícita a prisão do depositário infiel. A
resposta juridicamente certa hoje é "só a do devedor de alimentos", e com apenas a
legislação no corpus o sistema não tem como saber disso. A resposta sai fiel à fonte e
incompleta como direito.

Não há correção possível sem jurisprudência no corpus — e nem os precedentes do STJ
resolveriam, porque a súmula é do STF. Mascarar o caso com uma regra especial seria
esconder exatamente o que ele mostra: **um sistema que só lê lei responde o que a lei diz,
não o que os tribunais decidiram sobre ela.** Por isso a pergunta fica no golden, com o
esperado apontando para o dispositivo — o que se mede é se a citação está certa, não se o
direito está completo.

### Vigência com granularidade de ano

As notas do Planalto dizem "Redação dada pela Emenda Constitucional nº 90, de 2015", sem a
data da publicação. O sistema grava a vigência a partir de 1º de janeiro do ano; obter a
data exata exigiria baixar e interpretar cada emenda. Numa consulta retroativa, o erro
máximo é de cerca de um ano, na direção de antecipar a nova redação. Redações riscadas sem
ano extraível ficam com a revogação na data de publicação da norma — são 155 no corpus
atual — e com isso fora de toda consulta. Conservador na direção certa, porque nunca
apresenta texto morto como vigente, mas as torna invisíveis para a pesquisa histórica.

### O reranker não enxerga a data

Medido na avaliação de recuperação: numa consulta retroativa, a resposta certa pode ser
justamente o texto que ainda **não** mencionava o termo perguntado. "O transporte é um
direito social?" em 2010 deve trazer o art. 6º da CF na redação anterior à EC 90/2015, que
não fala em transporte. A fusão o põe em primeiro; o cross-encoder, que pontua por
conteúdo, o empurra para sexto. O filtro de vigência garante que o texto errado nunca
aparece — mas não garante que o certo venha em primeiro.

### Remissão só dentro da mesma norma

"Na forma do art. 37, § 6º" é resolvido; "nos termos da Lei nº 8.078" não é, porque exigiria
um catálogo de toda a legislação citada. A remissão entre normas se perde, e nenhuma é
inventada. E as remissões resolvidas ainda não entram na busca: ficam gravadas no SQLite
para a expansão por remissão do roadmap.

---

## Roadmap

- [x] Ingestão com parser estrutural — CF/88, Código Civil e CDC
- [x] Índice híbrido e busca com filtro de vigência
- [x] Geração com citations nativas — implementada, ainda sem execução contra a API
- [x] Geração local sem chave, via Ollama
- [x] API de consulta + front de teste com painel de recuperação
- [x] Golden set e eval de recuperação
- [ ] Primeira rodada do eval ponta a ponta — requer `ANTHROPIC_API_KEY`
- [ ] Jurisprudência: precedentes qualificados do STJ, cruzados com a legislação
- [ ] Expansão por remissões (1 hop)

---

## Licença

MIT — ver [LICENSE](LICENSE).
