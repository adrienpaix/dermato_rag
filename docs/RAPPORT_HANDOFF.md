# RAPPORT PROJET DERMATO_RAG
## Handoff pour agent IA - État au 9 avril 2026

---

## 1. CONTEXTE DU PROJET

**Objectif** : Construire un système RAG (Retrieval-Augmented Generation) spécialisé en dermatologie capable d'ingérer des sources documentaires variées (ebooks PDF, articles scientifiques, guidelines cliniques) et de les synthétiser en cours structurés de dermatologie de niveau médecin diplômé. Le système doit fonctionner en français et anglais.

**Repo GitHub** : `adrienpaix/dermato_rag`
**Branche de travail** : `claude/dermatology-rag-system-XASYf`
**État** : 1 commit initial (structure + plan). Aucun code fonctionnel encore.

---

## 2. DÉCISIONS TECHNIQUES PRISES (Benchmark Avril 2026)

Un benchmark complet a été réalisé (voir `docs/PLAN_DEVELOPPEMENT.md` pour tous les détails et sources).

### Stack retenue

| Composant | Choix | Justification | Coût |
|---|---|---|---|
| **LLM principal** | **Gemini 2.5 Flash** | Meilleur ratio coût/qualité, 1M context, bon français | $0.15 / $0.60 par 1M tokens |
| **LLM fallback** | **Mistral Large 3** | Entreprise FR, excellent français natif, 262K context | $0.50 / $1.50 par 1M tokens |
| **Embeddings** | **BGE-M3** (BAAI/bge-m3, self-hosted) | Gratuit, 100+ langues, dense+sparse, MTEB 63+ | Gratuit |
| **Reranker** | **Cohere Rerank v3.5** | Simple API, multilingue, abordable | $2.00 / 1000 recherches |
| **Vector DB** | **Chroma** (proto) → **Qdrant** (prod) | Chroma zero-config, Qdrant performant en prod | Gratuit (self-hosted) |
| **Framework** | **LangChain >= 0.3** | Écosystème mature, intégrations nombreuses | Gratuit |
| **Chunking** | Sémantique adaptatif | Aligné par section/sujet médical, 500-1500 tokens | - |
| **Query transform** | **HyDE** (Hypothetical Document Embeddings) | Résout le gap vocabulaire patient vs médical | - |

**Coût mensuel estimé : ~$3-5/mois**

### Modèles alternatifs considérés mais non retenus

- **Claude Sonnet 4.6** ($3/$15) : Top médical (91-94% MedQA) mais trop cher pour usage courant
- **GPT-4.1** ($2/$8) : Bon mais 4x plus cher que Gemini Flash en output
- **Mistral Small 3.1** ($0.03/$0.11) : Ultra low-cost mais pas assez de benchmarks médicaux publiés
- **Qwen 3.5-9B** : Meilleur open-source FR médical mais nécessite self-hosting GPU
- **MedGemma 1.5** : Spécialiste dermatologie (210 conditions, 51K images) - à considérer comme module complémentaire pour l'analyse d'images

---

## 3. ARCHITECTURE RAG

### Pipeline d'ingestion
```
Documents sources (PDF dans data/raw/)
    → Extraction texte + métadonnées (PyMuPDF)
    → Nettoyage & normalisation (Unicode, détection langue)
    → Chunking sémantique adaptatif (500-1500 tokens, overlap 100)
        - Ebooks : hiérarchique par chapitre/section
        - Articles : par section (Abstract, Méthodes, Résultats, Discussion)
        - Guidelines : par recommandation/algorithme décisionnel
    → Embedding BGE-M3 (dense 1024d + sparse)
    → Stockage Chroma/Qdrant avec métadonnées (source, chapitre, langue, type)
```

### Pipeline de requête (Advanced RAG)
```
Question utilisateur
    → Détection de langue (langdetect)
    → Transformation HyDE (LLM génère réponse hypothétique → embedding)
    → Recherche hybride dense+sparse (top-K=20, filtrage par langue)
    → Reranking Cohere v3.5 (top-20 → top-5)
    → Génération Gemini 2.5 Flash avec contexte + citations
        (fallback Mistral Large 3 pour cas complexes)
```

---

## 4. STRUCTURE DU PROJET (EXISTANTE)

```
dermato_rag/
├── docs/PLAN_DEVELOPPEMENT.md     # Plan complet avec benchmark détaillé
├── src/
│   ├── dermato_rag/
│   │   ├── __init__.py            # Version 0.1.0
│   │   ├── config.py              # Configuration Pydantic (clés API, params RAG)
│   │   ├── ingestion/__init__.py  # VIDE - à implémenter
│   │   ├── embeddings/__init__.py # VIDE - à implémenter
│   │   ├── retrieval/__init__.py  # VIDE - à implémenter
│   │   ├── generation/__init__.py # VIDE - à implémenter
│   │   └── api/__init__.py        # VIDE - à implémenter
│   └── tests/__init__.py          # VIDE - à implémenter
├── data/
│   ├── raw/                       # Documents PDF sources (gitignored, uploadés sur VPS)
│   └── processed/                 # Données traitées (gitignored)
├── notebooks/                     # Notebooks d'exploration
├── .env.example                   # Template des variables d'env
├── .gitignore
├── pyproject.toml                 # Dépendances Python définies
└── README.md
```

### Configuration existante (`config.py`)
- Utilise `pydantic-settings` avec fichier `.env`
- Clés API : `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `COHERE_API_KEY`
- Params RAG : chunk_size=1000, chunk_overlap=100, top_k_retrieval=20, top_k_rerank=5
- Modèles : primary=gemini-2.5-flash, fallback=mistral-large-latest, embedding=BAAI/bge-m3

### Dépendances (`pyproject.toml`)
- Python >=3.11
- LangChain >=0.3 + langchain-google-genai, langchain-mistralai, langchain-cohere
- FlagEmbedding, sentence-transformers (pour BGE-M3)
- chromadb, qdrant-client
- pymupdf, ebooklib, beautifulsoup4, python-docx (extraction documents)
- fastapi, uvicorn (API)
- python-dotenv, langdetect, pydantic>=2.0
- Dev : pytest, pytest-asyncio, ruff, ipykernel

**ATTENTION** : `pydantic-settings` est utilisé dans config.py mais n'est pas dans les dépendances pyproject.toml → à ajouter.

---

## 5. DOCUMENTS SOURCES

Les PDFs de dermatologie (sous-domaine oncologie/onco-dermatologie) sont en cours de transfert sur le VPS du propriétaire. Ils ne sont PAS dans le conteneur d'exécution Claude Code.

**Problème d'accès** : L'environnement Claude Code tourne dans une VM isolée sur le VPS (IP interne 192.0.2.2). Le filesystem n'est pas partagé avec le VPS hôte (37.27.90.184). Les fichiers uploadés via SCP sur le VPS ne sont pas directement accessibles depuis le conteneur.

**Solutions possibles pour l'agent suivant** :
1. L'utilisateur uploade les PDFs via l'interface web Claude Code (drag & drop si disponible)
2. L'utilisateur met les PDFs sur un service de partage (Google Drive, OneDrive) et fournit un lien public
3. L'utilisateur utilise `transfer.sh` depuis son PC et fournit les URLs de téléchargement
4. Si l'agent suivant tourne directement sur le VPS (pas dans un conteneur), les fichiers seront dans `/root/dermato_upload/Onco/`

---

## 6. PROCHAINES ÉTAPES (Phase 1 - Prototype)

### Étape 1 : Résoudre l'accès aux documents
- Obtenir les PDFs dans `data/raw/`

### Étape 2 : Setup environnement
- `pip install -e ".[dev]"`
- Ajouter `pydantic-settings` aux dépendances
- Créer `.env` avec les clés API (demander à l'utilisateur)

### Étape 3 : Pipeline d'ingestion (`src/dermato_rag/ingestion/`)
- `extractors.py` : extraction texte PDF avec PyMuPDF, conservation structure (titres, sections)
- `chunkers.py` : chunking sémantique adaptatif (500-1500 tokens), détection de sections
- `metadata.py` : extraction métadonnées (titre, auteur, langue, type de document)

### Étape 4 : Embeddings (`src/dermato_rag/embeddings/`)
- `bge_m3.py` : wrapper BGE-M3 pour embeddings dense+sparse
- Intégration avec LangChain

### Étape 5 : Retrieval (`src/dermato_rag/retrieval/`)
- `vector_store.py` : interface Chroma (prototype)
- `hybrid_search.py` : recherche hybride dense+sparse
- `reranker.py` : intégration Cohere Rerank v3.5
- `query_transform.py` : implémentation HyDE

### Étape 6 : Génération (`src/dermato_rag/generation/`)
- `llm.py` : interface Gemini Flash + fallback Mistral Large
- `prompts.py` : prompt système spécialisé dermatologie (rôle professeur, citations, niveau médecin)

### Étape 7 : API (`src/dermato_rag/api/`)
- `routes.py` : endpoint FastAPI pour query + ingestion

### Étape 8 : Tests
- Tests unitaires par module
- Test end-to-end avec question dermatologique

---

## 7. CLÉS API NÉCESSAIRES

L'utilisateur devra fournir :
1. **GOOGLE_API_KEY** : Pour Gemini 2.5 Flash → https://aistudio.google.com/apikey
2. **MISTRAL_API_KEY** : Pour Mistral Large 3 → https://console.mistral.ai/
3. **COHERE_API_KEY** : Pour Rerank v3.5 → https://dashboard.cohere.com/

---

## 8. NOTES IMPORTANTES

- Le projet cible des **médecins diplômés** : le niveau de langage et de détail doit être médical spécialisé, pas vulgarisé
- Les documents sources sont en **français et anglais** : le système doit gérer le bilinguisme dans l'indexation et la génération
- Le prompt de génération doit produire des **cours structurés** avec plan, points clés, diagnostics différentiels, et citations des sources
- Les documents actuels concernent l'**onco-dermatologie** (premier lot), d'autres sous-domaines suivront
- Budget : projet personnel, le coût doit rester **minimal** (<$10/mois)
