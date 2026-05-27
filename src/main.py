from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# from src.database.db_store import mysql_enabled, upsert_parsed_document, upsert_chunked_document, ensure_chat_session, insert_chat_message, insert_retrieved_sources, list_documents, list_chat_sessions, get_chat_messages
from src.database import db_store
from src.config.paths import CHROMA_DIR, CHUNKS_DIR, PROCESSED_JSON_DIR, RAW_IMAGE_DIR, RAW_PDF_DIR, STATIC_DIR, TEMPLATES_DIR
from src.rag.chunking import chunk_document
from src.rag.indexing import embed_and_store_chunks, embed_query, get_collection
from src.services.gemini_service import ask_gemini


load_dotenv()

app = FastAPI(title="FinMate AI Agent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
_pdf_router = None
_ocr_engine = None

SECTOR_MAP = {
    "은행": "bank",
    "카드": "card",
    "보험": "insurance",
    "투자": "stock",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _safe_filename(filename: str | None) -> str:
    return Path(filename or "uploaded_file").name


def _sector_to_eng(sector: str) -> str:
    clean_sector = (sector or "").strip()
    if clean_sector not in SECTOR_MAP:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 섹터명입니다: {clean_sector}")
    return SECTOR_MAP[clean_sector]


def _enrich_document(
    document: dict,
    *,
    user_id: str,
    sector: str,
    document_type: str,
    company: str,
    document_title: str,
) -> dict:
    document.setdefault("document_uuid", document.get("document_id") or uuid.uuid4().hex)
    document["user_id"] = user_id
    document["sector"] = sector
    if document_type:
        document["document_type"] = document_type
    if company:
        document["company"] = company
    document.setdefault("document_title", document_title)
    document.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    return document


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_pdf_router():
    global _pdf_router
    if _pdf_router is None:
        try:
            from src.preprocessing.pdf_router import PDFRouter
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "PDF 파서 의존성이 설치되어 있지 않습니다. PyMuPDF(fitz)를 설치하세요."
            ) from error
        _pdf_router = PDFRouter()
    return _pdf_router


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from src.preprocessing.image_ocr_engine import GeminiOCREngine

        _ocr_engine = GeminiOCREngine(project=PROJECT_ID, location=LOCATION)
    return _ocr_engine


def _store_document_to_mysql(document: dict, json_path: Path, original_filename: str, stored_path: Path) -> str:
    if not db_store.mysql_enabled():
        return "MySQL 비활성화: MYSQL_HOST/MYSQL_DATABASE 미설정"

    try:
        db_store.upsert_parsed_document(
            document=document,
            json_path=json_path,
            original_filename=original_filename,
            stored_path=stored_path,
            status="parsed",
        )
        return "MySQL 문서 저장 완료"
    except Exception as error:
        return f"MySQL 문서 저장 실패: {error}"


def _store_chunks_to_mysql(chunks: list[dict], chunk_path: Path, source_json_path: Path) -> str:
    if not db_store.mysql_enabled():
        return "MySQL 비활성화: 청크 저장 건너뜀"

    try:
        db_store.upsert_chunked_document(
            chunks=chunks,
            chunked_json_path=chunk_path,
            source_json_path=source_json_path,
            status="chunked",
        )
        return "MySQL 청크 저장 완료"
    except Exception as error:
        return f"MySQL 청크 저장 실패: {error}"


def _source_key(source: dict) -> tuple[str, str]:
    metadata = source.get("metadata") or {}
    return (
        str(metadata.get("document_id") or metadata.get("document_uuid") or ""),
        str(metadata.get("chunk_id") or ""),
    )


def _search_chunks(question: str, document_id: str | None = None, top_k: int = 4) -> list[dict]:
    collection = get_collection()
    where = {"document_id": document_id} if document_id else None
    results = collection.query(
        query_embeddings=[embed_query(question)],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for text, metadata, distance in zip(
        results.get("documents", [[]])[0],
        results.get("metadatas", [[]])[0],
        results.get("distances", [[]])[0],
    ):
        chunks.append(
            {
                "text": text,
                "metadata": metadata or {},
                "distance": distance,
                "score": round(1 - distance, 4),
            }
        )
    return chunks


def _answer_with_sources(
    question: str,
    document_id: str,
    top_k: int = 4,
    include_related_documents: bool = False,
    related_top_k: int = 4,
) -> tuple[str, list[dict]]:
    selected_chunks = _search_chunks(question, document_id=document_id, top_k=top_k)
    chunks = []
    seen = set()

    for source in selected_chunks:
        source["scope"] = "selected_document"
        metadata = source.setdefault("metadata", {})
        metadata["source_scope"] = "selected_document"
        chunks.append(source)
        seen.add(_source_key(source))

    if include_related_documents:
        for source in _search_chunks(question, document_id=None, top_k=related_top_k):
            key = _source_key(source)
            if key in seen:
                continue
            source["scope"] = "related_document"
            metadata = source.setdefault("metadata", {})
            metadata["source_scope"] = "related_document"
            chunks.append(source)
            seen.add(key)

    context = "\n\n---\n\n".join(
        (
            f"범위: {'현재 선택 문서' if source.get('scope') == 'selected_document' else 'DB 내 관련 문서'}, "
            f"문서: {source['metadata'].get('document_id', '-')}, "
            f"페이지: {source['metadata'].get('page_number', '-')}, "
            f"회사: {source['metadata'].get('company', '-')}]\n"
            f"{source['text']}"
        )
        for source in chunks
    )
    answer = ask_gemini(question=question, sector="financial", context=context)
    return answer, chunks


def _store_chat(
    *,
    user_id: str,
    document_id: str,
    question: str,
    answer: str,
    sources: list[dict],
    session_id: str | None,
) -> str | None:
    if not db_store.mysql_enabled():
        return session_id

    is_new_session = not session_id
    session_id = session_id or uuid.uuid4().hex
    db_store.ensure_chat_session(
        user_id=user_id or "anonymous",
        session_id=session_id,
        title=question[:255] if is_new_session else None,
    )
    db_store.insert_chat_message(session_id, user_id, "user", question, document_id)
    assistant_message_id = db_store.insert_chat_message(
        session_id,
        user_id,
        "assistant",
        answer,
        document_id,
    )
    db_store.insert_retrieved_sources(assistant_message_id, sources)
    return session_id


@app.get("/")
def index():
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form("000001"),
    document_sector: str = Form("bank"),
    document_type: str = Form("개인문서"),
    company: str = Form(""),
):
    return await parse_document(
        sector=document_sector,
        file=file,
        user_id=user_id,
        document_type=document_type,
        company=company,
    )


@app.post("/api/parse")
async def parse_document(
    sector: str = Form(...),
    file: UploadFile = File(...),
    user_id: str = Form("000001"),
    document_type: str = Form("개인문서"),
    company: str = Form(""),
):
    eng_sector = _sector_to_eng(sector)
    filename = _safe_filename(file.filename)
    file_suffix = Path(filename).suffix.lower()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    stored_filename = f"{timestamp}_{filename}"

    if file_suffix == ".pdf":
        target_dir = RAW_PDF_DIR / eng_sector
    elif file_suffix in IMAGE_EXTENSIONS:
        target_dir = RAW_IMAGE_DIR / eng_sector
    else:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 형식입니다 ({file_suffix}).")

    # 원본 파일 입시 저장
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / stored_filename
    with target_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        metadata = {
            "user_id": user_id,
            "document_title": filename,
            "document_type": document_type,
            "company": company,
            "sector": eng_sector,
        }
        if file_suffix == ".pdf":
            parsed = _get_pdf_router().process_pdf(
                sector=eng_sector,
                pdf_path=target_path,
                metadata=metadata,
            )
        else:
            parsed = _get_ocr_engine().process_image(image_path=target_path, metadata=metadata)

        if not parsed:
            raise HTTPException(status_code=500, detail="문서 파싱 결과 가공에 실패했습니다.")

        parsed = _enrich_document(
            parsed,
            user_id=user_id,
            sector=eng_sector,
            document_type=document_type,
            company=company,
            document_title=filename,
        )
        document_id = parsed["document_uuid"]

        parsed_json_path = PROCESSED_JSON_DIR / f"{document_id}.json"
        _write_json(parsed_json_path, parsed)
        db_document_status = _store_document_to_mysql(parsed, parsed_json_path, filename, target_path)

        chunks = chunk_document(parsed)
        chunk_json_path = CHUNKS_DIR / f"{document_id}_chunked.json"
        _write_json(chunk_json_path, chunks)
        db_chunk_status = _store_chunks_to_mysql(chunks, chunk_json_path, parsed_json_path)

        embedding_status = "Chroma 벡터 저장 완료"
        embedding_failed = False
        try:
            stored_count = embed_and_store_chunks(chunks)
        except Exception as error:
            stored_count = 0
            embedding_failed = True
            embedding_status = f"Chroma 벡터 저장 실패: {error}"

        log = "\n".join(
            [
                (
                    "파싱, 청킹 및 벡터화 완료"
                    if not embedding_failed
                    else "파싱 및 청킹 완료, 벡터화 미완료"
                ),
                f"document_id: {document_id}",
                f"chunks: {len(chunks)}",
                f"uploaded_vectors: {stored_count}",
                db_document_status,
                db_chunk_status,
                embedding_status,
            ]
        )

        return JSONResponse(
            {
                "status": "partial" if embedding_failed else "success",
                "message": (
                    "파싱, 청킹 및 벡터화 완료"
                    if not embedding_failed
                    else "파싱 및 청킹 완료, 벡터화 미완료"
                ),
                "document_id": document_id,
                "user_id": user_id,
                "uploaded_path": str(target_path),
                "db_status": db_document_status,
                "embedding_status": embedding_status,
                "log": log,
                "pipeline_summary": {
                    "document_uuid": document_id,
                    "total_chunks": len(chunks),
                    "uploaded_chunks_count": stored_count,
                },
                "data": parsed,
            }
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.post("/api/index")
async def index_document(payload: dict):
    document_id = payload.get("document_id")
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id가 필요합니다.")

    chunk_path = CHUNKS_DIR / f"{document_id}_chunked.json"
    if not chunk_path.exists():
        raise HTTPException(status_code=404, detail=f"청크 파일을 찾을 수 없습니다: {chunk_path}")

    chunks = json.loads(chunk_path.read_text(encoding="utf-8"))
    stored_count = embed_and_store_chunks(chunks)
    return JSONResponse(
        {
            "document_id": document_id,
            "log": f"Chroma 인덱싱 완료: 신규 저장 {stored_count}개",
        }
    )


@app.get("/api/documents")
async def documents(user_id: str | None = None):
    if not db_store.mysql_enabled():
        return JSONResponse({"documents": [], "message": "MySQL 비활성화"})

    rows = db_store.list_documents(user_id=user_id, limit=100)
    return JSONResponse(jsonable_encoder({"documents": rows}))


@app.get("/api/chat/sessions")
async def chat_sessions(user_id: str = "000001", q: str | None = None):
    if not db_store.mysql_enabled():
        return JSONResponse({"sessions": []})

    rows = db_store.list_chat_sessions(user_id=user_id, query=q, limit=80)
    return JSONResponse(jsonable_encoder({"sessions": rows}))


@app.get("/api/chat/sessions/{session_id}")
async def chat_session_messages(session_id: str, user_id: str = "000001"):
    if not db_store.mysql_enabled():
        return JSONResponse({"messages": []})

    rows = db_store.get_chat_messages(session_id=session_id, user_id=user_id)
    return JSONResponse(jsonable_encoder({"messages": rows}))


@app.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str, user_id: str = "000001"):
    if not db_store.mysql_enabled():
        raise HTTPException(status_code=400, detail="MySQL 비활성화")

    result = db_store.delete_chat_session(session_id=session_id, user_id=user_id)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=result.get("message", "채팅 세션을 찾을 수 없습니다."))
    return JSONResponse(jsonable_encoder(result))


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str, delete_chroma: bool = True, delete_files: bool = True):
    if not db_store.mysql_enabled():
        raise HTTPException(status_code=400, detail="MySQL 비활성화")

    db_result = db_store.delete_document(document_id=document_id)
    if not db_result.get("deleted"):
        raise HTTPException(status_code=404, detail=db_result.get("message", "문서를 찾을 수 없습니다."))

    chroma_result = None
    if delete_chroma:
        collection = get_collection()
        existing = collection.get(where={"document_id": document_id}, include=[])
        ids = existing.get("ids", [])
        if ids:
            collection.delete(ids=ids)
        chroma_result = {"deleted": True, "deleted_count": len(ids)}

    file_results = []
    if delete_files:
        for value in db_result.get("paths", {}).values():
            if value and Path(value).exists():
                Path(value).unlink()
                file_results.append({"path": value, "deleted": True})

    return JSONResponse({"document_id": document_id, "db": db_result, "chroma": chroma_result, "files": file_results})


@app.post("/api/ask")
async def ask(payload: dict):
    document_id = payload.get("document_id")
    question = payload.get("question")
    user_id = payload.get("user_id") or "000001"
    session_id = payload.get("session_id")

    if not document_id or not question:
        raise HTTPException(status_code=400, detail="document_id와 question이 필요합니다.")

    answer, sources = _answer_with_sources(
        question,
        document_id=document_id,
        top_k=4,
        include_related_documents=True,
        related_top_k=4,
    )
    session_id = _store_chat(
        user_id=user_id,
        document_id=document_id,
        question=question,
        answer=answer,
        sources=sources,
        session_id=session_id,
    )
    return JSONResponse({"answer": answer, "session_id": session_id, "sources": sources})


@app.post("/api/summary")
async def summary(payload: dict):
    document_id = payload.get("document_id")
    user_id = payload.get("user_id") or "000001"
    session_id = payload.get("session_id")

    if not document_id:
        raise HTTPException(status_code=400, detail="document_id가 필요합니다.")

    question = "이 문서의 핵심 내용을 요약해줘."
    answer, sources = _answer_with_sources(question, document_id=document_id, top_k=5)
    session_id = _store_chat(
        user_id=user_id,
        document_id=document_id,
        question="문서 요약",
        answer=answer,
        sources=sources,
        session_id=session_id,
    )
    return JSONResponse({"answer": answer, "session_id": session_id, "sources": sources})


@app.get("/api/health")
async def health(_: Request):
    return {
        "status": "ok",
        "mysql_enabled": db_store.mysql_enabled(),
        "chroma_dir": str(CHROMA_DIR),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="127.0.0.1", port=8080, reload=True)
