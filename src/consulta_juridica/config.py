"""Configuração do sistema, lida do ambiente e do .env.

Único lugar que conhece variáveis de ambiente. Nenhum outro módulo lê `os.environ`:
as dependências são construídas em `service.construir_servico` a partir daqui.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parâmetros de execução. Prefixo `CJ_` no ambiente; ver `.env.example`."""

    model_config = SettingsConfigDict(env_prefix="CJ_", env_file=".env", extra="ignore")

    # --- Índice vetorial ---
    qdrant_url: str = "http://localhost:6333"
    colecao: str = "dispositivos"

    # --- Caminhos ---
    dir_dados: Path = Path("data")
    caminho_sqlite: Path = Path("data/db/corpus.sqlite3")
    dir_cache_llm: Path = Path(".cache/llm")

    # --- Modelos locais ---
    modelo_denso: str = "BAAI/bge-m3"
    modelo_esparso: str = "Qdrant/bm25"
    modelo_rerank: str = "BAAI/bge-reranker-v2-m3"
    idioma_esparso: str = "portuguese"
    dim_densa: int = 1024

    # --- Recuperação ---
    # "dispositivo" (granularidade fina, depende de expansão) | "artigo" (autocontido).
    # Ver `ingest.chunking`: o nome é "dispositivo" e não "folha" porque o artigo que
    # tem incisos também vira chunk, pelo seu caput.
    estrategia_chunk: str = "dispositivo"
    # Até onde a expansão sobe: "artigo" | "nenhum" | "secao". Ver `retrieval.expansao`.
    nivel_expansao: str = "artigo"
    k_busca: int = 50
    k_prefetch: int = 150
    k_final: int = 8
    # Desligado por padrão. O cross-encoder custa ~11 s por consulta na CPU (fase 5) e,
    # com oito trechos indo ao modelo, quase não muda o que chega a ele (fase 6: recall@5
    # 0,970 sem, 0,879 com). O eval de recuperação passa explícito e mede os dois lados.
    usar_rerank: bool = False

    # --- Geração ---
    # "ollama" (local, sem chave — o padrão) | "api" (Claude com citations nativas, pede
    # ANTHROPIC_API_KEY) | "cli" (claude -p pela assinatura). Só "api" tem citations
    # nativas; os outros dois citam por âncora e o eval ponta a ponta os recusa.
    backend_llm: str = "ollama"
    # Só para o backend "api".
    modelo_llm: str = "claude-opus-5"
    usar_cache_llm: bool = True

    # --- Geração local (backend "ollama") ---
    ollama_url: str = "http://localhost:11434"
    modelo_local: str = "qwen3.5:4b"
    ollama_num_ctx: int = 16384
    ollama_keep_alive: str = "30m"
    # Ligado por padrão: desligado, o modelo de 4B afirmou texto posterior como vigente em
    # 2010 (medido). Desligar troca ~60 s por ~2 s de geração, e esse risco.
    ollama_pensar: bool = True

    @property
    def dir_raw(self) -> Path:
        return self.dir_dados / "raw"


def obter_settings() -> Settings:
    """Carrega as configurações. Chamado apenas na borda (API, CLIs)."""
    return Settings()
