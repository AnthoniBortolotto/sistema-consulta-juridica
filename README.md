# Consulta Jurídica com RAG

Busca e resposta fundamentada sobre legislação brasileira, com **citação verificável** —
cada afirmação da resposta carrega o trecho literal e o dispositivo de onde veio.

> **Status:** em desenvolvimento. O desenho está fechado e documentado abaixo; a
> implementação está em andamento. Seções marcadas com 🚧 ainda não têm código.

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
pergunta + filtros (vigência, norma)
      │
      ├──► busca densa    bge-m3        ──┐
      │                                    ├──► RRF ──► rerank ──► top-8
      ├──► busca esparsa  BM25          ──┘          bge-reranker-v2-m3
      │
      │    (filtro de vigência aplicado na travessia do índice)
      │
      └──► Claude, com os trechos como blocos `document` + citations
                  │
                  └──► resposta + citações rastreáveis até o dispositivo
```

---

## Decisões de arquitetura

A parte interessante do projeto não é chamar um LLM — é o que vem antes.

### Chunking estrutural, não janela fixa

Texto jurídico tem hierarquia explícita: Título → Capítulo → Seção → Artigo → Parágrafo →
Inciso → Alínea. Partir por janela de N tokens corta artigos no meio e separa o inciso do
caput que lhe dá sentido.

Aqui a unidade de indexação é o **dispositivo**, e cada chunk carrega a hierarquia inteira,
a norma de origem e o intervalo de vigência. Um inciso recuperado é expandido para o artigo
completo antes de ir ao modelo, porque inciso isolado frequentemente é ininteligível.

### Busca híbrida, não só vetorial

As duas consultas abaixo são igualmente comuns e exigem mecanismos diferentes:

| Consulta | Natureza |
|---|---|
| `art. 5º, LXXVIII` | léxica pura — embeddings erram em numeração |
| `responsabilidade objetiva do Estado` | semântica |

Busca densa e esparsa rodam juntas e são fundidas por Reciprocal Rank Fusion. RRF usa a
*posição* de cada candidato, não o score bruto, porque similaridade de cosseno e score BM25
não compartilham escala calibrada. Um reranker cross-encoder reordena o top-50 para top-8.

### Vigência como campo de primeira classe

Responder com norma revogada como se fosse vigente é o pior defeito possível no domínio.
Todo chunk carrega `vigencia_inicio` e `revogado_em`, e toda consulta filtra por uma data de
referência — que é parâmetro da API, não `now()` implícito, para permitir consulta sobre o
direito vigente à época de um fato.

### Fonte da verdade relacional, índice derivado

O corpus canônico — árvore de dispositivos, vigências e remissões — vive em SQLite. O Qdrant
é **índice derivado e reconstruível**, não banco primário.

A razão é concreta: bancos vetoriais não têm join, então expansão inciso→artigo, hierarquia
e remissões precisariam ser desnormalizadas no payload. Quando uma emenda altera um artigo,
a desnormalização exige reescrever todos os chunks filhos sem transação. Com índice
derivado, reingestão é rebuild — não edição de estado mutável.

*Tradeoff honesto:* isso custa um processo de build e duas cópias do texto. Em troca, a
correção da consistência é estrutural em vez de disciplinar.

### Citação garantida pela API, não pedida por prompt

Os trechos recuperados são enviados como blocos `document` com citations habilitadas na
Messages API. A resposta volta particionada, e os blocos citados carregam o `cited_text` e
os índices exatos do documento de origem.

A alternativa comum — instruir o modelo a citar e confiar na obediência — falha
silenciosamente e de forma difícil de detectar em escala.

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
| Reranker | `BAAI/bge-reranker-v2-m3` (local) |
| Geração | Claude via SDK `anthropic`, com citations |
| Front de teste | Vue 3 por CDN, servido como estático |

Embeddings e reranking rodam localmente — indexar o corpus inteiro não custa nada em API.

---

## Corpus

| Fonte | O que entra | Volume |
|---|---|---|
| [Planalto](https://www.planalto.gov.br/) | CF/88, Código Civil, CDC | ~2,9 MB · ~8.000 dispositivos |
| [Dados abertos do STJ](https://dadosabertos.web.stj.jus.br/) | Precedentes qualificados (teses firmadas) | 2,5 MB · 4.728 registros |

**Particularidades do Planalto**, descobertas testando as fontes: o servidor bloqueia
requisições sem `User-Agent` de navegador; as páginas são `cp1252` sem declarar `charset`;
não há tags semânticas, então a hierarquia vem de âncoras nomeadas (`<a name="art5lxxviii">`)
quando existem, e de padrão textual quando não. E o mais importante: **`<strike>` marca
redação superada, não revogação** — o art. 6º da CF aparece três vezes na página, duas
riscadas e a vigente fora. Isso faz da página um histórico temporal utilizável, e é o que
viabiliza o filtro de vigência; um parser que ignore o `<strike>` indexa versões
conflitantes do mesmo artigo como direito vigente.

**Sobre o LexML:** a API SRU está atrás de desafio anti-bot do Senado e devolve HTML de
interstício em vez de XML. Descartado como fonte automatizável.

**Sobre jurisprudência:** não existe API pública de busca de inteiro teor do STF ou do STJ.
A [API do DataJud](https://www.cnj.jus.br/sistemas/datajud/api-publica/) (CNJ) expõe
metadados de processos, não o texto das decisões. A escolha aqui são os **precedentes
qualificados** do STJ: teses vinculantes, curtas, canônicas, e com um campo
`referenciaLegislativa` que liga cada tese ao dispositivo que ela interpreta — o que permite
cruzar os dois corpora. Inteiro teor de acórdãos é problema de aquisição de dados, não de
recuperação, e fica fora do escopo.

---

## Avaliação 🚧

Um golden set de perguntas com o dispositivo correto anotado à mão, medindo:

- **`recall@k`** da recuperação — o dispositivo certo está entre os k recuperados?
- **acurácia de citação** — as citações da resposta apontam para o dispositivo correto?
- **taxa de abstenção** — o sistema diz "não encontrei" quando deveria?

A última importa tanto quanto as outras: um sistema jurídico que sempre responde é pior
que um que admite lacuna.

*Números a preencher conforme o eval for executado.*

---

## Rodando localmente 🚧

```bash
uv sync
cp .env.example .env     # preencha ANTHROPIC_API_KEY se for gerar respostas
docker compose up -d     # Qdrant local
```

Ingestão, em três estágios independentes — baixar depende de rede, parsear não, indexar
carrega modelos:

```bash
uv run python -m consulta_juridica.ingest baixar
uv run python -m consulta_juridica.ingest ingerir     # bruto -> SQLite
uv run python -m consulta_juridica.ingest reindexar   # SQLite -> Qdrant
```

Consulta:

```bash
# inspeciona a recuperação sem gastar token
uv run python -m consulta_juridica.retrieval "prazo para contestação" --data 2026-09-20

# API + front em http://localhost:8000
uv run uvicorn consulta_juridica.api.app:criar_app --factory --reload
```

`ANTHROPIC_API_KEY` só é necessária para a etapa de geração. Ingestão, indexação e
avaliação de recuperação rodam sem chave — os modelos de embedding e de rerank são locais.

---

## Limitações

- **Não é consulta jurídica.** É ferramenta de pesquisa. As respostas precisam ser
  conferidas contra a fonte oficial antes de qualquer uso profissional.
- Cobertura de jurisprudência é estreita, pelos motivos descritos acima.
- Direito sumulado e entendimento consolidado mudam; o corpus é um retrato datado.
- Sem cobertura de legislação estadual ou municipal.

---

## Roadmap

- [x] Ingestão com parser estrutural (CF/88 primeiro)
- [x] Índice híbrido e busca com filtro de vigência
- [ ] API de consulta com citations
- [ ] Front de teste com painel de recuperação
- [ ] Golden set e eval
- [ ] Expansão por remissões (1 hop)
- [ ] Ampliação do corpus de jurisprudência

---

## Licença

MIT — ver [LICENSE](LICENSE).
