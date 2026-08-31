#!/usr/bin/env python3
"""Evaluate this clinical RAG pipeline with `ragval` (no running API needed).

Builds the real retrieval + generation stack directly (same wiring as
api/core/dependencies.py, minus the DB / handoff loop), answers a set of
questions, then scores each answer with ragval's clinical domain profile +
pipeline-layer diagnosis. Complements scripts/evaluate.py (RAGAS).

    pip install ragval
    python scripts/ragval_eval.py                                 # first 5 of eval_data.json
    python scripts/ragval_eval.py --data scripts/ragval_cases.json --all
    python scripts/ragval_eval.py -n 3 --full-metrics

Cases JSON: list of {question, ground_truth, category?}. Default judge model is
the app's Groq model; set RAGVAL_JUDGE_MODEL for a faster one.

Writes scripts/ragval_results.json and scripts/ragval_report.md (both gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    for name in (".env.local", ".env"):
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)


_load_env()

from api.agents.generator import GeneratorAgent  # noqa: E402
from api.core.config import get_settings  # noqa: E402
from api.services.embedding_service import EmbeddingService  # noqa: E402
from api.services.llm_service import LLMService  # noqa: E402
from api.services.redis_service import RedisService  # noqa: E402
from api.services.retrieval_service import RetrievalService  # noqa: E402

from ragval import RAGEvaluator  # noqa: E402

DATA_PATH = ROOT / "scripts" / "eval_data.json"
RESULTS_PATH = ROOT / "scripts" / "ragval_results.json"
REPORT_PATH = ROOT / "scripts" / "ragval_report.md"

RERANK_TOP_K = 6
# ragval judge model (LiteLLM string). Reuses the app's Groq key.
# Override for a faster judge, e.g.
#   RAGVAL_JUDGE_MODEL=cohere/command-a-03-2025   (uses COHERE_API_KEY)
JUDGE_MODEL = os.environ.get("RAGVAL_JUDGE_MODEL", "groq/openai/gpt-oss-120b")


def _build_stack():
    import cohere
    from groq import AsyncGroq
    from pinecone import Pinecone
    from upstash_redis.asyncio import Redis

    settings = get_settings()
    redis_client = Redis(
        url=settings.upstash_redis_rest_url,
        token=settings.upstash_redis_rest_token,
    )
    cohere_client = cohere.AsyncClient(settings.cohere_api_key)
    groq_client = AsyncGroq(api_key=settings.groq_api_key)
    pinecone_index = Pinecone(api_key=settings.pinecone_api_key).Index(
        settings.pinecone_index
    )

    redis_service = RedisService(redis_client)
    embedding_service = EmbeddingService(cohere_client, redis_service)
    retrieval_service = RetrievalService(
        pinecone_index=pinecone_index,
        cohere_client=cohere_client,
        redis_service=redis_service,
        embedding_service=embedding_service,
    )
    llm_service = LLMService(groq_client, settings)
    return retrieval_service, llm_service


async def answer_one(retrieval, llm, question: str) -> dict:
    chunks = await retrieval.hybrid_retrieve(question, top_k=RERANK_TOP_K)
    generator = GeneratorAgent(llm_service=llm)
    result = await generator.run(query=question, chunks=chunks, conversation_history=[])
    return {
        "answer": result.data.get("answer", ""),
        "contexts": [c.text for c in chunks if getattr(c, "text", "")],
        "chunk_meta": [
            {
                "doc_name": c.doc_name,
                "authority_level": c.authority_level,
                "doc_type": c.doc_type,
                "year": c.publication_year,
            }
            for c in chunks
        ],
        "native_faithfulness": getattr(result.data.get("faithfulness"), "score", None),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5, help="number of cases (default 5)")
    ap.add_argument("--all", action="store_true", help="run every case")
    ap.add_argument("--data", default=str(DATA_PATH), help="path to cases JSON")
    ap.add_argument("--full-metrics", action="store_true",
                    help="run all ~38 metrics (heavy; may hit free-tier rate limits)")
    ap.add_argument("--concurrency", type=int, default=1)
    args = ap.parse_args()

    cases = json.loads(Path(args.data).read_text())
    if not args.all:
        cases = cases[: args.n]

    retrieval, llm = _build_stack()

    print(f"Answering {len(cases)} question(s) through the real pipeline...\n")
    rows = []
    for i, case in enumerate(cases, start=1):
        q = case["question"]
        print(f"[{i}/{len(cases)}] {q[:80]}")
        try:
            out = await answer_one(retrieval, llm, q)
        except Exception as exc:  # noqa: BLE001
            print(f"    pipeline error: {exc}")
            out = {"answer": "", "contexts": [], "chunk_meta": [], "native_faithfulness": None}
        rows.append({
            "question": q,
            "ground_truth": case.get("ground_truth"),
            "category": case.get("category"),
            **out,
        })

    usable = [r for r in rows if r["answer"] and r["contexts"]]
    print(f"\n{len(usable)}/{len(rows)} rows have an answer + contexts. Scoring with ragval...\n")

    evaluator = RAGEvaluator(
        model=JUDGE_MODEL,
        domain="clinical",
        metrics="all" if args.full_metrics else None,
        run_domain_metrics=args.full_metrics,
        timeout=90, max_concurrency=2,
        max_tokens=1200,
    )
    qa_pairs = [
        {
            "question": r["question"],
            "answer": r["answer"],
            "contexts": r["contexts"],
            "ground_truth": r["ground_truth"],
        }
        for r in usable
    ]
    batch = await evaluator.batch_evaluate(qa_pairs, concurrency=args.concurrency)

    print("=" * 78)
    print(batch.report())
    print("=" * 78)

    detail = []
    for r, res in zip(usable, batch.results):
        d = res.diagnosis
        dm = {k: (v.score if v and v.score is not None else None)
              for k, v in res.domain_metrics.items()}
        print(f"\n### [{res.verdict}] {r['category']}: {r['question'][:70]}")
        print(f"  overall {res.overall_score:.3f} | native faithfulness "
              f"{r['native_faithfulness']} | ragval faithfulness "
              f"{res.faithfulness.score if res.faithfulness else None}")
        print(f"  hallucination_detected: {res.hallucination_detected}")
        print(f"  clinical metrics: {dm}")
        if d:
            print(f"  diagnosis -> layer={d.failed_layer} conf={d.confidence}")
            print(f"    fix: {d.suggested_fix[:200]}")
        detail.append({
            "question": r["question"],
            "category": r["category"],
            "verdict": res.verdict,
            "overall_score": res.overall_score,
            "native_faithfulness": r["native_faithfulness"],
            "ragval": res.to_dict(),
        })

    RESULTS_PATH.write_text(json.dumps({"rows": rows, "detail": detail}, indent=2, default=str))
    REPORT_PATH.write_text(
        "# ragval report — clinical RAG pipeline\n\n"
        + batch.report()
        + "\n\n## Per-case diagnosis\n\n"
        + "\n".join(
            f"- **[{x['verdict']}] {x['category']}** — {x['question'][:90]}  \n"
            f"  overall {x['overall_score']:.3f}; "
            f"layer: {x['ragval'].get('diagnosis', {}).get('failed_layer')}"
            for x in detail
        )
        + "\n"
    )
    print(f"\nWrote {RESULTS_PATH.relative_to(ROOT)} and {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
