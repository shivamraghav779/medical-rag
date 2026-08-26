# Clinical Decision Support RAG Platform

Multi-agent clinical RAG for healthcare professionals: emergency detection, query-type routing, authority-weighted retrieval, drug interaction checks, lab interpretation, and faithfulness scoring.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # or fill .env with API keys
uvicorn api.main:app --reload
```

Open `/docs` for the interactive API.

## Observability (LangSmith)

Pipeline steps are optionally traced with LangSmith when `LANGSMITH_API_KEY` is set. Tracing is wrapped in a conditional decorator — if the key is missing, the API runs unchanged.

Configure in `.env`:

```
LANGSMITH_API_KEY=
LANGCHAIN_PROJECT=rag-platform-clinical
```

Traced surfaces include LLM calls, embeddings, retrieval, drug tools, and nested service calls inside the Orchestrator.

<!-- Screenshot placeholder: drop a LangSmith trace screenshot here -->
**[LangSmith trace screenshot placeholder]**

## Document Intelligence (Unstructured)

Clinical PDFs (guidelines, monographs, lab references) are table-heavy. Raw PyMuPDF flattens tables and loses section structure. Uploads go through `DocumentParser` (Unstructured) first:

- `hi_res` for clinical guidelines, drug monographs, and lab references
- `fast` for research papers
- `auto` otherwise

Tables are kept intact for chunking; titles become section boundaries. If Unstructured fails, the parser **silently falls back to PyMuPDF** and records `parse_method` on document metadata so uploads never fail because of parser choice.

## Evaluation (RAGAS)

Offline evaluation lives under `scripts/` (not on the request path):

```bash
uvicorn api.main:app --reload --port 8000
python scripts/evaluate.py
```

Metrics (placeholders until you run the script):

| Metric | Score |
|---|---|
| faithfulness | — |
| answer_relevancy | — |
| context_precision | — |
| context_recall | — |
| answer_correctness | — |

Results: `scripts/eval_results.json` and `scripts/EVAL_REPORT.md`.

## Architecture Decisions

**Built in-house**

- Multi-agent Orchestrator with clinical query-type routing
- Redis key registry, typed exception hierarchy, JSON structured logging with alert dedup
- Authority + recency weighted reranking for clinical document taxonomy
- Emergency / drug interaction / lab reference agents
- Session clinical context (specialty, age group) influencing prompts and filters

**External tools (and why)**

- **Groq** — fast JSON + streaming LLM for analysis, generation, faithfulness
- **Cohere** — embeddings + rerank quality
- **Pinecone** — dense vector index with metadata filters (`doc_type`, authority)
- **Upstash Redis** — caches, session context, drug/lab graphs, analytics
- **LangSmith** — optional run traces without coupling the API to a vendor SDK path
- **Unstructured** — structure-aware PDF parsing for table-heavy clinical docs
- **RAGAS** — offline quality metrics for README / reviewers

Generic “chat over docs” RAG is intentionally insufficient here: retrieval behavior, disclaimers, and scoring are domain-specific to clinical decision support.

## API surface

- `POST /api/upload` — PDF ingest with clinical metadata
- `POST /api/chat` — SSE multi-agent pipeline
- `GET /api/documents` / `DELETE /api/documents/{doc_id}`
- `GET /api/analytics` — Redis-backed query/drug/emergency/faithfulness stats
- `GET /health`
