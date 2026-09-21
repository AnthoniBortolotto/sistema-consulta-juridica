"""Aquisição dos documentos brutos (Planalto, LexML).

Separado do parser de propósito: baixar é lento e sujeito a rede, parsear é rápido e
determinístico. Guardar o bruto em disco permite reparsear o corpus inteiro sem baixar
nada de novo — e reparsear é o que mais acontece enquanto o parser amadurece.
"""

from __future__ import annotations

import hashlib
import json
import ssl
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

import httpx

from ..errors import ConsultaJuridicaError
from ..urn import CORPUS, urn_da_norma

#: O Planalto devolve zero byte e estoura o timeout sem User-Agent de navegador. Medido
#: em 2026-09-21: sem UA, timeout; com este, HTTP 200 em 1,7 s.
USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: O HTML do Planalto não declara `charset` e não é utf-8. Decodificar como utf-8 estoura
#: em qualquer artigo com acento — ou seja, no primeiro.
ENCODING_PLANALTO: Final = "cp1252"

TIMEOUT_PADRAO: Final = 60.0
SUFIXO_META: Final = ".meta.json"


def contexto_tls() -> ssl.SSLContext:
    """Contexto TLS ancorado no truststore do sistema operacional.

    O httpx verifica contra o bundle do `certifi`, que nesta máquina não fecha a cadeia
    do planalto.gov.br (`CERTIFICATE_VERIFY_FAILED: unable to get local issuer`). O
    `ssl.create_default_context()` carrega as raízes do SO, que é o que o navegador e o
    curl usam, e fecha.

    Desligar a verificação resolveria em uma linha e está fora de questão: o sha256 de
    procedência só vale alguma coisa se os bytes vieram comprovadamente do Planalto.
    """
    return ssl.create_default_context()


class FalhaDownload(ConsultaJuridicaError):
    """A fonte não devolveu o documento."""


@dataclass(frozen=True)
class ReferenciaNorma:
    """Ponteiro para uma norma antes do download."""

    urn: str
    url: str
    apelido: str | None = None


@dataclass(frozen=True)
class DocumentoBruto:
    """Bytes como vieram da fonte, com procedência."""

    urn: str
    url: str
    conteudo: bytes
    content_type: str
    sha256: str
    baixado_em: datetime

    def texto(self, encoding: str | None = None) -> str:
        """Decodifica os bytes, detectando o encoding quando não é informado.

        Encoding errado não levanta exceção: produz texto corrompido em silêncio, e o
        parser devolve uma árvore plausível e errada. Por isso a detecção é explícita —
        tenta utf-8 em modo estrito e só cai para cp1252 quando falha.

        Funciona porque os dois casos são mutuamente exclusivos na prática: em cp1252 os
        acentos do português são bytes soltos 0xE0-0xFC, que não formam sequência utf-8
        válida, então a página do Planalto sempre falha no utf-8 estrito.
        """
        if encoding is not None:
            return self.conteudo.decode(encoding, errors="replace")
        try:
            return self.conteudo.decode("utf-8")
        except UnicodeDecodeError:
            return self.conteudo.decode(ENCODING_PLANALTO, errors="replace")


class Fonte(Protocol):
    """Origem de documentos legais."""

    nome: str

    def listar(self) -> Iterator[ReferenciaNorma]:
        """Normas que esta fonte sabe entregar."""
        ...

    def baixar(self, ref: ReferenciaNorma) -> DocumentoBruto:
        """Baixa uma norma."""
        ...


def sha256_de(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


class FontePlanalto(Fonte):
    """HTML do planalto.gov.br."""

    nome = "planalto"

    def __init__(
        self, refs: list[ReferenciaNorma] | None = None, *, timeout: float = TIMEOUT_PADRAO
    ) -> None:
        #: Sem argumento, entrega o corpus fechado em `urn.CORPUS` — a mesma tabela que o
        #: golden set usa para resolver apelidos, para não haver duas listas de normas.
        self._refs = refs if refs is not None else [
            ReferenciaNorma(urn=n.urn, url=n.fonte_url, apelido=n.apelido) for n in CORPUS
        ]
        self._timeout = timeout

    def listar(self) -> Iterator[ReferenciaNorma]:
        yield from self._refs

    def baixar(self, ref: ReferenciaNorma) -> DocumentoBruto:
        try:
            resp = httpx.get(
                ref.url,
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
                follow_redirects=True,
                verify=contexto_tls(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise FalhaDownload(f"{ref.url}: {e}") from e

        if not resp.content:
            raise FalhaDownload(f"{ref.url}: resposta vazia (User-Agent bloqueado?)")

        return DocumentoBruto(
            urn=ref.urn,
            url=ref.url,
            conteudo=resp.content,
            content_type=resp.headers.get("content-type", "text/html"),
            sha256=sha256_de(resp.content),
            baixado_em=datetime.now(UTC),
        )


class FonteLexML(Fonte):
    """XML via API SRU do LexML.

    **Inviável hoje, e por isso não implementada.** Testado em 2026-09-21: a SRU está atrás
    do desafio anti-bot do Senado e devolve HTML de interstício ("Verificação de
    segurança"), não XML. A classe fica como lugar reservado — reavaliar, não usar.
    """

    nome = "lexml"

    def __init__(self, base_url: str = "https://www.lexml.gov.br/busca/SRU") -> None:
        self.base_url = base_url

    def listar(self) -> Iterator[ReferenciaNorma]:
        raise FalhaDownload("LexML: API SRU atrás de desafio anti-bot, eliminada como fonte")

    def baixar(self, ref: ReferenciaNorma) -> DocumentoBruto:
        raise FalhaDownload("LexML: API SRU atrás de desafio anti-bot, eliminada como fonte")


def _nome_arquivo(urn: str) -> str:
    """URN -> nome de arquivo seguro em qualquer sistema de arquivos."""
    return urn_da_norma(urn).replace(":", "_").replace(";", "-").replace("/", "-")


def salvar(doc: DocumentoBruto, destino: Path) -> Path:
    """Grava o bruto em `data/raw/`, junto de um .meta.json com a procedência."""
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / f"{_nome_arquivo(doc.urn)}.htm"
    caminho.write_bytes(doc.conteudo)
    caminho.with_suffix(caminho.suffix + SUFIXO_META).write_text(
        json.dumps(
            {
                "urn": doc.urn,
                "url": doc.url,
                "content_type": doc.content_type,
                "sha256": doc.sha256,
                "baixado_em": doc.baixado_em.isoformat(),
                "bytes": len(doc.conteudo),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return caminho


def carregar(caminho: Path) -> DocumentoBruto:
    """Relê um bruto salvo, para reparsear sem rede.

    Confere o sha256 gravado contra os bytes em disco: um bruto corrompido produziria uma
    árvore silenciosamente truncada, e `sha256_origem` deixaria de identificar a fonte.
    """
    meta_path = caminho.with_suffix(caminho.suffix + SUFIXO_META)
    if not meta_path.exists():
        raise FalhaDownload(f"sem procedência para {caminho}: falta {meta_path.name}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    conteudo = caminho.read_bytes()
    atual = sha256_de(conteudo)
    if atual != meta["sha256"]:
        raise FalhaDownload(
            f"{caminho}: sha256 não confere (gravado {meta['sha256'][:12]}…, "
            f"lido {atual[:12]}…) — rebaixe o documento"
        )

    return DocumentoBruto(
        urn=meta["urn"],
        url=meta["url"],
        conteudo=conteudo,
        content_type=meta.get("content_type", "text/html"),
        sha256=atual,
        baixado_em=datetime.fromisoformat(meta["baixado_em"]),
    )


def iter_brutos(dir_raw: Path) -> Iterator[DocumentoBruto]:
    """Todos os brutos salvos, para reparsear o corpus inteiro sem rede."""
    for meta in sorted(dir_raw.glob(f"*{SUFIXO_META}")):
        yield carregar(meta.with_name(meta.name[: -len(SUFIXO_META)]))
