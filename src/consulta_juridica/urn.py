"""Identificadores LexML e o ID canônico de dispositivo.

Usado por parser, filtros, expansão, eval e API. Sem um módulo próprio, a manipulação
dessas strings vira tratamento ad hoc espalhado por cinco lugares.

Esquema (decidido na fase 1, contra o corpus real — não é LexML estrito):

    urn:lex:br:federal:lei:1990-09-11;8078!art6_inc8
    |________ URN da norma ____________| |fragmento|

O **fragmento** é a identidade do dispositivo. Três regras, cada uma com um motivo medido
no corpus baixado:

1. **Ancestral estrutural não entra.** Um artigo é único dentro da norma: `art6` basta, e
   `tit3_cap1_art6` mudaria de ID se o parser errasse o capítulo. A posição na árvore vive
   em `Dispositivo.caminho` e em `parent_id`, que é onde ela pode ser corrigida sem
   invalidar o índice inteiro. Nó estrutural, que não tem artigo acima, usa o caminho todo.

2. **Componente antes do fragmento.** A CF/88 tem dois espaços de numeração: o corpo
   permanente (248 artigos) e o ADCT (135 âncoras `adctart*`). **134 números aparecem nos
   dois.** Sem o prefixo `adct_`, o ID colide e o `PRIMARY KEY` de `dispositivo` rejeita
   metade do ADCT na ingestão.

3. **Numeração sempre em arábico.** Inciso LXXVIII vira `inc78`, alínea "a" vira `ali1`.
   Ordenável, comparável, e o ID concorda com `Dispositivo.ordem`. A notação que o
   advogado lê vive em `rotulo` e sai por `queries.rotulo_completo`.

Duas irregularidades deliberadas: `cpt` e `parunico` não levam número, porque são
dispositivos singulares — um artigo tem um caput, e um artigo com parágrafo único não tem
§ 1º. Artigo e parágrafo acrescidos levam a letra colada: `art103a` para "Art. 103-A"
(45 ocorrências na CF, 11 no CDC, 7 no CC).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from .models import TipoDispositivo

SEPARADOR_FRAGMENTO: Final = "!"
SEPARADOR_SEGMENTO: Final = "_"
SEPARADOR_CAMINHO: Final = "/"

#: Sufixo de redação superada: `art6@1` é a 1ª redação do art. 6º, `art6` é a vigente.
#: Índice ordinal e não ano, porque 5 artigos da CF/88 têm duas redações superadas no
#: mesmo ano (art. 111 tem duas de 2016 e duas de 2022) e 57 redações não trazem ano
#: extraível da nota. Ver `ingest.parser`.
SEPARADOR_VERSAO: Final = "@"

#: Abreviação de cada nível. Fonte única: o parser monta o caminho com estas, e
#: `queries.subarvore` casa prefixo contra ele.
ABREV: Final[dict[TipoDispositivo, str]] = {
    TipoDispositivo.PARTE: "prt",
    TipoDispositivo.LIVRO: "liv",
    TipoDispositivo.TITULO: "tit",
    TipoDispositivo.CAPITULO: "cap",
    TipoDispositivo.SECAO: "sec",
    TipoDispositivo.SUBSECAO: "sub",
    TipoDispositivo.ARTIGO: "art",
    TipoDispositivo.CAPUT: "cpt",
    TipoDispositivo.PARAGRAFO: "par",
    TipoDispositivo.INCISO: "inc",
    TipoDispositivo.ALINEA: "ali",
    TipoDispositivo.ITEM: "ite",
}

TIPO_POR_ABREV: Final[dict[str, TipoDispositivo]] = {v: k for k, v in ABREV.items()}

#: Níveis de agrupamento: existem na árvore, mas não entram no ID de um artigo (regra 1).
ESTRUTURAIS: Final[frozenset[TipoDispositivo]] = frozenset(
    {
        TipoDispositivo.PARTE,
        TipoDispositivo.LIVRO,
        TipoDispositivo.TITULO,
        TipoDispositivo.CAPITULO,
        TipoDispositivo.SECAO,
        TipoDispositivo.SUBSECAO,
    }
)

#: Segmentos singulares, sem número.
SEGMENTO_CAPUT: Final = "cpt"
SEGMENTO_PARAGRAFO_UNICO: Final = "parunico"

#: Componentes de norma que abrem um espaço de numeração próprio (regra 2).
COMPONENTES: Final[frozenset[str]] = frozenset({"adct"})

_RE_SEGMENTO: Final = re.compile(
    r"^(?:"
    rf"(?P<singular>{SEGMENTO_PARAGRAFO_UNICO}|{SEGMENTO_CAPUT})"
    r"|(?P<abrev>prt|liv|tit|cap|sec|sub|art|par|inc|ali|ite)(?P<num>\d+)(?P<letra>[a-z]?)"
    rf")(?:{re.escape(SEPARADOR_VERSAO)}(?P<versao>\d+))?$"
)

_RE_URN: Final = re.compile(
    r"^urn:lex:br"
    r":(?P<esfera>[a-z.;]+)"
    r":(?P<tipo>[a-z.]+)"
    r":(?P<data>\d{4}-\d{2}-\d{2}|\d{4})"
    r";(?P<numero>[^!]+)"
    rf"(?:{re.escape(SEPARADOR_FRAGMENTO)}(?P<fragmento>.+))?$"
)


@dataclass(frozen=True)
class UrnPartes:
    """Decomposição de uma URN LexML."""

    esfera: str
    tipo: str
    data: date | None
    numero: str
    fragmento: str | None  # "art6_inc8", quando aponta para um dispositivo


@dataclass(frozen=True)
class NormaConhecida:
    """Uma norma do corpus, com o que é preciso saber antes de ela existir no SQLite.

    O golden set e os CLIs precisam resolver "CDC" para uma URN sem ter um banco aberto —
    por isso a tabela vive aqui, e não numa consulta à tabela `norma`.
    """

    urn: str
    apelido: str
    rotulo: str
    fonte_url: str
    apelidos: tuple[str, ...]


#: URLs conferidas com requisição real em 2026-09-21 (HTTP 200). O Planalto não tem uma
#: regra única de caminho: lei até 2001 fica em `/leis/`, de 2002 em diante em
#: `/leis/AAAA/`. Por isso a URL é dado, não fórmula.
CORPUS: Final[tuple[NormaConhecida, ...]] = (
    NormaConhecida(
        urn="urn:lex:br:federal:constituicao:1988-10-05;1988",
        apelido="CF/88",
        rotulo="Constituição Federal de 1988",
        fonte_url="https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm",
        apelidos=("cf/88", "cf88", "cf", "constituicao", "constituicao federal", "crfb"),
    ),
    NormaConhecida(
        urn="urn:lex:br:federal:lei:1990-09-11;8078",
        apelido="CDC",
        rotulo="Lei 8.078/1990",
        fonte_url="https://www.planalto.gov.br/ccivil_03/leis/l8078.htm",
        apelidos=("cdc", "codigo de defesa do consumidor", "lei 8.078/1990", "lei 8078"),
    ),
    NormaConhecida(
        urn="urn:lex:br:federal:lei:2002-01-10;10406",
        apelido="CC",
        rotulo="Lei 10.406/2002",
        fonte_url="https://www.planalto.gov.br/ccivil_03/leis/2002/l10406.htm",
        apelidos=("cc", "codigo civil", "lei 10.406/2002", "lei 10406"),
    ),
)

_POR_URN: Final[dict[str, NormaConhecida]] = {n.urn: n for n in CORPUS}
_POR_APELIDO: Final[dict[str, NormaConhecida]] = {a: n for n in CORPUS for a in n.apelidos}


# --------------------------------------------------------------------------------------
# Normalização de texto e de numeração
# --------------------------------------------------------------------------------------

_ACENTOS: Final = str.maketrans(
    "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
    "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC",
)

_ROMANOS_DUPLOS: Final = (("cm", 900), ("cd", 400), ("xc", 90), ("xl", 40), ("ix", 9), ("iv", 4))
_ROMANOS_SIMPLES: Final = {"m": 1000, "d": 500, "c": 100, "l": 50, "x": 10, "v": 5, "i": 1}
_RE_ROMANO: Final = re.compile(r"m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})")


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, espaços colapsados. Só para casar apelido e rótulo."""
    return re.sub(r"\s+", " ", texto.translate(_ACENTOS).lower()).strip()


def e_romano(texto: str) -> bool:
    """True se o texto é um numeral romano bem formado e não vazio."""
    s = normalizar(texto).replace(".", "")
    return bool(s) and _RE_ROMANO.fullmatch(s) is not None


def romano_para_int(texto: str) -> int:
    """"LXXVIII" -> 78. Levanta ValueError se não for romano bem formado.

    Validado por regex antes de somar: sem isso "IIII" e "VV" passariam e devolveriam
    números plausíveis, que é o pior jeito possível de errar um inciso.
    """
    s = normalizar(texto).replace(".", "")
    if not e_romano(s):
        raise ValueError(f"numeral romano malformado: {texto!r}")
    total, i = 0, 0
    while i < len(s):
        for sim, val in _ROMANOS_DUPLOS:
            if s.startswith(sim, i):
                total += val
                i += 2
                break
        else:
            total += _ROMANOS_SIMPLES[s[i]]
            i += 1
    return total


# --------------------------------------------------------------------------------------
# Segmentos do fragmento
# --------------------------------------------------------------------------------------

#: Prefixo verbal do rótulo ("Art.", "Inciso", "Capítulo"). Ordenado por comprimento
#: decrescente: com "par" antes de "parte", "Parte I" viraria "te i".
_PREFIXOS: Final = sorted(
    (
        "parte", "livro", "titulo", "capitulo", "secao", "subsecao", "artigo", "inciso",
        "paragrafo", "alinea", "item",
        "prt", "liv", "tit", "cap", "sec", "sub", "art", "inc", "par", "ali", "ite",
    ),
    key=len,
    reverse=True,
)
_RE_PREFIXO: Final = re.compile(rf"^(?:{'|'.join(_PREFIXOS)})\.?\s*")

#: Níveis cuja numeração a fonte escreve em romano ("Título II", "Art. 5º, LXXVIII").
#: Artigo é sempre arábico; alínea é letra — e "c" seria um romano válido (100), por isso
#: alínea NÃO pode entrar aqui.
_NUMERADOS_EM_ROMANO: Final[frozenset[TipoDispositivo]] = ESTRUTURAIS | {TipoDispositivo.INCISO}
_RE_NUM_LETRA: Final = re.compile(
    r"^(?P<num>\d+)\s*(?:º|°|ª)?\s*(?:-\s*(?P<letra>[A-Za-z]))?$"
)
_BORDAS: Final = " .§()\"'–—"

#: Separador de milhar no rótulo: o Planalto escreve "Art. 2.046.". São 1122 rótulos assim
#: só no Código Civil — metade do código. Sem remover, `segmento()` rejeita todos.
_RE_MILHAR: Final = re.compile(r"(?<=\d)\.(?=\d{3}(?!\d))")


def segmento(tipo: TipoDispositivo, rotulo: str) -> str:
    """Rótulo como a fonte escreve -> segmento de ID.

    ("inciso", "LXXVIII") -> "inc78";   ("artigo", "Art. 103-A")     -> "art103a"
    ("alinea", 'a)')      -> "ali1";    ("paragrafo", "§ 2º-A")      -> "par2a"
    ("caput", "")         -> "cpt";     ("paragrafo", "Parágrafo único") -> "parunico"

    Levanta ValueError no que não reconhece. É deliberado: ID errado em silêncio produz
    dispositivo órfão no índice, e o sintoma só aparece como recall baixo semanas depois.
    """
    if tipo is TipoDispositivo.CAPUT:
        return SEGMENTO_CAPUT

    s = _RE_MILHAR.sub("", _RE_PREFIXO.sub("", normalizar(rotulo))).strip(_BORDAS)

    if tipo is TipoDispositivo.PARAGRAFO and "unico" in s:
        return SEGMENTO_PARAGRAFO_UNICO

    abrev = ABREV[tipo]

    if tipo in _NUMERADOS_EM_ROMANO and e_romano(s):
        return f"{abrev}{romano_para_int(s)}"

    if tipo in (TipoDispositivo.ALINEA, TipoDispositivo.ITEM) and re.fullmatch(r"[a-z]", s):
        return f"{abrev}{ord(s) - ord('a') + 1}"

    if m := _RE_NUM_LETRA.match(s):
        return f"{abrev}{int(m.group('num'))}{(m.group('letra') or '').lower()}"

    raise ValueError(f"rótulo não reconhecido para {tipo.value}: {rotulo!r}")


def parse_segmento(seg: str) -> tuple[TipoDispositivo, int | None, str]:
    """Segmento -> (tipo, número, letra). Inverso parcial de `segmento`.

    O número é None nos singulares (`cpt`, `parunico`), que não têm numeração na fonte.
    O sufixo de versão é aceito e ignorado: `art6@1` e `art6` são ambos ARTIGO nº 6, e
    quem precisa da versão chama `versao_de_segmento`.
    """
    if seg in COMPONENTES:
        raise ValueError(f"{seg!r} é componente de norma, não segmento de dispositivo")
    m = _RE_SEGMENTO.match(seg)
    if not m:
        raise ValueError(f"segmento malformado: {seg!r}")
    if singular := m.group("singular"):
        if singular == SEGMENTO_CAPUT:
            return TipoDispositivo.CAPUT, None, ""
        return TipoDispositivo.PARAGRAFO, None, ""
    return TIPO_POR_ABREV[m.group("abrev")], int(m.group("num")), m.group("letra")


def versao_de_segmento(seg: str) -> int | None:
    """Índice da redação superada, ou None quando o segmento é a redação vigente."""
    m = _RE_SEGMENTO.match(seg)
    if not m:
        raise ValueError(f"segmento malformado: {seg!r}")
    v = m.group("versao")
    return int(v) if v else None


def com_versao(seg: str, versao: int) -> str:
    """Marca o segmento como redação superada: `art6` + 1 -> `art6@1`."""
    if versao < 1:
        raise ValueError(f"índice de versão começa em 1, recebi {versao}")
    if versao_de_segmento(seg) is not None:
        raise ValueError(f"segmento já versionado: {seg!r}")
    return f"{seg}{SEPARADOR_VERSAO}{versao}"


def montar_caminho(segmentos: Sequence[str]) -> str:
    """Segmentos da raiz até o nó -> materialized path ("adct/art5", "tit2/cap1/art5")."""
    if not segmentos:
        raise ValueError("caminho vazio")
    return SEPARADOR_CAMINHO.join(segmentos)


def fragmento_de_caminho(caminho: str) -> str:
    """Materialized path -> fragmento de ID, aplicando as regras 1 e 2 do docstring.

    "tit2/cap1/art5/inc78" -> "art5_inc78"   (estruturais caem)
    "adct/art5"            -> "adct_art5"    (componente fica)
    "tit2/cap1"            -> "tit2_cap1"    (nó estrutural: caminho inteiro)
    """
    segs = [s for s in caminho.split(SEPARADOR_CAMINHO) if s]
    if not segs:
        raise ValueError("caminho vazio")

    componentes = [s for s in segs if s in COMPONENTES]
    resto = [s for s in segs if s not in COMPONENTES]

    ultimo_artigo = next(
        (
            i
            for i in range(len(resto) - 1, -1, -1)
            if parse_segmento(resto[i])[0] is TipoDispositivo.ARTIGO
        ),
        None,
    )
    if ultimo_artigo is not None:
        resto = resto[ultimo_artigo:]

    return SEPARADOR_SEGMENTO.join(componentes + resto)


# --------------------------------------------------------------------------------------
# URNs
# --------------------------------------------------------------------------------------


def montar_urn_norma(tipo: str, numero: str, data: date, esfera: str = "federal") -> str:
    """Compõe a URN de uma norma inteira."""
    return f"urn:lex:br:{esfera}:{tipo}:{data.isoformat()};{numero}"


def montar_urn_dispositivo(norma_urn: str, caminho: str) -> str:
    """Anexa o fragmento de dispositivo à URN da norma.

    Recebe o `caminho`, não o fragmento pronto: quem chama é o parser, que conhece a
    árvore, e manter a regra "estrutural não entra no ID" num lugar só evita que metade do
    corpus seja indexada com uma convenção e metade com outra.
    """
    if SEPARADOR_FRAGMENTO in norma_urn:
        raise ValueError(f"esperava URN de norma, recebi dispositivo: {norma_urn!r}")
    return f"{norma_urn}{SEPARADOR_FRAGMENTO}{fragmento_de_caminho(caminho)}"


def parse_urn(urn: str) -> UrnPartes:
    """Decompõe uma URN. Levanta ValueError se malformada."""
    m = _RE_URN.match(urn.strip())
    if not m:
        raise ValueError(f"URN malformada: {urn!r}")
    bruta = m.group("data")
    return UrnPartes(
        esfera=m.group("esfera"),
        tipo=m.group("tipo"),
        data=date.fromisoformat(bruta) if len(bruta) == 10 else None,
        numero=m.group("numero"),
        fragmento=m.group("fragmento"),
    )


def urn_da_norma(dispositivo_id: str) -> str:
    """Descarta o fragmento: `...;8078!art6_inc8` -> `...;8078`."""
    return dispositivo_id.split(SEPARADOR_FRAGMENTO, 1)[0]


def resolver_apelido(texto: str) -> str | None:
    """Traduz apelido corrente para URN: "CDC" -> urn:lex:...;8078. None se desconhecido."""
    n = _POR_APELIDO.get(normalizar(texto))
    return n.urn if n else None


def norma_conhecida(urn: str) -> NormaConhecida | None:
    """Entrada do corpus para uma URN de norma; aceita também ID de dispositivo."""
    return _POR_URN.get(urn_da_norma(urn))


def rotulo_humano(urn: str) -> str:
    """URN -> "Lei 8.078/1990"."""
    norma_urn = urn_da_norma(urn)
    if n := _POR_URN.get(norma_urn):
        return n.rotulo
    p = parse_urn(norma_urn)
    ano = p.data.year if p.data else "?"
    if p.tipo == "constituicao":
        return f"Constituição Federal de {ano}"
    numero = f"{int(p.numero):,}".replace(",", ".") if p.numero.isdigit() else p.numero
    return f"{p.tipo.capitalize()} {numero}/{ano}"


def url_planalto(norma_urn: str) -> str:
    """URL pública da norma, para o usuário conferir a citação na fonte oficial."""
    n = norma_conhecida(norma_urn)
    if n is None:
        raise ValueError(f"norma fora do corpus, sem URL conhecida: {norma_urn!r}")
    return n.fonte_url


# --------------------------------------------------------------------------------------
# Rótulo humano -> ID canônico
# --------------------------------------------------------------------------------------

_SUFIXO_NUM: Final = r"\s*(?P<num>\d+)\s*[º°]?\s*(?:-\s*(?P<letra>[a-z]))?"
_RE_ARTIGO: Final = re.compile(r"^art(?:igo)?\.?" + _SUFIXO_NUM)
_RE_PARAGRAFO: Final = re.compile(r"^(?:§|par(?:agrafo)?\.?)" + _SUFIXO_NUM)
_RE_PAR_UNICO: Final = re.compile(r"^(?:§\s*)?(?:par(?:agrafo)?\.?\s*)?unico")
_RE_ITEM: Final = re.compile(r"^ite(?:m)?\.?\s*(?P<num>\d+)")
_RE_ALINEA: Final = re.compile(r"^(?:al(?:inea)?\.?\s*)?[\"']?(?P<letra>[a-z])[\"']?\)?(?=$|[\s,])")
_RE_TOKEN_ROMANO: Final = re.compile(r"^(?P<r>[ivxlcdm]+)(?=$|[\s,)])")
_BORDAS_ROTULO: Final = " ,.;:-)("


def resolver_rotulo(texto: str) -> str:
    """Rótulo como um humano anota -> ID canônico de dispositivo.

    "CF/88 art. 5º LXXVIII"  -> urn:lex:br:federal:constituicao:1988-10-05;1988!art5_inc78
    "CC art. 206 § 3º V"     -> urn:lex:br:federal:lei:2002-01-10;10406!art206_par3_inc5
    "CF/88 ADCT art. 5º"     -> urn:lex:br:federal:constituicao:1988-10-05;1988!adct_art5

    Existe para o `golden/seed.jsonl` continuar legível: anotação à mão é o artefato mais
    caro do eval, e obrigar um humano a escrever URN de 50 caracteres garante erro de
    digitação — que é exatamente o que `eval.golden.validar` deveria estar caçando.

    Levanta ValueError no que não entende, nunca devolve um ID chutado: um esperado errado
    derruba o recall em silêncio e faz parecer defeito da recuperação.
    """
    resto = _RE_MILHAR.sub("", normalizar(texto))

    # Maior apelido primeiro: "cf" é prefixo de "cf/88" e casaria antes, deixando "/88"
    # para o parser de artigo.
    candidatos = sorted(_POR_APELIDO, key=len, reverse=True)
    apelido = next((a for a in candidatos if re.match(rf"{re.escape(a)}\b", resto)), None)
    if apelido is None:
        raise ValueError(f"norma não reconhecida em {texto!r}")
    norma_urn = _POR_APELIDO[apelido].urn
    resto = resto[len(apelido) :].strip(_BORDAS_ROTULO)

    segmentos: list[str] = []
    if m := re.match(r"^adct\b", resto):
        segmentos.append("adct")
        resto = resto[m.end() :].strip(_BORDAS_ROTULO)

    if not resto:
        raise ValueError(f"{texto!r} aponta para a norma inteira, não para um dispositivo")

    m = _RE_ARTIGO.match(resto)
    if not m:
        raise ValueError(f"esperava 'art. N' em {texto!r}, encontrei {resto!r}")
    segmentos.append(f"art{int(m.group('num'))}{m.group('letra') or ''}")
    resto = resto[m.end() :].strip(_BORDAS_ROTULO)

    viu_inciso = False
    while resto:
        if m := _RE_PAR_UNICO.match(resto):
            segmentos.append(SEGMENTO_PARAGRAFO_UNICO)
        elif m := _RE_PARAGRAFO.match(resto):
            segmentos.append(f"par{int(m.group('num'))}{m.group('letra') or ''}")
        elif m := _RE_ITEM.match(resto):
            segmentos.append(f"ite{int(m.group('num'))}")
        elif (m := _RE_TOKEN_ROMANO.match(resto)) and not viu_inciso and e_romano(m.group("r")):
            # Letra solta antes de um inciso é inciso; depois de um, é alínea. A hierarquia
            # do texto legal é artigo > parágrafo > inciso > alínea, e "V" é ambíguo fora
            # dela — sem esta regra, o V do art. 206 § 3º viraria alínea "v".
            segmentos.append(f"inc{romano_para_int(m.group('r'))}")
            viu_inciso = True
        elif m := _RE_ALINEA.match(resto):
            segmentos.append(f"ali{ord(m.group('letra')) - ord('a') + 1}")
        else:
            raise ValueError(f"trecho não reconhecido em {texto!r}: {resto!r}")
        resto = resto[m.end() :].strip(_BORDAS_ROTULO)

    return montar_urn_dispositivo(norma_urn, montar_caminho(segmentos))
