"""Confiança TLS ancorada no sistema operacional.

Existe porque duas partes do projeto baixam coisas da internet e as duas quebravam pelo
mesmo motivo, em lugares diferentes:

- `ingest.fontes` busca o HTML do Planalto;
- `sentence-transformers` e `fastembed` buscam os modelos no huggingface.co.

O `certifi` — bundle que o `httpx` e o `requests` usam por padrão — não fecha a cadeia em
máquina com inspeção de TLS (antivírus ou proxy corporativo que reemite os certificados).
O navegador e o `curl` funcionam porque usam o truststore do SO, onde a CA da inspeção foi
instalada. Sintoma: `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`.

**Desligar a verificação resolveria em uma linha e está fora de questão.** O `sha256` de
procedência que `fontes.salvar` grava só significa alguma coisa se os bytes vieram
comprovadamente do Planalto; sem verificar o certificado, ele atesta a integridade de um
download que qualquer intermediário poderia ter trocado.

São duas ferramentas porque os dois casos são diferentes:

- `contexto()` para o código que controlamos: explícito, sem efeito global, testável.
- `confiar_no_sistema()` para biblioteca de terceiro que fixa o `certifi` por dentro e não
  aceita um contexto nosso. Tem efeito global, então só é chamada na BORDA (CLI e API),
  nunca na importação de um módulo de domínio.
"""

from __future__ import annotations

import ssl


def contexto() -> ssl.SSLContext:
    """Contexto TLS com as raízes do SO, para passar a um cliente HTTP explicitamente."""
    return ssl.create_default_context()


def confiar_no_sistema() -> bool:
    """Faz TODA verificação TLS do processo usar o truststore do SO. Devolve se aplicou.

    Chamada uma vez, na borda. O `truststore` substitui `ssl.SSLContext`, então bibliotecas
    que constroem o próprio contexto a partir do `certifi` — o `huggingface_hub` faz
    exatamente isso — passam a enxergar as raízes do sistema.

    Nunca levanta: numa máquina sem inspeção de TLS o `certifi` já funciona, e derrubar a
    ingestão porque um pacote opcional falhou seria pior que seguir com o padrão.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except Exception:  # noqa: BLE001 - qualquer falha aqui é degradação, não erro
        return False
