# Consulta Jurídica com RAG

Busca e resposta sobre legislação federal brasileira — Constituição, Código Civil e Código
de Defesa do Consumidor — que roda inteira na sua máquina, **sem chave de API**. A resposta
sai só de trechos de lei recuperados, **vigentes na data que você pergunta**, e aponta o
artigo de onde veio cada afirmação.

Projeto de portfólio, encerrado. O foco não é quantidade de funcionalidades: são decisões
defensáveis e números medidos — inclusive os que saíram ruins, registrados aqui junto com os
bons.

---

## Em resumo

- **A data de referência muda a resposta.** "O transporte é um direito social?" em 2026
  recupera o art. 6º da Constituição com o transporte na lista; em 2010, a redação daquele
  ano, que não o menciona. Cada redação superada da lei é indexada com a própria vigência.
- **Busca híbrida, medida:** recall@5 de 0,970 num golden set anotado à mão. A expansão
  inciso→artigo vale +0,09; o reranker melhora a ordem, mas derruba o recall numa consulta
  retroativa.
- **Tudo local:** embeddings, busca, rerank e geração — um Qwen 3.5 de 4B via Ollama, na
  GPU. Nenhuma chamada paga.
- **Os limites estão medidos, não escondidos.** Sem raciocínio, o modelo local afirmou texto
  posterior como vigente em 2010; com raciocínio, acerta esse caso e passa a recusar outro.
  Está em [Resultados](#resultados).
- 386 testes.

---

## Estado final

**O que o sistema faz:** baixa a legislação do site do Planalto e a transforma numa árvore
de dispositivos, com hierarquia e redações superadas; indexa num Qdrant com busca densa e
esparsa; recupera com filtro de vigência; gera a resposta com um modelo local; serve tudo
por uma API FastAPI, com um front de teste que mostra os trechos recuperados e seus scores;
e mede a recuperação contra um golden set.

**O que ficou de fora:**

- **Citação literal até o inciso.** O desenho original usava as citations nativas da API do
  Claude, que devolvem o trecho literal citado e a posição dele no documento — e o sistema
  traduz essa posição para o inciso ou parágrafo citado. O backend está no código
  (`generation/claude_api.py`) e testado contra dublês, mas nunca foi executado contra a
  API: a versão final é sem chave. Com o modelo local, a citação é uma âncora `[D1]` pedida
  pelo prompt, que aponta o artigo recuperado, não uma frase dele.
- **Avaliação de resposta e citação.** Está implementada, mas exige citations nativas:
  medir acurácia de citação sobre âncoras que o modelo pode ou não emitir não significaria
  nada. A geração local foi avaliada à mão, em quatro perguntas do golden.
- **Jurisprudência.** Os precedentes qualificados do STJ foram escolhidos como fonte e não
  chegaram a ser integrados.
- **Expansão por remissões.** As remissões ("na forma do art. 37, § 6º") são extraídas e
  gravadas no banco, mas a busca não as usa.

---

## Como rodar

### Requisitos

- Python 3.11+ e [uv](https://docs.astral.sh/uv/)
- Docker — no Windows, Docker Desktop com WSL2
- **GPU NVIDIA** com ~5 GB livres, para a geração. Sem ela, veja
  [Sem GPU NVIDIA](#sem-gpu-nvidia).
- ~15 GB de disco: modelos de busca (~6,5 GB), imagem do Ollama (~3,7 GB) e o Qwen (~3,4 GB)

### 1. Instalar

```bash
uv sync
```

Não há o que configurar: o padrão já é a geração local, sem chave, e com o reranker
desligado — ele custa ~11 s por consulta na CPU e, medido, não melhora o que chega ao
modelo. Para ajustar alguma coisa, copie `.env.example` para `.env`; cada opção está
comentada lá.

### 2. Subir os serviços e baixar o modelo

```bash
docker compose up -d                                  # Qdrant + Ollama
docker compose exec ollama ollama pull qwen3.5:4b     # ~3,4 GB, uma vez
```

### 3. Gerar o corpus e o índice (uma vez)

O repositório não traz os dados: eles são baixados do Planalto e indexados localmente. Leva
perto de uma hora, quase toda na indexação.

```bash
uv run python -m consulta_juridica.ingest baixar      # Planalto -> data/raw/   (segundos)
uv run python -m consulta_juridica.ingest ingerir     # bruto -> SQLite         (segundos)
uv run python -m consulta_juridica.ingest reindexar   # SQLite -> Qdrant        (~52 min na CPU)
uv run python -m consulta_juridica.ingest status      # confere o que foi gerado
```

Na primeira indexação, os modelos de embedding e de rerank descem do Hugging Face (~6,5 GB).

### 4. Abrir

```bash
uv run uvicorn consulta_juridica.api.app:criar_app --factory
```

Front em **http://localhost:8000**, documentação da API em `/docs`. Faça a pergunta e
escolha a data de referência — troque para 2010 para ver o direito vigente naquele ano.

A primeira resposta leva ~2 min, porque o modelo sobe para a GPU; as seguintes, 50 a 90 s.
O raciocínio do modelo fica ligado porque, sem ele, o modelo erra o caso mais grave (ver
[Resultados](#resultados)). Com `CJ_OLLAMA_PENSAR=false` num `.env`, as respostas caem para
4 a 9 s — com esse risco. Depois da primeira pergunta, `docker compose exec ollama ollama ps`
deve mostrar `100% GPU`; se mostrar CPU, a GPU não chegou ao container.

### Sem GPU NVIDIA

`docker compose up -d` falha na reserva de GPU do Ollama. Suba só o Qdrant
(`docker compose up -d qdrant`): a busca, o inspetor de recuperação e a avaliação funcionam;
a geração não.

### Sem subir a API

```bash
# o que a busca recupera para uma pergunta, com os scores de fusão e de rerank
uv run python -m consulta_juridica.retrieval "o transporte é um direito social?" --data 2010-01-01

# recall@k e MRR contra o golden set
uv run python -m consulta_juridica.eval recuperacao

# testes — os que precisam do Qdrant pulam se ele não estiver de pé
uv run pytest
```

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
   Qwen 3.5 4B via Ollama, na GPU  ◄──────────────────────────────────────────┘
            │
            └─► resposta, com cada afirmação apontando o artigo de onde veio
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
cosseno e score BM25 não compartilham escala calibrada. Um reranker cross-encoder pode
reordenar os 50 candidatos da fusão, mas vem desligado: custa ~11 s por consulta na CPU e,
medido, melhora a ordem mas não o recall — o recall@5 até cai.

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

### Citação: o que o desenho previa e o que a versão final entrega

O desenho original mandava os trechos à API do Claude como blocos `document` com citations
nativas. A resposta volta com o trecho literal de cada citação e a posição dele no
documento; o sistema traduz a posição de volta ao inciso ou parágrafo citado e confere,
citação a citação, que o texto está mesmo no trecho enviado. É citação garantida pela API,
não pedida por prompt.

A versão final roda sem chave, e com um modelo local a citação **é** pedida por prompt: o
modelo marca `[D1]` ao fim de cada afirmação, e a marca aponta o trecho — o artigo —, não
uma frase dele. É mais fraco, e o sistema não finge o contrário: a avaliação de citação se
recusa a rodar sobre âncoras. O backend do Claude continua no código, testado contra
dublês, mas nunca foi executado contra a API.

### Geração local: o que só a execução mostrou

- **O contexto padrão do Ollama cortaria a instrução de sistema.** O padrão é 4.096 tokens,
  e cada pedido tem ~3.100 com oito trechos. Quando o prompt passa do limite, o Ollama corta
  o começo em silêncio — que é onde está a regra de só usar os trechos. O sistema fixa
  16.384 e trata o limite atingido como erro.
- **O raciocínio do modelo fica ligado, e a escolha contrária foi refutada por medição.**
  Sem ele, o modelo recebeu a redação de 2010 do art. 6º, sem a palavra "transporte", e
  afirmou, citando-a, que ela listava o transporte.
- **O modelo cabe na GPU, e o maior não.** O `qwen3.5:9b` tem 6,6 GB e não cabe nos ~5 GB
  livres de uma GPU de 6 GB; o de 4B tem 3,4 GB e roda `100% GPU`.
- **O modelo fica num volume do Docker, não numa pasta do repositório.** Lida do disco do
  Windows pela VM do WSL2, cada recarga levava ~100 s; do volume, 10 s.

### Sem framework de orquestração

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
| Embeddings | `BAAI/bge-m3` (local, CPU) |
| Busca esparsa | BM25 (`Qdrant/bm25` via fastembed, com stemmer em português) |
| Reranker | `BAAI/bge-reranker-v2-m3` (local, CPU, desligado por padrão) |
| Geração | `qwen3.5:4b` via Ollama, em container com a GPU |
| Front de teste | Vue 3 por CDN, servido como estático |

---

## Resultados

Um golden set de 13 perguntas, com o dispositivo correto anotado à mão, cobre os mecanismos
que o sistema precisa acertar: busca léxica e semântica, vigência, expansão, ambiguidade e
abstenção — duas perguntas não têm resposta no corpus, e o certo é o sistema dizer isso.
Está em [`golden/seed.jsonl`](golden/seed.jsonl).

### Recuperação — medida em 2026-09-22

11 perguntas (as 2 de abstenção ficam fora da média — não há dispositivo a recuperar),
corpus de 8.345 chunks, 50 candidatos por busca:

| configuração | recall@5 | recall@10 | MRR |
|---|---|---|---|
| fusão RRF, sem expansão | 0,879 | 0,879 | 0,705 |
| **+ expansão até o artigo** | **0,970** | **1,000** | 0,803 |
| + rerank cross-encoder | 0,879 | 1,000 | **0,879** |

Como ler: a expansão inciso→artigo vale +0,09 de recall@5. O cross-encoder leva 8 das 11
perguntas para a primeira posição (MRR 0,88), mas **derruba** o recall@5 num caso — a
consulta retroativa, onde a resposta certa é justamente o texto que ainda não mencionava o
termo perguntado. O reranker pontua por conteúdo e é cego à data.

Com 11 perguntas, uma única mudança de posição move o agregado em 9 pontos: os números
servem para comparar configurações entre si, não como medida absoluta de qualidade. Cada
linha reproduz idêntica entre execuções.

### Geração local — medida à mão, em quatro perguntas

RTX 3060 Laptop (6 GB), recuperação sem rerank, respostas pela API:

| | raciocínio desligado | raciocínio ligado (padrão) |
|---|---|---|
| tempo por resposta | 4–9 s | 47–96 s |
| `vig-02`: transporte era direito social em 2010? | ❌ afirma que o art. 6º de 2010 lista o transporte — não lista | ✅ nota a ausência na lista de 2010 (mas sob a marca de abstenção, e a resposta existe: "não") |
| `vig-01`: a mesma pergunta em 2026 | não medido | ✅ art. 6º, com o transporte |
| `abs-01`: prazo de recurso no processo civil (não está no corpus) | recusa, mas sem a marca: o sistema não detecta | ✅ recusa, detectada |
| `sem-02`: devolver compra feita pela internet | ✅ art. 49 do CDC | ❌ recusa: lê o "especialmente" do art. 49 como taxativo |

**Nenhum dos dois modos é confiável com um modelo de 4B**, e quatro perguntas são amostra
pequena. O padrão é o raciocínio ligado porque a falha dele é recusar demais; a do modo
rápido é apresentar texto posterior como direito vigente na data — o pior defeito possível
neste domínio.

### Resposta e citação — não medida

A avaliação ponta a ponta está implementada (`eval e2e`), com três métricas: acurácia de
citação, abstenção correta (das perguntas sem resposta, em quantas o sistema admite a
lacuna) e abstenção indevida (das respondíveis, em quantas recusa à toa). As duas taxas de
abstenção andam juntas porque cada uma sozinha se ganha trapaceando — um sistema que sempre
se abstém acerta todas as perguntas sem resposta.

Ela nunca rodou: exige o backend do Claude, com citations nativas, e a versão final não usa
chave.

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

**Sobre jurisprudência:** não há nenhuma no corpus. Não existe API pública de busca de
inteiro teor do STF ou do STJ; a [API do DataJud](https://www.cnj.jus.br/sistemas/datajud/api-publica/)
(CNJ) expõe metadados de processos, não o texto das decisões. O candidato escolhido foram os
**precedentes qualificados** do [STJ](https://dadosabertos.web.stj.jus.br/) — 4.728
registros de teses vinculantes, com um campo `referenciaLegislativa` que liga cada tese ao
dispositivo que ela interpreta —, que não chegaram a ser integrados.

---

## Limitações

- **Não é consulta jurídica.** É ferramenta de pesquisa. As respostas precisam ser
  conferidas contra a fonte oficial antes de qualquer uso profissional.
- **O modelo local não é confiável**, como mostram os [resultados](#geração-local--medida-à-mão-em-quatro-perguntas):
  erra ou recusa em perguntas que a recuperação acertou.
- **Não há jurisprudência no corpus**, só legislação federal: CF/88, Código Civil e CDC.
- Direito sumulado e entendimento consolidado mudam; o corpus é um retrato datado.
- Sem cobertura de legislação estadual ou municipal.

### A letra da lei não é o direito vigente — o caso da prisão civil

A pergunta `lim-01` do golden set existe para deixar este limite à vista. "Prisão civil por
dívida é permitida no Brasil?" O art. 5º, LXVII, da Constituição responde que não, **salvo**
a do devedor de pensão alimentícia e a do depositário infiel. É o texto vigente, e é o que
o sistema recupera — corretamente.

Mas a Súmula Vinculante 25 do STF declarou ilícita a prisão do depositário infiel. A
resposta juridicamente certa hoje é "só a do devedor de alimentos", e com apenas a
legislação no corpus o sistema não tem como saber disso. A resposta sai fiel à fonte e
incompleta como direito.

Não há correção possível sem jurisprudência no corpus — e nem os precedentes do STJ
resolveriam, porque a súmula é do STF. Mascarar o caso com uma regra especial seria
esconder exatamente o que ele mostra: **um sistema que só lê lei responde o que a lei diz,
não o que os tribunais decidiram sobre ela.**

### Vigência com granularidade de ano

As notas do Planalto dizem "Redação dada pela Emenda Constitucional nº 90, de 2015", sem a
data da publicação. O sistema grava a vigência a partir de 1º de janeiro do ano; obter a
data exata exigiria baixar e interpretar cada emenda. Numa consulta retroativa, o erro
máximo é de cerca de um ano, na direção de antecipar a nova redação. Redações riscadas sem
ano extraível ficam com a revogação na data de publicação da norma — são 155 no corpus — e
com isso fora de toda consulta. Conservador na direção certa, porque nunca apresenta texto
morto como vigente, mas as torna invisíveis para a pesquisa histórica.

### O reranker não enxerga a data

Numa consulta retroativa, a resposta certa pode ser justamente o texto que ainda **não**
mencionava o termo perguntado. "O transporte é um direito social?" em 2010 deve trazer o
art. 6º da CF na redação anterior à EC 90/2015, que não fala em transporte. A fusão o põe
em primeiro; o cross-encoder, que pontua por conteúdo, o empurra para sexto. O filtro de
vigência garante que o texto errado nunca aparece — mas não garante que o certo venha em
primeiro. É um dos motivos de o reranker vir desligado por padrão.

### Remissão só dentro da mesma norma

"Na forma do art. 37, § 6º" é resolvido; "nos termos da Lei nº 8.078" não é, porque exigiria
um catálogo de toda a legislação citada. A remissão entre normas se perde, e nenhuma é
inventada.

---

## Estrutura do código

- [`CODEBASE-MAP.md`](CODEBASE-MAP.md) — índice de onde cada coisa vive, módulo a módulo.
  Comece por ele.
- `src/consulta_juridica/` — `ingest/` (download, parser, chunking, indexação), `store/`
  (SQLite), `retrieval/` (filtro de vigência, busca, rerank, expansão), `generation/`
  (prompt, backends, cache, citação), `api/` (FastAPI e o front), `eval/` (golden set e
  métricas).
- [`golden/seed.jsonl`](golden/seed.jsonl) — as 13 perguntas anotadas à mão.
- `tests/` — 386 testes, com prioridade para os de vigência: erro ali não levanta exceção,
  só devolve o resultado errado.
- [`CLAUDE.md`](CLAUDE.md) — as regras do projeto: decisões de arquitetura e as seis
  armadilhas do domínio que separam o sistema funcionar de parecer funcionar.

---

## Licença

MIT — ver [LICENSE](LICENSE).
