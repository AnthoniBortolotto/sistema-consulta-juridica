"""Árvore de dispositivos -> chunks indexáveis.

Módulo próprio porque esta é a decisão mais consequente do sistema e a que mais vai ser
trocada. Granularidade e quanto contexto hierárquico entra no texto embedado determinam a
qualidade da recuperação, e a estratégia escolhida define o que `retrieval.expansao`
precisa reconstruir depois.

Jamais um splitter por contagem de caracteres: cortar no meio de um artigo separa o inciso
do caput que lhe dá sentido.

Duas decisões desta fase, ambas tomadas contra o corpus ingerido e não por analogia:

1. **A unidade é o dispositivo com texto próprio, não a folha da árvore.** Folha estrita
   deixaria de fora os 1124 artigos que têm filhos — 199 KB de caput fora do índice — e o
   art. 927 do Código Civil, que o golden `lex-02` espera como resposta, não viraria chunk
   nenhum, porque tem um parágrafo único pendurado.

2. **O `texto_indexado` carrega o TEXTO dos ancestrais, não só os rótulos.** 15% das
   folhas do corpus têm menos de 60 caracteres ("VI - defesa da paz;",
   "VII - criação de despesa obrigatória; e"). Embedar isso sozinho não recupera nada; o
   que dá sentido ao inciso é o caput que o abre. O `texto` continua cru — é ele que vai
   ser citado, e citação tem de bater com a fonte caractere a caractere.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Final, Protocol

from ..models import (
    Chunk,
    ChunkPayload,
    Dispositivo,
    Norma,
    TipoDispositivo,
    linha_dispositivo,
    ordem_documento,
)
from ..urn import ESTRUTURAIS, SEPARADOR_CAMINHO, rotulo_completo_de, rotulo_humano
from ..vectorstore import dia, dia_ou_sentinela, id_ponto
from .parser import RE_TEXTO_REVOGADO

#: Separadores do `texto_indexado`. Quebra de linha entre níveis, seta na trilha: o
#: modelo de embedding lida bem com os dois, e a inspeção humana do prompt fica legível.
SETA: Final = " > "


class Estrategia(Protocol):
    """Como transformar dispositivos em chunks. O nome vai para o relatório de eval."""

    nome: str

    def chunks(self, disps: Sequence[Dispositivo], norma: Norma) -> Iterator[Chunk]: ...


def tem_conteudo(d: Dispositivo) -> bool:
    """Descarta o que não tem o que indexar.

    Além do texto vazio, o literal "(revogado)": o Planalto substitui o dispositivo por
    essa marca, e são 29 chunks cujo conteúdo inteiro seria a palavra "revogado".
    """
    texto = d.texto.strip()
    return bool(texto) and not RE_TEXTO_REVOGADO.match(texto)


class Arvore:
    """Índice em memória da árvore de uma norma.

    O chunking não recebe conexão de banco de propósito: a estratégia é função pura da
    árvore, o que a torna testável sem SQLite e reexecutável sobre o resultado do parser
    antes de qualquer gravação.
    """

    def __init__(self, disps: Sequence[Dispositivo]) -> None:
        self._por_caminho = {d.caminho: d for d in disps}
        self._disps = list(disps)

    def __iter__(self) -> Iterator[Dispositivo]:
        return iter(self._disps)

    def ancestrais(self, d: Dispositivo) -> list[Dispositivo]:
        """Da raiz até o pai. Prefixos do materialized path, sem recursão."""
        segs = d.caminho.split(SEPARADOR_CAMINHO)
        caminhos = (SEPARADOR_CAMINHO.join(segs[: i + 1]) for i in range(len(segs) - 1))
        return [a for c in caminhos if (a := self._por_caminho.get(c)) is not None]

    def descendentes(self, d: Dispositivo) -> list[Dispositivo]:
        """Subárvore, em ordem de documento e sem o próprio nó."""
        prefixo = d.caminho + SEPARADOR_CAMINHO
        return ordem_documento([x for x in self._disps if x.caminho.startswith(prefixo)])


#: Reconstruir a pontuação importa: o texto indexado é lido por humano na depuração do
#: prompt, e "VIII a facilitação" não é como a lei se escreve. A formatação vive em
#: `models.linha_dispositivo` porque `retrieval.expansao` monta com ela o texto que vai
#: ao modelo — duas implementações divergiriam.
linha = linha_dispositivo


def texto_contextualizado(
    d: Dispositivo, ancestrais: Sequence[Dispositivo], norma: Norma
) -> str:
    """Monta o `texto_indexado`: prefixa o texto próprio com a hierarquia.

    Três camadas, e a terceira é a que importa:

        Lei 8.078/1990 > Título I — Dos Direitos do Consumidor > Capítulo III — Dos
        Direitos Básicos
        Art. 6º São direitos básicos do consumidor:
        VIII - a facilitação da defesa de seus direitos, inclusive com a inversão do
        ônus da prova, a seu favor, no processo civil;

    A trilha de agrupamentos entra com a epígrafe, que é texto com significado ("Dos
    Direitos Básicos") e é o que permite à busca densa distinguir dois incisos de redação
    parecida em capítulos diferentes. Os ancestrais citáveis entram com o TEXTO deles: sem
    o caput, "VI - defesa da paz;" é um vetor sem informação.
    """
    linhas: list[str] = []

    trilha = [norma.apelido or rotulo_humano(norma.urn)]
    for a in ancestrais:
        if a.tipo not in ESTRUTURAIS:
            continue
        epigrafe = a.texto.strip()
        trilha.append(f"{a.rotulo} — {epigrafe}" if epigrafe else a.rotulo)
    linhas.append(SETA.join(trilha))

    for a in ancestrais:
        if a.tipo in ESTRUTURAIS or not tem_conteudo(a):
            continue
        linhas.append(linha(a))

    linhas.append(linha(d))
    return "\n".join(linhas)


#: Fallback para dispositivo sem vigência e norma sem data de publicação. O campo é
#: obrigatório no payload, e `range(lte=ref)` no Qdrant exclui ponto sem o campo — deixar
#: ausente sumiria com o chunk em vez de dar erro.
_EPOCA: Final = date(1, 1, 1)


def _payload(
    d: Dispositivo, cadeia: Sequence[Dispositivo], norma: Norma, texto: str
) -> ChunkPayload:
    return ChunkPayload(
        dispositivo_id=d.id,
        norma_urn=norma.urn,
        norma_apelido=norma.apelido,
        tipo=d.tipo,
        caminho=d.caminho,
        rotulo_completo=rotulo_completo_de(cadeia),
        vigencia_inicio_dia=dia(d.vigencia_inicio or norma.data_publicacao or _EPOCA),
        revogado_em_dia=dia_ou_sentinela(d.revogado_em),
        sha256_texto=hashlib.sha256(texto.encode("utf-8")).hexdigest(),
    )


class ChunkPorDispositivo(Estrategia):
    """Cada dispositivo com texto próprio vira um chunk.

    Recuperação mais precisa, mas depende de `expansao` para devolver o artigo inteiro ao
    modelo — inciso isolado costuma ser ininteligível.

    "Com texto próprio", não "folha": o artigo que tem incisos abaixo continua virando
    chunk pelo seu caput. Ver a decisão 1 no topo do módulo.
    """

    nome = "dispositivo"

    def chunks(self, disps: Sequence[Dispositivo], norma: Norma) -> Iterator[Chunk]:
        arv = Arvore(disps)
        for d in arv:
            if d.tipo in ESTRUTURAIS or not tem_conteudo(d):
                continue
            anc = arv.ancestrais(d)
            yield Chunk(
                id=id_ponto(d.id),
                texto=d.texto,
                texto_indexado=texto_contextualizado(d, anc, norma),
                payload=_payload(d, [*anc, d], norma, d.texto),
            )


class ChunkPorArtigo(Estrategia):
    """Artigo e seus filhos viram um chunk único.

    Autocontido, dispensa expansão; perde precisão em artigos longos, onde o inciso
    relevante fica diluído no vetor do artigo inteiro.

    **Perde vigência junto**, e é esse o custo que o eval da fase 6 deve medir. O chunk
    tem uma janela só — a do artigo — então ele só pode representar o artigo no FIM dessa
    janela. Filho revogado antes disso fica de fora: a consulta retroativa perde
    granularidade, mas texto revogado nunca é apresentado como vigente. O inverso
    (incluir tudo e deixar o filtro resolver) é impossível: o filtro age sobre o chunk
    inteiro, e o chunk não tem como ser meio vigente.
    """

    nome = "artigo"

    def chunks(self, disps: Sequence[Dispositivo], norma: Norma) -> Iterator[Chunk]:
        arv = Arvore(disps)
        for d in arv:
            if d.tipo is not TipoDispositivo.ARTIGO:
                continue
            filhos = [f for f in arv.descendentes(d) if tem_conteudo(f) and _sobrevive(f, d)]
            partes = [d, *filhos]
            if not any(tem_conteudo(p) for p in partes):
                continue
            texto = "\n".join(linha(p) for p in partes if tem_conteudo(p))
            anc = arv.ancestrais(d)
            trilha = texto_contextualizado(d, anc, norma).split("\n")[0]
            yield Chunk(
                id=id_ponto(d.id),
                texto=texto,
                texto_indexado=f"{trilha}\n{texto}",
                payload=_payload(d, [*anc, d], norma, texto),
            )


def _sobrevive(filho: Dispositivo, artigo: Dispositivo) -> bool:
    """O filho ainda vale no fim da janela de vigência do artigo?

    Não é teste de sobreposição: um artigo vigente hoje *se sobrepõe* a um inciso revogado
    em 2020, e incluí-lo colocaria texto revogado dentro de um chunk marcado como vigente.
    O que vale é o fim da janela — se o artigo segue vivo, só entra filho não revogado.
    """
    if filho.revogado_em is None:
        return True
    if artigo.revogado_em is None:
        return False
    return filho.revogado_em >= artigo.revogado_em


_ESTRATEGIAS: Final[dict[str, Estrategia]] = {
    e.nome: e for e in (ChunkPorDispositivo(), ChunkPorArtigo())
}


def obter_estrategia(nome: str) -> Estrategia:
    """Resolve a estratégia pelo nome vindo de `Settings`."""
    try:
        return _ESTRATEGIAS[nome]
    except KeyError:
        conhecidas = ", ".join(sorted(_ESTRATEGIAS))
        raise ValueError(f"estratégia de chunk desconhecida: {nome!r} (há: {conhecidas})") from None
