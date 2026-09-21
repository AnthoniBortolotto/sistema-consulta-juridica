# Mapa do código

Índice de onde cada coisa vive. Consulte antes de procurar no repositório; atualize ao
criar um módulo novo.

**Formato:** uma linha por módulo — `caminho` — o que vive ali / quando olhar aqui.
Índice, não documentação: sem detalhes de implementação, sem exemplos de uso.

---

## Núcleo

Do que todo o resto depende. Mexer aqui repercute em tudo.

- `src/consulta_juridica/models.py` — tipos de domínio (Dispositivo, Chunk, Trecho, Citacao, Resposta). Contrato central; `ChunkPayload` é fronteira de serialização com o Qdrant.
- `src/consulta_juridica/config.py` — `Settings`. Único módulo que lê variáveis de ambiente.
- `src/consulta_juridica/errors.py` — exceções de domínio.
- `src/consulta_juridica/urn.py` — identificadores LexML: montar, parsear, apelidos, URL da fonte.
- `src/consulta_juridica/vectorstore.py` — vocabulário da coleção Qdrant (nomes de vetor e de campo), sentinela de vigência, `id_ponto`. Olhe aqui antes de escrever qualquer nome de payload.
- `src/consulta_juridica/embedding.py` — `Encoder`: denso (bge-m3) e esparso (BM25). Indexação e consulta lado a lado, de propósito.
- `src/consulta_juridica/service.py` — `Servico` e composition root. Único lugar que constrói as dependências.

## Fonte da verdade — SQLite

- `src/consulta_juridica/store/schema.sql` — DDL do corpus.
- `src/consulta_juridica/store/db.py` — conexão (com modo somente-leitura) e transação.
- `src/consulta_juridica/store/writer.py` — escrita. Só a ingestão importa.
- `src/consulta_juridica/store/queries.py` — leitura: ancestrais, subárvore, artigo ancestral, remissões.

## Ingestão

- `src/consulta_juridica/ingest/fontes.py` — download do Planalto e LexML para `data/raw/`.
- `src/consulta_juridica/ingest/parser.py` — bruto → árvore de dispositivos. Fidelidade à fonte, nada de chunking.
- `src/consulta_juridica/ingest/chunking.py` — estratégias de chunk. A decisão mais consequente do sistema.
- `src/consulta_juridica/ingest/indexer.py` — escrita no Qdrant (apaga e reinsere por norma).
- `src/consulta_juridica/ingest/pipeline.py` — estágios `baixar` / `ingerir` / `reindexar`.
- `src/consulta_juridica/ingest/__main__.py` — CLI da ingestão.

## Recuperação

- `src/consulta_juridica/retrieval/filtros.py` — `Criterios` → filtro Qdrant. Onde moram os bugs de vigência.
- `src/consulta_juridica/retrieval/busca.py` — busca híbrida com RRF server-side.
- `src/consulta_juridica/retrieval/rerank.py` — cross-encoder, e um reranker identidade para medir a recuperação pura.
- `src/consulta_juridica/retrieval/expansao.py` — inciso → artigo via SQLite, reaplicando a vigência.
- `src/consulta_juridica/retrieval/pipeline.py` — `Recuperador`, com os modelos injetados.
- `src/consulta_juridica/retrieval/__main__.py` — inspeção da recuperação sem gastar token.

## Geração

- `src/consulta_juridica/generation/backend.py` — `LLMBackend`, `Pedido`, `BlocoDocumento`. Decisão sync-não-async registrada aqui.
- `src/consulta_juridica/generation/claude_api.py` — Messages API com citations nativas.
- `src/consulta_juridica/generation/claude_cli.py` — `claude -p` por subprocess, sem citations.
- `src/consulta_juridica/generation/cache.py` — decorator de cache em disco sobre qualquer backend.
- `src/consulta_juridica/generation/prompt.py` — todo o texto de instrução e `VERSAO_PROMPT`.
- `src/consulta_juridica/generation/citacoes.py` — citação bruta → `Citacao` de domínio.

## API

- `src/consulta_juridica/api/app.py` — `criar_app` e lifespan (carrega modelos uma vez).
- `src/consulta_juridica/api/deps.py` — injeção do serviço a partir de `app.state`.
- `src/consulta_juridica/api/schemas.py` — DTOs HTTP, separados do domínio.
- `src/consulta_juridica/api/web/index.html` — front de teste (placeholder).

## Eval

- `src/consulta_juridica/eval/golden.py` — golden set anotado e sua validação contra o SQLite.
- `src/consulta_juridica/eval/metrics.py` — recall@k, MRR, acurácia de citação, taxa de abstenção. Funções puras.
- `src/consulta_juridica/eval/run.py` — `avaliar_recuperacao` (sem LLM) e `avaliar_ponta_a_ponta`.
- `src/consulta_juridica/eval/__main__.py` — CLI do eval.

## Testes

- `tests/test_filtros.py` — vigência. Prioridade máxima: erro aqui não levanta exceção.
- `tests/test_expansao.py` — inciso → artigo, incluindo o vazamento de vigência pelo SQLite.
- `tests/test_parser.py` — hierarquia, contra HTML real em `tests/fixtures/`.
- `tests/test_chunking.py` — granularidade e texto contextualizado.

---

> Todos os módulos existem como esqueleto: docstring, imports e assinaturas, com o corpo em
> `NotImplementedError`. A estrutura está de pé; a implementação, não.
