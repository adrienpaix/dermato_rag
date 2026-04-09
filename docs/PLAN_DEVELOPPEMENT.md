# Plan de Développement - RAG Dermatologie
## Système de synthèse de cours de dermatologie pour médecins diplômés

**Date du benchmark : Avril 2026**

---

## 1. Vue d'ensemble du projet

### Objectif
Construire un système RAG (Retrieval-Augmented Generation) spécialisé en dermatologie capable de :
- Ingérer des sources documentaires variées (ebooks, articles scientifiques, guidelines cliniques)
- Synthétiser ces sources en cours structurés de niveau médecin diplômé
- Fonctionner en **français et anglais**
- Rester **peu coûteux** à l'utilisation

### Architecture cible
**Advanced RAG** avec pipeline modulaire :
```
Documents → Ingestion → Chunking Sémantique → Embeddings → Vector DB
                                                                ↓
User Query → Transformation de requête (HyDE) → Retrieval → Reranking → Génération LLM
```

---

## 2. Benchmark des modèles LLM (Avril 2026)

### 2.1 Modèles propriétaires (API)

| Modèle | Context | Input/Output ($/1M tokens) | MedQA | Français | Note |
|---|---|---|---|---|---|
| **Gemini 2.5 Flash** | 1M | **$0.15 / $0.60** | ~88% (estimé) | Bon (100+ langues) | **Meilleur rapport qualité/prix** |
| Gemini 2.5 Pro | 1M | $1.25 / $10.00 | ~88% | Bon | Premium, surcoût >200K tokens |
| **Mistral Small 3.1** (24B) | 128K | **$0.03 / $0.11** | Non publié | **Excellent** (entreprise FR) | Ultra low-cost, natif français |
| Mistral Large 3 | 262K | $0.50 / $1.50 | Non publié | **Excellent** | Bon compromis |
| GPT-4.1 | 1M | $2.00 / $8.00 | ~90% | Bon | Cher pour usage courant |
| GPT-4.1 nano | 1M | $0.10 / $0.40 | >GPT-4o | Bon | Alternative budget OpenAI |
| Claude Sonnet 4.6 | 1M | $3.00 / $15.00 | 91-94% | Bon | Top médical, cher |
| Claude Haiku 4.5 | 1M | $1.00 / $5.00 | ~85% | Bon | Compromis Anthropic |
| Cohere Command R+ | 128K | $2.50 / $10.00 | Non publié | Bon (100+ langues) | RAG natif, citations |
| o4-mini (OpenAI) | 200K | $1.10 / $4.40 | **95.2%** | Bon | Top MedQA mais raisonnement = tokens élevés |

### 2.2 Modèles open-source

| Modèle | Params | Context | MedQA | Français | Hardware |
|---|---|---|---|---|---|
| **Qwen 3.5-9B** | 9B | 262K | Amélioré (FR-MEDICAL) | **Excellent** (119 langues) | 10-16 GB RAM |
| Qwen 3.5-27B | 27B | 262K | Amélioré | **Excellent** | ~20 GB VRAM |
| Llama 4 Scout | 17B actifs | 10M | Non publié | Bon | 1x H100 (INT4) |
| DeepSeek V3.2 | 37B actifs | 164K | ~73% (multilingue) | Bon (73% FR) | Multi-GPU |
| **MedGemma 1.5** | 4B / 27B | - | ~91% | Modéré | GPU unique | 
| BioMistral | 7B | - | 50-55% | **Oui** (8 langues) | GPU unique |
| Meditron (Llama 3) | 8B / 70B | - | 60-65% | Non | GPU unique / Multi-GPU |

### 2.3 Modèles d'embedding

| Modèle | Dimensions | Context | Multilingue | Prix | Score MTEB |
|---|---|---|---|---|---|
| **BGE-M3** | 1024 | 8192 | 100+ langues | **Gratuit** (self-hosted) | 63.0+ |
| **Voyage 3.5** | 1024 | 32K | Fort | $0.06/1M | Top-tier |
| Cohere Embed v4 | 1024 | 128K | 100+ langues | $0.12/1M | **65.2** |
| OpenAI text-embedding-3-large | 3072 | 8191 | Bon | $0.13/1M | 64.6 |
| OpenAI text-embedding-3-small | 1536 | 8191 | Bon | $0.02/1M | ~62 |
| Gemini Embedding 2 | 3072 | 8192 | 100+ langues | $0.20/1M | TBD |
| Mistral Embed | 1024 | 8192 | Modéré | $0.10/1M | ~58 |
| CamemBERT-bio | 768 | **512** | FR uniquement | Gratuit | Spécialiste biomédical FR |

### 2.4 Rerankers

| Reranker | Type | Multilingue | Prix | Performance |
|---|---|---|---|---|
| **Cohere Rerank v3.5** | Cross-encoder API | Oui | $2.00/1000 recherches | +10-30% vs bi-encoder |
| **BGE-reranker-v2-m3** | Cross-encoder local | Oui (100+ langues) | Gratuit | Équivalent Cohere |
| FlashRank | Cross-encoder léger | Limité | Gratuit | Bon, très rapide |
| ColBERTv2 | Late interaction | Modéré | Gratuit | Proche cross-encoder |

---

## 3. Stack technique retenue

### 3.1 Choix principaux

| Composant | Choix retenu | Justification |
|---|---|---|
| **LLM Générateur** | **Gemini 2.5 Flash** (principal) | $0.15/$0.60, 1M context, bon FR, excellent ratio coût/qualité |
| **LLM Fallback** | **Mistral Large 3** | $0.50/$1.50, entreprise française, excellent FR, 262K context |
| **Embeddings** | **BGE-M3** (self-hosted) | Gratuit, 100+ langues, dense+sparse, MTEB 63+ |
| **Reranker** | **Cohere Rerank v3.5** | $2/1000 recherches, simple à intégrer, multilingue |
| **Vector DB** | **Chroma** → **Qdrant** | Chroma pour prototypage, Qdrant pour production |
| **Framework** | **LangChain** | Écosystème mature, intégrations nombreuses, communauté active |

### 3.2 Justification du choix Gemini 2.5 Flash

1. **Coût** : 20x moins cher que Claude Sonnet, 13x moins cher que GPT-4.1 (output)
2. **Context window** : 1M tokens permet d'injecter de longs passages médicaux
3. **Multilingue** : Support natif de 100+ langues dont le français
4. **Performance médicale** : L'écosystème Gemini inclut MedGemma, montrant l'investissement de Google en médecine
5. **API gratuite** : Tier gratuit généreux pour le prototypage

### 3.3 Justification du choix BGE-M3

1. **Gratuit** : Self-hosted, aucun coût par token
2. **Multilingue** : Entraîné sur 100+ langues, performant en français
3. **Dense + Sparse** : Supporte la recherche hybride (vectorielle + lexicale) nativement
4. **Performance** : Score MTEB 63+, proche des solutions commerciales
5. **Context** : 8192 tokens, suffisant pour des chunks médicaux

### 3.4 Estimation des coûts mensuels

| Usage | Détail | Coût estimé |
|---|---|---|
| Génération (Gemini Flash) | ~2M tokens output/mois | ~$1.20 |
| Génération (Mistral fallback) | ~500K tokens output/mois | ~$0.75 |
| Embeddings (BGE-M3) | Self-hosted | $0 |
| Reranking (Cohere) | ~500 recherches/mois | ~$1.00 |
| Vector DB (Qdrant) | Self-hosted | $0 |
| **Total estimé** | | **~$3-5/mois** |

---

## 4. Architecture RAG détaillée

### 4.1 Pipeline d'ingestion des documents

```
Documents sources (PDF, EPUB, articles)
        │
        ▼
┌─────────────────────────┐
│  1. Extraction de texte  │  ← PyMuPDF (PDF), ebooklib (EPUB), 
│     + métadonnées        │    markdown parser, DOI resolver
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  2. Nettoyage &          │  ← Suppression headers/footers, 
│     normalisation        │    normalisation Unicode, détection langue
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  3. Chunking sémantique  │  ← Découpage adaptatif par section/sujet
│     + hiérarchique       │    Chunks: 500-1500 tokens
│                          │    Métadonnées: source, chapitre, langue,
│                          │    type (guideline/article/cours)
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  4. Embedding BGE-M3     │  ← Vecteurs dense (1024d) + sparse
│     (dense + sparse)     │    
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  5. Stockage Qdrant      │  ← Indexation avec métadonnées
│     (ou Chroma proto)    │    Collections par type de document
└─────────────────────────┘
```

### 4.2 Pipeline de requête (Advanced RAG)

```
Question utilisateur
        │
        ▼
┌─────────────────────────┐
│  1. Détection de langue   │  ← langdetect / fasttext
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  2. Transformation HyDE   │  ← Le LLM génère une réponse hypothétique
│     (Hypothetical Doc     │    qui est ensuite embedée pour la recherche
│      Embeddings)          │    → Meilleur alignement vocabulaire médical
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  3. Recherche hybride     │  ← Dense (cosine) + Sparse (BM25)
│     Qdrant                │    Top-K = 20 résultats
│                           │    Filtrage par langue si pertinent
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  4. Reranking             │  ← Cohere Rerank v3.5
│     Cohere Rerank         │    Top-K → Top-5 résultats réordonnés
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  5. Génération            │  ← Gemini 2.5 Flash (standard)
│     avec contexte         │    ou Mistral Large 3 (complexe)
│                           │    Prompt spécialisé dermatologie
│                           │    avec citations des sources
└─────────────────────────┘
```

### 4.3 Stratégie de chunking médical

| Type de document | Stratégie | Taille chunk | Overlap |
|---|---|---|---|
| **Ebooks/Cours** | Hiérarchique par chapitre/section | 800-1200 tokens | 100 tokens |
| **Articles scientifiques** | Par section (Abstract, Méthodes, Résultats, Discussion) | 500-1000 tokens | 80 tokens |
| **Guidelines cliniques** | Par recommandation/algorithme décisionnel | 300-800 tokens | 50 tokens |
| **Fiches de synthèse** | Document entier si <1500 tokens, sinon sémantique | 500-1500 tokens | 100 tokens |

### 4.4 Prompt système spécialisé

Le prompt de génération sera structuré pour :
1. **Rôle** : Professeur de dermatologie s'adressant à des médecins diplômés
2. **Format** : Cours structuré avec plan, points clés, diagnostics différentiels
3. **Rigueur** : Citation systématique des sources utilisées
4. **Langue** : Réponse dans la langue de la question (FR ou EN)
5. **Niveau** : Vocabulaire médical spécialisé, pas de vulgarisation excessive

---

## 5. Phases de développement

### Phase 1 : Prototype (Semaines 1-2)
- [ ] Setup du repo et de l'environnement Python
- [ ] Pipeline d'ingestion basique (PDF → texte → chunks fixes)
- [ ] Intégration BGE-M3 + Chroma
- [ ] Requête simple sans reranking
- [ ] Génération avec Gemini 2.5 Flash
- [ ] Test avec 2-3 documents de dermatologie

### Phase 2 : Advanced RAG (Semaines 3-4)
- [ ] Chunking sémantique adaptatif
- [ ] Extraction de métadonnées (langue, type, source)
- [ ] Intégration HyDE pour la transformation de requêtes
- [ ] Recherche hybride dense + sparse
- [ ] Intégration Cohere Rerank v3.5
- [ ] Prompt engineering spécialisé dermatologie

### Phase 3 : Multi-sources & Qualité (Semaines 5-6)
- [ ] Support multi-format (PDF, EPUB, Markdown, DOI)
- [ ] Pipeline d'ingestion robuste avec gestion d'erreurs
- [ ] Migration Chroma → Qdrant
- [ ] Fallback Mistral Large 3 pour cas complexes
- [ ] Système de citations et traçabilité des sources
- [ ] Évaluation qualitative avec questions dermatologiques

### Phase 4 : Interface & Production (Semaines 7-8)
- [ ] API REST (FastAPI)
- [ ] Interface web simple (Streamlit ou Gradio)
- [ ] Gestion des collections de documents
- [ ] Logging et monitoring des requêtes
- [ ] Tests automatisés
- [ ] Documentation utilisateur

---

## 6. Structure du projet

```
dermato_rag/
├── docs/                          # Documentation du projet
│   ├── PLAN_DEVELOPPEMENT.md      # Ce document
│   └── ARCHITECTURE.md            # Détails d'architecture
├── src/
│   ├── dermato_rag/               # Package principal
│   │   ├── __init__.py
│   │   ├── config.py              # Configuration centralisée
│   │   ├── ingestion/             # Pipeline d'ingestion
│   │   │   ├── __init__.py
│   │   │   ├── extractors.py      # Extraction texte (PDF, EPUB, etc.)
│   │   │   ├── chunkers.py        # Stratégies de chunking
│   │   │   └── metadata.py        # Extraction de métadonnées
│   │   ├── embeddings/            # Gestion des embeddings
│   │   │   ├── __init__.py
│   │   │   └── bge_m3.py          # Wrapper BGE-M3
│   │   ├── retrieval/             # Pipeline de retrieval
│   │   │   ├── __init__.py
│   │   │   ├── vector_store.py    # Interface Chroma/Qdrant
│   │   │   ├── hybrid_search.py   # Recherche hybride
│   │   │   ├── reranker.py        # Intégration Cohere Rerank
│   │   │   └── query_transform.py # HyDE, multi-query
│   │   ├── generation/            # Pipeline de génération
│   │   │   ├── __init__.py
│   │   │   ├── llm.py             # Interface LLM (Gemini/Mistral)
│   │   │   └── prompts.py         # Templates de prompts
│   │   └── api/                   # API REST
│   │       ├── __init__.py
│   │       └── routes.py          # Endpoints FastAPI
│   └── tests/                     # Tests
│       ├── __init__.py
│       ├── test_ingestion.py
│       ├── test_retrieval.py
│       └── test_generation.py
├── data/
│   ├── raw/                       # Documents sources (non versionné)
│   └── processed/                 # Données traitées (non versionné)
├── notebooks/                     # Notebooks d'exploration
│   └── 01_prototype.ipynb
├── .env.example                   # Variables d'environnement (template)
├── .gitignore
├── pyproject.toml                 # Configuration du projet Python
└── README.md                      # README du projet
```

---

## 7. Dépendances principales

```toml
[project]
dependencies = [
    # Framework RAG
    "langchain>=0.3",
    "langchain-google-genai",      # Gemini
    "langchain-mistralai",         # Mistral
    "langchain-cohere",            # Reranker
    
    # Embeddings
    "FlagEmbedding",               # BGE-M3
    "sentence-transformers",
    
    # Vector stores
    "chromadb",                    # Prototypage
    "qdrant-client",               # Production
    
    # Extraction de documents
    "pymupdf",                     # PDF
    "ebooklib",                    # EPUB
    "beautifulsoup4",              # HTML parsing
    "python-docx",                 # DOCX
    
    # API
    "fastapi",
    "uvicorn",
    
    # Utils
    "python-dotenv",
    "langdetect",                  # Détection de langue
    "pydantic>=2.0",
]
```

---

## 8. Sources du benchmark

### Pricing API
- [Anthropic Claude Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [OpenAI API Pricing](https://platform.openai.com/docs/pricing/)
- [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Mistral AI Pricing](https://mistral.ai/pricing)
- [Cohere Pricing](https://cohere.com/pricing)

### Benchmarks médicaux
- [MedQA Leaderboard 2026](https://pricepertoken.com/leaderboards/benchmark/medqa)
- [OpenAI HealthBench](https://openai.com/index/healthbench/)
- [Claude Healthcare Technical Guide](https://intuitionlabs.ai/articles/claude-healthcare-life-sciences-ai-capabilities-2026)
- [MedGemma Technical Report](https://arxiv.org/html/2507.05201v1)
- [DeepSeek Multilingual Medical Evaluation](https://mededu.jmir.org/2026/1/e81399)
- [French Medical Domain Adaptation (Qwen 3)](https://arxiv.org/html/2604.06903)

### Embeddings & RAG
- [BGE-M3 (Hugging Face)](https://huggingface.co/BAAI/bge-m3)
- [Voyage AI Pricing](https://docs.voyageai.com/docs/pricing)
- [CamemBERT-bio](https://huggingface.co/almanach/camembert-bio-base)
- [RAG in Healthcare Review (MDPI)](https://www.mdpi.com/2673-2688/6/9/226)
- [Clinical Chunking Study (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12649634/)
- [Embedding Models Comparison 2026](https://reintech.io/blog/embedding-models-comparison-2026-openai-cohere-voyage-bge)
