"""Backend via Ollama local: geração sem chave de API e sem custo.

O Ollama roda como serviço do `docker-compose.yml`, com a GPU passada ao container. Este
módulo só fala HTTP com ele, com o `httpx` que o projeto já tem — nada de runtime de modelo
dentro do processo Python, que obrigaria a trocar o torch do projeto pela build CUDA.

Mesmo lugar no sistema que `claude_cli`: **sem citations nativas**. As citações vêm das
âncoras `[D1]` que o prompt pede, apontam para o trecho e não para um intervalo, e o eval
ponta a ponta recusa este backend. Serve para ver o sistema responder de graça, não para
medir citação.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ..models import Uso
from .backend import LLMBackend, Pedido, ResultadoGeracao
from .citacoes import extrair_ancoras
from .prompt import render_conteudo

if TYPE_CHECKING:
    import httpx

#: Contexto pedido ao Ollama. Obrigatório, não afinação: o padrão dele nesta máquina é 4096
#: (lido do log de inicialização), os pedidos medidos têm ~3100 tokens com oito trechos, e
#: quando o prompt passa do limite o Ollama corta o COMEÇO em silêncio — que é onde está o
#: sistema, com a instrução de só usar os trechos e de se abster.
NUM_CTX_PADRAO: Final = 16384

#: Quanto o modelo fica na GPU depois da última chamada. O padrão do Ollama é 5 min, e cada
#: recarga custa ~10 s (medido, volume do Docker); numa demonstração com perguntas
#: espaçadas, isso é pago quase toda vez.
KEEP_ALIVE_PADRAO: Final = "30m"


class BackendOllama(LLMBackend):
    """Qwen (ou qualquer modelo do Ollama) por `/api/chat`, síncrono.

    Três decisões de chamada, todas medidas contra o serviço rodando:

    - **Sistema no papel de sistema**, e trechos + pergunta no do usuário. Misturar os dois
      numa string só tira do modelo a precedência que ele dá à instrução de sistema.
    - **Raciocínio LIGADO por padrão**, e a escolha contrária foi refutada por medição. Com
      `think: false` a resposta sai em ~2 s, mas o qwen3.5:4b, perguntado se o transporte
      era direito social em 2010, recebeu a redação do art. 6º daquele ano — sem a palavra
      "transporte" — e afirmou, citando-a, que ela "lista explicitamente o transporte". É o
      pior defeito deste domínio: texto posterior apresentado como o vigente na data. Com
      raciocínio, acertou, e passou a usar a marca literal de abstenção. Custa 40 a 75 s
      por resposta em vez de 2 s. `pensar=False` fica disponível para quem aceita o risco.
    - **Temperatura 0.** A mesma pergunta com o mesmo contexto tem de dar a mesma resposta:
      é o que torna o cache honesto e uma regressão reproduzível.
    """

    nome = "ollama"
    suporta_citacoes = False

    def __init__(
        self,
        modelo: str,
        *,
        url: str = "http://localhost:11434",
        num_ctx: int = NUM_CTX_PADRAO,
        keep_alive: str = KEEP_ALIVE_PADRAO,
        pensar: bool = True,
        timeout_s: float = 300,
        client: httpx.Client | None = None,
    ) -> None:
        if client is None:
            import httpx

            # Timeout largo de propósito: a primeira chamada depois de o container subir
            # carrega o modelo do disco da VM (medido, 76 s) e o raciocínio leva até ~75 s.
            client = httpx.Client(base_url=url, timeout=timeout_s)
        self._client = client
        self.modelo = modelo
        self.url = url
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.pensar = pensar
        # Sobrepõe o atributo de classe: o diretório de cache é por `nome`, então tudo que
        # muda a resposta tem de estar aqui. O raciocínio muda — medido, é a diferença entre
        # acertar e errar `vig-02` —, e sem ele no nome, quem ligasse o raciocínio depois de
        # rodar sem receberia do cache a resposta errada do modo rápido, sem aviso.
        self.nome = f"ollama:{modelo}" + ("" if pensar else ":sem-raciocinio")

    def gerar(self, pedido: Pedido) -> ResultadoGeracao:
        import httpx

        from ..errors import BackendIndisponivel

        try:
            r = self._client.post(
                "/api/chat",
                json={
                    "model": self.modelo,
                    "stream": False,
                    # O raciocínio volta em `message.thinking`, separado: não vaza para a
                    # resposta, e conta em `num_predict` — medido, até ~4700 tokens.
                    "think": self.pensar,
                    "keep_alive": self.keep_alive,
                    "options": {
                        "num_ctx": self.num_ctx,
                        "temperature": 0,
                        "num_predict": pedido.max_tokens,
                    },
                    "messages": [
                        {"role": "system", "content": pedido.sistema},
                        {"role": "user", "content": render_conteudo(pedido)},
                    ],
                },
            )
        except httpx.ConnectError as e:
            raise BackendIndisponivel(
                f"Ollama fora do ar em {self.url}: `docker compose up -d ollama`"
            ) from e
        except httpx.TimeoutException as e:
            raise BackendIndisponivel(f"Ollama não respondeu a tempo em {self.url}") from e

        if r.status_code == 404:
            raise BackendIndisponivel(
                f"modelo {self.modelo!r} não baixado: "
                f"`docker compose exec ollama ollama pull {self.modelo}`"
            )
        if r.status_code >= 400:
            raise BackendIndisponivel(f"Ollama respondeu {r.status_code}: {r.text[:300]}")

        corpo = r.json()
        lidos = int(corpo.get("prompt_eval_count") or 0)
        # O Ollama NÃO avisa quando corta: devolve `prompt_eval_count` igual ao contexto e
        # gera assim mesmo, sem o sistema. Resposta gerada sem a instrução de abstenção é
        # pior que resposta nenhuma — então vira erro, com o que fazer.
        if lidos >= self.num_ctx:
            raise BackendIndisponivel(
                f"o pedido encheu o contexto de {self.num_ctx} tokens e o Ollama cortou o "
                f"começo, onde fica a instrução de sistema: aumente CJ_OLLAMA_NUM_CTX"
            )

        texto = (corpo.get("message") or {}).get("content", "").strip()
        return ResultadoGeracao(
            texto=texto,
            citacoes=extrair_ancoras(texto),
            modelo=self.nome,
            uso=Uso(tokens_entrada=lidos, tokens_saida=int(corpo.get("eval_count") or 0)),
        )
