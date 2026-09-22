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
from typing import Final

from .config import Settings
from .generation import citacoes, prompt
from .generation.backend import LLMBackend
from .models import MotivoAbstencao, Resposta
from .retrieval.filtros import Criterios
from .retrieval.pipeline import Recuperador

#: Texto devolvido quando a recuperação não traz nada. Fixo, e não gerado: chamar o modelo
#: para ele dizer que não recebeu trecho nenhum custaria token para não acrescentar nada.
TEXTO_SEM_CANDIDATOS: Final = (
    "Não encontrei nenhum dispositivo no corpus que trate do que foi perguntado, "
    "na data de referência da consulta."
)


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

        Os dois motivos de abstenção são distintos de propósito e não devem ser fundidos no
        relatório: `SEM_CANDIDATOS` é a recuperação não achando nada — defeito de busca ou
        pergunta fora do corpus; `CONTEXTO_INSUFICIENTE` é o modelo lendo os trechos e
        dizendo que não bastam, que é o comportamento desejado em `abs-01` e `abs-02`.
        """
        trechos = self.recuperador.recuperar(
            c.pergunta,
            Criterios(data_referencia=c.data_referencia, normas=c.normas),
            k_final=c.top_k,
        )
        if not trechos:
            return Resposta(
                texto=TEXTO_SEM_CANDIDATOS,
                trechos=[],
                abstencao=MotivoAbstencao.SEM_CANDIDATOS,
                data_referencia=c.data_referencia,
                modelo="",
                versao_prompt=prompt.VERSAO_PROMPT,
            )

        pedido = prompt.montar_pedido(
            c.pergunta,
            trechos,
            c.data_referencia,
            suporta_citacoes=self.backend.suporta_citacoes,
        )
        resultado = self.backend.gerar(pedido)
        return Resposta(
            texto=resultado.texto,
            citacoes=citacoes.resolver(resultado.citacoes, pedido.documentos, trechos),
            trechos=list(trechos),
            abstencao=(
                MotivoAbstencao.CONTEXTO_INSUFICIENTE
                if citacoes.detectar_abstencao(resultado.texto)
                else None
            ),
            data_referencia=c.data_referencia,
            modelo=resultado.modelo,
            versao_prompt=prompt.VERSAO_PROMPT,
            uso=resultado.uso,
        )


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


def construir_backend(cfg: Settings) -> LLMBackend:
    """Escolhe o backend e o envolve no cache. Só o composition root chama isto.

    O cache é ligado por padrão e vale para os dois backends: em desenvolvimento, mexer na
    recuperação muda o contexto de algumas perguntas e não de outras, e só as que mudaram
    são re-cobradas.
    """
    from .generation.cache import BackendComCache

    if cfg.backend_llm == "api":
        from .generation.claude_api import BackendMessagesAPI

        backend: LLMBackend = BackendMessagesAPI(cfg.modelo_llm)
    elif cfg.backend_llm == "cli":
        from .generation.claude_cli import BackendClaudeCLI

        backend = BackendClaudeCLI()
    else:
        raise ValueError(
            f"backend_llm desconhecido: {cfg.backend_llm!r} (há: api, cli)"
        )

    if cfg.usar_cache_llm:
        return BackendComCache(backend, cfg.dir_cache_llm)
    return backend


def construir_servico(cfg: Settings) -> Servico:
    """Monta o serviço completo, escolhendo o backend conforme `cfg.backend_llm`."""
    backend = construir_backend(cfg)
    return Servico(
        recuperador=construir_recuperador(cfg),
        backend=backend,
        modelo=cfg.modelo_llm,
    )
