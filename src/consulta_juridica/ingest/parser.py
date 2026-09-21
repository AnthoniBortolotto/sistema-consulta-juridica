"""Documento bruto -> árvore de dispositivos.

Responsabilidade única: FIDELIDADE À FONTE. O parser reconstrói a hierarquia tal como
publicada e não decide granularidade de recuperação — isso é `chunking.py`. Misturar as
duas obriga a reparsear todo o corpus a cada experimento de chunk.

Arquitetura: **um caminho de parsing, textual**, com a âncora HTML como sinal auxiliar.
Decidido contra o corpus baixado, não contra a documentação do Planalto:

- O sinal textual é uniforme nas três normas (cabeçalho estrutural em linha própria,
  "§ N", "Parágrafo único", inciso romano seguido de travessão, alínea "a)").
- A âncora é irregular: cobre 33% dos cabeçalhos estruturais do Código Civil, usa nomes
  inconsistentes abaixo do artigo (`art3i`, `art3.`, `art2p`, `art3§1`) e — o que elimina
  o caminho por âncora — **aponta para a redação morta**: na CF/88 `art6` é a redação da
  EC 26/2000, `art6.` a da EC 64/2010 e `art6...` a vigente. O Planalto desambigua âncoras
  repetidas colando pontos.
- Onde a âncora existe, ela ainda vale como corroboração de que "Art. N" abrindo um bloco
  é rótulo de dispositivo e não citação em prosa (513 ocorrências para 250 artigos na CF).

Duas convenções de modelagem:

1. **O caput não vira nó.** O texto próprio do artigo fica no nó ARTIGO, e incisos e
   parágrafos são filhos diretos dele. É o que `models.Dispositivo.caminho` já ilustrava
   ("tit3/cap1/art6/inc8"). `TipoDispositivo.CAPUT` continua no vocabulário para o
   chunking, mas este parser não o emite.
2. **Redação superada vira dispositivo próprio**, com `@N` no ID e a janela de vigência
   preenchida. É o que faz a consulta com data retroativa funcionar sem join: o texto de
   2010 é um ponto no índice como qualquer outro. Ver `urn.SEPARADOR_VERSAO`.
"""

from __future__ import annotations

import contextlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from lxml import html as lxml_html

from ..errors import ParserIndisponivel
from ..models import Dispositivo, Norma, Remissao, TipoDispositivo
from ..urn import (
    ESTRUTURAIS,
    SEPARADOR_CAMINHO,
    com_versao,
    montar_caminho,
    montar_urn_dispositivo,
    norma_conhecida,
    parse_urn,
    romano_para_int,
)
from .fontes import DocumentoBruto

T = TipoDispositivo

#: Profundidade de cada nível. O pai de um nó é o último nó aberto com nível menor —
#: é isso que faz o inciso pendurar no § quando há um, e no artigo quando não há.
NIVEL: Final[dict[TipoDispositivo, int]] = {
    T.PARTE: 0, T.LIVRO: 1, T.TITULO: 2, T.CAPITULO: 3, T.SECAO: 4, T.SUBSECAO: 5,
    T.ARTIGO: 6, T.CAPUT: 7, T.PARAGRAFO: 7, T.INCISO: 8, T.ALINEA: 9, T.ITEM: 10,
}

TAGS_RISCADO: Final = frozenset({"strike", "s", "del"})
TAGS_BLOCO: Final = ("p", "td", "li")

_ESTRUTURAL_POR_PALAVRA: Final[dict[str, TipoDispositivo]] = {
    "PARTE": T.PARTE, "LIVRO": T.LIVRO, "TITULO": T.TITULO, "TÍTULO": T.TITULO,
    "CAPITULO": T.CAPITULO, "CAPÍTULO": T.CAPITULO, "SECAO": T.SECAO, "SEÇÃO": T.SECAO,
    "SUBSECAO": T.SUBSECAO, "SUBSEÇÃO": T.SUBSECAO,
}

# --- reconhecimento de rótulo. Todos ancorados em ^: é o que separa rótulo de
# --- dispositivo de citação em prosa, porque a citação nunca abre um bloco.
_RE_ESTRUTURAL: Final = re.compile(
    r"^(PARTE|LIVRO|T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O)\s+"
    r"(?P<num>[IVXLCDM]+|\d+|[ÚU]NIC[AO]|PRELIMINAR|GERAL|ESPECIAL|COMPLEMENTAR)\b"
    r"\s*(?P<epigrafe>.*)$",
    re.I,
)
#: `o(?![a-zà-ú])` porque o Planalto escreve o ordinal como `Art. 6<sup>o</sup>`, que vira
#: "Art. 6o" ao achatar as tags — sem a negação, "Art. 6 outros" casaria.
_ORDINAL: Final = r"(?:[º°ª]|o(?![a-zà-úA-ZÀ-Ú]))?"
_RE_ARTIGO: Final = re.compile(
    rf"^Art\.?\s*(?P<num>\d[\d.]*)\s*{_ORDINAL}\s*(?:-\s*(?P<letra>[A-Za-z])\b)?"
    r"\s*[.\-–—º°]?\s*(?P<resto>.*)$",
    re.S,
)
_RE_PARAGRAFO: Final = re.compile(
    rf"^§\s*(?P<num>\d+)\s*{_ORDINAL}\s*(?:-\s*(?P<letra>[A-Za-z])\b)?"
    r"\s*[.\-–—]?\s*(?P<resto>.*)$",
    re.S,
)
_RE_PAR_UNICO: Final = re.compile(r"^Par[áa]grafo\s+[úu]nico\s*[.\-–—]?\s*(?P<resto>.*)$", re.S)
_RE_INCISO: Final = re.compile(r"^(?P<num>[IVXLCDM]+)\s*[-–—]\s*(?P<resto>.*)$", re.S)
_RE_ALINEA: Final = re.compile(r"^(?P<letra>[a-z])\s*\)\s*(?P<resto>.*)$", re.S)
#: Item usa 1-2 dígitos: sem o limite, uma linha começando com "1988." viraria item.
_RE_ITEM: Final = re.compile(r"^(?P<num>\d{1,2})\s*[.\-–—)]\s+(?P<resto>.*)$", re.S)

_RE_ADCT: Final = re.compile(r"^ATO\s+DAS\s+DISPOSI[ÇC][ÕO]ES\s+CONSTITUCIONAIS", re.I)

#: Notas de alteração. "Vide" não muda vigência — é remissão, e entra separado.
_RE_NOTA: Final = re.compile(
    r"\(\s*(?P<acao>Inclu[íi]d[oa]|Reda[çc][ãa]o dada|Revogad[oa]|Renumerad[oa])"
    r"[^)]*?(?:de\s+|/)?(?P<ano>(?:18|19|20)\d{2})[^)]*\)",
    re.I,
)
_RE_ANCORA_ARTIGO: Final = re.compile(r"^art\.?\d+", re.I)

#: Dispositivo cujo texto virou só a marca de revogação. Público porque o chunking também
#: precisa dele: indexar um chunk cujo conteúdo é a palavra "(revogado)" é ruído puro.
RE_TEXTO_REVOGADO: Final = re.compile(r"^\(\s*revogad[oa]s?\s*\)\s*[.;,]?$", re.I)

#: Remissão em prosa. Deliberadamente conservador: prefere não achar a achar errado,
#: porque remissão falsa vira expansão de contexto irrelevante no prompt.
_RE_REMISSAO: Final = re.compile(
    r"\bart(?:s|igos?)?\.?\s*(?P<num>\d[\d.]*)\s*[º°o]?"
    r"(?:\s*,?\s*§\s*(?P<par>\d+))?"
    r"(?:\s*,?\s*(?P<inc>[IVXLCDM]{1,8})\b(?![a-zà-ú]))?",
    re.I,
)


@dataclass
class NormaParseada:
    """Resultado do parse de um documento."""

    norma: Norma
    #: ordem de documento, pais sempre antes dos filhos
    dispositivos: list[Dispositivo]
    remissoes: list[Remissao]
    #: estruturas não reconhecidas; alimenta o refino do parser
    avisos: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Bloco:
    """Um elemento de bloco do HTML, já achatado em texto."""

    texto: str
    #: True quando a redação inteira está sob `<strike>`: é redação SUPERADA, não revogação
    superado: bool
    ancoras: tuple[str, ...]
    nota: str | None
    acao: str | None
    ano: int | None


# --------------------------------------------------------------------------------------
# Achatar o HTML
# --------------------------------------------------------------------------------------


def _texto_de(el, *, pular: frozenset[str] = frozenset()) -> str:
    partes: list[str] = []

    def desce(no, raiz: bool) -> None:
        if not raiz and no.tag in pular:
            if no.tail:
                partes.append(no.tail)
            return
        if no.text:
            partes.append(no.text)
        for filho in no:
            desce(filho, False)
        if not raiz and no.tail:
            partes.append(no.tail)

    desce(el, True)
    return "".join(partes)


def _limpar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.replace("\xa0", " ")).strip()


def _bloco_de(el) -> Bloco | None:
    completo = _limpar(_texto_de(el))
    if not completo:
        return None

    vivo = _limpar(_texto_de(el, pular=TAGS_RISCADO))
    superado = not vivo
    texto = completo if superado else vivo

    nota = acao = None
    ano = None
    if m := _RE_NOTA.search(texto):
        nota, ano = m.group(0), int(m.group("ano"))
        acao = _limpar(m.group("acao")).lower()
        texto = _limpar(texto[: m.start()] + texto[m.end() :])

    return Bloco(
        texto=texto,
        superado=superado,
        ancoras=tuple(a.lower() for a in el.xpath(".//a/@name")),
        nota=nota,
        acao=acao,
        ano=ano,
    )


def blocos_de(html_texto: str) -> list[Bloco]:
    """HTML -> blocos em ordem de documento.

    Um bloco cuja redação inteira está riscada é marcado, não descartado: é o texto que
    responde "o que valia em 2010?". Riscado PARCIAL tem a parte riscada removida, que é
    o caso de uma expressão substituída dentro de um dispositivo que continua vigente.
    """
    raiz = lxml_html.fromstring(html_texto)
    blocos: list[Bloco] = []
    for el in raiz.iter(*TAGS_BLOCO):
        # Só bloco FOLHA: um <td> que contém <p> é moldura de layout, e emiti-lo junto
        # duplicaria o texto. Comparar por identidade e não por `id()`: os elementos do
        # lxml são proxies transitórios e o CPython reaproveita o `id` de um coletado,
        # o que faz `id(x) in vistos` dar falso positivo e engolir blocos.
        if any(d is not el for d in el.iter(*TAGS_BLOCO)):
            continue
        if b := _bloco_de(el):
            blocos.append(b)
    return blocos


# --------------------------------------------------------------------------------------
# Classificar
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rotulo:
    """Um bloco reconhecido como abertura de dispositivo."""

    tipo: TipoDispositivo
    segmento: str
    rotulo: str
    texto: str


def _rotulo_numerico(prefixo: str, num: int, letra: str) -> str:
    """Rótulo como a técnica legislativa manda escrever, não como o HTML veio.

    Ordinal até o nono, cardinal do décimo em diante ("Art. 9º", "Art. 10"), e separador
    de milhar acima de 999 ("Art. 2.046"). É o que o usuário lê e confere contra a fonte,
    então reconstruir é mais seguro que repassar o que o Planalto escreveu — o `<sup>`
    achatado produzia "Art. 6 o" e o ponto do rótulo produzia "Art. 927.º".
    """
    corpo = f"{num:,}".replace(",", ".") if num > 999 else str(num)
    ordinal = "º" if num < 10 else ""
    sufixo = f"-{letra.upper()}" if letra else ""
    return f"{prefixo} {corpo}{ordinal}{sufixo}"


def _num_estrutural(bruto: str) -> int | None:
    b = bruto.upper()
    if b.isdigit():
        return int(b)
    if b in {"ÚNICA", "ÚNICO", "UNICA", "UNICO", "PRELIMINAR", "GERAL"}:
        return 1
    if b == "ESPECIAL":
        return 2
    if b == "COMPLEMENTAR":
        # "LIVRO COMPLEMENTAR" do Codigo Civil (arts. 2.028-2.046). Numero alto e
        # arbitrario para nao colidir com LIVRO I..V, que sao os livros numerados.
        return 99
    try:
        return romano_para_int(b)
    except ValueError:
        return None


def classificar(texto: str) -> Rotulo | None:
    """Texto de um bloco -> rótulo de dispositivo, ou None se for continuação/prosa."""
    if m := _RE_ESTRUTURAL.match(texto):
        tipo = _ESTRUTURAL_POR_PALAVRA[m.group(1).upper()]
        num = _num_estrutural(m.group("num"))
        if num is None:
            return None
        abrev = {T.PARTE: "prt", T.LIVRO: "liv", T.TITULO: "tit",
                 T.CAPITULO: "cap", T.SECAO: "sec", T.SUBSECAO: "sub"}[tipo]
        rot = _limpar(f"{m.group(1)} {m.group('num')}")
        return Rotulo(tipo, f"{abrev}{num}", rot, _limpar(m.group("epigrafe")))

    if m := _RE_ARTIGO.match(texto):
        num = int(m.group("num").replace(".", ""))
        letra = (m.group("letra") or "").lower()
        return Rotulo(
            T.ARTIGO,
            f"art{num}{letra}",
            _rotulo_numerico("Art.", num, letra),
            _limpar(m.group("resto")),
        )

    if m := _RE_PAR_UNICO.match(texto):
        return Rotulo(T.PARAGRAFO, "parunico", "Parágrafo único", _limpar(m.group("resto")))

    if m := _RE_PARAGRAFO.match(texto):
        num, letra = int(m.group("num")), (m.group("letra") or "").lower()
        return Rotulo(
            T.PARAGRAFO,
            f"par{num}{letra}",
            _rotulo_numerico("§", num, letra),
            _limpar(m.group("resto")),
        )

    if m := _RE_INCISO.match(texto):
        try:
            num = romano_para_int(m.group("num"))
        except ValueError:
            return None
        return Rotulo(T.INCISO, f"inc{num}", m.group("num").upper(), _limpar(m.group("resto")))

    if m := _RE_ALINEA.match(texto):
        letra = m.group("letra")
        n = ord(letra) - ord("a") + 1
        return Rotulo(T.ALINEA, f"ali{n}", f"{letra})", _limpar(m.group("resto")))

    if m := _RE_ITEM.match(texto):
        num = int(m.group("num"))
        return Rotulo(T.ITEM, f"ite{num}", f"{num}.", _limpar(m.group("resto")))

    return None


# --------------------------------------------------------------------------------------
# Montar a árvore
# --------------------------------------------------------------------------------------


@dataclass
class _No:
    segmento: str
    tipo: TipoDispositivo
    rotulo: str
    caminho: str
    texto: str
    ordem: int
    parent_caminho: str | None
    nota: str | None
    acao: str | None
    ano: int | None
    superado: bool


class ParserPlanaltoHTML:
    """HTML do Planalto. Marcação irregular — a hierarquia vem de heurística sobre rótulos."""

    nome = "planalto-html"

    def suporta(self, doc: DocumentoBruto) -> bool:
        return "html" in doc.content_type.lower() or doc.url.lower().endswith((".htm", ".html"))

    def parse(self, doc: DocumentoBruto) -> NormaParseada:
        conhecida = norma_conhecida(doc.urn)
        if conhecida is None:
            raise ParserIndisponivel(f"norma fora de `urn.CORPUS`: {doc.urn}")

        publicacao = _data_da_urn(doc.urn)
        norma = Norma(
            urn=doc.urn,
            tipo=doc.urn.split(":")[4],
            numero=doc.urn.rsplit(";", 1)[-1],
            ano=publicacao.year,
            data_publicacao=publicacao,
            apelido=conhecida.apelido,
            fonte_url=doc.url,
            sha256_origem=doc.sha256,
        )

        nos, avisos = self._montar(blocos_de(doc.texto()))
        nos = _podar_estrutura_vazia(nos, avisos)
        dispositivos = self._materializar(nos, norma, publicacao, avisos)
        remissoes = [r for d in dispositivos for r in extrair_remissoes(d.texto, d)]
        return NormaParseada(norma, dispositivos, remissoes, avisos)

    # -- montagem ----------------------------------------------------------------------

    def _montar(self, blocos: list[Bloco]) -> tuple[list[_No], list[str]]:
        nos: list[_No] = []
        pilha: list[tuple[int, str]] = []  # (nivel, caminho)
        componente: list[str] = []
        ordem_por_pai: defaultdict[str | None, int] = defaultdict(int)
        #: caminho -> nó vivo naquele caminho. Precisa ser um índice mantido, e não
        #: uma contagem: versionar um pai reescreve o caminho dos filhos.
        por_caminho: dict[str, _No] = {}
        versoes: defaultdict[str, int] = defaultdict(int)
        avisos: list[str] = []
        ultimo_artigo = 0
        ultimo_caminho: str | None = None

        for bloco in blocos:
            # `ultimo_artigo > 0` e não "está em maiúsculas": o ADCT é um SEGUNDO espaço
            # de numeração, logo só pode abrir depois que o primeiro teve artigo. Sem a
            # guarda, o link de navegação no topo da página (bloco nº 9 de 4328) jogava
            # a Constituição inteira para dentro do ADCT.
            if _RE_ADCT.match(bloco.texto) and ultimo_artigo > 0 and "adct" not in componente:
                # a CF tem dois espaços de numeração e 134 números em ambos: sem trocar
                # de componente aqui, o ADCT colide com o corpo permanente no PRIMARY KEY
                componente, pilha, ultimo_artigo = ["adct"], [], 0
                continue

            rot = classificar(bloco.texto)

            if rot is None:
                # continuação: anexa ao dispositivo aberto
                if ultimo_caminho is not None and bloco.texto:
                    alvo = next(n for n in reversed(nos) if n.caminho == ultimo_caminho)
                    alvo.texto = _limpar(f"{alvo.texto} {bloco.texto}")
                continue

            if rot.tipo is T.ARTIGO:
                num = int(re.match(r"art(\d+)", rot.segmento).group(1))
                tem_ancora = any(_RE_ANCORA_ARTIGO.match(a) for a in bloco.ancoras)
                if num < ultimo_artigo and not tem_ancora:
                    # "Art. N" abrindo bloco, número retrocedendo e sem âncora: é prosa.
                    avisos.append(f"'Art. {num}' tratado como prosa após art. {ultimo_artigo}")
                    if ultimo_caminho is not None:
                        alvo = next(n for n in reversed(nos) if n.caminho == ultimo_caminho)
                        alvo.texto = _limpar(f"{alvo.texto} {bloco.texto}")
                    continue
                ultimo_artigo = max(ultimo_artigo, num)

            nivel = NIVEL[rot.tipo]
            while pilha and pilha[-1][0] >= nivel:
                pilha.pop()
            parent = pilha[-1][1] if pilha else None

            prefixo = parent if parent else SEPARADOR_CAMINHO.join(componente)
            caminho = f"{prefixo}{SEPARADOR_CAMINHO}{rot.segmento}" if prefixo else rot.segmento

            if (anterior := por_caminho.get(caminho)) is not None and rot.tipo in ESTRUTURAIS:
                # Cabeçalho estrutural repetido NÃO é nova redação: o Código Civil traz
                # um sumário no fim do documento que reapresenta a árvore inteira.
                # Versionar aqui foi o pior bug desta fase — `liv1` virava `liv1@1` e os
                # 1847 artigos abaixo herdavam o `@` no caminho, em cascata, a partir de
                # um único cabeçalho. Reentrar no nó existente é inofensivo: a entrada de
                # sumário não traz artigo nenhum atrás dela.
                avisos.append(f"cabeçalho estrutural repetido, reusando nó: {caminho}")
                pilha.append((nivel, caminho))
                ultimo_caminho = caminho
                continue

            if anterior is not None:
                # Redação repetida do mesmo dispositivo: a anterior passa a ser superada.
                # A vigente é sempre a última do documento, e fica com o ID sem sufixo.
                versoes[caminho] += 1
                antigo = anterior.caminho
                novo = _versionar(antigo, versoes[caminho])
                anterior.caminho = novo
                anterior.segmento = com_versao(anterior.segmento, versoes[caminho])
                por_caminho.pop(antigo, None)
                por_caminho[novo] = anterior
                # Redação superada é folha no corpus medido, mas se um dia não for, o
                # filho não pode ficar apontando para um caminho que deixou de existir.
                for filho in nos:
                    if filho.parent_caminho == antigo:
                        filho.parent_caminho = novo
                    if filho.caminho.startswith(antigo + SEPARADOR_CAMINHO):
                        por_caminho.pop(filho.caminho, None)
                        filho.caminho = novo + filho.caminho[len(antigo) :]
                        por_caminho[filho.caminho] = filho

            ordem_por_pai[parent] += 1
            no = _No(
                segmento=rot.segmento,
                tipo=rot.tipo,
                rotulo=rot.rotulo,
                caminho=caminho,
                texto=rot.texto,
                ordem=ordem_por_pai[parent],
                parent_caminho=parent,
                nota=bloco.nota,
                acao=bloco.acao,
                ano=bloco.ano,
                superado=bloco.superado,
            )
            nos.append(no)
            por_caminho[caminho] = no
            pilha.append((nivel, caminho))
            ultimo_caminho = caminho

        return nos, avisos

    # -- vigência e conversão ----------------------------------------------------------

    def _materializar(
        self, nos: list[_No], norma: Norma, publicacao: date, avisos: list[str]
    ) -> list[Dispositivo]:
        # encadeia as versões: a redação superada morre quando a seguinte nasce
        por_base: defaultdict[str, list[_No]] = defaultdict(list)
        for n in nos:
            por_base[_sem_versao(n.caminho)].append(n)

        inicio: dict[str, date] = {}
        for n in nos:
            inicio[n.caminho] = date(n.ano, 1, 1) if n.ano else publicacao

        fim: dict[str, date | None] = {}
        for versoes in por_base.values():
            for i, n in enumerate(versoes):
                # O Planalto às vezes substitui o dispositivo pelo literal "(revogado)" e
                # rotula a nota como "Redação dada por", não "Revogado por". Sem tratar,
                # 29 dispositivos do corpus entram no índice como DIREITO VIGENTE cujo
                # texto é a palavra "(revogado)".
                revogado = bool(n.acao and n.acao.startswith("revogad")) or bool(
                    RE_TEXTO_REVOGADO.match(n.texto)
                )
                if revogado:
                    fim[n.caminho] = date(n.ano, 1, 1) if n.ano else publicacao
                elif i + 1 < len(versoes):
                    # a redação superada morre exatamente quando a seguinte nasce
                    fim[n.caminho] = inicio[versoes[i + 1].caminho]
                elif n.superado:
                    # Última versão e ainda assim riscada, sem nota de revogação: é texto
                    # morto sem data. Deixar `revogado_em = None` o indexaria como direito
                    # vigente, que é o pior defeito possível neste domínio.
                    fim[n.caminho] = date(n.ano, 1, 1) if n.ano else publicacao
                    if not n.ano:
                        avisos.append(
                            f"{n.caminho}: riscado sem data — revogado_em caiu na publicação"
                        )
                else:
                    fim[n.caminho] = None

        saida: list[Dispositivo] = []
        for n in nos:
            saida.append(
                Dispositivo(
                    id=montar_urn_dispositivo(norma.urn, n.caminho),
                    norma_urn=norma.urn,
                    parent_id=(
                        montar_urn_dispositivo(norma.urn, n.parent_caminho)
                        if n.parent_caminho
                        else None
                    ),
                    tipo=n.tipo,
                    rotulo=n.rotulo,
                    ordem=n.ordem,
                    caminho=n.caminho,
                    texto=n.texto,
                    vigencia_inicio=inicio[n.caminho],
                    revogado_em=fim[n.caminho],
                    nota_alteracao=n.nota,
                )
            )
        return saida


class ParserLexMLXML:
    """XML do LexML. Hierarquia explícita no schema; caminho preferencial quando disponível.

    Não implementada: a API SRU do LexML está atrás de desafio anti-bot e foi eliminada
    como fonte automatizável. Ver `fontes.FonteLexML`.
    """

    nome = "lexml-xml"

    def suporta(self, doc: DocumentoBruto) -> bool:
        return False

    def parse(self, doc: DocumentoBruto) -> NormaParseada:
        raise ParserIndisponivel("parser LexML não implementado: fonte eliminada")


PARSERS: Final = (ParserPlanaltoHTML(), ParserLexMLXML())


def escolher_parser(doc: DocumentoBruto):
    """Primeiro parser registrado que suporta o documento.

    Levanta `ParserIndisponivel` se nenhum souber tratá-lo.
    """
    for p in PARSERS:
        if p.suporta(doc):
            return p
    raise ParserIndisponivel(f"nenhum parser trata {doc.content_type} de {doc.url}")


# --------------------------------------------------------------------------------------
# Remissões
# --------------------------------------------------------------------------------------


def extrair_remissoes(texto: str, origem: Dispositivo) -> list[Remissao]:
    """Acha referências cruzadas no texto ("na forma do art. 37, § 6º").

    Assume a mesma norma da origem: resolver "Lei nº 8.078" para URN exigiria um
    catálogo de toda a legislação citada, e `writer.resolver_remissoes_pendentes` já
    descarta destino inexistente. Perde remissão entre normas; não inventa nenhuma.
    """
    achados: list[Remissao] = []
    for m in _RE_REMISSAO.finditer(texto):
        num = int(m.group("num").replace(".", ""))
        segmentos = [f"art{num}"]
        if m.group("par"):
            segmentos.append(f"par{int(m.group('par'))}")
        if bruto := m.group("inc"):
            # romano malformado: cai fora e a remissão fica no nível do artigo, que é
            # menos preciso mas não é errado
            with contextlib.suppress(ValueError):
                segmentos.append(f"inc{romano_para_int(bruto)}")
        destino = montar_urn_dispositivo(origem.norma_urn, montar_caminho(segmentos))
        if destino == origem.id:
            continue
        achados.append(
            Remissao(
                origem_id=origem.id,
                destino_urn=destino,
                texto_original=_limpar(m.group(0)),
            )
        )
    # dedup preservando ordem: a chave natural do banco é (origem, destino, texto)
    unicos: dict[tuple[str, str], Remissao] = {}
    for r in achados:
        unicos.setdefault((r.destino_urn, r.texto_original), r)
    return list(unicos.values())


# --------------------------------------------------------------------------------------


def _podar_estrutura_vazia(nos: list[_No], avisos: list[str]) -> list[_No]:
    """Descarta agrupamento sem nenhum artigo abaixo.

    É o que sobra do sumário: uma entrada "LIVRO III Do Direito das Coisas" que não tem
    artigo atrás dela não é estrutura da norma, é linha de índice. Manter poluiria o
    `caminho` de nada e apareceria no `texto_contextualizado` do chunking.
    """
    com_artigo: set[str] = set()
    for n in nos:
        if n.tipo is not T.ARTIGO:
            continue
        partes = n.caminho.split(SEPARADOR_CAMINHO)
        for i in range(1, len(partes)):
            com_artigo.add(SEPARADOR_CAMINHO.join(partes[:i]))

    mantidos = [n for n in nos if n.tipo not in ESTRUTURAIS or n.caminho in com_artigo]
    if (descartados := len(nos) - len(mantidos)):
        avisos.append(f"{descartados} agrupamentos sem artigo abaixo descartados (sumário)")

    # Podar um pai deixaria o filho apontando para um `parent_id` inexistente, e a FK de
    # `dispositivo.parent_id` rejeitaria a ingestão inteira. Reata no ancestral vivo mais
    # próximo — o nó sobe na árvore em vez de sumir com o texto junto.
    vivos = {n.caminho for n in mantidos}
    reatados = 0
    for n in mantidos:
        if n.parent_caminho is None or n.parent_caminho in vivos:
            continue
        partes = n.caminho.split(SEPARADOR_CAMINHO)[:-1]
        while partes and SEPARADOR_CAMINHO.join(partes) not in vivos:
            partes.pop()
        n.parent_caminho = SEPARADOR_CAMINHO.join(partes) or None
        reatados += 1
    if reatados:
        avisos.append(f"{reatados} nós reatados ao ancestral vivo após a poda")
    return mantidos


def _sem_versao(caminho: str) -> str:
    return re.sub(r"@\d+$", "", caminho)


def _versionar(caminho: str, idx: int) -> str:
    return f"{_sem_versao(caminho)}@{idx}"


def _data_da_urn(urn: str) -> date:
    p = parse_urn(urn)
    if p.data is None:
        raise ParserIndisponivel(f"URN sem data de publicação: {urn}")
    return p.data
