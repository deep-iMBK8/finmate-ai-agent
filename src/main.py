from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config.paths import (
    CHROMA_DIR,
    CHUNKS_DIR,
    PROCESSED_JSON_DIR,
    RAW_IMAGE_DIR,
    RAW_PDF_DIR,
    STATIC_DIR,
    TEMPLATES_DIR,
)
from src.database import db_store
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

# TODO: 매핑 키 수정 필요ㅇ - 영어 없애기
SECTOR_MAP = {
    "은행": "bank",
    "bank": "bank",
    "카드": "card",
    "card": "card",
    "보험": "insurance",
    "insurance": "insurance",
    "투자": "stock",
    "증권": "stock",
    "stock": "stock",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DOCUMENT_TYPE_KEYWORDS = ["약관", "명세서", "신청서", "설명서", "안내장"]
EXACT_MARKERS = [
    "금액",
    "날짜",
    "계좌",
    "이율",
    "수수료",
    "한도",
    "조항",
    "고객명",
    "이름",
    "번호",
    "연회비",
    "기본연회비",
    "제휴연회비",
    "얼마",
]
QUERY_STOPWORDS = {
    "얼마야",
    "얼마",
    "뭐야",
    "무엇",
    "알려줘",
    "찾아줘",
    "기본적인",
    "내용",
    "관련",
    "문서",
}


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
        db_store.ensure_hybrid_search_schema()
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
        str(_chunk_key(metadata.get("chunk_id")) or ""),
    )


def _chunk_key(chunk_id) -> int | str | None:
    if chunk_id is None:
        return None
    text = str(chunk_id)
    if text.isdigit():
        return int(text)
    match = re.search(r"_chunk(\d+)$", text)
    if match:
        return int(match.group(1))
    return text


def _keyword_terms(question: str) -> list[str]:
    terms = []
    for term in re.findall(r"[0-9A-Za-z가-힣]+", question or ""):
        if len(term) < 2 or term in QUERY_STOPWORDS:
            continue
        terms.append(term)
        stripped = re.sub(r"(이|가|은|는|을|를|와|과|도|만|에|의)$", "", term)
        if len(stripped) >= 2 and stripped != term:
            terms.append(stripped)
        if term.endswith("적인") and len(term) > 3:
            terms.append(term[:-2])
    for marker in EXACT_MARKERS:
        if marker in (question or ""):
            terms.append(marker)
    return list(dict.fromkeys(terms))[:12]


def _analyze_query(question: str, user_id: str) -> dict:
    clean_question = " ".join((question or "").split())
    filters = {
        "keyword_query": clean_question,
        "has_exact_marker": any(marker in clean_question for marker in EXACT_MARKERS),
        "keyword_terms": _keyword_terms(clean_question),
    }

    for keyword, sector in SECTOR_MAP.items():
        if keyword in clean_question:
            filters["sector"] = sector
            break

    for document_type in DOCUMENT_TYPE_KEYWORDS:
        if document_type in clean_question:
            filters["document_type"] = document_type
            break

    if db_store.mysql_enabled():
        try:
            for company in db_store.list_known_companies(user_id):
                if company and company in clean_question:
                    filters["company"] = company
                    break
        except Exception as error:
            print(f"회사명 추출 실패: {error}")

    return filters


def _keyword_search_chunks(
    question: str,
    user_id: str,
    document_id: str | None = None,
    filters: dict | None = None,
    top_k: int = 12,
) -> list[dict]:
    if not db_store.mysql_enabled():
        return []
    try:
        db_store.ensure_hybrid_search_schema()
        return db_store.keyword_search_chunks(question, user_id, document_id, filters, top_k)
    except Exception as error:
        print(f"MySQL keyword search 실패: {error}")
        return []


def _metadata_search_chunks(
    user_id: str,
    document_id: str | None = None,
    filters: dict | None = None,
    top_k: int = 12,
) -> list[dict]:
    if not db_store.mysql_enabled():
        return []
    try:
        db_store.ensure_hybrid_search_schema()
        return db_store.metadata_search_chunks(user_id, document_id, filters, top_k)
    except Exception as error:
        print(f"MySQL metadata search 실패: {error}")
        return []


def _semantic_search_chunks(
    question: str,
    document_id: str | None = None,
    filters: dict | None = None,
    top_k: int = 12,
) -> list[dict]:
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
                "vector_score": 1 - distance,
                "retrieval_methods": ["semantic"],
            }
        )
    return chunks


def _chroma_keyword_search_chunks(
    question: str,
    document_id: str | None = None,
    filters: dict | None = None,
    top_k: int = 12,
) -> list[dict]:
    terms = (filters or {}).get("keyword_terms") or _keyword_terms(question)
    if not terms:
        return []

    collection = get_collection()
    where = {"document_id": document_id} if document_id else None
    if where:
        rows = collection.get(where=where, include=["documents", "metadatas"])
    else:
        rows = collection.get(include=["documents", "metadatas"])
    candidates = []
    for text, metadata in zip(rows.get("documents", []), rows.get("metadatas", [])):
        text = text or ""
        score = _lexical_score(text, terms)
        if score <= 0:
            continue
        candidates.append(
            {
                "text": text,
                "metadata": metadata or {},
                "keyword_score": score,
                "retrieval_methods": ["chroma_keyword"],
            }
        )

    candidates.sort(key=lambda result: result.get("keyword_score", 0.0), reverse=True)
    return candidates[:top_k]


def _lexical_score(text: str, terms: list[str]) -> float:
    score = 0.0
    for term in terms:
        count = text.count(term)
        if not count:
            continue
        capped_count = min(count, 3)
        score += capped_count
        if term in EXACT_MARKERS:
            score += capped_count * 2
    has_amount = bool(re.search(r"\d[\d,]*\s*(원|만원|천원|%)", text))
    if has_amount:
        score += 2.0
    if "얼마" in terms and has_amount:
        score += 8.0
    if "연회비" in terms and "기본연회비" in text:
        score += 5.0
    return score


def _merge_search_results(*result_groups: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for results in result_groups:
        for source in results:
            metadata = source.get("metadata") or {}
            key = (
                str(metadata.get("document_id") or metadata.get("document_uuid") or ""),
                str(_chunk_key(metadata.get("chunk_id")) or metadata.get("chroma_id") or ""),
            )
            if key not in merged:
                copied = dict(source)
                copied["metadata"] = dict(metadata)
                copied["retrieval_methods"] = set(source.get("retrieval_methods") or [])
                merged[key] = copied
                continue

            target = merged[key]
            target["retrieval_methods"].update(source.get("retrieval_methods") or [])
            for score_key in ("keyword_score", "metadata_score", "vector_score", "distance", "score"):
                if source.get(score_key) is not None:
                    target[score_key] = source[score_key]
            if not target.get("text") and source.get("text"):
                target["text"] = source["text"]
            target["metadata"].update(metadata)

    for result in merged.values():
        result["retrieval_methods"] = sorted(result.get("retrieval_methods") or [])
    return list(merged.values())


def _normalize_scores(results: list[dict], score_key: str, normalized_key: str) -> None:
    scores = [float(result[score_key]) for result in results if result.get(score_key) is not None]
    if not scores:
        for result in results:
            result[normalized_key] = 0.0
        return

    min_score = min(scores)
    max_score = max(scores)
    span = max_score - min_score
    for result in results:
        score = result.get(score_key)
        if score is None:
            result[normalized_key] = 0.0
        elif span == 0:
            result[normalized_key] = 1.0
        else:
            result[normalized_key] = (float(score) - min_score) / span


def _choose_weights(filters: dict | None) -> dict:
    filters = filters or {}
    if filters.get("has_exact_marker"):
        return {"vector": 0.40, "keyword": 0.45, "metadata": 0.10, "multi": 0.05}
    if filters.get("company") or filters.get("document_type"):
        return {"vector": 0.45, "keyword": 0.25, "metadata": 0.25, "multi": 0.05}
    return {"vector": 0.65, "keyword": 0.20, "metadata": 0.10, "multi": 0.05}


def _rerank_results(
    question: str,
    merged_results: list[dict],
    filters: dict | None = None,
    top_k: int = 5,
) -> list[dict]:
    del question
    _normalize_scores(merged_results, "keyword_score", "keyword_score_norm")
    _normalize_scores(merged_results, "metadata_score", "metadata_score_norm")
    _normalize_scores(merged_results, "vector_score", "vector_score_norm")
    weights = _choose_weights(filters)

    for result in merged_results:
        methods = result.get("retrieval_methods") or []
        multi_match_boost = min(max(len(methods) - 1, 0), 2) / 2
        result["multi_match_boost"] = multi_match_boost
        result["final_score"] = (
            weights["vector"] * result.get("vector_score_norm", 0.0)
            + weights["keyword"] * result.get("keyword_score_norm", 0.0)
            + weights["metadata"] * result.get("metadata_score_norm", 0.0)
            + weights["multi"] * multi_match_boost
        )

    return sorted(merged_results, key=lambda result: result.get("final_score", 0.0), reverse=True)[:top_k]


def _hybrid_search_chunks(
    question: str,
    user_id: str,
    document_id: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    filters = _analyze_query(question, user_id)
    candidate_k = max(top_k * 3, 12)

    chroma_keyword_results = _chroma_keyword_search_chunks(question, document_id, filters, candidate_k)
    keyword_results = []
    metadata_results = []
    semantic_results = []

    if not (filters.get("has_exact_marker") and len(chroma_keyword_results) >= top_k):
        keyword_results = _keyword_search_chunks(question, user_id, document_id, filters, candidate_k)
        metadata_results = _metadata_search_chunks(user_id, document_id, filters, candidate_k)
        try:
            semantic_results = _semantic_search_chunks(question, document_id, filters, candidate_k)
        except Exception as error:
            print(f"Chroma semantic search 실패: {error}")

    merged = _merge_search_results(keyword_results, chroma_keyword_results, metadata_results, semantic_results)
    return _rerank_results(question, merged, filters=filters, top_k=top_k)


def _load_document_chunks(document_id: str, limit: int = 4, question: str | None = None) -> list[dict]:
    chunk_path = CHUNKS_DIR / f"{document_id}_chunked.json"
    if not chunk_path.exists():
        return []

    chunks = json.loads(chunk_path.read_text(encoding="utf-8"))
    if question:
        terms = _keyword_terms(question)
        chunks = sorted(
            chunks,
            key=lambda chunk: _lexical_score(chunk.get("chunk", ""), terms),
            reverse=True,
        )
    fallback_sources = []
    for chunk in chunks[:limit]:
        metadata = chunk.get("metadata") or {}
        metadata["source_scope"] = "selected_document"
        fallback_sources.append(
            {
                "text": chunk.get("chunk", ""),
                "metadata": metadata,
                "distance": None,
                "score": None,
                "retrieval_methods": ["json_fallback"],
                "scope": "selected_document",
            }
        )
    return fallback_sources


def _answer_with_sources(
    question: str,
    document_id: str,
    user_id: str,
    top_k: int = 4,
    include_related_documents: bool = False,
    related_top_k: int = 4,
) -> tuple[str, list[dict]]:
    try:
        selected_chunks = _hybrid_search_chunks(question, user_id, document_id=document_id, top_k=top_k)
        search_error = None
    except Exception as error:
        selected_chunks = _load_document_chunks(document_id, limit=top_k, question=question)
        search_error = error
    if not selected_chunks:
        selected_chunks = _load_document_chunks(document_id, limit=top_k, question=question)

    chunks = []
    seen = set()

    for source in selected_chunks:
        source["scope"] = "selected_document"
        metadata = source.setdefault("metadata", {})
        metadata["source_scope"] = "selected_document"
        chunks.append(source)
        seen.add(_source_key(source))

    if include_related_documents and search_error is None:
        try:
            related_sources = _hybrid_search_chunks(question, user_id, document_id=None, top_k=related_top_k)
        except Exception:
            related_sources = []
        for source in related_sources:
            key = _source_key(source)
            if key in seen:
                continue
            source["scope"] = "related_document"
            metadata = source.setdefault("metadata", {})
            metadata["source_scope"] = "related_document"
            chunks.append(source)
            seen.add(key)

    if not chunks:
        raise HTTPException(status_code=404, detail="질문에 사용할 문서 청크를 찾을 수 없습니다.")

    context = "\n\n---\n\n".join(
        (
            f"범위: {'현재 선택 문서' if source.get('scope') == 'selected_document' else 'DB 내 관련 문서'}, "
            f"문서: {source['metadata'].get('document_id', '-')}, "
            f"페이지: {source['metadata'].get('page_number', '-')}, "
            f"회사: {source['metadata'].get('company', '-')}, "
            f"검색방식: {', '.join(source.get('retrieval_methods') or [])}, "
            f"점수: final_score={source.get('final_score', '-')}\n"
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
    try:
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
    except Exception as error:
        print(f"채팅 저장 실패: {error}")
    return session_id


def _db_unavailable_payload(error: Exception) -> dict:
    return {
        "message": "MySQL 연결 실패",
        "detail": str(error),
    }


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

    try:
        rows = db_store.list_documents(user_id=user_id, limit=100)
    except Exception as error:
        payload = _db_unavailable_payload(error)
        payload["documents"] = []
        return JSONResponse(payload, status_code=200)
    return JSONResponse(jsonable_encoder({"documents": rows}))


@app.get("/api/chat/sessions")
async def chat_sessions(user_id: str = "000001", q: str | None = None):
    if not db_store.mysql_enabled():
        return JSONResponse({"sessions": []})

    try:
        rows = db_store.list_chat_sessions(user_id=user_id, query=q, limit=80)
    except Exception as error:
        payload = _db_unavailable_payload(error)
        payload["sessions"] = []
        return JSONResponse(payload, status_code=200)
    return JSONResponse(jsonable_encoder({"sessions": rows}))


@app.get("/api/chat/sessions/{session_id}")
async def chat_session_messages(session_id: str, user_id: str = "000001"):
    if not db_store.mysql_enabled():
        return JSONResponse({"messages": []})

    try:
        rows = db_store.get_chat_messages(session_id=session_id, user_id=user_id)
    except Exception as error:
        payload = _db_unavailable_payload(error)
        payload["messages"] = []
        return JSONResponse(payload, status_code=200)
    return JSONResponse(jsonable_encoder({"messages": rows}))


@app.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str, user_id: str = "000001"):
    if not db_store.mysql_enabled():
        raise HTTPException(status_code=400, detail="MySQL 비활성화")

    try:
        result = db_store.delete_chat_session(session_id=session_id, user_id=user_id)
    except Exception as error:
        raise HTTPException(status_code=503, detail=_db_unavailable_payload(error)) from error
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=result.get("message", "채팅 세션을 찾을 수 없습니다."))
    return JSONResponse(jsonable_encoder(result))


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str, delete_chroma: bool = True, delete_files: bool = True):
    if not db_store.mysql_enabled():
        raise HTTPException(status_code=400, detail="MySQL 비활성화")

    try:
        db_result = db_store.delete_document(document_id=document_id)
    except Exception as error:
        raise HTTPException(status_code=503, detail=_db_unavailable_payload(error)) from error
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
        user_id=user_id,
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
    answer, sources = _answer_with_sources(question, document_id=document_id, user_id=user_id, top_k=5)
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
