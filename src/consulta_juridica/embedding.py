"""Geração de vetores — denso (bge-m3) e esparso (BM25).

Mora na raiz do pacote, e não em `ingest/`, de propósito: a consulta também precisa
embedar, e `retrieval` importando `ingest` inverteria a dependência. Mais importante,
indexação e consulta PRECISAM usar o mesmo modelo e a mesma tokenização — misturar
tokenizadores gera IDs de termo incompatíveis e recall silenciosamente ruim. Manter
`documentos()` e `consulta()` na mesma tela é o que torna essa divergência visível.

As duas pernas são assimétricas de propósito, e essa é a parte fácil de errar:

- **Denso:** o bge-m3 é simétrico, sem prefixo de instrução para a consulta. Documento e
  pergunta passam pelo mesmo caminho, normalizados para cosseno.
- **Esparso:** `embed()` para documento, `query_embed()` para pergunta. Não é
  preciosismo — o BM25 do fastembed NÃO aplica IDF no vetor; quem aplica é o Qdrant, via
  `Modifier.IDF` na coleção. Usar `embed()` na consulta pontuaria o termo pela frequência
  dentro da própria pergunta, que é ruído.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from qdrant_client.models import SparseVector

if TYPE_CHECKING:
    from .config import Settings

#: Sem isto o fastembed usa o stemmer inglês (é o default dele), e o recall em texto
#: jurídico brasileiro cai: "prescrição"/"prescricional"/"prescritas" deixam de casar.
IDIOMA_PADRAO: Final = "portuguese"

#: Quantos textos entram numa chamada a `encode`. Quanto maior, melhor a ordenação por
#: comprimento que a biblioteca faz internamente — limitado pela memória dos vetores.
JANELA_PADRAO: Final = 512

#: Quantos textos o MODELO processa por vez. Pequeno de propósito: o padding de um lote
#: grande com textos de comprimento desigual domina o custo. Ver `documentos`.
BATCH_MODELO: Final = 32


@dataclass(frozen=True)
class Vetores:
    """Par denso + esparso de um mesmo texto."""

    denso: list[float]
    esparso: SparseVector


class Encoder:
    """Contrato de codificação. Implementações carregam modelos pesados — reutilize."""

    nome_denso: str
    nome_esparso: str
    dim: int

    def documentos(self, textos: Sequence[str], *, lote: int = 32) -> Iterator[Vetores]:
        """Codifica textos para indexação."""
        raise NotImplementedError

    def consulta(self, texto: str) -> Vetores:
        """Codifica a pergunta do usuário."""
        raise NotImplementedError


def _para_sparse(bruto) -> SparseVector:
    """`SparseEmbedding` do fastembed -> `SparseVector` do Qdrant."""
    return SparseVector(indices=bruto.indices.tolist(), values=bruto.values.tolist())


class EncoderLocal(Encoder):
    """bge-m3 via sentence-transformers (denso) + Qdrant/bm25 via fastembed (esparso).

    O BM25 é instanciado com `language="portuguese"`: com o stemmer inglês, o recall em
    texto jurídico brasileiro cai bastante.

    Carrega alguns GB e leva segundos. Construir por requisição inviabiliza a API — por
    isso nasce só em `service.construir_servico` e nos CLIs.
    """

    def __init__(
        self,
        modelo_denso: str,
        modelo_esparso: str,
        *,
        idioma: str = IDIOMA_PADRAO,
        threads: int | None = None,
    ) -> None:
        # Import tardio: `sentence_transformers` puxa torch e custa segundos só para
        # importar. Quem só quer `Vetores` ou o Protocol não deve pagar isso.
        import torch
        from fastembed import SparseTextEmbedding
        from sentence_transformers import SentenceTransformer

        if threads is not None:
            # Medido: o default do torch usa os núcleos físicos (8 de 16 nesta
            # máquina) e render 2,68 chunks/s; com os 16 lógicos, 3,20 — 19% a mais.
            # Quem decide é a borda: a API serve requisições concorrentes e não deve
            # tomar todos os núcleos para uma só.
            torch.set_num_threads(threads)

        self._denso = SentenceTransformer(modelo_denso)
        self._esparso = SparseTextEmbedding(model_name=modelo_esparso, language=idioma)
        self.nome_denso = modelo_denso
        self.nome_esparso = modelo_esparso
        self.idioma = idioma
        self.dim = int(self._denso.get_sentence_embedding_dimension())

    def documentos(self, textos: Sequence[str], *, lote: int = JANELA_PADRAO) -> Iterator[Vetores]:
        """Codifica textos para indexação, em janelas e preservando a ordem de entrada.

        Gerador: o corpus tem milhares de chunks e materializar todos os vetores de uma
        vez só para depois enviá-los ao Qdrant dobra o pico de memória sem ganho.

        `lote` é o tamanho da JANELA, não o `batch_size` do modelo. Confundir os dois
        custou 75% da velocidade e vale o parágrafo:

        o `sentence-transformers` ordena por comprimento o que recebe e só então divide em
        `batch_size`, o que reduz o padding. Passar fatias pequenas com `batch_size` igual
        ao tamanho da fatia anula as duas coisas. Medido no corpus, em 16 threads:
        fatias de 128 com `batch_size=128` dão 1,50 chunk/s; janelas de 512 com
        `batch_size=32`, 2,66 — de 93 para 52 minutos de reindexação.

        (Ordenar por comprimento antes de chamar não ajuda: já é o que a biblioteca faz.
        Medido, 2,41 contra 2,62 — a hipótese estava errada.)
        """
        for i in range(0, len(textos), lote):
            janela = list(textos[i : i + lote])
            densos = self._denso.encode(
                janela,
                batch_size=BATCH_MODELO,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            esparsos = list(self._esparso.embed(janela, batch_size=BATCH_MODELO))
            for d, e in zip(densos, esparsos, strict=True):
                yield Vetores(denso=d.tolist(), esparso=_para_sparse(e))

    def consulta(self, texto: str) -> Vetores:
        """Codifica a pergunta do usuário.

        `query_embed`, não `embed`: ver a nota sobre IDF no topo do módulo.
        """
        denso = self._denso.encode(
            [texto], normalize_embeddings=True, show_progress_bar=False
        )[0]
        esparso = next(iter(self._esparso.query_embed(texto)))
        return Vetores(denso=denso.tolist(), esparso=_para_sparse(esparso))


def construir_encoder(cfg: Settings, *, threads: int | None = None) -> Encoder:
    """Fábrica. Chamada uma única vez, pelo composition root.

    `threads` só é passado pela indexação em lote, que é o único caso em que tomar a
    máquina inteira é o comportamento certo.
    """
    return EncoderLocal(
        cfg.modelo_denso, cfg.modelo_esparso, idioma=cfg.idioma_esparso, threads=threads
    )
