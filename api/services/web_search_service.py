"""WebSearchService — PubMed literature search for supplemental clinical context."""

from __future__ import annotations

from typing import Any

import httpx

from api.core.logger import get_logger, log_exception
from api.models.schemas import WebSearchResult

logger = get_logger(__name__)

PUBMED_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class WebSearchService:
    async def search_pubmed(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        query = (query or "").strip()
        if not query:
            return []

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                search_resp = await client.get(
                    f"{PUBMED_EUTILS}/esearch.fcgi",
                    params={
                        "db": "pubmed",
                        "term": query,
                        "retmax": max_results,
                        "retmode": "json",
                        "sort": "relevance",
                    },
                )
                search_resp.raise_for_status()
                payload = search_resp.json()
                ids: list[str] = payload.get("esearchresult", {}).get("idlist", [])
                if not ids:
                    return []

                summary_resp = await client.get(
                    f"{PUBMED_EUTILS}/esummary.fcgi",
                    params={
                        "db": "pubmed",
                        "id": ",".join(ids),
                        "retmode": "json",
                    },
                )
                summary_resp.raise_for_status()
                summary_payload = summary_resp.json()
                result_map: dict[str, Any] = summary_payload.get("result", {})

                results: list[WebSearchResult] = []
                for pmid in ids:
                    item = result_map.get(pmid)
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "Untitled").strip()
                    source = str(item.get("source") or "PubMed").strip()
                    pubdate = str(item.get("pubdate") or "").strip()
                    snippet_parts = [p for p in (source, pubdate) if p]
                    snippet = " · ".join(snippet_parts) if snippet_parts else "PubMed article"
                    results.append(
                        WebSearchResult(
                            title=title,
                            snippet=snippet,
                            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                            source="pubmed",
                        )
                    )
                return results
        except Exception as exc:
            log_exception(logger, exc)
            return []

    @staticmethod
    def format_context(results: list[WebSearchResult]) -> str:
        if not results:
            return ""
        lines = []
        for idx, item in enumerate(results, start=1):
            lines.append(
                f"[Web-{idx}] {item.title}\n"
                f"Source: {item.snippet}\n"
                f"URL: {item.url}"
            )
        return "\n\n".join(lines)
