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
from .generation.backend import LLMBackend, Pedido
from .models import MotivoAbstencao, Resposta, Trecho
from .retrieval.filtros import Criterios
from .retrieval.pipeline import Recuperador
from .store import queries

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
        trechos, pedido = self.montar(c)
        if pedido is None:
            return Resposta(
                texto=TEXTO_SEM_CANDIDATOS,
                trechos=[],
                abstencao=MotivoAbstencao.SEM_CANDIDATOS,
                data_referencia=c.data_referencia,
                modelo="",
                versao_prompt=prompt.VERSAO_PROMPT,
            )

        resultado = self.backend.gerar(pedido)
        return Resposta(
            texto=resultado.texto,
            citacoes=citacoes.resolver(
                resultado.citacoes, pedido.documentos, trechos, self._rotulo_de
            ),
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

    def montar(self, c: Consulta) -> tuple[list[Trecho], Pedido | None]:
        """Recupera e monta o pedido, SEM chamar o modelo.

        Separado de `responder` para o eval poder estimar o custo de uma rodada antes de
        gastá-lo: o pedido é exatamente o que seria enviado, e a chave dele diz se a
        resposta já está no cache. `None` quando não há trecho — o caminho que não custa.
        """
        trechos = self.recuperador.recuperar(
            c.pergunta,
            Criterios(data_referencia=c.data_referencia, normas=c.normas),
            k_final=c.top_k,
        )
        if not trechos:
            return [], None
        pedido = prompt.montar_pedido(
            c.pergunta,
            trechos,
            c.data_referencia,
            suporta_citacoes=self.backend.suporta_citacoes,
        )
        return list(trechos), pedido

    def _rotulo_de(self, dispositivo_id: str) -> str:
        """Rótulo humano do dispositivo CITADO, que não é o do trecho.

        Com expansão até o artigo, o trecho é "Art. 49" e a citação pode cair no parágrafo
        único. Herdar o rótulo do trecho deixaria o par inconsistente: o ID apontando para
        o parágrafo e o rótulo dizendo o artigo — e é o rótulo que o usuário lê e confere.

        Vive aqui, e não em `citacoes`, porque é o serviço que tem a conexão: manter
        `resolver` puro é o que permite testá-lo sem banco.
        """
        return queries.rotulo_completo(self.recuperador.conn, dispositivo_id)


def construir_recuperador(
    cfg: Settings, *, com_rerank: bool | None = None, threads: int | None = None
) -> Recuperador:
    """Monta o recuperador. Carrega os modelos — chame uma vez por processo.

    A conexão nasce somente-leitura: recuperação nunca escreve, e o banco impedindo é mais
    barato que confiar na disciplina. Quem a fecha é quem chamou — a API pelo lifespan, o
    CLI no fim do processo.

    `com_rerank=False` troca o cross-encoder pela identidade, que é como o eval mede a
    recuperação pura sem esperar os segundos de carga do modelo. `None` segue
    `cfg.usar_rerank` — o eval passa explícito porque mede os dois lados.
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
        reranker=construir_reranker(
            cfg, com_rerank=cfg.usar_rerank if com_rerank is None else com_rerank
        ),
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
    elif cfg.backend_llm == "ollama":
        from .generation.ollama import BackendOllama

        backend = BackendOllama(
            cfg.modelo_local,
            url=cfg.ollama_url,
            num_ctx=cfg.ollama_num_ctx,
            keep_alive=cfg.ollama_keep_alive,
            pensar=cfg.ollama_pensar,
        )
    else:
        raise ValueError(
            f"backend_llm desconhecido: {cfg.backend_llm!r} (há: api, cli, ollama)"
        )

    if cfg.usar_cache_llm:
        return BackendComCache(backend, cfg.dir_cache_llm)
    return backend


def construir_servico(cfg: Settings, *, threads: int | None = None) -> Servico:
    """Monta o serviço completo, escolhendo o backend conforme `cfg.backend_llm`.

    `threads` fica em None na API — ela serve requisições concorrentes e não deve tomar a
    máquina — e é passado pelo eval, que roda em lote.
    """
    backend = construir_backend(cfg)
    return Servico(
        recuperador=construir_recuperador(cfg, threads=threads),
        backend=backend,
        # O modelo configurado para o backend em uso: com o local ligado, dizer
        # "claude-opus-5" aqui mentiria para a estimativa de custo e para o relatório.
        modelo=cfg.modelo_local if cfg.backend_llm == "ollama" else cfg.modelo_llm,
    )
