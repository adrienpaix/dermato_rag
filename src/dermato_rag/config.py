"""Configuration centralisée du système DermatoRAG.

Patch d'audit (avr 2026) : aligné sur le déploiement VPS (`/opt/dermato_rag/.env`).
La classe `Config` (et le singleton `config`) reproduit l'API utilisée par les
modules ingestion / embeddings / retrieval / generation / api du serveur en
production. La classe `Settings` historique (pydantic-settings) est conservée
pour compat ascendante.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Charge un .env s'il existe (cherche d'abord à côté du dépôt, puis /opt/dermato_rag).
_LOCAL_ENV = Path(__file__).resolve().parents[3] / ".env"
_PROD_ENV = Path("/opt/dermato_rag/.env")
for _candidate in (_LOCAL_ENV, _PROD_ENV):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        break


class Config:
    """Config "à plat" — celle utilisée par les modules runtime.

    Toutes les valeurs sont lues une seule fois à l'import. Pour les changer
    en production, modifier `/opt/dermato_rag/.env` puis redémarrer uvicorn.
    """

    # --- Clés API ---
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    COHERE_API_KEY: str = os.getenv("COHERE_API_KEY", "")
    MISTRAL_API_KEY: str = os.getenv("MISTRAL_API_KEY", "")

    # --- Modèles ---
    # IMPORTANT : ne PAS revenir à BioBERT / MiniLM / bge-small. Voir l'audit.
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    PRIMARY_LLM: str = os.getenv("PRIMARY_LLM", "gemini-2.5-flash")
    FALLBACK_LLM: str = os.getenv("FALLBACK_LLM", "mistral-large-latest")
    RERANKER_MODEL: str = os.getenv("RERANKER_MODEL", "rerank-multilingual-v3.0")

    # --- Qdrant ---
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6335")
    COLLECTION_NAME: str = os.getenv("COLLECTION_NAME", "dermato_rag_v3")

    # --- Chunking ---
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "1000"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "150"))

    # --- Retrieval / reranking ---
    # top_k_retrieval augmenté à 30 pour les questions thérapeutiques (cf. audit).
    TOP_K_RETRIEVAL: int = int(os.getenv("TOP_K_RETRIEVAL", "30"))
    TOP_K_RERANK: int = int(os.getenv("TOP_K_RERANK", "10"))
    USE_RERANKER: bool = os.getenv("USE_RERANKER", "true").lower() in ("1", "true", "yes")

    # --- Paths ---
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/root/dermato_upload/Onco"))


# Singleton importé partout dans le projet
config = Config()


# ---------------------------------------------------------------------------
# Compat ascendante : la classe Settings historique (pydantic-settings).
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Configuration historique (pydantic-settings) — conservée pour compat."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    google_api_key: str = Field(default="", description="Clé API Google (Gemini)")
    mistral_api_key: str = Field(default="", description="Clé API Mistral")
    cohere_api_key: str = Field(default="", description="Clé API Cohere (Rerank)")

    primary_llm: str = Field(default="gemini-2.5-flash", description="LLM principal")
    fallback_llm: str = Field(default="mistral-large-latest", description="LLM fallback")
    embedding_model: str = Field(default="BAAI/bge-m3", description="Modèle d'embedding")

    chunk_size: int = Field(default=1000)
    chunk_overlap: int = Field(default=150)

    top_k_retrieval: int = Field(default=30)
    top_k_rerank: int = Field(default=10)

    data_raw_dir: Path = Field(default=Path("data/raw"))
    data_processed_dir: Path = Field(default=Path("data/processed"))
    vector_db_dir: Path = Field(default=Path("chroma_db"))


settings = Settings()
