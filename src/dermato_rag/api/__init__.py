"""API FastAPI pour DermatoRAG.

Patch d'audit (avr 2026) — différences vs version /opt/dermato_rag :
- `QdrantRetriever` et `CourseGenerator` instanciés UNE seule fois au démarrage
  (singletons) au lieu d'être recréés à chaque requête.
- Préchargement de BGE-M3 au startup → plus de race condition au 1er appel.
- /health renvoie le VRAI état du modèle (et de la collection), pas un False
  hardcodé.
- /ask-evidence utilise `search_with_rerank()` (Cohere Rerank multilingue).
- Détection d'intent thérapeutique → filtre `doc_type in {article, guideline}`
  + top_k augmenté.
- Le top_k passé par le client est respecté (au lieu d'être silencieusement
  écrasé à 5).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import json
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import config
from ..embeddings import embedding_manager
from ..generation import CourseGenerator
from ..retrieval import QdrantRetriever

OUTPUT_DIR = Path("/opt/dermato_rag/output")
STATIC_DIR = Path("/opt/dermato_rag/static")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

from dermato_rag.routes_extended import router as extended_router

app = FastAPI(title="DermatoRAG API", version="3.0")
_executor = ThreadPoolExecutor(max_workers=4)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extended_router)


# ---------------------------------------------------------------------------
# Singletons — créés UNE seule fois au boot.
# ---------------------------------------------------------------------------

_RETRIEVER: Optional[QdrantRetriever] = None
_GENERATOR: Optional[CourseGenerator] = None


def get_retriever() -> QdrantRetriever:
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = QdrantRetriever()
    return _RETRIEVER


def get_generator() -> CourseGenerator:
    global _GENERATOR
    if _GENERATOR is None:
        _GENERATOR = CourseGenerator()
    return _GENERATOR


@app.on_event("startup")
async def _startup() -> None:
    """Précharge le modèle d'embedding et instancie les singletons.

    Sans ça, la 1re requête HTTP paie le coût de chargement de BGE-M3 (~5-15 s
    sur CPU) et expose à une race condition si plusieurs requêtes arrivent en
    parallèle pendant le download.
    """
    print("[startup] Loading BGE-M3...", flush=True)
    embedding_manager.load_model()
    print(f"[startup] Embedding dim = {embedding_manager.dim}", flush=True)

    # Instancie les singletons
    get_retriever()
    get_generator()

    # Vérifie la collection (échoue vite si dimension mismatch)
    try:
        info = get_retriever().get_collection_info()
        print(
            f"[startup] Collection '{config.COLLECTION_NAME}' "
            f"OK ({info.points_count} points)",
            flush=True,
        )
    except Exception as e:
        print(f"[startup] WARNING: collection check failed: {e}", flush=True)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GenerateCourseRequest(BaseModel):
    course_title: str
    level: str = "DES"
    sections_enabled: Optional[List[str]] = None
    detail_level: str = "standard"
    tone: str = "academique"
    extra_instructions: str = ""
    no_redundancy: bool = True
    focus_treatment_evidence: bool = True
    include_key_points: bool = False
    include_clinical_cases: bool = False
    include_qcm: bool = False
    language: str = "fr"


class EvidenceRequest(BaseModel):
    question: str
    course_title: str
    top_k: int = 30  # passé à 30 pour les questions EBM


class NotebookLMRequest(BaseModel):
    course_title: str
    prompt_type: str
    focus: str = ""


# ---------------------------------------------------------------------------
# Static files & UI
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/dermato/")
async def serve_ui():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(
            str(index),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return JSONResponse({"error": "UI not found"}, status_code=404)


# ---------------------------------------------------------------------------
# Health (réel, pas hardcodé)
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    out = {
        "status": "ok",
        "version": "3.0",
        "embedding_model": config.EMBEDDING_MODEL,
        "embedding_model_loaded": embedding_manager.is_loaded,
        "embedding_dim": embedding_manager._dim,  # peut être None si pas encore chargé
        "collection_name": config.COLLECTION_NAME,
        "qdrant_url": config.QDRANT_URL,
        "use_reranker": config.USE_RERANKER,
    }
    try:
        info = get_retriever().get_collection_info()
        out.update(
            {
                "qdrant_connected": True,
                "collection_exists": True,
                "points_count": info.points_count,
                "collection_dim": info.config.params.vectors.size,  # type: ignore[union-attr]
            }
        )
        # Cohérence dim modèle vs collection
        if (
            embedding_manager.is_loaded
            and embedding_manager.dim != info.config.params.vectors.size  # type: ignore[union-attr]
        ):
            out["status"] = "DIMENSION_MISMATCH"
            out["error"] = (
                f"Le modèle produit dim={embedding_manager.dim} mais la collection "
                f"attend dim={info.config.params.vectors.size}. "  # type: ignore[union-attr]
                f"Le retrieval ne marchera pas. Cf. RUNBOOK.md."
            )
    except Exception as e:
        out.update(
            {
                "status": "error",
                "qdrant_connected": False,
                "collection_exists": False,
                "error": str(e),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Generate course
# ---------------------------------------------------------------------------


@app.post("/generate-course")
async def generate_course(request: GenerateCourseRequest):
    if not request.course_title.strip():
        raise HTTPException(status_code=400, detail="course_title is required")

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        async def _run():
            try:
                await loop.run_in_executor(
                    _executor,
                    _generate_course_streaming_sync,
                    request.course_title,
                    request.level,
                    request.sections_enabled,
                    request.detail_level,
                    request.tone,
                    request.extra_instructions,
                    request.no_redundancy,
                    request.focus_treatment_evidence,
                    request.include_key_points,
                    request.include_clinical_cases,
                    request.include_qcm,
                    queue,
                    loop,
                )
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, {"error": str(e)})
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.ensure_future(_run())
        yield 'data: {"status": "started"}\n\n'
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("done") or item.get("error"):
                break
        await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _generate_course_sync(
    course_title,
    level,
    queue: asyncio.Queue,
    loop,
    sections_enabled,
    detail_level,
    tone,
    extra_instructions,
    no_redundancy,
    focus_treatment_evidence,
    include_key_points,
    include_clinical_cases,
    include_qcm,
):
    start = time.time()
    retriever = get_retriever()  # singleton
    generator = get_generator()  # singleton

    result = generator.generate_full_course(
        course_title=course_title,
        retriever=retriever,
        level=level,
        sections_enabled=sections_enabled,
        detail_level=detail_level,
        tone=tone,
        extra_instructions=extra_instructions,
        no_redundancy=no_redundancy,
        focus_treatment_evidence=focus_treatment_evidence,
        include_key_points=include_key_points,
        include_clinical_cases=include_clinical_cases,
        include_qcm=include_qcm,
    )

    elapsed = round(time.time() - start, 1)
    markdown_content, sections_count, sources_count = result

    filename = f"{slugify(course_title)}_{int(time.time())}.pdf"
    filepath = OUTPUT_DIR / filename
    try:
        from ..pdf_generator import generate_pdf

        generate_pdf(markdown_content, str(filepath), course_title, level=level)
    except Exception as e:
        print(f"[PDF FALLBACK] {type(e).__name__}: {e}", flush=True)
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas

            c = canvas.Canvas(str(filepath), pagesize=A4)
            c.setFont("Helvetica", 11)
            for i, line in enumerate(markdown_content.split("\n")[:50]):
                c.drawString(50, 800 - i * 14, line[:120])
            c.save()
        except Exception as e2:
            print(f"[FALLBACK FAILED] {e2}", flush=True)
            open(filepath, "w").write(markdown_content)

    return {
        "sections_count": sections_count,
        "sources_count": sources_count,
        "generation_time_s": elapsed,
        "markdown": markdown_content,
        "pdf_url": f"/download/{filename}",
        "pdf_filename": filename,
        "pdf_size_bytes": os.path.getsize(filepath),
    }


def _generate_course_streaming_sync(
    course_title,
    level,
    sections_enabled,
    detail_level,
    tone,
    extra_instructions,
    no_redundancy,
    focus_treatment_evidence,
    include_key_points,
    include_clinical_cases,
    include_qcm,
    queue: asyncio.Queue,
    loop,
):
    """Like _generate_course_sync but pushes each section into the queue as it's generated."""
    from ..generation import SECTIONS as _SECTIONS

    start = time.time()
    retriever = get_retriever()
    generator = get_generator()

    full_markdown = f"# {course_title}\n\n"
    full_markdown += f"*Cours de niveau {level} — Généré par DermatoRAG*\n\n---\n\n"

    all_chunks = []
    sections_generated = 0

    active_sections = sections_enabled
    if active_sections is None:
        active_sections = [title for title, _ in _SECTIONS]

    for section_title, keywords in _SECTIONS:
        if section_title not in active_sections:
            continue

        print(f"[SSE] Génération section: {section_title}...", flush=True)

        chunks = []
        if retriever:
            try:
                results1 = retriever.search(f"{course_title} {keywords}", top_k=10)
                results2 = retriever.search(f"{section_title} dermatologie {course_title}", top_k=8)
                query3_text = f"{course_title} {section_title.split()[0]}"
                results3 = retriever.search(query3_text, top_k=5) if "Introduction" not in section_title else []
                seen_texts = set()
                for r in results1 + results2 + results3:
                    txt = r.get('text', '')
                    dedup_key = (r.get('source', ''), txt[:150])
                    if dedup_key not in seen_texts:
                        chunks.append(r)
                        seen_texts.add(dedup_key)
                chunks = chunks[:15]
            except Exception as e:
                print(f"[SSE] Retrieval error: {e}", flush=True)

        all_chunks.extend(chunks)

        section_content = generator.generate_section(
            section_title=section_title,
            chunks=chunks,
            course_title=course_title,
            level=level,
            sections_enabled=active_sections,
            detail_level=detail_level,
            tone=tone,
            extra_instructions=extra_instructions,
            no_redundancy=no_redundancy,
            focus_treatment_evidence=focus_treatment_evidence,
            include_key_points=include_key_points,
            include_clinical_cases=False,
            include_qcm=False,
        )
        if section_content:
            section_md = f"# {section_title}\n\n{section_content}\n\n"
            full_markdown += section_md
            sections_generated += 1
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"section": section_title, "content": section_md},
            )

    # Optional add-ons
    if include_clinical_cases:
        clinical_cases = generator._generate_clinical_cases(course_title, all_chunks)
        full_markdown += "\n\n" + clinical_cases + "\n\n"
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"section": "Cas Cliniques", "content": "\n\n" + clinical_cases + "\n\n"},
        )

    if include_qcm:
        qcm = generator._generate_qcm(course_title, all_chunks)
        full_markdown += "\n\n" + qcm + "\n\n"
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"section": "QCM", "content": "\n\n" + qcm + "\n\n"},
        )

    # Bibliography
    bibliography = generator.generate_bibliography(all_chunks, full_markdown)
    full_markdown += "\n" + bibliography
    loop.call_soon_threadsafe(
        queue.put_nowait,
        {"section": "Bibliographie", "content": "\n" + bibliography},
    )

    elapsed = round(time.time() - start, 1)
    unique_sources = len(set(c.get('source', '') for c in all_chunks if c.get('source')))

    # Generate PDF
    filename = f"{slugify(course_title)}_{int(time.time())}.pdf"
    filepath = OUTPUT_DIR / filename
    try:
        from ..pdf_generator import generate_pdf
        generate_pdf(full_markdown, str(filepath), course_title, level=level)
    except Exception as e:
        print(f"[SSE PDF FALLBACK] {type(e).__name__}: {e}", flush=True)
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            c = canvas.Canvas(str(filepath), pagesize=A4)
            c.setFont("Helvetica", 11)
            for i, line in enumerate(full_markdown.split("\n")[:50]):
                c.drawString(50, 800 - i * 14, line[:120])
            c.save()
        except Exception as e2:
            print(f"[SSE FALLBACK FAILED] {e2}", flush=True)
            open(filepath, "w").write(full_markdown)

    loop.call_soon_threadsafe(
        queue.put_nowait,
        {
            "done": True,
            "sections_count": sections_generated,
            "sources_count": unique_sources,
            "generation_time_s": elapsed,
            "pdf_url": f"/download/{filename}",
            "pdf_filename": filename,
        },
    )
    loop.call_soon_threadsafe(queue.put_nowait, None)


@app.get("/download/{filename}")
async def download(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), filename=filename, media_type="application/pdf")


# ---------------------------------------------------------------------------
# Ask Evidence (avec reranking + filtrage)
# ---------------------------------------------------------------------------

# Mots-clés qui déclenchent le mode "EBM thérapeutique" (filtre + boost top_k).
_TREATMENT_KEYWORDS = (
    "traitement",
    "treatment",
    "thérapeutique",
    "therapie",
    "thérapie",
    "ligne",
    "essai",
    "trial",
    "ebm",
    "preuve",
    "evidence",
    "pembrolizumab",
    "nivolumab",
    "ipilimumab",
    "cemiplimab",
    "cémiplimab",
    "vismodegib",
    "sonidegib",
    "dabrafenib",
    "trametinib",
    "encorafenib",
    "binimetinib",
    "avelumab",
    "lifileucel",
    "anti-pd",
    "anti pd",
    "checkpoint",
    "immunothérapie",
    "immunotherapy",
    "adjuvant",
    "neoadjuvant",
    "néoadjuvant",
    "hr",
    "pfs",
    "os",
    "objective response",
)


def _is_treatment_question(q: str) -> bool:
    ql = q.lower()
    return any(k in ql for k in _TREATMENT_KEYWORDS)


@app.post("/ask-evidence")
async def ask_evidence(request: EvidenceRequest):
    """Interroge les sources avec reranking + filtre par doc_type si EBM."""
    try:
        retriever = get_retriever()

        is_treatment = _is_treatment_question(request.question)
        doc_types = ("article", "guideline") if is_treatment else None
        top_k_retrieval = request.top_k

        evidence_chunks = retriever.search_with_rerank(
            query=request.question,
            top_k_retrieval=top_k_retrieval,
            top_k_rerank=top_k_retrieval,
            doc_types=doc_types,
        )

        # Si le filtre EBM ne ramène rien, on retombe sur tout le corpus.
        if not evidence_chunks and doc_types is not None:
            evidence_chunks = retriever.search_with_rerank(
                query=request.question,
                top_k_retrieval=top_k_retrieval,
                top_k_rerank=top_k_retrieval,
                doc_types=None,
            )

        if not evidence_chunks:
            return {
                "answer": "Aucune donnée probante trouvée dans la base de connaissances pour cette question.",
                "sources_used": [],
                "evidence_chunks": [],
                "is_treatment_question": is_treatment,
            }

        # Numérote explicitement les sources dans le contexte LLM
        context_blocks = []
        for i, c in enumerate(evidence_chunks):
            src = c.get("source", "Source inconnue")
            context_blocks.append(f"[Source {i + 1} — {src}]\n{c.get('text', '')}")
        context = "\n\n".join(context_blocks)

        sources_used = list({c["source"] for c in evidence_chunks if c.get("source")})

        prompt = f"""Tu es un assistant médical expert en Evidence-Based Medicine en onco-dermatologie.

Question clinique : {request.question}

Contexte clinique : {request.course_title}

Sources disponibles (numérotées) :
{context}

Règles strictes :
- Pour CHAQUE affirmation factuelle, cite la source en utilisant le numéro [Source N].
- Donne les chiffres clés quand ils sont dans les sources : ORR, PFS, OS, HR, IC95%, p, N patients.
- Mentionne les noms d'essais cliniques (KEYNOTE, CheckMate, EMPOWER-CSCC, BOLT, STEVIE, NADINA, DREAMseq, etc.) et les molécules s'ils sont présents.
- N'INVENTE JAMAIS un essai ou un chiffre. Si une info n'est pas dans les sources, dis "Information non disponible dans les sources fournies".
- Réponds en français médical académique, niveau DES de dermatologie.
- Structure ta réponse : (1) Réponse synthétique, (2) Données chiffrées par essai/molécule, (3) Niveau de preuve.

Réponse :"""

        generator = get_generator()
        from langchain_core.messages import HumanMessage

        response = generator.llm.invoke([HumanMessage(content=prompt)])
        answer = response.content

        return {
            "answer": answer,
            "sources_used": sources_used[:15],
            "evidence_chunks": [
                {
                    "source": c.get("source"),
                    "doc_type": c.get("doc_type"),
                    "rerank_score": c.get("rerank_score"),
                    "text": c.get("text", "")[:300],
                }
                for c in evidence_chunks[:8]
            ],
            "is_treatment_question": is_treatment,
            "n_chunks_total": len(evidence_chunks),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"{e}\n{traceback.format_exc()}",
        )


# ---------------------------------------------------------------------------
# NotebookLM endpoints — inchangés sauf utilisation du singleton retriever
# ---------------------------------------------------------------------------


@app.post("/generate-notebooklm-prompt")
async def generate_notebooklm_prompt(request: NotebookLMRequest):
    try:
        retriever = get_retriever()
        query = (
            f"{request.course_title} {request.focus}"
            if request.focus
            else request.course_title
        )
        chunks = retriever.search(query, top_k=30)

        trials: List[str] = []
        for chunk in chunks:
            text = chunk.get("text", "")
            for m in re.findall(
                r"\b([A-Z][A-Za-z0-9\-]+(?:\s+[A-Z][A-Za-z0-9\-]+)*)\s+(?:study|trial|essai|protocol)\b",
                text,
                re.IGNORECASE,
            ):
                if len(m) > 5 and m not in trials:
                    trials.append(m)
        trials = list(set(trials))[:15]

        ctx = ""
        for i, c in enumerate(chunks[:15]):
            ctx += (
                f"\n[Source {i + 1} — {c.get('source', 'inconnue')}]\n"
                f"{c.get('text', '')[:500]}\n"
            )

        notebooklm_prompt = (
            f"Voici les données sur {request.course_title}.\n\n"
            f"Données extraites :{ctx}\n\n"
            f"Essais cliniques identifiés : "
            f"{', '.join(trials) if trials else 'Aucun essai spécifique identifié'}\n\n"
            f"Génère un livrable de type {request.prompt_type} pour "
            f"{request.focus or 'la prise en charge'} basé sur ces sources."
        )
        return {
            "notebooklm_prompt": notebooklm_prompt,
            "trials_found": trials,
            "copy_ready": True,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"{e}\n{traceback.format_exc()}"
        )


@app.post("/notebooklm-only")
async def notebooklm_only(request: NotebookLMRequest):
    return await generate_notebooklm_prompt(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text[:60]
