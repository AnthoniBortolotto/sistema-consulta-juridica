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
    estrategia_chunk: str = "folha"
    k_busca: int = 50
    k_prefetch: int = 150
    k_final: int = 8

    # --- Geração ---
    modelo_llm: str = "claude-opus-5"
    backend_llm: str = "api"  # "api" (citations nativas) | "cli" (assinatura, sem citations)
    usar_cache_llm: bool = True

    @property
    def dir_raw(self) -> Path:
        return self.dir_dados / "raw"


def obter_settings() -> Settings:
    """Carrega as configurações. Chamado apenas na borda (API, CLIs)."""
    return Settings()
