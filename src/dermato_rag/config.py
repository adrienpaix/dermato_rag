"""Configuration centralisée du système RAG."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration du système DermatoRAG."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # --- LLM ---
    google_api_key: str = Field(default="", description="Clé API Google (Gemini)")
    mistral_api_key: str = Field(default="", description="Clé API Mistral")
    cohere_api_key: str = Field(default="", description="Clé API Cohere (Rerank)")

    # Modèles
    primary_llm: str = Field(default="gemini-2.5-flash", description="LLM principal")
    fallback_llm: str = Field(default="mistral-large-latest", description="LLM fallback")
    embedding_model: str = Field(default="BAAI/bge-m3", description="Modèle d'embedding")

    # --- Chunking ---
    chunk_size: int = Field(default=1000, description="Taille des chunks en tokens")
    chunk_overlap: int = Field(default=100, description="Overlap entre chunks")

    # --- Retrieval ---
    top_k_retrieval: int = Field(default=20, description="Nombre de résultats retrieval")
    top_k_rerank: int = Field(default=5, description="Nombre de résultats après reranking")

    # --- Paths ---
    data_raw_dir: Path = Field(default=Path("data/raw"))
    data_processed_dir: Path = Field(default=Path("data/processed"))
    vector_db_dir: Path = Field(default=Path("chroma_db"))


settings = Settings()
