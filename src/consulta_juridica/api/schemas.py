"""DTOs HTTP.

Separados dos tipos de domínio de propósito: o contrato da API pode evoluir sem arrastar o
domínio, e o domínio pode mudar sem quebrar clientes. Reusar `models.py` como DTO acopla os
dois para sempre.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from ..models import Resposta


class ConsultaRequest(BaseModel):
    """Corpo de POST /v1/consultas."""

    pergunta: str = Field(min_length=3, max_length=2000)
    #: Obrigatório no contrato HTTP. Sem default para que o cliente diga explicitamente
    #: sobre qual direito vigente está perguntando.
    data_referencia: date
    normas: list[str] = Field(default_factory=list)
    top_k: int = Field(default=8, ge=1, le=20)


class CitacaoOut(BaseModel):
    dispositivo: str
    rotulo: str
    texto_citado: str
    url: str


class TrechoOut(BaseModel):
    """Exposto para o painel de depuração do front."""

    dispositivo: str
    rotulo: str
    texto: str
    score: float
    score_fusao: float | None = None
    score_rerank: float | None = None


class RespostaOut(BaseModel):
    resposta: str
    citacoes: list[CitacaoOut]
    trechos_recuperados: list[TrechoOut]
    abstencao: str | None = None
    data_referencia: date
    modelo: str


def de_dominio(r: Resposta) -> RespostaOut:
    """Converte a resposta de domínio para o contrato HTTP.

    `versao_prompt` e `uso` ficam de fora de propósito: são instrumentação de eval, não
    contrato de cliente. Expô-los agora obrigaria a mantê-los para sempre.
    """
    return RespostaOut(
        resposta=r.texto,
        citacoes=[
            CitacaoOut(
                dispositivo=c.dispositivo_id,
                rotulo=c.rotulo_completo,
                texto_citado=c.texto_citado,
                url=c.fonte_url,
            )
            for c in r.citacoes
        ],
        trechos_recuperados=[
            TrechoOut(
                dispositivo=t.dispositivo_id,
                rotulo=t.rotulo_completo,
                texto=t.texto,
                score=t.score,
                score_fusao=t.score_fusao,
                score_rerank=t.score_rerank,
            )
            for t in r.trechos
        ],
        abstencao=r.abstencao.value if r.abstencao else None,
        data_referencia=r.data_referencia,
        modelo=r.modelo,
    )
