"""Upload router — PDF ingest with clinical document metadata."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.core.chunker import chunk_structured, chunk_text
from api.core.config import Settings
from api.core.dependencies import (
    DocumentParserDep,
    EmbeddingServiceDep,
    RedisServiceDep,
    RetrievalServiceDep,
    SettingsDep,
)
from api.core.exceptions import (
    DocumentParsingException,
    DocumentTooLargeException,
    PineconeUpsertException,
)
from api.core.logger import get_logger, log_exception
from api.models.schemas import UploadResponse

logger = get_logger(__name__)

from api.core.constants import (
    AUTHORITY_LEVEL_MAX,
    AUTHORITY_LEVEL_MIN,
    MAX_UPLOAD_BYTES,
)

def _page_for_offset(char_start: int, page_offsets: list[tuple[int, int]]) -> int:
    page = 1
    for start_offset, page_number in page_offsets:
        if char_start >= start_offset:
            page = page_number
        else:
            break
    return page


def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class UploadRouter:
    def __init__(self) -> None:
        self.router = APIRouter()
        self.register(self.router)

    def register(self, app_or_router) -> None:
        @app_or_router.post("/api/upload", response_model=UploadResponse)
        async def upload_document(
            embedding_service: EmbeddingServiceDep,
            redis_service: RedisServiceDep,
            retrieval_service: RetrievalServiceDep,
            document_parser: DocumentParserDep,
            settings: SettingsDep,
            file: UploadFile = File(...),
            doc_type: Optional[str] = Form(default=None),
            source_org: Optional[str] = Form(default=None),
            authority_level: int = Form(default=1),
            version: Optional[str] = Form(default=None),
            publication_year: Optional[int] = Form(default=None),
            guideline_version: Optional[str] = Form(default=None),
            issuing_body: Optional[str] = Form(default=None),
            disease_area: Optional[str] = Form(default=None),
            drug_generic_name: Optional[str] = Form(default=None),
            drug_class: Optional[str] = Form(default=None),
            atc_code: Optional[str] = Form(default=None),
            condition_name: Optional[str] = Form(default=None),
            criteria_system: Optional[str] = Form(default=None),
        ) -> UploadResponse:
            return await self._upload(
                file=file,
                doc_type=doc_type,
                source_org=source_org,
                authority_level=authority_level,
                version=version,
                publication_year=publication_year,
                guideline_version=guideline_version,
                issuing_body=issuing_body,
                disease_area=disease_area,
                drug_generic_name=drug_generic_name,
                drug_class=drug_class,
                atc_code=atc_code,
                condition_name=condition_name,
                criteria_system=criteria_system,
                embedding_service=embedding_service,
                redis_service=redis_service,
                retrieval_service=retrieval_service,
                document_parser=document_parser,
                settings=settings,
            )

    async def _upload(self, *, file: UploadFile, document_parser, embedding_service,
                      redis_service, retrieval_service, settings: Settings, **meta) -> UploadResponse:
        doc_type = meta.get("doc_type")
        source_org = meta.get("source_org")
        authority_level = max(
            AUTHORITY_LEVEL_MIN,
            min(AUTHORITY_LEVEL_MAX, int(meta.get("authority_level") or 1)),
        )
        version = meta.get("version")
        publication_year = meta.get("publication_year")

        if file.content_type not in ("application/pdf", "application/x-pdf") and not (
            file.filename and file.filename.lower().endswith(".pdf")
        ):
            raise HTTPException(status_code=400, detail="Only PDF files are supported.")

        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        if len(pdf_bytes) > MAX_UPLOAD_BYTES:
            raise DocumentTooLargeException(
                f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit."
            )

        doc_name = file.filename or f"document-{uuid.uuid4()}.pdf"
        doc_id = str(uuid.uuid4())
        parse_method = "pymupdf"

        try:
            parsed = await asyncio.to_thread(document_parser.parse, pdf_bytes, doc_type)
            parse_method = parsed.parse_method
            chunks = chunk_structured(parsed, doc_name)
            if not chunks and parsed.full_text.strip():
                chunks = chunk_text(parsed.full_text, doc_name)
        except Exception as exc:
            log_exception(logger, exc)
            raise DocumentParsingException(f"Could not parse PDF file: {doc_name}") from exc

        if not chunks:
            raise DocumentParsingException(
                "No extractable text found in PDF (it may be scanned/image-only)."
            )

        chunks = [
            chunk.model_copy(update={
                "chunk_id": f"{doc_id}-{chunk.chunk_id}",
                "doc_type": doc_type,
                "source_org": source_org,
                "authority_level": authority_level,
                "version": version,
                "publication_year": publication_year,
            })
            for chunk in chunks
        ]

        embeddings = await embedding_service.get_embeddings_batch(
            [chunk.text for chunk in chunks], "search_document"
        )

        vectors = []
        for chunk, embedding in zip(chunks, embeddings):
            md = {
                "chunk_id": chunk.chunk_id,
                "doc_name": chunk.doc_name,
                "page_number": chunk.page_number,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "text": chunk.text,
                "authority_level": authority_level,
            }
            for key in (
                "doc_type", "source_org", "version", "publication_year",
                "guideline_version", "issuing_body", "disease_area",
                "drug_generic_name", "drug_class", "atc_code",
                "condition_name", "criteria_system",
            ):
                val = meta.get(key) if key not in ("doc_type", "source_org", "version", "publication_year") else getattr(chunk, key, None) or meta.get(key)
                if val is not None:
                    md[key] = val
            vectors.append({"id": chunk.chunk_id, "values": embedding, "metadata": md})

        try:
            for batch in _batched(vectors, settings.pinecone_upsert_batch_size):
                await retrieval_service.upsert_vectors(batch)
        except PineconeUpsertException:
            raise

        await asyncio.gather(*(
            redis_service.store_chunk_text(chunk.chunk_id, chunk.text) for chunk in chunks
        ))
        await asyncio.gather(*(
            redis_service.store_chunk_metadata(
                chunk.chunk_id,
                chunk.doc_name,
                chunk.page_number,
                authority_level=authority_level,
                doc_type=doc_type,
                source_org=source_org,
                version=version,
                publication_year=publication_year,
                **{k: meta[k] for k in (
                    "guideline_version", "issuing_body", "disease_area",
                    "drug_generic_name", "drug_class", "atc_code",
                    "condition_name", "criteria_system",
                ) if meta.get(k) is not None},
            )
            for chunk in chunks
        ))

        await redis_service.store_document_metadata(doc_id, {
            "doc_id": doc_id,
            "doc_name": doc_name,
            "chunk_count": len(chunks),
            "upload_timestamp": time.time(),
            "chunk_ids": [chunk.chunk_id for chunk in chunks],
            "doc_type": doc_type,
            "source_org": source_org,
            "authority_level": authority_level,
            "version": version,
            "publication_year": publication_year,
            "parse_method": parse_method,
            **{k: meta[k] for k in (
                "guideline_version", "issuing_body", "disease_area",
                "drug_generic_name", "drug_class", "atc_code",
                "condition_name", "criteria_system",
            ) if meta.get(k) is not None},
        })

        await redis_service.invalidate_bm25_cache()

        logger.info(
            f"Document uploaded: {doc_name} ({len(chunks)} chunks) via {parse_method}",
            extra={"doc_id": doc_id, "doc_type": doc_type, "source_org": source_org},
        )

        return UploadResponse(
            doc_id=doc_id,
            doc_name=doc_name,
            chunk_count=len(chunks),
            success=True,
            message=f"Uploaded and indexed {len(chunks)} chunks from {doc_name}.",
            parse_method=parse_method,
        )


upload_router = UploadRouter()
router = upload_router.router
