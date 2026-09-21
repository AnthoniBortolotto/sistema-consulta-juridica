"""Tipos de domínio compartilhados.

Contrato central do sistema: ingestão, recuperação, geração e eval falam estes tipos.

Atenção a `ChunkPayload`: ele é fronteira de serialização com o Qdrant. Alterar um campo
obriga a reindexar todo o corpus, porque o payload já gravado não muda sozinho.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class TipoDispositivo(StrEnum):
    """Níveis da hierarquia de um texto legal, do maior para o menor."""

    PARTE = "parte"
    LIVRO = "livro"
    TITULO = "titulo"
    CAPITULO = "capitulo"
    SECAO = "secao"
    SUBSECAO = "subsecao"
    ARTIGO = "artigo"
    CAPUT = "caput"
    PARAGRAFO = "paragrafo"
    INCISO = "inciso"
    ALINEA = "alinea"
    ITEM = "item"


class Norma(BaseModel):
    """Uma norma inteira (lei, código, constituição) tal como publicada."""

    urn: str  # urn:lex:br:federal:lei:1990-09-11;8078
    tipo: str
    numero: str | None = None
    ano: int
    data_publicacao: date | None = None
    apelido: str | None = None  # "CDC", "CF/88"
    ementa: str | None = None
    fonte_url: str
    sha256_origem: str  # hash do documento baixado; detecta mudança na fonte


class Dispositivo(BaseModel):
    """Um nó da árvore da norma: artigo, parágrafo, inciso, alínea...

    `texto` é o texto PRÓPRIO do nó, sem o dos filhos. A concatenação é feita quando
    necessária, para que a granularidade continue sendo decisão de chunking.
    """

    id: str  # urn com fragmento: ...;8078!art6_inc8
    norma_urn: str
    parent_id: str | None = None
    tipo: TipoDispositivo
    rotulo: str  # "Art. 6º", "§ 1º", "VIII"
    ordem: int  # posição entre irmãos
    caminho: str  # materialized path: "tit3/cap1/art6/inc8"
    texto: str
    vigencia_inicio: date | None = None
    revogado_em: date | None = None
    nota_alteracao: str | None = None  # "Redação dada pela Lei 9.870/1999"


def ordem_documento(disps: Sequence[Dispositivo]) -> list[Dispositivo]:
    """Ordena em ordem de leitura do texto legal.

    Não dá para ordenar por `caminho`: lexicograficamente "inc10" vem antes de "inc2", e o
    artigo chegaria ao modelo com os incisos embaralhados. A ordem correta é uma busca em
    profundidade seguindo `ordem`, que é a posição entre irmãos.

    Vive aqui, e não em `store.queries`, porque `ingest.chunking` precisa da mesma ordem
    sem abrir banco: duas implementações divergiriam e o sintoma seria texto legal fora de
    sequência dentro do prompt.

    Um nó cujo pai não está na entrada (pai filtrado por vigência, filho não) vira raiz em
    vez de sumir: no pior caso a ordem fica aproximada, mas nenhum texto é descartado.
    """
    presentes = {d.id for d in disps}
    filhos: dict[str | None, list[Dispositivo]] = {}
    for d in disps:
        chave = d.parent_id if d.parent_id in presentes else None
        filhos.setdefault(chave, []).append(d)
    for lista in filhos.values():
        lista.sort(key=lambda d: (d.ordem, d.caminho))

    saida: list[Dispositivo] = []
    pilha = list(reversed(filhos.get(None, [])))
    while pilha:
        atual = pilha.pop()
        saida.append(atual)
        pilha.extend(reversed(filhos.get(atual.id, [])))
    return saida


class Remissao(BaseModel):
    """Referência cruzada de um dispositivo para outro ("na forma do art. 37, §6º")."""

    origem_id: str
    destino_urn: str
    destino_id: str | None = None
    texto_original: str
    resolvida: bool = False


class ChunkPayload(BaseModel):
    """Payload gravado no Qdrant. Os nomes dos campos vivem em `vectorstore.Campo`.

    Datas são gravadas como `date.toordinal()` porque o filtro de range do Qdrant exclui
    pontos sem o campo — um dispositivo vigente (`revogado_em = None`) desapareceria do
    resultado. Ver `vectorstore.SENTINELA_VIGENTE`.
    """

    dispositivo_id: str
    norma_urn: str
    norma_apelido: str | None = None
    tipo: TipoDispositivo
    caminho: str
    rotulo_completo: str  # "Lei 8.078/1990, Art. 6º, VIII"
    vigencia_inicio_dia: int
    revogado_em_dia: int  # SENTINELA_VIGENTE quando não revogado
    sha256_texto: str


class Chunk(BaseModel):
    """Unidade de indexação, produzida por `ingest.chunking`.

    Os dois textos são deliberadamente distintos: `texto_indexado` carrega o contexto
    hierárquico para melhorar a recuperação, `texto` é o que pode ser citado literalmente.
    """

    id: UUID  # uuid5 determinístico — ver `vectorstore.id_ponto`
    texto: str
    texto_indexado: str
    payload: ChunkPayload


class Trecho(BaseModel):
    """Unidade pós-recuperação, já expandida (inciso -> artigo) e pronta para o modelo."""

    dispositivo_id: str
    norma_urn: str
    rotulo_completo: str
    texto: str
    fonte_url: str
    score: float
    score_fusao: float | None = None
    score_rerank: float | None = None


class Citacao(BaseModel):
    """Uma citação da resposta, resolvida de volta ao dispositivo de origem."""

    dispositivo_id: str
    rotulo_completo: str
    texto_citado: str
    fonte_url: str
    inicio_char: int | None = None
    fim_char: int | None = None


class Uso(BaseModel):
    """Consumo de tokens de uma chamada ao modelo."""

    tokens_entrada: int = 0
    tokens_saida: int = 0
    tokens_cache_leitura: int = 0


class MotivoAbstencao(StrEnum):
    """Por que o sistema não respondeu.

    Os dois caminhos são distintos de propósito: `SEM_CANDIDATOS` acontece antes de
    qualquer chamada ao modelo e não custa nada.
    """

    SEM_CANDIDATOS = "sem_candidatos"
    CONTEXTO_INSUFICIENTE = "contexto_insuficiente"


class Resposta(BaseModel):
    """Saída completa de uma consulta.

    `versao_prompt` e `modelo` entram aqui para que duas rodadas de eval sejam comparáveis.
    """

    texto: str
    citacoes: list[Citacao] = Field(default_factory=list)
    trechos: list[Trecho] = Field(default_factory=list)
    abstencao: MotivoAbstencao | None = None
    data_referencia: date
    modelo: str
    versao_prompt: str
    uso: Uso = Field(default_factory=Uso)
