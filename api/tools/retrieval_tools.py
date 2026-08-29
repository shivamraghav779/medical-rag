"""LLM-callable general-purpose retrieval tools — PubMed literature search
and unrestricted knowledge-base search.

Thin wrappers over WebSearchService / RetrievalService, dependencies passed
in as parameters. No new business logic, no new prompts, not called by the
live Orchestrator pipeline — see api/tools/clinical_tools.py module docstring
for the same disclaimer.
"""

from __future__ import annotations

from typing import Optional

from api.models.schemas import RetrievedChunk
from api.services.retrieval_service import RetrievalService
from api.services.web_search_service import WebSearchService


async def search_pubmed(
    web_search_service: WebSearchService,
    query: str,
    max_results: int = 5,
) -> list[dict]:
    """Search PubMed for recent peer-reviewed literature on a clinical topic.

    Use this to supplement the internal knowledge base with current research
    when the internal guidelines/monographs don't fully answer the question.

    Args:
        query: The clinical topic or question to search PubMed for.
        max_results: Maximum number of PubMed results to return.

    Returns:
        A list of dicts with title, snippet, url, and source ("pubmed").
    """
    results = await web_search_service.search_pubmed(query, max_results=max_results)
    return [item.model_dump() for item in results]


async def search_knowledge_base(
    retrieval_service: RetrievalService,
    query: str,
    top_k: int = 5,
    doc_filter: Optional[list[str]] = None,
) -> list[dict]:
    """Search the full internal clinical knowledge base (guidelines,
    monographs, protocols, lab references, research papers) with no
    document-type restriction.

    Use this for a general clinical question that doesn't fit a more
    specific tool (guidelines, drug monograph, lab range, treatment
    protocol, diagnostic criteria).

    Args:
        query: The clinical question to search the knowledge base for.
        top_k: Maximum number of passages to return.
        doc_filter: Optional list of specific document names to restrict to.

    Returns:
        A list of chunks, each with doc_name, page_number, text, score,
        doc_type, authority_level, and publication_year.
    """
    chunks: list[RetrievedChunk] = await retrieval_service.hybrid_retrieve(
        query, top_k, doc_filter=doc_filter
    )
    return [chunk.model_dump() for chunk in chunks]
