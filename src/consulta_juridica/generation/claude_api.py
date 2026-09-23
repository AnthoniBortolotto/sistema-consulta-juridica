"""Backend via Messages API, com citations nativas. Caminho de eval e de demonstração."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from ..models import Uso
from .backend import CitacaoBruta, LLMBackend, Pedido, ResultadoGeracao

if TYPE_CHECKING:
    from anthropic import Anthropic


def bloco_documento(d) -> dict[str, Any]:
    """`BlocoDocumento` -> bloco `document` da Messages API, com citations ligadas.

    `source.type = "text"` e não conteúdo customizado: com texto puro, a API divide o
    documento em sentenças e devolve `char_location`, o que permite citar UMA frase dentro
    de um artigo de oito mil caracteres. Com blocos customizados a granularidade seria o
    dispositivo inteiro — mais simples de mapear, e inútil para o leitor que precisa saber
    qual parte do caput sustenta a afirmação.

    O preço dessa escolha é ter de traduzir deslocamento de caractere de volta para
    dispositivo, o que `citacoes.resolver` faz usando as linhas montadas por
    `retrieval.expansao`. Por isso `texto` vai cru: ver a invariante em `prompt`.
    """
    return {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": d.texto},
        "title": d.titulo,
        "context": d.contexto,
        # Precisa estar em TODOS os documentos ou em nenhum — a API rejeita a mistura.
        "citations": {"enabled": True},
    }


class BackendMessagesAPI(LLMBackend):
    """Envia cada trecho como bloco `document` com citations habilitadas.

    É o diferencial do projeto: a resposta volta particionada em blocos de texto, e os
    blocos citados carregam o texto citado e a localização no documento de origem — citação
    verificada pela API, não pedida por prompt e torcida para que o modelo obedeça.

    Três decisões de chamada, todas conferidas contra a documentação e não escritas de
    memória:

    - **Nada de `output_config.format`.** Citations e structured outputs são incompatíveis
      e a API devolve 400. A resposta é texto, e a estrutura vem das citations.
    - **Thinking fica no padrão.** No Opus 5 omitir o parâmetro já roda thinking adaptativo;
      `budget_tokens` foi removido e agora dá 400. Não pedimos o resumo do raciocínio
      porque nada o consome.
    - **Sem `fallbacks` de recusa.** Trocar de modelo no meio de uma recusa deixaria
      `Resposta.modelo` mentindo sobre quem respondeu, e duas rodadas de eval deixariam de
      ser comparáveis. Uma recusa aqui é sinal de defeito e tem de aparecer, não de ser
      contornada — vira `RecusaDoModelo`.
    """

    nome = "messages-api"
    suporta_citacoes = True

    def __init__(self, modelo: str, client: Anthropic | None = None) -> None:
        if client is None:
            from anthropic import Anthropic as _Anthropic

            client = _Anthropic()
        self._client = client
        self.modelo = modelo
        # Sobrepõe o atributo de classe: o relatório de eval e o diretório de cache
        # precisam distinguir dois modelos por trás do mesmo backend.
        self.nome = f"messages-api:{modelo}"

    def gerar(self, pedido: Pedido) -> ResultadoGeracao:
        from ..errors import RecusaDoModelo

        with _traduzir_erros():
            resposta = self._client.messages.create(
                model=self.modelo,
                max_tokens=pedido.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": pedido.sistema,
                        # O sistema é idêntico entre consultas da mesma data; os documentos
                        # mudam a cada pergunta e ficam DEPOIS do ponto de corte.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            *(bloco_documento(d) for d in pedido.documentos),
                            {"type": "text", "text": pedido.pergunta},
                        ],
                    }
                ],
            )

        # Checar ANTES de ler o conteúdo: numa recusa o `content` pode não trazer o que se
        # espera, e `stop_details` só é preenchido neste caso.
        if resposta.stop_reason == "refusal":
            detalhes = getattr(resposta, "stop_details", None)
            raise RecusaDoModelo(
                f"o modelo recusou a requisição (categoria: "
                f"{getattr(detalhes, 'category', None)!r})"
            )

        return ResultadoGeracao(
            texto="".join(b.text for b in resposta.content if b.type == "text"),
            citacoes=list(_citacoes(resposta, pedido)),
            modelo=resposta.model,
            uso=_uso(resposta),
        )


@contextmanager
def _traduzir_erros():
    """Erro do SDK -> erro de domínio, e só os que NÃO são defeito nosso.

    Falta de chave, limite de taxa e queda de rede são indisponibilidade: o serviço não
    pôde chamar o modelo, e a API HTTP responde 503. Já um 400 é requisição malformada —
    defeito deste código, e tem de estourar com o traceback inteiro em vez de virar uma
    mensagem educada que ninguém investiga.
    """
    from anthropic import (
        APIConnectionError,
        AuthenticationError,
        PermissionDeniedError,
        RateLimitError,
    )

    from ..errors import BackendIndisponivel

    try:
        yield
    except TypeError as e:
        # Sem credencial NENHUMA o SDK nem chega a enviar: valida os cabeçalhos e levanta
        # TypeError. É o erro mais provável de quem acabou de clonar o repositório, então
        # merece uma mensagem que diga o que fazer.
        #
        # Checar a chave antes de chamar seria mais limpo e estaria errado: o SDK também
        # aceita perfil OAuth e federação de identidade, em que `api_key` é None e as
        # requisições funcionam. Quem decide se há credencial é o SDK; aqui só se traduz
        # o veredito. Se a mensagem mudar, volta a ser um 500 — que é o que já era.
        if "authentication" not in str(e).lower():
            raise
        raise BackendIndisponivel(
            "nenhuma credencial da Anthropic encontrada: defina ANTHROPIC_API_KEY (ou use "
            "CJ_BACKEND_LLM=ollama, local e sem custo, mas sem citations nativas)"
        ) from e
    except AuthenticationError as e:
        raise BackendIndisponivel(
            "a Anthropic recusou a credencial: defina ANTHROPIC_API_KEY (ou use "
            "CJ_BACKEND_LLM=ollama, local e sem custo, mas sem citations nativas)"
        ) from e
    except PermissionDeniedError as e:
        raise BackendIndisponivel(f"credencial sem permissão para {e.__class__.__name__}") from e
    except RateLimitError as e:
        raise BackendIndisponivel("limite de taxa da Anthropic atingido") from e
    except APIConnectionError as e:
        raise BackendIndisponivel(f"falha de rede ao chamar a Anthropic: {e}") from e


def _citacoes(resposta, pedido: Pedido):
    """Blocos de texto citados -> `CitacaoBruta`.

    A resposta vem particionada: cada bloco de texto é uma afirmação, e os que se apoiam em
    documento carregam `citations`. `document_index` é 0-indexed sobre TODOS os blocos
    `document` da requisição — e os nossos são os únicos, na ordem em que os montamos.
    """
    for bloco in resposta.content:
        if bloco.type != "text":
            continue
        for c in getattr(bloco, "citations", None) or []:
            if not 0 <= c.document_index < len(pedido.documentos):
                continue
            yield CitacaoBruta(
                ref_documento=pedido.documentos[c.document_index].ref,
                texto_citado=c.cited_text,
                # `char_location` para documento de texto puro. Outro tipo de location
                # (página de PDF, bloco customizado) não tem deslocamento de caractere —
                # não enviamos nenhum dos dois, mas adivinhar o campo seria pior.
                inicio_char=getattr(c, "start_char_index", None),
                fim_char=getattr(c, "end_char_index", None),
            )


def _uso(resposta) -> Uso:
    u = resposta.usage
    return Uso(
        tokens_entrada=u.input_tokens,
        tokens_saida=u.output_tokens,
        tokens_cache_leitura=getattr(u, "cache_read_input_tokens", 0) or 0,
    )
