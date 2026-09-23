"""Montagem do prompt. Único lugar onde texto de instrução existe.

Instruções espalhadas pelo código impedem comparar duas rodadas de eval: `VERSAO_PROMPT`
entra em `Resposta` e no relatório justamente para tornar a comparação possível.

**Invariante desta fase:** `BlocoDocumento.texto` é byte a byte igual a `Trecho.texto`.
Rótulo, URL e vigência vão em `titulo` e `contexto`, nunca no corpo. As citations da
Messages API devolvem deslocamentos de caractere DENTRO do corpo que enviamos; decorar o
corpo desloca todos eles, e o efeito é cada citação apontar para o dispositivo errado —
silenciosamente. `citacoes.resolver` confere isso, e `tests/test_prompt.py` também.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Final

from ..models import Trecho
from .backend import BlocoDocumento, Pedido

VERSAO_PROMPT: Final = "v1"

#: Primeira linha da resposta quando os trechos não bastam. Marca literal, e não uma frase
#: livre, porque `citacoes.detectar_abstencao` precisa reconhecê-la sem heurística: a taxa
#: de abstenção é métrica do eval, e medi-la por adivinhação de linguagem natural mediria
#: o detector, não o sistema.
MARCA_ABSTENCAO: Final = "SEM FUNDAMENTO NOS TRECHOS"

#: Âncora que o backend sem citations nativas precisa emitir. `[D1]`, não uma referência
#: em prosa, porque `citacoes.extrair_ancoras` tem de achá-la por regex.
PREFIXO_REF: Final = "D"

#: Teto de saída. Com thinking adaptativo (padrão no Opus 5) os blocos de raciocínio
#: contam para este limite, e uma resposta jurídica truncada no meio de uma ressalva é
#: pior que o custo de alguns milhares de tokens.
MAX_TOKENS_PADRAO: Final = 8192


def sistema(data_referencia: date, *, exigir_ancoras: bool) -> str:
    """Prompt de sistema.

    Precisa impor três coisas: responder apenas a partir dos trechos fornecidos, abster-se
    explicitamente quando eles não bastarem, e tratar `data_referencia` como a data de
    vigência da consulta. `exigir_ancoras` liga a instrução de marcar as referências no
    texto, necessária apenas no backend sem citations nativas.

    A instrução sobre a data é mais sutil do que parece: os trechos JÁ vêm filtrados por
    vigência, então o modelo não deve ressalvar que "a lei pode ter mudado" — a ressalva
    correta já foi aplicada pela recuperação, e repeti-la faz a resposta parecer insegura
    quando ela é justamente o contrário.
    """
    regras = [
        "Você é um assistente de PESQUISA em legislação brasileira. Não presta consultoria "
        "jurídica: seu papel é localizar e explicar o que os dispositivos dizem.",
        "",
        "REGRAS, em ordem de prioridade:",
        "",
        "1. Responda EXCLUSIVAMENTE com base nos trechos de lei fornecidos abaixo. Você "
        "provavelmente conhece outros dispositivos de cor; não os use. Um dispositivo "
        "correto que não está nos trechos é, para esta resposta, uma invenção.",
        "",
        f"2. A data de referência da consulta é {data_referencia.isoformat()}. Os trechos "
        "já foram filtrados por vigência nessa data: o que está aqui vigorava, e o que "
        "havia sido revogado não está. Não acrescente ressalvas do tipo 'verifique se a "
        "lei mudou' — a verificação já foi feita. Se a pergunta for sobre outra data, diga "
        "que a consulta foi feita para esta.",
        "",
        f"3. Se os trechos não sustentarem a resposta, comece sua mensagem com a linha "
        f"exata:\n\n{MARCA_ABSTENCAO}\n\ne, na linha seguinte, diga em uma frase o que "
        "faltou. Vale também quando os trechos tratam de assunto próximo mas não do que "
        "foi perguntado. Admitir a lacuna é a resposta certa, não uma falha.",
        "",
        "4. Cite o dispositivo pelo rótulo ao afirmar cada coisa — 'art. 6º, VIII, do CDC' "
        "— para o leitor poder conferir na fonte oficial.",
        "",
        "5. Comece pela conclusão, em uma ou duas frases, e só então desdobre. Se os "
        "trechos se contradizem ou tratam de hipóteses diferentes (prazos distintos para "
        "situações distintas, por exemplo), diga isso em vez de escolher um.",
    ]
    if exigir_ancoras:
        regras += [
            "",
            f"6. Ao usar um trecho, marque-o com a referência dele entre colchetes ao fim "
            f"da frase: [{PREFIXO_REF}1]. Uma marca por afirmação, com a referência exata "
            "do trecho que a sustenta.",
        ]
    return "\n".join(regras)


def montar_documentos(trechos: Sequence[Trecho]) -> list[BlocoDocumento]:
    """Trechos recuperados -> blocos de documento, com refs estáveis D1..Dn.

    `contexto` carrega a procedência: a URL para conferir, o ID canônico e a afirmação de
    vigência. A documentação da API diz que `title` é curto e que `context` é o lugar de
    metadado — e nenhum dos dois é citável, que é exatamente o que se quer: o modelo lê a
    procedência, mas ela não pode aparecer dentro de uma citação como se fosse lei.
    """
    return [
        BlocoDocumento(
            ref=f"{PREFIXO_REF}{i}",
            titulo=t.rotulo_completo,
            contexto=f"fonte: {t.fonte_url} | id: {t.dispositivo_id}",
            texto=t.texto,
            dispositivo_id=t.dispositivo_id,
        )
        for i, t in enumerate(trechos, start=1)
    ]


def montar_pedido(
    pergunta: str,
    trechos: Sequence[Trecho],
    data_referencia: date,
    *,
    suporta_citacoes: bool,
    max_tokens: int = MAX_TOKENS_PADRAO,
) -> Pedido:
    """Monta o pedido completo."""
    return Pedido(
        sistema=sistema(data_referencia, exigir_ancoras=not suporta_citacoes),
        documentos=tuple(montar_documentos(trechos)),
        pergunta=pergunta,
        max_tokens=max_tokens,
    )


def render_conteudo(pedido: Pedido) -> str:
    """Trechos e pergunta em texto corrido, SEM o sistema.

    Para backend que aceita papel de sistema separado mas não blocos `document` — um modelo
    local via chat, por exemplo. O sistema vai no papel dele; misturá-lo ao conteúdo tira
    dele a precedência que o modelo dá às instruções de sistema.

    O delimitador é XML-ish porque é o que o modelo separa melhor de texto corrido, e
    porque texto de lei tem parênteses, aspas e travessões em abundância — qualquer
    delimitador leve colidiria com o próprio conteúdo.
    """
    partes = ["TRECHOS DE LEI:", ""]
    for d in pedido.documentos:
        partes += [
            f'<trecho ref="{d.ref}" titulo="{d.titulo}" {d.contexto}>',
            d.texto,
            "</trecho>",
            "",
        ]
    partes += ["PERGUNTA:", pedido.pergunta]
    return "\n".join(partes)


def render_texto_unico(pedido: Pedido) -> str:
    """Lineariza o pedido em uma string só, para o backend CLI, que não aceita blocos."""
    return f"{pedido.sistema}\n\n{render_conteudo(pedido)}"
