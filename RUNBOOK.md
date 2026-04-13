# RUNBOOK — Réparation DermatoRAG (audit avril 2026)

**But :** déployer les fix d'audit sur le VPS `37.27.90.184` et faire passer le
RAG de « retrieval complètement cassé » à « retrieval BGE-M3 + Cohere rerank
fonctionnel ».

**Prérequis :** SSH accessible au VPS (`ssh vps_hetzner`). Environ 1 h de
présence, dont ~30-45 min d'ingestion automatique pendant laquelle on ne
fait rien.

**Principe de sécurité :** à chaque étape on laisse l'ancien système en place
tant que le nouveau n'est pas validé. On peut rollback à tout moment.

---

## Résumé du fix en une ligne

Remplacer l'embedding model actuel (`dmis-lab/biobert-v1.1`, qui produit des
vecteurs dégénérés) par **BGE-M3** (1024-dim, multilingue, contrastif), dans
une **nouvelle collection `dermato_rag_v3`**, en **ré-indexant proprement** les
85 PDFs de `/root/dermato_upload/Onco/`, et en redémarrant l'API avec le
**code patché** (singletons, vrai `/health`, Cohere Rerank v3.5, filtrage par
`doc_type`).

---

## Étape 0 — Revert du `.env` cassé (URGENT, 1 min)

Pendant notre audit, on a exécuté un `sed` qui a mis `COLLECTION_NAME` sur
`dermato_rag_v3` (qui n'existe pas encore). Si le process uvicorn redémarre
maintenant, l'API plantera.

```bash
ssh vps_hetzner
sed -i 's|^EMBEDDING_MODEL=.*|EMBEDDING_MODEL=dmis-lab/biobert-v1.1|' /opt/dermato_rag/.env
sed -i 's|^COLLECTION_NAME=.*|COLLECTION_NAME=dermato_rag_v2|' /opt/dermato_rag/.env
grep -E "(COLLECTION|EMBED)" /opt/dermato_rag/.env
```

**Checkpoint :** le `.env` doit afficher `dmis-lab/biobert-v1.1` et
`dermato_rag_v2`. Si oui, tout redémarrage sera désormais sans risque.

---

## Étape 1 — Backup complet (5 min, sans risque)

```bash
ssh vps_hetzner
TS=$(date +%Y%m%d-%H%M%S)

# Backup code + env
cp -r /opt/dermato_rag /opt/dermato_rag.backup-$TS
echo "Backup code : /opt/dermato_rag.backup-$TS"

# Backup Qdrant (snapshot par collection)
curl -sS -X POST http://127.0.0.1:6335/collections/dermato_rag/snapshots
curl -sS -X POST http://127.0.0.1:6335/collections/dermato_rag_v2/snapshots
curl -sS http://127.0.0.1:6335/collections/dermato_rag/snapshots
curl -sS http://127.0.0.1:6335/collections/dermato_rag_v2/snapshots
```

**Checkpoint :** les snapshots apparaissent dans la sortie du dernier curl.
En cas de pépin, on peut les restaurer avec
`curl -X PUT /collections/<name>/snapshots/recover`.

---

## Étape 2 — Déployer le code patché (10 min)

Option recommandée : cloner le repo Git de l'audit dans `/opt/dermato_rag_v3/`
et copier les fichiers patchés dans `/opt/dermato_rag/src/`.

```bash
ssh vps_hetzner

# Cloner la branche d'audit
cd /tmp
git clone -b claude/audit-rag-dermato-AA6kc https://github.com/adrienpaix/dermato_rag.git dermato_rag_audit
cd dermato_rag_audit

# Vérifier que les fichiers patchés sont là
ls -la src/dermato_rag/embeddings/__init__.py \
       src/dermato_rag/retrieval/__init__.py \
       src/dermato_rag/ingestion/__init__.py \
       src/dermato_rag/api/__init__.py \
       src/dermato_rag/config.py \
       scripts/rebuild_collection.py \
       scripts/validate.py

# Copier les fichiers patchés par-dessus (le backup de l'étape 1 nous couvre)
cp src/dermato_rag/embeddings/__init__.py /opt/dermato_rag/src/dermato_rag/embeddings/__init__.py
cp src/dermato_rag/retrieval/__init__.py  /opt/dermato_rag/src/dermato_rag/retrieval/__init__.py
cp src/dermato_rag/ingestion/__init__.py  /opt/dermato_rag/src/dermato_rag/ingestion/__init__.py
cp src/dermato_rag/api/__init__.py        /opt/dermato_rag/src/dermato_rag/api/__init__.py
cp src/dermato_rag/config.py              /opt/dermato_rag/src/dermato_rag/config.py

# Copier les scripts dans /opt/dermato_rag/scripts/
mkdir -p /opt/dermato_rag/scripts
cp scripts/rebuild_collection.py /opt/dermato_rag/scripts/
cp scripts/validate.py           /opt/dermato_rag/scripts/
chmod +x /opt/dermato_rag/scripts/*.py
```

**Checkpoint :** `grep "version=\"3.0\"" /opt/dermato_rag/src/dermato_rag/api/__init__.py`
doit retourner une ligne (c'est la nouvelle version d'API).

**Note importante :** à ce stade, le code est patché **mais le process uvicorn
tourne encore avec l'ancien code en mémoire** (Python ne recharge pas les
modules). L'API continue donc de répondre comme avant. On ne redémarrera
qu'après avoir construit la nouvelle collection (étape 3).

---

## Étape 3 — Ré-indexation dans `dermato_rag_v3` (30-90 min)

```bash
ssh vps_hetzner
cd /opt/dermato_rag

# Vérifier que l'ancien venv a bien les dépendances (BGE-M3 est déjà en cache)
.venv/bin/python -c "from dermato_rag.ingestion import PDFIngester, detect_doc_type; print('imports OK')"
.venv/bin/python -c "from dermato_rag.embeddings import embedding_manager; print('imports OK')"
.venv/bin/python -c "from dermato_rag.retrieval import QdrantRetriever; print('imports OK')"

# Dry-run d'abord (pas d'upsert, juste pour vérifier la classification)
.venv/bin/python scripts/rebuild_collection.py \
    --source /root/dermato_upload/Onco \
    --collection dermato_rag_v3 \
    --dry-run --limit 5

# Si le dry-run passe, lancer le vrai run
# NB: ~30-45 min sur CPU 8 cores — lancez en nohup pour pouvoir vous déconnecter
nohup .venv/bin/python scripts/rebuild_collection.py \
    --source /root/dermato_upload/Onco \
    --collection dermato_rag_v3 \
    --batch-size 8 \
    > /tmp/rebuild_v3.log 2>&1 &

echo "PID : $!"
# Suivre les logs
tail -f /tmp/rebuild_v3.log
# (Ctrl+C pour arrêter le tail, le script continue en arrière-plan)
```

**Checkpoint :** à la fin, `/tmp/rebuild_v3.log` doit contenir :
```
✅ Done.
```
Et un rapport qui montre ~85 sources distinctes et ~2000-4000 chunks, avec
une répartition par `doc_type` du genre :
```
Par doc_type        : {'article': ~60, 'guideline': ~15, 'ebook': ~5, 'unknown': <5}
Par langue          : {'en': ~70, 'fr': ~10, 'unknown': <5}
```

Vérifier aussi dans Qdrant :

```bash
curl -sS http://127.0.0.1:6335/collections/dermato_rag_v3 | python3 -m json.tool
```

→ doit afficher `"size": 1024` et `"points_count": ~2000-4000`.

---

## Étape 4 — Bascule du `.env` vers v3 (1 min)

```bash
ssh vps_hetzner

# Mettre à jour .env proprement
sed -i 's|^EMBEDDING_MODEL=.*|EMBEDDING_MODEL=BAAI/bge-m3|' /opt/dermato_rag/.env
sed -i 's|^COLLECTION_NAME=.*|COLLECTION_NAME=dermato_rag_v3|' /opt/dermato_rag/.env

# Ajouter la config reranker si absente
grep -q "^USE_RERANKER=" /opt/dermato_rag/.env \
    || echo "USE_RERANKER=true" >> /opt/dermato_rag/.env
grep -q "^RERANKER_MODEL=" /opt/dermato_rag/.env \
    || echo "RERANKER_MODEL=rerank-multilingual-v3.0" >> /opt/dermato_rag/.env
grep -q "^TOP_K_RETRIEVAL=" /opt/dermato_rag/.env \
    || echo "TOP_K_RETRIEVAL=30" >> /opt/dermato_rag/.env
grep -q "^TOP_K_RERANK=" /opt/dermato_rag/.env \
    || echo "TOP_K_RERANK=10" >> /opt/dermato_rag/.env

# Vérifier
grep -E "(COLLECTION|EMBED|RERANK|TOP_K)" /opt/dermato_rag/.env
```

**Checkpoint :** `.env` doit afficher `BAAI/bge-m3`, `dermato_rag_v3`,
`USE_RERANKER=true`, `TOP_K_RETRIEVAL=30`, `TOP_K_RERANK=10`.

---

## Étape 5 — Redémarrage propre de l'API (2 min)

```bash
ssh vps_hetzner

# 1. Identifier le process actuel
pgrep -af "uvicorn dermato_rag"

# 2. Le tuer proprement
pkill -TERM -f "uvicorn dermato_rag.api:app"
sleep 3
pgrep -f "uvicorn dermato_rag" || echo "Process arrêté"

# 3. Relancer (commande miroir de celle trouvée dans ps auxf)
cd /opt/dermato_rag
nohup .venv/bin/uvicorn dermato_rag.api:app \
    --host 0.0.0.0 --port 8001 \
    > /tmp/dermato_api.log 2>&1 &
echo "Nouveau PID : $!"

# 4. Attendre le préchargement de BGE-M3 (~10-15s)
sleep 20

# 5. Vérifier que le process a bien chargé le bon env
NEWPID=$(pgrep -f "uvicorn dermato_rag.api:app" | head -1)
cat /proc/$NEWPID/environ | tr '\0' '\n' | grep -E "(COLLECTION|EMBED)"

# 6. Vérifier les logs de démarrage
tail -30 /tmp/dermato_api.log
```

**Checkpoint :** les logs doivent contenir :
```
[startup] Loading BGE-M3...
[embeddings] Loading BAAI/bge-m3...
[embeddings] Loaded. dim=1024
[startup] Embedding dim = 1024
[startup] Collection 'dermato_rag_v3' OK (N points)
```

Et `/proc/$NEWPID/environ` doit montrer :
```
EMBEDDING_MODEL=BAAI/bge-m3
COLLECTION_NAME=dermato_rag_v3
```

---

## Étape 6 — Validation avec les 3 tests de l'audit (2 min)

```bash
ssh vps_hetzner
cd /opt/dermato_rag

# Lancer le script de validation
.venv/bin/python scripts/validate.py --url http://localhost:8001 --verbose
```

**Checkpoint succès :** `3/3 tests passent.` et dans les réponses on doit
voir apparaître des sources comme `Migden 2020`, `NEJMoa1805131`,
`Wolchok 2025`, `Stratigos 2023`, `Amaral 2025`, etc.

**Avant/après attendu :**

| Test | Avant (BioBERT) | Après (BGE-M3) |
|---|---|---|
| Cémiplimab cSCC | "Aucune donnée probante" + chunks de R2C non pertinents | Réponse avec chiffres ORR/PFS + cite Migden 2020, NEJMoa1805131 |
| ABCDE mélanome | "Aucune donnée probante" | Réponse correcte citant Alikhan/R2C/articles mélanome |
| Mélanome BRAF | "Aucune donnée probante" | Réponse citant CheckMate/KEYNOTE/Dabrafenib+Trametinib |

---

## Étape 7 — Test manuel côté UI (2 min)

Ouvrir **http://37.27.90.184:8001/dermato/** dans le navigateur.

1. Générer un cours sur « Carcinome épidermoïde cutané avancé »
2. Ouvrir le modal « Interroger les sources (EBM) »
3. Poser la question : *« Quelle est la justification EBM du cémiplimab en
   première ligne ? »*
4. Vérifier que la réponse cite **plusieurs articles** et non uniquement le R2C

---

## Rollback — en cas de problème à n'importe quelle étape

```bash
ssh vps_hetzner

# 1. Tuer le process foireux
pkill -9 -f "uvicorn dermato_rag.api:app"

# 2. Restaurer le code
TS=20260413-XXXXXX  # à adapter selon l'étape 1
rm -rf /opt/dermato_rag
mv /opt/dermato_rag.backup-$TS /opt/dermato_rag

# 3. Restaurer .env si nécessaire
# (déjà dans /opt/dermato_rag via le backup ci-dessus)

# 4. Relancer l'ancienne API
cd /opt/dermato_rag
nohup .venv/bin/uvicorn dermato_rag.api:app --host 0.0.0.0 --port 8001 \
    > /tmp/dermato_api.log 2>&1 &

# 5. Vérifier
curl http://127.0.0.1:8001/health
```

Note : les collections `dermato_rag` et `dermato_rag_v2` ne sont jamais
touchées par le fix — elles restent disponibles telles quelles dans Qdrant
comme filet de sécurité. On pourra les supprimer 1 ou 2 semaines après la
bascule réussie :

```bash
curl -X DELETE http://127.0.0.1:6335/collections/dermato_rag
curl -X DELETE http://127.0.0.1:6335/collections/dermato_rag_v2
```

---

## Checklist rapide

- [ ] Étape 0 — `.env` reverté sur BioBERT + v2 (sécurise le redémarrage)
- [ ] Étape 1 — Backup code + snapshots Qdrant des 2 collections
- [ ] Étape 2 — Code patché copié dans `/opt/dermato_rag/src/`
- [ ] Étape 3 — `rebuild_collection.py` tourne et produit `dermato_rag_v3` avec size=1024
- [ ] Étape 4 — `.env` mis à jour vers BGE-M3 + v3 + reranker activé
- [ ] Étape 5 — uvicorn redémarré, logs de startup OK, env en mémoire = BGE-M3 + v3
- [ ] Étape 6 — `validate.py` passe 3/3
- [ ] Étape 7 — Test UI manuel concluant
- [ ] (plus tard) — nettoyage des anciennes collections et scripts `run_*.py` dans `/opt/dermato_rag/scripts_legacy/`

---

## Ce qui est corrigé par ce fix

| # | Bug | Fix |
|---|---|---|
| 1 | `dmis-lab/biobert-v1.1` utilisé comme sentence encoder → embeddings dégénérés, mêmes chunks pour toutes les questions | BGE-M3 forcé, vérif dim au startup |
| 2 | Dimension mismatch collection vs modèle silencieux | `ensure_collection()` vérifie et plante avec message clair |
| 3 | IDs Qdrant `id=idx` → écrasement entre runs d'ingestion | IDs déterministes uuid5 |
| 4 | Pas de reranking malgré la clé Cohere dans `.env` | Pipeline `search_with_rerank()` branché sur `/ask-evidence` |
| 5 | `/health` avec `embedding_model_loaded: False` hardcodé | `/health` réel qui inspecte le modèle et la collection |
| 6 | `QdrantRetriever()` recréé à chaque requête HTTP | Singletons créés au startup |
| 7 | `top_k_rerank` coincé à 5 pour les questions thérapeutiques | Configurable via `.env`, défaut 10, adaptatif selon l'intent |
| 8 | Aucun filtrage par type de document | Détection d'intent thérapeutique → filtre `doc_type in (article, guideline)` |
| 9 | 5 scripts `run_*.py` ad-hoc qui s'écrasent | Remplacés par un seul `rebuild_collection.py` robuste |
| 10 | Pas d'eval | `validate.py` avec 3 questions + critères de succès |

## Ce qui reste à améliorer (plus tard)

- Recherche hybride dense + BM25 (Qdrant 1.13.2 le supporte nativement via les sparse vectors de BGE-M3)
- Mise à jour de `sentence-transformers` 2.7 → 3.x pour le support officiel BGE-M3
- Eval set élargi (20-30 questions) + métrique faithfulness
- Logs structurés avec scores de retrieval pour debug continu
- Systemd unit file au lieu du `nohup ... &`
- Monitoring : alerting si `/health.status != "ok"`
