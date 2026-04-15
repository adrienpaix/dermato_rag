"""Pipeline d'ingestion PDF → chunks pour DermatoRAG.

Patch d'audit (avr 2026) :
- Détection automatique du `doc_type` (article / guideline / ebook / unknown)
  à partir d'heuristiques sur le nom de fichier. Permet de filtrer/booster
  les sources EBM dans le retrieval.
- Chunking adaptatif selon le doc_type :
  - articles, guidelines : 1500 tokens, overlap 200 (préserve les protocoles)
  - ebooks : 1000 tokens, overlap 150
- chunk_id stable et idempotent : `f"{source}::p{page}::{hash[:10]}"`
  → re-run d'ingestion = upsert propre via les uuid5 du retrieval.
- Détection de langue (langdetect) sur chaque chunk pour permettre un éventuel
  filtrage FR/EN au query-time.
- Compteur de tokens (tiktoken cl100k_base) — précis, contrairement au split
  par caractères qui sous-estime sévèrement les chunks.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # pymupdf
import tiktoken

from ..config import config

try:
    from langdetect import DetectorFactory, LangDetectException, detect

    DetectorFactory.seed = 0  # langdetect = déterministe
    _HAS_LANGDETECT = True
except ImportError:
    _HAS_LANGDETECT = False


# ---------------------------------------------------------------------------
# Heuristiques de classification des documents
# ---------------------------------------------------------------------------

# Patterns inspirés des noms de fichiers réellement présents dans
# /root/dermato_upload/Onco/ (cf. dump d'audit).
_GUIDELINE_HINTS = (
    "guideline",
    "guidelines",
    "esmo",
    "nccn",
    "european consensus",
    "consensus-based",
    "asco",
    "has",
    "recommend",
    "stratigos",  # auteur récurrent de guidelines cSCC
    "garbe",  # auteur récurrent de guidelines mélanome
    "peris",  # auteur récurrent de guidelines BCC
    "gauci",  # auteur récurrent de guidelines Merkel
    "amaral",  # auteur récurrent de guidelines mélanome ESMO
)

_ARTICLE_HINTS = (
    " et al",  # "Smith et al. - 2023 - ..."
    "et al.",
    "nejmoa",  # NEJM article DOIs
    "10.1056",  # NEJM DOI prefix
    "10.1200",  # JCO DOI prefix
    "10.1158",  # AACR DOI prefix
    "phase ii",
    "phase iii",
    "randomized",
    "randomised",
    "trial",
    "study",
    "open-label",
    "double-blind",
    "keynote",
    "checkmate",
    "keynote-",
    "checkmate-",
)

_EBOOK_HINTS = (
    "handbook",
    "review of dermatology",
    "textbook",
    "atlas",
    "manual",
    "r2c",  # collection R2C des étudiants ECN
    "r2c-",
    "ecn",
    "compressed-compressed",  # signature des PDFs ebooks ré-compressés
)


def detect_doc_type(filename: str) -> str:
    """Heuristique simple basée sur le nom de fichier."""
    name = filename.lower()

    # Guidelines en premier (prioritaire pour le boost EBM)
    if any(h in name for h in _GUIDELINE_HINTS):
        return "guideline"

    # Ebooks ensuite (avant articles, sinon "handbook of dermatology" pourrait
    # matcher "handbook" et passer en ebook, mais on garde l'ordre explicite).
    if any(h in name for h in _EBOOK_HINTS):
        return "ebook"

    # Articles scientifiques
    if any(h in name for h in _ARTICLE_HINTS):
        return "article"

    # Heuristique de fallback : un nom du type "Author - YYYY - Title.pdf" est
    # quasi-certainement un article scientifique exporté de Zotero/Mendeley.
    if re.search(r"\b\d{4}\b", name) and (" - " in name or "_" in name):
        return "article"

    return "unknown"


# ---------------------------------------------------------------------------
# Chunking — paramétres adaptatifs par doc_type
# ---------------------------------------------------------------------------

# (chunk_size_tokens, overlap_tokens)
_CHUNK_PROFILE: Dict[str, tuple] = {
    "article": (1500, 200),
    "guideline": (1500, 200),
    "ebook": (1000, 150),
    "unknown": (1000, 150),
}


def _safe_lang(text: str) -> str:
    if not _HAS_LANGDETECT or len(text) < 30:
        return "unknown"
    try:
        return detect(text)
    except LangDetectException:
        return "unknown"


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:10]


class PDFIngester:
    """Extracteur + chunker pour les PDFs onco-dermato."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else Path(config.DATA_DIR)
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract_text(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """Extrait le texte page par page."""
        doc = fitz.open(pdf_path)
        pages_text: List[Dict[str, Any]] = []
        try:
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text("text").strip()
                if text:
                    pages_text.append(
                        {
                            "text": text,
                            "page": page_num + 1,
                            "source": pdf_path.name,
                            "title": pdf_path.stem,
                        }
                    )
        finally:
            doc.close()
        return pages_text

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _count(self, text: str) -> int:
        return len(self.tokenizer.encode(text))

    def chunk_pages(
        self,
        pages: List[Dict[str, Any]],
        doc_type: str,
        chunk_size: Optional[int] = None,
        overlap: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Découpe les pages en chunks, en regroupant les paragraphes."""
        default_size, default_overlap = _CHUNK_PROFILE.get(
            doc_type, _CHUNK_PROFILE["unknown"]
        )
        chunk_size = chunk_size or default_size
        overlap = overlap or default_overlap

        chunks: List[Dict[str, Any]] = []

        for page in pages:
            paragraphs = [p.strip() for p in page["text"].split("\n\n") if p.strip()]
            current: List[str] = []
            current_tokens = 0

            def flush():
                nonlocal current, current_tokens
                if not current:
                    return
                chunk_text = "\n\n".join(current)
                cid = (
                    f"{page['source']}::p{page['page']}::{_short_hash(chunk_text)}"
                )
                chunks.append(
                    {
                        "text": chunk_text,
                        "source": page["source"],
                        "page": page["page"],
                        "chunk_id": cid,
                        "title": page["title"],
                        "doc_type": doc_type,
                        "lang": _safe_lang(chunk_text),
                        "tokens": current_tokens,
                    }
                )
                # Garder un overlap (en repartant des derniers paragraphes)
                kept: List[str] = []
                kept_tokens = 0
                for p in reversed(current):
                    pt = self._count(p)
                    if kept_tokens + pt > overlap:
                        break
                    kept.insert(0, p)
                    kept_tokens += pt
                current = kept
                current_tokens = kept_tokens

            for para in paragraphs:
                pt = self._count(para)

                # Paragraphe géant : on le sous-découpe en phrases.
                if pt > chunk_size:
                    sentences = [
                        s.strip() + "."
                        for s in re.split(r"(?<=[.!?])\s+", para)
                        if s.strip()
                    ]
                    for sent in sentences:
                        st = self._count(sent)
                        if current_tokens + st > chunk_size and current:
                            flush()
                        current.append(sent)
                        current_tokens += st
                    continue

                if current_tokens + pt > chunk_size and current:
                    flush()
                current.append(para)
                current_tokens += pt

            flush()

        return chunks

    # ------------------------------------------------------------------
    # Pipeline complet
    # ------------------------------------------------------------------

    def ingest_one(self, pdf_path: Path) -> List[Dict[str, Any]]:
        doc_type = detect_doc_type(pdf_path.name)
        pages = self.extract_text(pdf_path)
        chunks = self.chunk_pages(pages, doc_type=doc_type)
        return chunks

    def ingest(self) -> List[Dict[str, Any]]:
        all_chunks: List[Dict[str, Any]] = []
        pdf_files = sorted(self.data_dir.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(f"Aucun PDF dans {self.data_dir}")

        type_counter: Dict[str, int] = {}
        for pdf_path in pdf_files:
            chunks = self.ingest_one(pdf_path)
            doc_type = chunks[0]["doc_type"] if chunks else "empty"
            type_counter[doc_type] = type_counter.get(doc_type, 0) + 1
            all_chunks.extend(chunks)
            print(
                f"[ingestion] {pdf_path.name} → {len(chunks):4d} chunks "
                f"(type={doc_type})",
                flush=True,
            )

        print(
            f"[ingestion] Done. {len(pdf_files)} PDFs, {len(all_chunks)} chunks. "
            f"Breakdown: {type_counter}",
            flush=True,
        )
        return all_chunks


if __name__ == "__main__":
    ingester = PDFIngester()
    chunks = ingester.ingest()
    if chunks:
        print(f"\nExemple chunk[0]:\n{chunks[0]['text'][:200]}...")
