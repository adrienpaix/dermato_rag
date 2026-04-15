#!/usr/bin/env python3
"""Validation post-bascule du pipeline DermatoRAG.

Rejoue les 3 questions de test de l'audit contre une API DermatoRAG et
vérifie que :
  1. La réponse n'est plus « Aucune donnée probante trouvée... »
  2. `sources_used` contient >= 3 sources distinctes
  3. Au moins une source contient un nom d'article/guideline identifiable

À lancer après la bascule vers la collection BGE-M3 v3 pour confirmer que
le retrieval fonctionne enfin correctement.

Usage :
    python scripts/validate.py                              # contre localhost:8001
    python scripts/validate.py --url http://37.27.90.184:8001
    python scripts/validate.py --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Tuple

import urllib.request

TESTS: List[Tuple[str, str, List[str]]] = [
    (
        "Cémiplimab cSCC EBM",
        "Quelle est la justification EBM du cémiplimab en première ligne du carcinome épidermoïde cutané métastatique ou localement avancé ? Cite les essais cliniques pivots et les chiffres (ORR, PFS, OS).",
        ["migden", "cemiplimab", "cémiplimab", "empower", "nejmoa1805131"],
    ),
    (
        "ABCDE mélanome",
        "Quels sont les critères ABCDE pour le diagnostic clinique du mélanome cutané ?",
        ["mélanome", "abcde", "dermoscop", "nevus", "pigmented"],
    ),
    (
        "Mélanome avancé BRAF",
        "Quelles sont les options thérapeutiques systémiques du mélanome avancé BRAF muté et non muté ? Cite les essais pivots et les molécules (anti-PD1, anti-CTLA4, BRAFi+MEKi).",
        ["checkmate", "keynote", "nivolumab", "pembrolizumab", "dabrafenib", "wolchok", "larkin", "robert"],
    ),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8001", help="Base URL de l'API DermatoRAG")
    p.add_argument("--course-title", default="Onco-dermatologie", help="Valeur de course_title pour /ask-evidence")
    p.add_argument("--verbose", "-v", action="store_true", help="Affiche le détail des chunks")
    return p.parse_args()


def post_json(url: str, payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url: str, timeout: int = 10) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_health(base_url: str) -> None:
    print(f"\n▶ /health sur {base_url}")
    try:
        h = get_json(f"{base_url}/health")
    except Exception as e:
        print(f"  ❌ /health inaccessible : {e}")
        sys.exit(2)
    for key in (
        "status",
        "embedding_model",
        "embedding_model_loaded",
        "embedding_dim",
        "collection_name",
        "collection_dim",
        "points_count",
        "use_reranker",
    ):
        if key in h:
            print(f"  {key:24s} = {h[key]}")
    if h.get("status") == "DIMENSION_MISMATCH":
        print(f"\n  ❌ {h.get('error')}")
        sys.exit(3)
    if h.get("status") != "ok":
        print(f"\n  ⚠️  status={h.get('status')}")


def score_answer(answer: str, sources: List[str], expected: List[str]) -> Tuple[bool, str]:
    """Retourne (success, reason)."""
    ans_low = answer.lower()
    src_low = " ".join(sources).lower()

    if "aucune donnée probante" in ans_low or "aucune donnée trouvée" in ans_low:
        return False, "Réponse = 'aucune donnée probante' → retrieval KO"

    if len(sources) < 3:
        return False, f"seulement {len(sources)} sources distinctes (attendu >= 3)"

    expected_hits = [kw for kw in expected if kw in ans_low or kw in src_low]
    if len(expected_hits) < 2:
        return (
            False,
            f"mots-clés attendus manquants — hits={expected_hits} (attendu >= 2 parmi {expected})",
        )

    return True, f"hits={expected_hits}, {len(sources)} sources"


def run_tests(base_url: str, course_title: str, verbose: bool) -> int:
    results = []
    for name, question, expected in TESTS:
        print(f"\n▶ Test : {name}")
        print(f"  Q : {question}")
        t0 = time.time()
        try:
            resp = post_json(
                f"{base_url}/ask-evidence",
                {"question": question, "course_title": course_title, "top_k": 30},
            )
        except Exception as e:
            print(f"  ❌ HTTP error: {e}")
            results.append((name, False, f"HTTP error: {e}"))
            continue

        dt = time.time() - t0
        answer = resp.get("answer", "")
        sources = resp.get("sources_used", [])
        is_treatment = resp.get("is_treatment_question", False)
        n_chunks = resp.get("n_chunks_total", len(resp.get("evidence_chunks", [])))

        ok, reason = score_answer(answer, sources, expected)
        results.append((name, ok, reason))

        print(f"  ⏱  {dt:.1f}s | treatment={is_treatment} | n_chunks={n_chunks} | sources={len(sources)}")
        print(f"  Sources : {sources[:8]}")
        if verbose:
            print(f"\n  --- Réponse ---\n{answer[:1200]}\n  ---")
            for c in resp.get("evidence_chunks", [])[:3]:
                print(
                    f"  [{c.get('doc_type')}] {c.get('source')} "
                    f"(rerank={c.get('rerank_score')})"
                )
                print(f"    {c.get('text', '')[:200]}")

        verdict = "✅ OK" if ok else "❌ FAIL"
        print(f"  {verdict} — {reason}")

    print("\n" + "=" * 60)
    print("  RÉSULTAT GLOBAL")
    print("=" * 60)
    n_ok = sum(1 for _, ok, _ in results if ok)
    for name, ok, reason in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name:30s} — {reason}")
    print(f"\n  {n_ok}/{len(results)} tests passent.")
    return 0 if n_ok == len(results) else 1


def main() -> int:
    args = parse_args()
    check_health(args.url)
    return run_tests(args.url, args.course_title, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
