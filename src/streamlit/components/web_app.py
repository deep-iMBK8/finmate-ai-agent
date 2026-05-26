import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scripts import db_store


APP_ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = APP_ROOT / "data" / "uploads_web"
OUTPUT_DIR = APP_ROOT / "data" / "processed" / "ocr_text"
CHROMA_DIR = APP_ROOT / "data" / "chroma_db"
CHAT_LOG = APP_ROOT / "data" / "chat_logs" / "web_rag_chat.jsonl"


app = FastAPI(title="Financial OCR RAG Demo")
app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=APP_ROOT / "templates")


def run_command(args, timeout=900):
    result = subprocess.run(
        args,
        cwd=str(APP_ROOT),
        text=True,
        capture_output=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())

    return result.stdout.strip()


def extract_output_path(log: str, label: str):
    pattern = rf"^{re.escape(label)}:\s*(.+)$"

    for line in reversed((log or "").splitlines()):
        match = re.match(pattern, line.strip())
        if match:
            return Path(match.group(1).strip())

    return None


def safe_document_id(filename: str):
    document_id = Path(filename).stem
    document_id = re.sub(r'[\\/*?:"<>|]', "_", document_id)
    document_id = re.sub(r"\s+", "_", document_id)
    return document_id.strip("_")


def try_store_document(json_path, original_filename, stored_path):
    if not db_store.mysql_enabled():
        return "MySQL 비활성화: MYSQL_HOST/MYSQL_DATABASE 미설정"

    try:
        db_store.upsert_document_from_json(
            json_path=str(json_path),
            original_filename=original_filename,
            stored_path=str(stored_path),
        )
        return "MySQL 저장 완료"
    except Exception as error:
        return f"MySQL 저장 실패: {error}"


def load_document_id_from_json(json_path: Path, fallback: str):
    try:
        document = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return fallback

    return document.get("document_id") or fallback


def load_latest_sources_from_chat_log():
    if not CHAT_LOG.exists():
        return []

    last_line = ""
    with CHAT_LOG.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last_line = line

    if not last_line:
        return []

    try:
        return json.loads(last_line).get("sources", [])
    except json.JSONDecodeError:
        return []


def try_store_chat(user_id, document_id, user_content, assistant_content, sources, session_id=None):
    if not db_store.mysql_enabled():
        return None

    try:
        is_new_session = not session_id
        session_id = session_id or uuid.uuid4().hex
        db_store.ensure_chat_session(
            user_id=user_id or "anonymous",
            session_id=session_id,
            title=(user_content[:255] if is_new_session and user_content else f"문서 질의응답: {document_id}"),
        )
        db_store.insert_chat_message(
            session_id=session_id,
            user_id=user_id or "anonymous",
            role="user",
            content=user_content,
            document_id=document_id,
        )
        assistant_message_id = db_store.insert_chat_message(
            session_id=session_id,
            user_id=user_id or "anonymous",
            role="assistant",
            content=assistant_content,
            document_id=document_id,
        )
        db_store.insert_retrieved_sources(assistant_message_id, sources)
        return session_id
    except Exception as error:
        print(f"MySQL chat 저장 실패: {error}", flush=True)
        return None


def try_delete_chroma_document(document_id: str):
    try:
        import chromadb
    except ImportError:
        return {
            "deleted": False,
            "message": "Chroma 삭제 건너뜀: chromadb 패키지가 없습니다.",
        }

    try:
        chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collection = chroma_client.get_collection(name="financial_documents")
        existing = collection.get(where={"document_id": document_id}, include=[])
        ids = existing.get("ids", [])
        if ids:
            collection.delete(ids=ids)
        return {
            "deleted": True,
            "deleted_count": len(ids),
            "message": f"Chroma vector {len(ids)}개 삭제",
        }
    except Exception as error:
        return {
            "deleted": False,
            "message": f"Chroma 삭제 실패: {error}",
        }


def delete_local_file(path_value: str):
    if not path_value:
        return {"path": path_value, "deleted": False, "message": "경로 없음"}

    path = Path(path_value)
    if not path.is_absolute():
        path = APP_ROOT / path

    try:
        resolved = path.resolve()
        allowed_roots = [
            UPLOAD_DIR.resolve(),
            OUTPUT_DIR.resolve(),
        ]
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            return {
                "path": str(path),
                "deleted": False,
                "message": "허용된 데이터 폴더 밖의 파일은 삭제하지 않습니다.",
            }
        if not resolved.exists():
            return {"path": str(path), "deleted": False, "message": "파일 없음"}
        resolved.unlink()
        return {"path": str(path), "deleted": True, "message": "삭제 완료"}
    except Exception as error:
        return {"path": str(path), "deleted": False, "message": str(error)}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form("000001"),
    document_sector: str = Form("bank"),
    document_type: str = Form("개인문서"),
    company: str = Form(""),
):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    original_filename = Path(file.filename or "uploaded_file").name
    if not original_filename:
        raise HTTPException(status_code=400, detail="업로드 파일명이 비어 있습니다.")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    upload_path = UPLOAD_DIR / f"{timestamp}_{original_filename}"

    with upload_path.open("wb") as f:
        f.write(await file.read())

    if upload_path.stat().st_size == 0:
        raise HTTPException(status_code=400, detail="업로드 파일이 비어 있습니다.")

    args = [
        sys.executable,
        "scripts/process_uploaded_file.py",
        str(upload_path),
        "--upload-dir",
        str(UPLOAD_DIR),
        "--output-dir",
        str(OUTPUT_DIR),
        "--user-id",
        user_id,
        "--document-sector",
        document_sector,
        "--document-type",
        document_type,
    ]

    if company:
        args.extend(["--company", company])

    try:
        log = run_command(args)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    output_base_name = safe_document_id(upload_path.name)
    json_path = extract_output_path(log, "JSON") or (OUTPUT_DIR / f"{output_base_name}.json")
    if not json_path.is_absolute():
        json_path = APP_ROOT / json_path

    if not json_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"처리 결과 JSON을 찾을 수 없습니다: {json_path}",
        )

    document_id = load_document_id_from_json(json_path, fallback=output_base_name)
    db_status = try_store_document(
        json_path=json_path,
        original_filename=original_filename,
        stored_path=upload_path,
    )
    log = f"{log}\n\n[MySQL]\n{db_status}"

    return JSONResponse(
        {
            "document_id": document_id,
            "user_id": user_id,
            "uploaded_path": str(upload_path),
            "db_status": db_status,
            "log": log,
        }
    )


@app.post("/api/index")
async def index_document(payload: dict):
    document_id = payload.get("document_id")
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id가 필요합니다.")

    args = [
        sys.executable,
        "scripts/index_to_chroma.py",
        "--json-dir",
        str(OUTPUT_DIR),
        "--chroma-dir",
        str(CHROMA_DIR),
        "--document-id",
        document_id,
        "--batch-size",
        "10",
        "--batch-sleep",
        "15",
        "--retry-sleep",
        "30",
    ]

    try:
        log = run_command(args, timeout=1800)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return JSONResponse({"document_id": document_id, "log": log})


@app.get("/api/documents")
async def documents(user_id: str = None):
    if not db_store.mysql_enabled():
        return JSONResponse(
            {
                "documents": [],
                "message": "MySQL 비활성화: MYSQL_HOST/MYSQL_DATABASE 환경변수를 설정하세요.",
            }
        )

    try:
        rows = db_store.list_documents(user_id=user_id, limit=100)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    for row in rows:
        for key, value in list(row.items()):
            if hasattr(value, "isoformat"):
                row[key] = value.isoformat()

    return JSONResponse(jsonable_encoder({"documents": rows}))


@app.get("/api/chat/sessions")
async def chat_sessions(user_id: str = "000001", q: str = None):
    if not db_store.mysql_enabled():
        return JSONResponse({"sessions": []})

    try:
        rows = db_store.list_chat_sessions(user_id=user_id, query=q, limit=80)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return JSONResponse(jsonable_encoder({"sessions": rows}))


@app.get("/api/chat/sessions/{session_id}")
async def chat_session_messages(session_id: str, user_id: str = "000001"):
    if not db_store.mysql_enabled():
        return JSONResponse({"messages": []})

    try:
        rows = db_store.get_chat_messages(session_id=session_id, user_id=user_id)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return JSONResponse(jsonable_encoder({"messages": rows}))


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str, delete_chroma: bool = True, delete_files: bool = True):
    if not db_store.mysql_enabled():
        raise HTTPException(
            status_code=400,
            detail="MySQL 비활성화: MYSQL_HOST/MYSQL_DATABASE 환경변수를 설정하세요.",
        )

    try:
        db_result = db_store.delete_document(document_id=document_id)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    if not db_result.get("deleted"):
        raise HTTPException(status_code=404, detail=db_result.get("message", "문서를 찾을 수 없습니다."))

    chroma_result = None
    if delete_chroma:
        chroma_result = try_delete_chroma_document(document_id)

    file_results = []
    if delete_files:
        seen_paths = set()
        for path_value in db_result.get("paths", {}).values():
            if not path_value or path_value in seen_paths:
                continue
            seen_paths.add(path_value)
            file_results.append(delete_local_file(path_value))

    return JSONResponse(
        {
            "document_id": document_id,
            "db": db_result,
            "chroma": chroma_result,
            "files": file_results,
        }
    )


@app.post("/api/ask")
async def ask(payload: dict):
    document_id = payload.get("document_id")
    question = payload.get("question")
    user_id = payload.get("user_id")
    session_id = payload.get("session_id")

    if not document_id or not question:
        raise HTTPException(status_code=400, detail="document_id와 question이 필요합니다.")

    args = [
        sys.executable,
        "scripts/rag_chat.py",
        "ask",
        question,
        "--document-id",
        document_id,
        "--top-k",
        "4",
        "--llm-max-retries",
        "5",
        "--llm-retry-sleep",
        "12",
        "--chat-log",
        str(CHAT_LOG),
    ]
    if user_id:
        args.extend(["--user-id", user_id])

    try:
        answer = run_command(args)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    sources = load_latest_sources_from_chat_log()
    session_id = try_store_chat(
        user_id=user_id,
        document_id=document_id,
        user_content=question,
        assistant_content=answer,
        sources=sources,
        session_id=session_id,
    )

    return JSONResponse({"answer": answer, "session_id": session_id})


@app.post("/api/summary")
async def summary(payload: dict):
    document_id = payload.get("document_id")
    user_id = payload.get("user_id")
    session_id = payload.get("session_id")

    if not document_id:
        raise HTTPException(status_code=400, detail="document_id가 필요합니다.")

    args = [
        sys.executable,
        "scripts/rag_chat.py",
        "summary",
        "--document-id",
        document_id,
        "--summary-type",
        "detailed",
        "--top-k",
        "5",
        "--llm-max-retries",
        "5",
        "--llm-retry-sleep",
        "12",
        "--chat-log",
        str(CHAT_LOG),
    ]
    if user_id:
        args.extend(["--user-id", user_id])

    try:
        answer = run_command(args)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    sources = load_latest_sources_from_chat_log()
    session_id = try_store_chat(
        user_id=user_id,
        document_id=document_id,
        user_content="문서 요약",
        assistant_content=answer,
        sources=sources,
        session_id=session_id,
    )

    return JSONResponse({"answer": answer, "session_id": session_id})
