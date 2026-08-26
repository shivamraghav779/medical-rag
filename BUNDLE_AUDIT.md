<!--
BUNDLE SIZE AUDIT — heavy package imports under api/
Generated before any remediation changes.

=============================================================================
HEAVY PACKAGE IMPORT SCAN (api/ only)
=============================================================================

torch
  — NOT IMPORTED anywhere under api/

torchvision
  — NOT IMPORTED anywhere under api/

transformers
  — NOT IMPORTED anywhere under api/

sentence_transformers
  — NOT IMPORTED anywhere under api/

unstructured
  api/core/document_parser.py:50
    from unstructured.partition.pdf import partition_pdf
    (lazy import inside DocumentParser._parse_unstructured)
  RELATED (string references, not imports):
    api/core/document_parser.py:31   — call to self._parse_unstructured(...)
    api/core/document_parser.py:49   — def _parse_unstructured(...)
    api/core/document_parser.py:129  — parse_method="unstructured"
    api/routers/upload.py:128        — parse_method = "unstructured" (default string)
    api/models/clinical_schemas.py:150 — parse_method: str = "unstructured"

detectron2
  — NOT IMPORTED anywhere under api/
  (transitive via unstructured[pdf] hi_res only)

paddlepaddle
  — NOT IMPORTED anywhere under api/
  (transitive via unstructured[pdf] hi_res only)

pdf2image
  — NOT IMPORTED anywhere under api/

pytesseract
  — NOT IMPORTED anywhere under api/

cv2
  — NOT IMPORTED anywhere under api/

PIL
  — NOT IMPORTED anywhere under api/

sklearn
  — NOT IMPORTED anywhere under api/
  (BM25 uses rank_bm25 — already present)

numpy
  — NOT IMPORTED anywhere under api/
  — not currently in requirements.txt; keep if added later, do not remove if present
  — decision: no action needed (not a dependency today)

scipy
  — NOT IMPORTED anywhere under api/

fitz / pymupdf (KEEP — lightweight replacement target)
  api/core/document_parser.py:8
    import fitz
  Also used in _parse_pymupdf / _pymupdf_page_count (fallback path today)

rank_bm25 (KEEP — already covers sparse retrieval)
  api/tools/retrieval_tools.py:8
    from rank_bm25 import BM25Okapi

=============================================================================
LOCAL ML MODEL CACHE / DOWNLOAD PATTERNS (entire project)
=============================================================================

SentenceTransformer(...)     — NONE
AutoModel.from_pretrained    — NONE
pipeline(... model=...)      — NONE
HuggingFace cache dirs       — NONE
Dockerfile model downloads   — NONE (no Dockerfile found)

Embeddings already use Cohere API:
  api/core/embedder.py
  api/services/embedding_service.py
  api/tools/embedding_tools.py

LLM already uses Groq API:
  api/services/llm_service.py
  api/tools/llm_tools.py

Rerank already uses Cohere API:
  api/services/retrieval_service.py
  api/tools/retrieval_tools.py

=============================================================================
requirements.txt HEAVY / NON-RUNTIME ENTRIES
=============================================================================

DIRECT HEAVY (must remove):
  unstructured[pdf]>=0.15.0     # line 29 — pulls torch/detectron2/paddle chain
                                # sole consumer: document_parser.py:50

NOT IMPORTED IN api/ (offline / test only — remove from deploy requirements):
  ragas>=0.1.0                  # line 32 — eval pipeline only
  datasets>=2.19.0              # line 33 — ragas transitive companion
  pytest>=8.0.0                 # line 36 — test suite
  pytest-asyncio>=0.24.0        # line 37 — test suite

ALREADY LIGHTWEIGHT / KEEP:
  PyMuPDF==1.24.14              # fitz — primary parser after fix
  rank-bm25==0.2.2              # BM25 — no sklearn needed
  cohere==5.5.0                 # embed + rerank API
  groq>=0.11.0                  # LLM API
  pinecone-client==4.1.0
  fastapi / uvicorn / pydantic / sqlalchemy / etc. (direct imports)

NOTE ON TABLE MARKERS:
  Chunker (api/core/chunker.py) does NOT look for "TABLE START"/"TABLE END" text.
  It expects structured_blocks with type == "table" (and type == "title").
  Replacement parser must emit the same ParsedDocument / structured_blocks schema.

=============================================================================
ROOT CAUSE OF ~5848MB BUNDLE
=============================================================================

unstructured[pdf] is the only heavy package still listed in requirements.txt and
still imported (lazy) under api/. Its [pdf] extra pulls torch + OCR/layout ML
stacks. No other audited heavy package is directly imported in api/.

Estimated post-fix size: removing unstructured[pdf] + ragas/datasets/pytest
should drop the install well under 400MB (PyMuPDF ~20MB; remaining API deps
are typically tens of MB each).
-->

# Bundle Audit Summary

## Verdict

The Vercel 5848MB bundle is caused almost entirely by **`unstructured[pdf]`** in `requirements.txt`. No torch / sentence-transformers / sklearn / numpy imports remain under `api/`.

## Heavy imports found

| Package | Files / lines | Action |
|---------|---------------|--------|
| `unstructured` | `api/core/document_parser.py:50` | Replace with pymupdf |
| `fitz` (PyMuPDF) | `api/core/document_parser.py:8` | Keep; make primary path |
| `rank_bm25` | `api/tools/retrieval_tools.py:8` | Keep |
| torch / torchvision / transformers / sentence_transformers | none | N/A (already gone from code) |
| detectron2 / paddlepaddle / pdf2image / pytesseract / cv2 / PIL / sklearn / scipy / numpy | none | N/A |

## Model downloads / local caches

None found. Embeddings = Cohere, LLM = Groq, rerank = Cohere.

## Next step (awaiting confirmation)

2. Rewrite `api/core/document_parser.py` to use pymupdf only (tables via `page.find_tables()`, titles via font-size heuristic), preserving `ParsedDocument` schema.
