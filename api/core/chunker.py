import re
from typing import Optional

from api.models.clinical_schemas import ParsedDocument
from api.models.schemas import Chunk

from api.core.constants import (
    CHARS_PER_PAGE_ESTIMATE,
    MIN_CHUNK_TOKENS,
    NARRATIVE_TOKEN_LIMIT,
    OVERLAP_TOKENS,
    TABLE_TOKEN_LIMIT,
)

def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _make_chunk(
    doc_name: str,
    chunk_index: int,
    text: str,
    char_start: int,
    char_end: int,
    page_number: Optional[int] = None,
) -> Chunk:
    return Chunk(
        chunk_id=f"{doc_name}-chunk-{chunk_index}",
        doc_name=doc_name,
        page_number=page_number if page_number and page_number > 0 else max(1, char_start // CHARS_PER_PAGE_ESTIMATE),
        char_start=max(0, char_start),
        char_end=max(char_start, char_end),
        text=text.strip(),
    )


def _flush_sentences(
    sentences: list[str],
    doc_name: str,
    chunk_index: int,
    char_position: int,
    page_number: Optional[int] = None,
) -> tuple[list[Chunk], int, int, list[str], int]:
    """Emit sentence-based chunks from an accumulated sentence buffer.

    Returns (new_chunks, next_chunk_index, next_char_position, overlap_sentences, overlap_tokens).
    """
    chunks: list[Chunk] = []
    current_chunk: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = estimate_tokens(sentence)

        if current_tokens + sentence_tokens > NARRATIVE_TOKEN_LIMIT and current_chunk:
            chunk_text_str = " ".join(current_chunk)
            if estimate_tokens(chunk_text_str) >= MIN_CHUNK_TOKENS:
                chunks.append(_make_chunk(
                    doc_name,
                    chunk_index,
                    chunk_text_str,
                    char_position - len(chunk_text_str),
                    char_position,
                    page_number,
                ))
                chunk_index += 1

            overlap: list[str] = []
            overlap_tokens = 0
            for s in reversed(current_chunk):
                t = estimate_tokens(s)
                if overlap_tokens + t <= OVERLAP_TOKENS:
                    overlap.insert(0, s)
                    overlap_tokens += t
                else:
                    break
            current_chunk = overlap
            current_tokens = overlap_tokens

        current_chunk.append(sentence)
        current_tokens += sentence_tokens
        char_position += len(sentence) + 1

    return chunks, chunk_index, char_position, current_chunk, current_tokens


def chunk_text(text: str, doc_name: str) -> list[Chunk]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current_chunk = []
    current_tokens = 0
    char_position = 0
    chunk_index = 0

    for sentence in sentences:
        sentence_tokens = estimate_tokens(sentence)

        if current_tokens + sentence_tokens > NARRATIVE_TOKEN_LIMIT and current_chunk:
            chunk_text_str = " ".join(current_chunk)
            if estimate_tokens(chunk_text_str) >= MIN_CHUNK_TOKENS:
                chunks.append(Chunk(
                    chunk_id=f"{doc_name}-chunk-{chunk_index}",
                    doc_name=doc_name,
                    page_number=max(1, char_position // CHARS_PER_PAGE_ESTIMATE),
                    char_start=max(0, char_position - len(chunk_text_str)),
                    char_end=char_position,
                    text=chunk_text_str.strip(),
                ))
                chunk_index += 1

            overlap = []
            overlap_tokens = 0
            for s in reversed(current_chunk):
                t = estimate_tokens(s)
                if overlap_tokens + t <= OVERLAP_TOKENS:
                    overlap.insert(0, s)
                    overlap_tokens += t
                else:
                    break
            current_chunk = overlap
            current_tokens = overlap_tokens

        current_chunk.append(sentence)
        current_tokens += sentence_tokens
        char_position += len(sentence) + 1

    if current_chunk:
        chunk_text_str = " ".join(current_chunk)
        if estimate_tokens(chunk_text_str) >= MIN_CHUNK_TOKENS:
            chunks.append(Chunk(
                chunk_id=f"{doc_name}-chunk-{chunk_index}",
                doc_name=doc_name,
                page_number=max(1, char_position // CHARS_PER_PAGE_ESTIMATE),
                char_start=max(0, char_position - len(chunk_text_str)),
                char_end=char_position,
                text=chunk_text_str.strip(),
            ))

    return chunks


def _split_table_by_rows(table_text: str) -> list[str]:
    """Split a table on newlines without cutting mid-row."""
    rows = [row for row in table_text.splitlines() if row.strip()]
    if not rows:
        return [table_text] if table_text.strip() else []

    parts: list[str] = []
    current_rows: list[str] = []
    current_tokens = 0

    for row in rows:
        row_tokens = estimate_tokens(row)
        if (
            current_rows
            and current_tokens + row_tokens > TABLE_TOKEN_LIMIT
        ):
            parts.append("\n".join(current_rows))
            current_rows = [row]
            current_tokens = row_tokens
        else:
            current_rows.append(row)
            current_tokens += row_tokens

    if current_rows:
        parts.append("\n".join(current_rows))
    return parts


def _emit_table_chunks(
    table_text: str,
    doc_name: str,
    chunk_index: int,
    char_position: int,
    page_number: Optional[int],
    section_prefix: str = "",
) -> tuple[list[Chunk], int, int]:
    chunks: list[Chunk] = []
    prefixed = f"{section_prefix}\n{table_text}".strip() if section_prefix else table_text
    if estimate_tokens(prefixed) <= TABLE_TOKEN_LIMIT:
        pieces = [prefixed]
    else:
        # Keep section title on the first row-group only.
        row_parts = _split_table_by_rows(table_text)
        pieces = []
        for i, part in enumerate(row_parts):
            if i == 0 and section_prefix:
                pieces.append(f"{section_prefix}\n{part}".strip())
            else:
                pieces.append(part)

    for piece in pieces:
        if not piece.strip():
            continue
        end = char_position + len(piece)
        chunks.append(_make_chunk(
            doc_name, chunk_index, piece, char_position, end, page_number
        ))
        chunk_index += 1
        char_position = end + 1

    return chunks, chunk_index, char_position


def _finalize_narrative_buffer(
    buffer_sentences: list[str],
    doc_name: str,
    chunk_index: int,
    char_position: int,
    page_number: Optional[int],
    section_prefix: str,
) -> tuple[list[Chunk], int, int]:
    if not buffer_sentences:
        return [], chunk_index, char_position

    text = " ".join(buffer_sentences).strip()
    if section_prefix and text:
        text = f"{section_prefix}\n{text}".strip()
    elif section_prefix and not text:
        return [], chunk_index, char_position

    sentences = re.split(r"(?<=[.!?])\s+", text) if text else []
    new_chunks, chunk_index, char_position, remainder, _ = _flush_sentences(
        sentences, doc_name, chunk_index, char_position, page_number
    )
    if remainder:
        chunk_text_str = " ".join(remainder).strip()
        if chunk_text_str and estimate_tokens(chunk_text_str) >= MIN_CHUNK_TOKENS:
            new_chunks.append(_make_chunk(
                doc_name,
                chunk_index,
                chunk_text_str,
                char_position - len(chunk_text_str),
                char_position,
                page_number,
            ))
            chunk_index += 1
        elif chunk_text_str and not new_chunks:
            # Keep short leftover narrative if it's the only content for a section.
            new_chunks.append(_make_chunk(
                doc_name,
                chunk_index,
                chunk_text_str,
                char_position - len(chunk_text_str),
                char_position,
                page_number,
            ))
            chunk_index += 1
    return new_chunks, chunk_index, char_position


def chunk_structured(parsed: ParsedDocument, doc_name: str) -> list[Chunk]:
    """Structure-aware chunking for ParsedDocument.

    - Tables are never split mid-row (whole table if under ~800 tokens, else by rows).
    - New chunks prefer to start at section titles.
    - Narrative text uses the existing sentence-based chunking approach.
    - ``doc_type`` / authority fields are left unset for the upload router to fill.
    """
    blocks = parsed.structured_blocks
    if not blocks:
        return chunk_text(parsed.full_text or "", doc_name)

    chunks: list[Chunk] = []
    chunk_index = 0
    char_position = 0
    section_title = ""
    narrative_buffer: list[str] = []
    narrative_page: Optional[int] = None

    def flush_narrative() -> None:
        nonlocal chunks, chunk_index, char_position, narrative_buffer, narrative_page
        new_chunks, chunk_index, char_position = _finalize_narrative_buffer(
            narrative_buffer,
            doc_name,
            chunk_index,
            char_position,
            narrative_page,
            section_title,
        )
        chunks.extend(new_chunks)
        narrative_buffer = []
        narrative_page = None

    for block in blocks:
        block_type = (block.get("type") or "narrative").lower()
        text = (block.get("text") or "").strip()
        page_number = block.get("page_number")
        if isinstance(page_number, int) and page_number <= 0:
            page_number = None

        if not text and block_type != "title":
            continue

        if block_type == "title":
            flush_narrative()
            section_title = text
            continue

        if block_type == "table":
            flush_narrative()
            table_chunks, chunk_index, char_position = _emit_table_chunks(
                text,
                doc_name,
                chunk_index,
                char_position,
                page_number,
                section_prefix=section_title,
            )
            chunks.extend(table_chunks)
            # Table consumed the section prefix; subsequent narrative still
            # prefers the current section title as context.
            continue

        # narrative / default
        if narrative_page is None and page_number is not None:
            narrative_page = page_number
        sentences = re.split(r"(?<=[.!?])\s+", text)
        narrative_buffer.extend(s for s in sentences if s.strip())

        # Opportunistically flush oversized narrative buffers mid-section.
        if estimate_tokens(" ".join(narrative_buffer)) > NARRATIVE_TOKEN_LIMIT * 2:
            flush_narrative()

    flush_narrative()

    if not chunks and (parsed.full_text or "").strip():
        return chunk_text(parsed.full_text, doc_name)

    return chunks
