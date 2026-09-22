"""Reordenação por cross-encoder.

Maior ganho de qualidade por linha de código do pipeline: o bi-encoder compara vetores
pré-computados, o cross-encoder lê pergunta e trecho juntos.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, Protocol

from .busca import Candidato

if TYPE_CHECKING:
    from ..config import Settings

#: Tokens por par (pergunta, trecho). Medido: o texto que chega ao cross-encoder tem
#: mediana de 148 caracteres e máximo de 761 no corpus, então nem 256 chega a truncar, e
#: baixar o limite não muda o tempo (11,0s contra 11,2s para 50 candidatos). O número que
#: manda é a QUANTIDADE de pares — 0,22 s cada, em CPU. 512 fica por ser folga de graça.
MAX_TOKENS: Final = 512

#: Pares por passagem no modelo. Pequeno porque em CPU o lote grande só aumenta o pico de
#: memória: não há paralelismo de lote a ganhar como na GPU.
LOTE_PADRAO: Final = 16


class Reranker(Protocol):
    nome: str

    def pontuar(self, consulta: str, textos: Sequence[str]) -> list[float]: ...


def texto_para_rerank(c: Candidato) -> str:
    """O que o cross-encoder lê.

    Leva o rótulo completo junto com o texto, e não só o texto: metade do golden set
    pergunta por numeração ("art. 5º, LXXVIII"), e sem o rótulo o cross-encoder não tem
    como distinguir o dispositivo certo de um vizinho com redação parecida. O rótulo já
    vem no payload, então isso não custa nenhuma consulta.
    """
    return f"{c.payload.rotulo_completo}\n{c.texto}"


class RerankerCrossEncoder(Reranker):
    """`bge-reranker-v2-m3` local. Carregue uma vez e reutilize — são segundos de carga."""

    nome = "bge-reranker-v2-m3"

    def __init__(
        self, modelo: str, *, max_tokens: int = MAX_TOKENS, lote: int = LOTE_PADRAO
    ) -> None:
        # Import tardio pelo mesmo motivo de `embedding.EncoderLocal`: puxa torch, e quem
        # só quer o Protocol (o eval com `RerankerIdentidade`) não deve pagar segundos.
        from sentence_transformers import CrossEncoder

        self._modelo = CrossEncoder(modelo, max_length=max_tokens)
        self._lote = lote
        # Sobrepõe o atributo de classe: o relatório de eval precisa dizer qual modelo
        # rodou de fato, não qual era o default quando a classe foi escrita.
        self.nome = modelo

    def pontuar(self, consulta: str, textos: Sequence[str]) -> list[float]:
        if not textos:
            return []
        scores = self._modelo.predict(
            [(consulta, t) for t in textos],
            batch_size=self._lote,
            show_progress_bar=False,
        )
        return [float(s) for s in scores]


class RerankerIdentidade(Reranker):
    """No-op: preserva a ordem da fusão.

    Existe para o eval poder medir a recuperação pura e atribuir o ganho ao reranker.
    """

    nome = "identidade"

    def pontuar(self, consulta: str, textos: Sequence[str]) -> list[float]:
        return [0.0] * len(textos)


def rerankear(
    r: Reranker, consulta: str, cands: Sequence[Candidato], *, k: int = 8
) -> list[Candidato]:
    """Pontua e devolve os k melhores, com `score_rerank` preenchido.

    A ordenação é estável, e isso é requisito e não detalhe: `RerankerIdentidade` dá 0.0
    a todo mundo, e é a estabilidade que faz a ordem da fusão sobreviver intacta. Sem
    ela, o eval "sem rerank" mediria uma permutação arbitrária.
    """
    if not cands:
        return []
    scores = r.pontuar(consulta, [texto_para_rerank(c) for c in cands])
    for c, s in zip(cands, scores, strict=True):
        c.score_rerank = s
    return sorted(cands, key=lambda c: -(c.score_rerank or 0.0))[:k]


def construir_reranker(cfg: Settings, *, com_rerank: bool = True) -> Reranker:
    """Fábrica. Chamada só pelo composition root (`service.construir_recuperador`).

    `com_rerank=False` devolve a identidade: é como o eval mede a recuperação pura, sem
    carregar o cross-encoder nem esperar os segundos de modelo.
    """
    if not com_rerank:
        return RerankerIdentidade()
    return RerankerCrossEncoder(cfg.modelo_rerank)
