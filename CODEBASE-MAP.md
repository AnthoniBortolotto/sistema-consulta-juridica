# Mapa do código

Índice de onde cada coisa vive. Consulte antes de procurar no repositório; atualize ao
criar um módulo novo.

**Formato:** uma linha por módulo — `caminho` — o que vive ali / quando olhar aqui.
Índice, não documentação: sem detalhes de implementação, sem exemplos de uso.

---

## Núcleo

Do que todo o resto depende. Mexer aqui repercute em tudo.

- `src/consulta_juridica/models.py` — tipos de domínio (Dispositivo, Chunk, Trecho, Citacao, Resposta), `ordem_documento` e `linha_dispositivo`. Contrato central; `ChunkPayload` é fronteira de serialização com o Qdrant.
- `src/consulta_juridica/config.py` — `Settings`. Único módulo que lê variáveis de ambiente.
- `src/consulta_juridica/errors.py` — exceções de domínio.
- `src/consulta_juridica/tls.py` — confiança TLS pelo truststore do SO. Olhe aqui se um download falhar com `CERTIFICATE_VERIFY_FAILED`.
- `src/consulta_juridica/urn.py` — ID canônico do dispositivo: esquema do fragmento, segmento por rótulo, apelidos do corpus, rótulo humano <-> ID, `rotulo_completo_de`. Leia o docstring antes de inventar um ID.
- `src/consulta_juridica/vectorstore.py` — vocabulário da coleção Qdrant (nomes de vetor e de campo), sentinela de vigência, `id_ponto`, criação da coleção e checagem de drift. Olhe aqui antes de escrever qualquer nome de payload.
- `src/consulta_juridica/embedding.py` — `Encoder`: denso (bge-m3) e esparso (BM25). Indexação e consulta lado a lado, de propósito — `embed` para documento, `query_embed` para pergunta.
- `src/consulta_juridica/service.py` — `Servico` e composition root. Único lugar que constrói as dependências.

## Fonte da verdade — SQLite

- `src/consulta_juridica/store/schema.sql` — DDL do corpus.
- `src/consulta_juridica/store/db.py` — conexão (somente-leitura e compartilhável entre threads) e transação.
- `src/consulta_juridica/store/writer.py` — escrita. Só a ingestão importa.
- `src/consulta_juridica/store/queries.py` — leitura: ancestrais, subárvore, artigo ancestral, remissões, redações de um dispositivo. `PREDICADO_VIGENTE` mora aqui.

## Ingestão

- `src/consulta_juridica/ingest/fontes.py` — download do Planalto para `data/raw/`, com procedência (.meta.json + sha256) e detecção de encoding. LexML está fora: fonte eliminada.
- `src/consulta_juridica/ingest/parser.py` — bruto → árvore de dispositivos. Um caminho textual, âncora só corrobora; redação superada vira dispositivo `@N`. Fidelidade à fonte, nada de chunking.
- `src/consulta_juridica/ingest/chunking.py` — `ChunkPorDispositivo` e `ChunkPorArtigo`, e o `texto_indexado` com contexto. A decisão mais consequente do sistema.
- `src/consulta_juridica/ingest/indexer.py` — escrita no Qdrant (apaga e reinsere por norma).
- `src/consulta_juridica/ingest/pipeline.py` — estágios `baixar` / `ingerir` / `reindexar`.
- `src/consulta_juridica/ingest/__main__.py` — CLI da ingestão.

## Recuperação

- `src/consulta_juridica/retrieval/filtros.py` — `Criterios` → filtro Qdrant. Onde moram os bugs de vigência.
- `src/consulta_juridica/retrieval/busca.py` — busca híbrida com RRF server-side, `Candidato` e `hidratar` (o texto vem do SQLite, não do payload).
- `src/consulta_juridica/retrieval/rerank.py` — cross-encoder, reranker identidade para medir a recuperação pura, e o que o modelo lê (`texto_para_rerank`).
- `src/consulta_juridica/retrieval/expansao.py` — inciso → artigo via SQLite, reaplicando a vigência; funde candidatos do mesmo artigo e reserva orçamento para o dispositivo que a busca achou.
- `src/consulta_juridica/retrieval/pipeline.py` — `Recuperador`, com os modelos injetados.
- `src/consulta_juridica/retrieval/__main__.py` — inspeção da recuperação sem gastar token.

## Geração

- `src/consulta_juridica/generation/backend.py` — `LLMBackend`, `Pedido`, `BlocoDocumento`, e a chave de cache do pedido. Decisão sync-não-async registrada aqui.
- `src/consulta_juridica/generation/claude_api.py` — Messages API com citations nativas; documento de texto puro, `char_location`. Leia antes de mexer na forma da requisição.
- `src/consulta_juridica/generation/claude_cli.py` — `claude -p` por subprocess, sem citations.
- `src/consulta_juridica/generation/cache.py` — decorator de cache em disco sobre qualquer backend.
- `src/consulta_juridica/generation/prompt.py` — todo o texto de instrução, `VERSAO_PROMPT` e a marca de abstenção. O corpo do documento é o trecho cru: ver a invariante no docstring.
- `src/consulta_juridica/generation/citacoes.py` — citação bruta → `Citacao` de domínio: deslocamento de caractere → dispositivo, e o que é descartado.

## API

- `src/consulta_juridica/api/app.py` — `criar_app`, lifespan (carrega modelos uma vez), `/v1/consultas`, `/v1/saude` e os erros de backend virando 502/503.
- `src/consulta_juridica/api/deps.py` — injeção do serviço a partir de `app.state`.
- `src/consulta_juridica/api/schemas.py` — DTOs HTTP, separados do domínio.
- `src/consulta_juridica/api/web/index.html` — front de teste: Vue por CDN, painel de trechos com scores e realce do texto citado.

## Eval

- `src/consulta_juridica/eval/golden.py` — golden set anotado e sua validação contra o SQLite.
- `src/consulta_juridica/eval/metrics.py` — recall@k, MRR, acurácia de citação, taxa de abstenção. Funções puras; o ranking é uma lista de conjuntos, um por trecho.
- `src/consulta_juridica/eval/run.py` — `avaliar_recuperacao` (sem LLM, com quebra por mecanismo) e `avaliar_ponta_a_ponta`.
- `src/consulta_juridica/eval/__main__.py` — CLI do eval.

## Dados anotados — versionados

- `golden/seed.jsonl` — golden set inicial, 13 perguntas com dispositivo esperado conferido contra o corpus. Fica fora de `data/` porque é trabalho manual e precisa de versionamento.

## Testes

- `tests/test_filtros.py` — vigência. Prioridade máxima: erro aqui não levanta exceção.
- `tests/test_expansao.py` — inciso → artigo, incluindo o vazamento de vigência pelo SQLite.
- `tests/test_recuperacao.py` — contrato da chamada ao Qdrant (o filtro nos prefetch), rerank e o `Recuperador` ponta a ponta. Pula sem Qdrant.
- `tests/test_eval.py` — métricas puras, validação do golden e a agregação do relatório.
- `tests/test_geracao.py` — prompt, cache, resolução de citação e os dois backends, com dublês. Não chama o Claude.
- `tests/test_api.py` — contrato HTTP, injeção do serviço e o estático. Serviço dublê, sem carregar modelo.
- `tests/test_chunking.py` — granularidade, texto indexado vs citável, vigência no chunk por artigo.
- `tests/test_indexacao.py` — coleção, drift, apagar-antes-de-inserir e os estágios. Pula sem Qdrant.
- `tests/test_tls.py` — a verificação TLS nunca pode ser desligada.
- `tests/test_urn.py` — esquema de ID, numeral romano, colisão ADCT, golden -> ID.
- `tests/test_store.py` — conexão, escrita, leitura e o vazamento de vigência pelo SQLite.
- `tests/test_fontes.py` — procedência, conferência de sha256 e detecção de encoding.
- `tests/test_parser.py` — hierarquia, `<strike>`, versões e vigência, contra HTML real.

---

> **Implementados:** o núcleo (`models`, `config`, `errors`, `urn`, `tls`, `embedding`,
> `vectorstore`), o `store/` inteiro, a ingestão inteira (`fontes`, `parser`, `chunking`,
> `indexer`, `pipeline`, `__main__`), a recuperação inteira (`filtros`, `busca`, `rerank`,
> `expansao`, `pipeline`, `__main__`), a geração inteira (`prompt`, `claude_api`,
> `claude_cli`, `cache`, `citacoes`), o `service.py` inteiro, a API inteira (`schemas`,
> `deps`, `app`, `web/index.html`) e o eval de recuperação (`golden`,
> `metrics.recall_em_k`/`mrr`, `run.avaliar_recuperacao`, `eval/__main__`).
>
> **Esqueleto** (docstring, imports e assinaturas, corpo em `NotImplementedError`):
> só o ponta a ponta do eval (`acuracia_citacao`, `taxa_abstencao`,
> `avaliar_ponta_a_ponta`).
>
> **Sem execução real contra a API:** a geração nunca foi exercitada contra a Messages API —
> não há chave nesta máquina. O contrato está coberto por dublês, e a API HTTP roda ponta a
> ponta com recuperação real: o que falta é a chamada ao modelo.
