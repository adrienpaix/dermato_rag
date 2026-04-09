# DermatoRAG

Système RAG (Retrieval-Augmented Generation) spécialisé en dermatologie pour la synthèse de cours médicaux de niveau médecin diplômé.

## Stack technique

| Composant | Technologie |
|---|---|
| LLM principal | Gemini 2.5 Flash |
| LLM fallback | Mistral Large 3 |
| Embeddings | BGE-M3 (self-hosted) |
| Reranker | Cohere Rerank v3.5 |
| Vector DB | Chroma (proto) → Qdrant (prod) |
| Framework | LangChain |

## Installation

```bash
# Cloner le repo
git clone https://github.com/adrienpaix/dermato_rag.git
cd dermato_rag

# Créer l'environnement virtuel
python -m venv .venv
source .venv/bin/activate

# Installer les dépendances
pip install -e ".[dev]"

# Configurer les clés API
cp .env.example .env
# Éditer .env avec vos clés
```

## Structure du projet

```
src/dermato_rag/
├── config.py              # Configuration centralisée
├── ingestion/             # Extraction et chunking des documents
├── embeddings/            # Gestion des embeddings BGE-M3
├── retrieval/             # Recherche hybride + reranking
├── generation/            # Génération LLM + prompts
└── api/                   # API REST FastAPI
```

## Documents sources

Placer les documents dans `data/raw/` :
- PDF (ebooks, articles, guidelines)
- EPUB (ebooks)
- DOCX (documents Word)
- Markdown

## Documentation

Voir [docs/PLAN_DEVELOPPEMENT.md](docs/PLAN_DEVELOPPEMENT.md) pour le plan complet, le benchmark des modèles et l'architecture détaillée.
