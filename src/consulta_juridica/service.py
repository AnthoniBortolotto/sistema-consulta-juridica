"""Orquestração da consulta e composition root.

Existe para que a API e o eval percorram exatamente o mesmo caminho. Se `api/app.py`
chamasse busca, rerank e geração diretamente, o domínio moraria na camada de transporte e o
eval teria de subir um servidor HTTP para medir qualquer coisa.

Este é também o ÚNICO módulo que lê `Settings` e constrói encoder, reranker, cliente Qdrant
e backend. Nenhum outro módulo instancia as próprias dependências.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .config import Settings
from .generation.backend import LLMBackend
from .models import Resposta
from .retrieval.pipeline import Recuperador


@dataclass(frozen=True)
class Consulta:
    """Uma pergunta e seu recorte."""

    pergunta: str
    data_referencia: date
    normas: tuple[str, ...] = ()
    top_k: int = 8


@dataclass
class Servico:
    """Recuperação + geração."""

    recuperador: Recuperador
    backend: LLMBackend
    modelo: str

    def responder(self, c: Consulta) -> Resposta:
        """Caminho completo.

        Abstém-se sem chamar o modelo quando a recuperação volta vazia: é o caminho
        `SEM_CANDIDATOS`, e ele não custa nada.
        """
        raise NotImplementedError


def construir_recuperador(
    cfg: Settings, *, com_rerank: bool = True, threads: int | None = None
) -> Recuperador:
    """Monta o recuperador. Carrega os modelos — chame uma vez por processo.

    A conexão nasce somente-leitura: recuperação nunca escreve, e o banco impedindo é mais
    barato que confiar na disciplina. Quem a fecha é quem chamou — a API pelo lifespan, o
    CLI no fim do processo.

    `com_rerank=False` troca o cross-encoder pela identidade, que é como o eval mede a
    recuperação pura sem esperar os segundos de carga do modelo.
    """
    from .embedding import construir_encoder
    from .retrieval.expansao import Nivel
    from .retrieval.rerank import construir_reranker
    from .store import db
    from .vectorstore import cliente

    return Recuperador(
        conn=db.conectar(cfg.caminho_sqlite, somente_leitura=True),
        client=cliente(cfg),
        encoder=construir_encoder(cfg, threads=threads),
        reranker=construir_reranker(cfg, com_rerank=com_rerank),
        colecao=cfg.colecao,
        nivel=Nivel(cfg.nivel_expansao),
        k_prefetch=cfg.k_prefetch,
    )


def construir_servico(cfg: Settings) -> Servico:
    """Monta o serviço completo, escolhendo o backend conforme `cfg.backend_llm`."""
    raise NotImplementedError
