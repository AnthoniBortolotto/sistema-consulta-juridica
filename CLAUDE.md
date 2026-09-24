# Instruções do projeto

RAG sobre legislação brasileira com citação verificável. Projeto de **portfólio** — o valor
está em decisões defensáveis e números medidos, não em quantidade de features.

Responda em português.

---

## CODEBASE-MAP.md — leia antes de procurar

[CODEBASE-MAP.md](CODEBASE-MAP.md) é o índice de onde cada coisa vive.

**Ao começar uma tarefa:** leia o mapa antes de sair varrendo o repositório. Se o que você
procura está lá, vá direto ao arquivo.

**Ao criar um módulo novo:** adicione a linha correspondente no mapa, no mesmo commit. Uma
linha por módulo, no formato `caminho` — o que vive ali / quando olhar aqui. Se um módulo
mudar de propósito ou for removido, corrija a linha em vez de deixá-la apodrecer.

O mapa é índice, não documentação: sem explicação de implementação, sem exemplos de uso.
Se uma entrada precisa de mais de duas linhas, o lugar dela é um docstring.

---

## Decisões travadas — não reabrir

| | |
|---|---|
| Linguagem | Python, uma só (ingestão e API) |
| Índice vetorial | **Qdrant** — decisão do usuário, já debatida contra pgvector |
| Fonte da verdade | SQLite; o Qdrant é índice **derivado e reconstruível** |
| Embeddings / reranker | `bge-m3` e `bge-reranker-v2-m3`, locais via `sentence-transformers` |
| Geração | **Local via Ollama** (`qwen3.5:4b`, container com GPU), sem chave de API. O backend do Claude com citations nativas fica no código, fora do caminho padrão |
| Front | Vue 3 por CDN, sem build, servido como estático pelo FastAPI |

Decididas depois: sem LangChain; e geração local sem chave como padrão — que troca a
citação literal garantida pela API por âncoras pedidas no prompt. Essa perda é conhecida e
está no README; não é para ser escondida.

---

## Restrições

- **Sem arquivos de CI ou deploy.** Nada de `.github/workflows/`, `Dockerfile` de
  aplicação, `Procfile`, `fly.toml`, `render.yaml` ou manifestos de Kubernetes. Não gerar
  nem sugerir. (O `docker-compose.yml` do Qdrant local é dependência de desenvolvimento.)
- Embeddings e reranking rodam **localmente**. Não trocar por API paga sem pedir.
- Em desenvolvimento, respostas do LLM vão para cache em disco por hash do prompt —
  reexecutar o eval não deve re-cobrar o que não mudou.

---

## Armadilhas do domínio

Estas seis são a diferença entre o projeto funcionar e parecer funcionar:

1. **Chunking é estrutural, nunca por janela de tokens.** A unidade é o dispositivo
   (artigo/parágrafo/inciso), com a hierarquia inteira nos metadados. Não usar splitter
   genérico por contagem de caracteres — ele destrói o que faz o sistema funcionar.

2. **Vigência filtra toda consulta.** `vigencia_inicio` e `revogado_em` em todo chunk, e a
   data de referência é **parâmetro da requisição**, nunca `now()` implícito — é preciso
   poder consultar o direito vigente à época de um fato.

3. **Busca é sempre híbrida.** Denso + esparso fundidos por RRF, depois rerank. Consulta
   como `art. 5º, LXXVIII` é léxica pura e embedding sozinho erra.

4. **Qdrant não tem join.** Expansão inciso→artigo, hierarquia e remissões se resolvem no
   SQLite, não no payload. Não desnormalizar texto de artigo dentro dos chunks filhos:
   quando uma emenda altera o artigo, isso vira inconsistência sem transação.

5. **A vigência precisa ser reaplicada na expansão.** O filtro do Qdrant exclui o inciso
   revogado, mas a expansão lê o artigo inteiro do SQLite e traz os irmãos revogados junto.
   Por isso `expandir()` e `subarvore()` recebem `data_referencia` obrigatório — é o bug
   mais provável do sistema, e ele não levanta exceção.

6. **Sentinela de vigência no payload.** `range(gt=ref)` no Qdrant exclui pontos sem o
   campo, então um dispositivo vigente (`revogado_em = None`) sumiria do resultado. Datas
   vão para o payload como `date.toordinal()`, e o não revogado carrega
   `vectorstore.SENTINELA_VIGENTE`. Nunca gravar `None` nesse campo.

---

## Convenções de código

- **Nomenclatura:** domínio jurídico em português (`dispositivo`, `vigencia`, `remissao`,
  `busca`), infraestrutura em inglês (`store`, `indexer`, `rerank`, `backend`). A regra é
  deliberada; sem ela a mistura vira arbitrária.
- **Tudo síncrono.** qdrant-client, fastembed, cross-encoder e subprocess são síncronos e
  CPU-bound. Endpoints do FastAPI são declarados `def`, nunca `async def` — o framework os
  despacha no threadpool. Tornar o `LLMBackend` assíncrono é refactor em cascata.
- **Nada constrói as próprias dependências.** Encoder, reranker, cliente Qdrant e backend
  nascem em `service.construir_servico`, o composition root. Os modelos somam alguns GB e
  segundos de carga: construir por requisição inviabiliza a API.
- **`Settings` só é lido na borda** (API, CLIs) e em `service.py`.

---

## Antes de mexer no backend do Claude

Só vale para `generation/claude_api.py`, que está fora do caminho padrão. Carregue a skill
`claude-api` e leia `python/claude-api/README.md` antes de alterar a chamada. Model IDs, formato de `thinking` e a API de citations mudaram recentemente — não
escrever de memória.

O prompt enviado precisa ser inspecionável: em RAG, a maior parte da depuração é descobrir
o que exatamente chegou ao modelo. Nada de camada que esconda isso.

---

## Avaliação

Mudança em chunking, busca ou prompt sem medir não está terminada. As métricas são
`recall@k`, acurácia de citação e taxa de abstenção — esta última importa: um sistema
jurídico que sempre responde é pior que um que admite lacuna.

---

## Contexto adicional

`NOTAS-DESIGN.local.md`, se presente, tem o histórico das decisões com o raciocínio
completo e as alternativas recusadas. É arquivo local e temporário, fora do versionamento.
