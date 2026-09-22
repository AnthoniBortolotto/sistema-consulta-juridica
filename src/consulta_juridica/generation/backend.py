"""Contrato de geração.

TUDO SÍNCRONO, de propósito. qdrant-client, fastembed, o cross-encoder e o subprocess do
CLI são todos síncronos e CPU-bound; os endpoints do FastAPI são declarados `def` (não
`async def`) para o framework despachá-los no threadpool. Tornar este Protocol assíncrono
depois é refactor em cascata por todo o projeto — se for para mudar, mude cedo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from ..models import Uso


@dataclass(frozen=True)
class BlocoDocumento:
    """Um trecho recuperado, pronto para virar bloco `document` da Messages API."""

    ref: str  # "D1" — âncora usada pelo fallback de citação do backend CLI
    titulo: str  # "Lei 8.078/1990, Art. 6º"
    contexto: str  # caminho hierárquico + vigência
    texto: str
    dispositivo_id: str


@dataclass(frozen=True)
class Pedido:
    """Requisição de geração, independente de backend."""

    sistema: str
    documentos: tuple[BlocoDocumento, ...]
    pergunta: str
    max_tokens: int = 8192

    def chave(self) -> str:
        """sha256 canônico do pedido, usado pelo cache em disco.

        Entra TUDO que muda a resposta: o texto de sistema (que carrega a data de
        referência e a versão do prompt), cada documento inteiro e a pergunta. Se o
        chunking ou a recuperação mudarem, o texto dos documentos muda e a chave muda
        sozinha — que é a propriedade desejada: reexecutar o eval depois de mexer só no
        rerank não re-cobra as consultas cujo contexto ficou igual.

        O modelo NÃO entra aqui, de propósito: `Pedido` é independente de backend. Quem
        separa modelos é `cache.BackendComCache`, pelo diretório.
        """
        canonico = json.dumps(
            {
                "sistema": self.sistema,
                "pergunta": self.pergunta,
                "max_tokens": self.max_tokens,
                "documentos": [
                    [d.ref, d.titulo, d.contexto, d.texto, d.dispositivo_id]
                    for d in self.documentos
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CitacaoBruta:
    """Citação como o backend a devolveu, antes de ser resolvida ao dispositivo."""

    ref_documento: str
    texto_citado: str
    inicio_char: int | None = None
    fim_char: int | None = None


@dataclass
class ResultadoGeracao:
    texto: str
    citacoes: list[CitacaoBruta]
    modelo: str
    uso: Uso


class LLMBackend(Protocol):
    """Backend de geração.

    `suporta_citacoes` é onde a abstração vaza, e o vazamento é intencional e visível: o
    backend via Messages API tem citações garantidas pela API, o via CLI depende do modelo
    obedecer a instrução de marcar âncoras. O eval registra o `nome` do backend porque
    medir acurácia de citação com o backend CLI produz um número inválido.
    """

    nome: str
    suporta_citacoes: bool

    def gerar(self, pedido: Pedido) -> ResultadoGeracao: ...
