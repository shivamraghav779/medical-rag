"""DocumentParser — Unstructured PDF parse with PyMuPDF fallback."""

from __future__ import annotations

import tempfile
from typing import Any, Optional

import fitz

from api.core.constants import FAST_DOC_TYPES, HI_RES_DOC_TYPES
from api.core.logger import get_logger, log_exception
from api.models.clinical_schemas import ParsedDocument, ParsedTable

logger = get_logger(__name__)


class DocumentParser:
    """Parse clinical PDFs into a structure-aware ``ParsedDocument``.

    Never raises for Unstructured failures — falls back to PyMuPDF and
    records ``parse_method`` / ``strategy`` on the result.
    """

    def parse(
        self,
        pdf_bytes: bytes,
        doc_type: Optional[str] = None,
    ) -> ParsedDocument:
        strategy = self._select_strategy(doc_type)
        try:
            return self._parse_unstructured(pdf_bytes, strategy)
        except Exception as exc:
            logger.warning(
                "Unstructured PDF parse failed; falling back to PyMuPDF",
                extra={"doc_type": doc_type, "strategy": strategy},
            )
            log_exception(logger, exc)
            return self._parse_pymupdf(pdf_bytes)

    @staticmethod
    def _select_strategy(doc_type: Optional[str]) -> str:
        normalized = (doc_type or "").strip().lower()
        if normalized in HI_RES_DOC_TYPES:
            return "hi_res"
        if normalized in FAST_DOC_TYPES:
            return "fast"
        return "auto"

    def _parse_unstructured(self, pdf_bytes: bytes, strategy: str) -> ParsedDocument:
        from unstructured.partition.pdf import partition_pdf

        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()
            elements = partition_pdf(
                filename=tmp.name,
                strategy=strategy,
                include_page_breaks=False,
            )

        tables: list[ParsedTable] = []
        section_titles: list[str] = []
        structured_blocks: list[dict[str, Any]] = []
        text_parts: list[str] = []
        page_numbers: set[int] = set()
        element_count = 0

        for el in elements:
            category = getattr(el, "category", None) or type(el).__name__
            cat_lower = str(category).lower()

            # Drop running headers/footers.
            if cat_lower in ("header", "footer"):
                continue

            text = (getattr(el, "text", None) or str(el) or "").strip()
            if not text:
                continue

            element_count += 1
            meta = getattr(el, "metadata", None)
            page_number = getattr(meta, "page_number", None) if meta is not None else None
            if isinstance(page_number, int) and page_number > 0:
                page_numbers.add(page_number)

            if cat_lower == "title":
                section_titles.append(text)
                structured_blocks.append({
                    "type": "title",
                    "text": text,
                    "page_number": page_number,
                })
                text_parts.append(text)
            elif cat_lower == "table":
                html = None
                if meta is not None:
                    html = getattr(meta, "text_as_html", None)
                tables.append(ParsedTable(
                    page_number=page_number,
                    text=text,
                    html=html,
                ))
                structured_blocks.append({
                    "type": "table",
                    "text": text,
                    "html": html,
                    "page_number": page_number,
                })
                text_parts.append(text)
            else:
                # NarrativeText, ListItem, and other body content.
                structured_blocks.append({
                    "type": "narrative",
                    "text": text,
                    "page_number": page_number,
                })
                text_parts.append(text)

        page_count = max(page_numbers) if page_numbers else self._pymupdf_page_count(pdf_bytes)

        return ParsedDocument(
            full_text="\n\n".join(text_parts),
            tables=tables,
            section_titles=section_titles,
            page_count=page_count,
            element_count=element_count,
            has_tables=bool(tables),
            strategy=strategy,
            parse_method="unstructured",
            structured_blocks=structured_blocks,
        )

    def _parse_pymupdf(self, pdf_bytes: bytes) -> ParsedDocument:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page_texts: list[str] = []
            structured_blocks: list[dict[str, Any]] = []
            for page_index, page in enumerate(doc):
                text = (page.get_text("text") or "").strip()
                if not text:
                    continue
                page_number = page_index + 1
                page_texts.append(text)
                structured_blocks.append({
                    "type": "narrative",
                    "text": text,
                    "page_number": page_number,
                })
            full_text = "\n\n".join(page_texts)
            return ParsedDocument(
                full_text=full_text,
                tables=[],
                section_titles=[],
                page_count=doc.page_count,
                element_count=len(structured_blocks),
                has_tables=False,
                strategy="pymupdf_fallback",
                parse_method="pymupdf",
                structured_blocks=structured_blocks,
            )
        finally:
            doc.close()

    @staticmethod
    def _pymupdf_page_count(pdf_bytes: bytes) -> int:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                return int(doc.page_count)
            finally:
                doc.close()
        except Exception:
            return 0
