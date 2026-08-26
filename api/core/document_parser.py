"""DocumentParser — structure-aware PDF parsing via PyMuPDF only."""

from __future__ import annotations

from typing import Any, Optional

import fitz

from api.core.logger import get_logger, log_exception
from api.models.clinical_schemas import ParsedDocument, ParsedTable

logger = get_logger(__name__)

# Lines whose max span size is at least this multiple of the page body
# average are treated as section titles (replaces Unstructured Title).
_TITLE_SIZE_RATIO = 1.2
# Ignore tiny running headers/footers relative to body text.
_HEADER_FOOTER_SIZE_RATIO = 0.85
# Max chars for a title candidate (avoids classifying large-font paragraphs).
_TITLE_MAX_CHARS = 200


class DocumentParser:
    """Parse clinical PDFs into a structure-aware ``ParsedDocument``.

    Uses PyMuPDF for text, ``page.find_tables()`` for tables, and a font-size
    heuristic for section titles. Output schema matches the previous
    Unstructured-based parser so chunking and upload stay unchanged.
    """

    def parse(
        self,
        pdf_bytes: bytes,
        doc_type: Optional[str] = None,
    ) -> ParsedDocument:
        del doc_type  # retained for call-site compatibility
        try:
            return self._parse_pymupdf(pdf_bytes)
        except Exception as exc:
            logger.error("PyMuPDF PDF parse failed")
            log_exception(logger, exc)
            raise

    def _parse_pymupdf(self, pdf_bytes: bytes) -> ParsedDocument:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            tables: list[ParsedTable] = []
            section_titles: list[str] = []
            structured_blocks: list[dict[str, Any]] = []
            text_parts: list[str] = []

            for page_index, page in enumerate(doc):
                page_number = page_index + 1
                page_items = self._extract_page_items(page)

                for item in page_items:
                    kind = item["type"]
                    text = item["text"]
                    if not text.strip():
                        continue

                    if kind == "title":
                        section_titles.append(text)
                        structured_blocks.append({
                            "type": "title",
                            "text": text,
                            "page_number": page_number,
                        })
                        text_parts.append(text)
                    elif kind == "table":
                        html = item.get("html")
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
                        structured_blocks.append({
                            "type": "narrative",
                            "text": text,
                            "page_number": page_number,
                        })
                        text_parts.append(text)

            return ParsedDocument(
                full_text="\n\n".join(text_parts),
                tables=tables,
                section_titles=section_titles,
                page_count=doc.page_count,
                element_count=len(structured_blocks),
                has_tables=bool(tables),
                strategy="pymupdf",
                parse_method="pymupdf",
                structured_blocks=structured_blocks,
            )
        finally:
            doc.close()

    def _extract_page_items(self, page: fitz.Page) -> list[dict[str, Any]]:
        """Return reading-order blocks: title | table | narrative."""
        table_regions = self._extract_tables(page)
        body_avg = self._body_font_size(page)
        page_height = float(page.rect.height)

        text_items: list[dict[str, Any]] = []
        for line in self._iter_text_lines(page):
            if self._overlaps_any_table(line["bbox"], table_regions):
                continue

            size = line["size"]
            text = line["text"].strip()
            if not text:
                continue

            # Drop likely running headers/footers (small type at page edges).
            y0, y1 = line["bbox"][1], line["bbox"][3]
            near_edge = y0 < page_height * 0.06 or y1 > page_height * 0.94
            if (
                body_avg > 0
                and size < body_avg * _HEADER_FOOTER_SIZE_RATIO
                and near_edge
            ):
                continue

            is_title = (
                body_avg > 0
                and size >= body_avg * _TITLE_SIZE_RATIO
                and len(text) <= _TITLE_MAX_CHARS
                and "\n" not in text
            )
            text_items.append({
                "type": "title" if is_title else "narrative",
                "text": text,
                "y0": line["bbox"][1],
                "html": None,
            })

        items: list[dict[str, Any]] = text_items + [
            {
                "type": "table",
                "text": region["text"],
                "y0": region["bbox"][1],
                "html": region.get("html"),
            }
            for region in table_regions
        ]
        items.sort(key=lambda item: (item["y0"], 0 if item["type"] == "title" else 1))
        return items

    @staticmethod
    def _extract_tables(page: fitz.Page) -> list[dict[str, Any]]:
        """Extract tables via pymupdf find_tables (no ML)."""
        regions: list[dict[str, Any]] = []
        try:
            finder = page.find_tables()
        except Exception:
            return regions

        tables = getattr(finder, "tables", None) or []
        for tab in tables:
            try:
                rows = tab.extract() or []
            except Exception:
                continue

            cleaned_rows: list[list[str]] = []
            for row in rows:
                cells = [("" if c is None else str(c)).strip() for c in row]
                if any(cells):
                    cleaned_rows.append(cells)
            if not cleaned_rows:
                continue

            plain_lines = [" | ".join(cells) for cells in cleaned_rows]
            plain = "\n".join(plain_lines)
            # Markers the upload/chunk pipeline associates with table blocks.
            marked = f"TABLE START\n{plain}\nTABLE END"

            html_rows = []
            for cells in cleaned_rows:
                tds = "".join(f"<td>{_escape_html(c)}</td>" for c in cells)
                html_rows.append(f"<tr>{tds}</tr>")
            html = "<table>" + "".join(html_rows) + "</table>"

            bbox = getattr(tab, "bbox", None) or (0.0, 0.0, 0.0, 0.0)
            regions.append({
                "bbox": tuple(float(x) for x in bbox),
                "text": marked,
                "html": html,
            })
        return regions

    @staticmethod
    def _body_font_size(page: fitz.Page) -> float:
        sizes: list[float] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    size = float(span.get("size") or 0.0)
                    if text and size > 0:
                        sizes.append(size)
        if not sizes:
            return 0.0
        sizes.sort()
        return sizes[len(sizes) // 2]

    @staticmethod
    def _iter_text_lines(page: fitz.Page) -> list[dict[str, Any]]:
        lines_out: list[dict[str, Any]] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans") or []
                if not spans:
                    continue
                parts = [(span.get("text") or "") for span in spans]
                text = "".join(parts).strip()
                if not text:
                    continue
                max_size = max(float(span.get("size") or 0.0) for span in spans)
                bbox = line.get("bbox") or (0.0, 0.0, 0.0, 0.0)
                lines_out.append({
                    "text": text,
                    "size": max_size,
                    "bbox": tuple(float(x) for x in bbox),
                })
        return lines_out

    @staticmethod
    def _overlaps_any_table(
        bbox: tuple[float, float, float, float],
        tables: list[dict[str, Any]],
        iou_threshold: float = 0.3,
    ) -> bool:
        for region in tables:
            if _bbox_overlap_ratio(bbox, region["bbox"]) >= iou_threshold:
                return True
        return False


def _bbox_overlap_ratio(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    if area_a <= 0:
        return 0.0
    return inter / area_a


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
