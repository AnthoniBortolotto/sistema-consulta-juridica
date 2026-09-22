"""Cache em disco das respostas do modelo."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import Uso
from .backend import CitacaoBruta, LLMBackend, Pedido, ResultadoGeracao


def _slug(nome: str) -> str:
    """Nome de backend -> nome de diretório. `messages-api:claude-opus-5` tem `:`, que o
    Windows não aceita em caminho."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nome)


class BackendComCache(LLMBackend):
    """Decorator sobre qualquer `LLMBackend`, indexado pela chave do `Pedido`.

    Decorator, e não módulo de cache solto, para compor com os dois backends e com o eval
    sem duplicação. É o maior ganho de custo do desenvolvimento: ao mexer só na recuperação,
    apenas as consultas cujo contexto mudou são efetivamente re-cobradas.

    Um diretório por backend, porque `Pedido.chave()` não inclui o modelo: o pedido é
    independente de backend por construção, e duas respostas de modelos diferentes ao mesmo
    pedido não podem compartilhar entrada — o eval compararia um modelo com o cache do
    outro sem perceber.
    """

    def __init__(self, interno: LLMBackend, dir_cache: Path) -> None:
        self.interno = interno
        self.dir_cache = dir_cache
        self.nome = f"{interno.nome}+cache"
        self.suporta_citacoes = interno.suporta_citacoes

    def _caminho(self, pedido: Pedido) -> Path:
        return self.dir_cache / _slug(self.interno.nome) / f"{pedido.chave()}.json"

    def gerar(self, pedido: Pedido) -> ResultadoGeracao:
        caminho = self._caminho(pedido)
        if caminho.exists():
            return _desserializar(json.loads(caminho.read_text(encoding="utf-8")))

        resultado = self.interno.gerar(pedido)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        # Escrita atômica: uma interrupção no meio do eval deixaria um JSON truncado, e o
        # próximo run leria lixo como se fosse resposta do modelo.
        temporario = caminho.with_suffix(".parcial")
        temporario.write_text(
            json.dumps(_serializar(resultado), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        temporario.replace(caminho)
        return resultado


def _serializar(r: ResultadoGeracao) -> dict:
    return {
        "texto": r.texto,
        "modelo": r.modelo,
        "uso": r.uso.model_dump(),
        "citacoes": [
            {
                "ref_documento": c.ref_documento,
                "texto_citado": c.texto_citado,
                "inicio_char": c.inicio_char,
                "fim_char": c.fim_char,
            }
            for c in r.citacoes
        ],
    }


def _desserializar(d: dict) -> ResultadoGeracao:
    return ResultadoGeracao(
        texto=d["texto"],
        citacoes=[CitacaoBruta(**c) for c in d["citacoes"]],
        modelo=d["modelo"],
        uso=Uso.model_validate(d["uso"]),
    )
