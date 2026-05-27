from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = BASE_DIR / "mysql" / "schema.sql"


def mysql_enabled() -> bool:
    return bool(os.getenv("MYSQL_HOST") and os.getenv("MYSQL_DATABASE"))


def get_connection(database: str | None = None):
    try:
        import pymysql
    except ImportError as error:
        raise ImportError(
            "PyMySQL이 설치되어 있지 않습니다. `pip install pymysql`을 실행하세요."
        ) from error

    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database if database is not None else os.getenv("MYSQL_DATABASE"),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def execute_schema(schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> None:
    database = os.getenv("MYSQL_DATABASE")
    if not database:
        raise ValueError("MYSQL_DATABASE 환경변수가 필요합니다.")

    statements = [
        statement.strip()
        for statement in Path(schema_path).read_text(encoding="utf-8").split(";")
        if statement.strip()
    ]

    conn = get_connection(database=None)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {_quote_identifier(database)} "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(f"USE {_quote_identifier(database)}")
            for statement in statements:
                cursor.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() == "null":
        return default
    return text


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _document_id_from_raw(document: dict) -> str:
    return (
        _safe_str(document.get("document_uuid"))
        or _safe_str(document.get("document_id"))
        or _safe_str(document.get("doc_id"))
        or uuid.uuid4().hex
    )


def _metadata_from_chunk(chunk: dict) -> dict:
    metadata = chunk.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _chunk_number(chunk_id: Any, fallback: int) -> int:
    text = _safe_str(chunk_id)
    if text.isdigit():
        return int(text)
    if "_chunk" in text:
        tail = text.rsplit("_chunk", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return fallback


def _chroma_id_for_chunk(chunk: dict, fallback: int) -> str:
    metadata = _metadata_from_chunk(chunk)
    document_id = _safe_str(metadata.get("document_uuid"), "unknown")
    return _safe_str(chunk.get("chunk_id"), f"{document_id}_chunk{fallback}")


def _source_txt(document: dict) -> str | None:
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    if metadata.get("source_txt"):
        return metadata.get("source_txt")

    source_evidence = document.get("source_evidence")
    if isinstance(source_evidence, list) and source_evidence:
        return source_evidence[0].get("source_txt")

    return None


def upsert_user(cursor, user_id: str) -> None:
    cursor.execute(
        """
        INSERT INTO users (user_id)
        VALUES (%s)
        ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
        """,
        (user_id,),
    )


def upsert_parsed_document(
    document: dict,
    json_path: str | Path | None = None,
    original_filename: str | None = None,
    stored_path: str | None = None,
    status: str = "parsed",
    error_message: str | None = None,
):
    document_id = _document_id_from_raw(document)
    user_id = _safe_str(document.get("user_id"), document_id)
    pages = document.get("pages") if isinstance(document.get("pages"), list) else []

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            upsert_user(cursor, user_id)
            cursor.execute(
                """
                INSERT INTO documents (
                  document_id, user_id, original_filename, stored_path, txt_path, json_path,
                  document_sector, document_date, document_type, company, document_title,
                  status, error_message, chunk_count, page_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  user_id = VALUES(user_id),
                  original_filename = COALESCE(VALUES(original_filename), original_filename),
                  stored_path = COALESCE(VALUES(stored_path), stored_path),
                  txt_path = COALESCE(VALUES(txt_path), txt_path),
                  json_path = COALESCE(VALUES(json_path), json_path),
                  document_sector = VALUES(document_sector),
                  document_date = VALUES(document_date),
                  document_type = VALUES(document_type),
                  company = VALUES(company),
                  document_title = VALUES(document_title),
                  status = VALUES(status),
                  error_message = VALUES(error_message),
                  chunk_count = GREATEST(chunk_count, VALUES(chunk_count)),
                  page_count = VALUES(page_count),
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document_id,
                    user_id,
                    original_filename,
                    stored_path,
                    _source_txt(document),
                    str(json_path) if json_path else None,
                    _safe_str(document.get("sector") or document.get("document_sector")),
                    _safe_str(document.get("document_date")),
                    _safe_str(document.get("document_type")),
                    _safe_str(document.get("company")),
                    _safe_str(document.get("document_title") or document.get("title"), document_id),
                    status,
                    error_message,
                    0,
                    document.get("pages_count") or document.get("page_count") or len(pages) or None,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"document_id": document_id, "user_id": user_id}


def upsert_chunked_document(
    chunks: list[dict],
    chunked_json_path: str | Path | None = None,
    source_json_path: str | Path | None = None,
    status: str = "chunked",
):
    if not chunks:
        return None

    first_meta = _metadata_from_chunk(chunks[0])
    document_id = _safe_str(first_meta.get("document_uuid"))
    if not document_id:
        raise ValueError("청크 metadata.document_uuid가 필요합니다.")

    user_id = _safe_str(first_meta.get("user_id"), document_id)
    page_count = first_meta.get("pages_count")

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            upsert_user(cursor, user_id)
            cursor.execute(
                """
                INSERT INTO documents (
                  document_id, user_id, json_path, document_sector, document_date,
                  document_type, company, document_title, status, chunk_count, page_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  user_id = VALUES(user_id),
                  json_path = COALESCE(VALUES(json_path), json_path),
                  document_sector = VALUES(document_sector),
                  document_date = VALUES(document_date),
                  document_type = VALUES(document_type),
                  company = VALUES(company),
                  document_title = VALUES(document_title),
                  status = VALUES(status),
                  chunk_count = VALUES(chunk_count),
                  page_count = VALUES(page_count),
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document_id,
                    user_id,
                    str(source_json_path or chunked_json_path) if (source_json_path or chunked_json_path) else None,
                    _safe_str(first_meta.get("sector")),
                    _safe_str(first_meta.get("document_date")),
                    _safe_str(first_meta.get("document_type")),
                    _safe_str(first_meta.get("company")),
                    _safe_str(first_meta.get("document_title"), document_id),
                    status,
                    len(chunks),
                    page_count,
                ),
            )

            for index, chunk in enumerate(chunks, start=1):
                metadata = _metadata_from_chunk(chunk)
                chunk_number = _chunk_number(chunk.get("chunk_id"), index)
                chroma_id = _chroma_id_for_chunk(chunk, index)
                text_preview = _safe_str(chunk.get("chunk"))[:500]
                cursor.execute(
                    """
                    INSERT INTO document_chunks (
                      document_id, chunk_id, page_number, chroma_id, text_preview
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      page_number = VALUES(page_number),
                      chroma_id = VALUES(chroma_id),
                      text_preview = VALUES(text_preview)
                    """,
                    (
                        document_id,
                        chunk_number,
                        metadata.get("page_number"),
                        chroma_id,
                        text_preview,
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"document_id": document_id, "chunk_count": len(chunks)}


def upsert_document_from_json(
    json_path: str,
    original_filename: str | None = None,
    stored_path: str | None = None,
    status: str = "parsed",
    error_message: str | None = None,
):
    json_path_obj = Path(json_path)
    payload = _read_json(json_path_obj)
    if isinstance(payload, list):
        return upsert_chunked_document(
            payload,
            chunked_json_path=json_path_obj,
            status="chunked",
        )

    return upsert_parsed_document(
        payload,
        json_path=json_path_obj,
        original_filename=original_filename,
        stored_path=stored_path,
        status=status,
        error_message=error_message,
    )


def mark_chunks_embedded(
    chunks: list[dict],
    chroma_ids: list[str],
    collection_name: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
):
    if not chunks:
        return 0

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            updated = 0
            document_ids = set()
            for chunk, chroma_id in zip(chunks, chroma_ids):
                metadata = _metadata_from_chunk(chunk)
                document_id = _safe_str(metadata.get("document_uuid"))
                chunk_number = _chunk_number(chunk.get("chunk_id") or chunk.get("generated_id"), 0)
                if not document_id or not chunk_number:
                    continue
                document_ids.add(document_id)
                cursor.execute(
                    """
                    UPDATE document_chunks
                    SET chroma_id = %s
                    WHERE document_id = %s AND chunk_id = %s
                    """,
                    (chroma_id, document_id, chunk_number),
                )
                updated += cursor.rowcount

            for document_id in document_ids:
                cursor.execute(
                    """
                    UPDATE documents
                    SET status = 'embedded', updated_at = CURRENT_TIMESTAMP
                    WHERE document_id = %s
                    """,
                    (document_id,),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return updated


def create_chat_session(user_id: str, title: str | None = None):
    session_id = uuid.uuid4().hex
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            upsert_user(cursor, user_id)
            cursor.execute(
                "INSERT INTO chat_sessions (session_id, user_id, title) VALUES (%s, %s, %s)",
                (session_id, user_id, title),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return session_id


def ensure_chat_session(user_id: str, session_id: str | None = None, title: str | None = None):
    if session_id:
        return session_id
    return create_chat_session(user_id=user_id, title=title)


def list_chat_sessions(user_id: str | None = None, query: str | None = None, limit: int = 50):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            where = []
            params = []
            if user_id:
                where.append("s.user_id = %s")
                params.append(user_id)
            if query:
                where.append(
                    """
                    (
                      s.title LIKE %s OR EXISTS (
                        SELECT 1 FROM chat_messages m2
                        WHERE m2.session_id = s.session_id AND m2.content LIKE %s
                      )
                    )
                    """
                )
                like_query = f"%{query}%"
                params.extend([like_query, like_query])

            where_sql = f"WHERE {' AND '.join(where)}" if where else ""
            params.append(limit)
            cursor.execute(
                f"""
                SELECT
                  s.session_id,
                  s.user_id,
                  CASE
                    WHEN s.title IS NULL OR s.title = '' OR s.title = 'Web RAG Chat'
                      OR s.title LIKE '문서 질의응답:%%'
                    THEN COALESCE(
                      (
                        SELECT LEFT(m1.content, 255)
                        FROM chat_messages m1
                        WHERE m1.session_id = s.session_id AND m1.role = 'user'
                        ORDER BY m1.created_at ASC, m1.message_id ASC
                        LIMIT 1
                      ),
                      s.title,
                      '제목 없는 채팅'
                    )
                    ELSE s.title
                  END AS title,
                  s.created_at,
                  s.updated_at,
                  COUNT(m.message_id) AS message_count,
                  MAX(m.created_at) AS last_message_at,
                  (
                    SELECT m3.content
                    FROM chat_messages m3
                    WHERE m3.session_id = s.session_id
                    ORDER BY m3.created_at DESC, m3.message_id DESC
                    LIMIT 1
                  ) AS last_message
                FROM chat_sessions s
                LEFT JOIN chat_messages m ON m.session_id = s.session_id
                {where_sql}
                GROUP BY s.session_id, s.user_id, s.title, s.created_at, s.updated_at
                ORDER BY COALESCE(MAX(m.created_at), s.updated_at) DESC
                LIMIT %s
                """,
                params,
            )
            return list(cursor.fetchall())
    finally:
        conn.close()


def get_chat_messages(session_id: str, user_id: str | None = None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            params = [session_id]
            user_filter = ""
            if user_id:
                user_filter = "AND user_id = %s"
                params.append(user_id)
            cursor.execute(
                f"""
                SELECT message_id, session_id, user_id, document_id, role, content, created_at
                FROM chat_messages
                WHERE session_id = %s {user_filter}
                ORDER BY created_at ASC, message_id ASC
                """,
                params,
            )
            return list(cursor.fetchall())
    finally:
        conn.close()


def delete_chat_session(session_id: str, user_id: str | None = None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            params = [session_id]
            user_filter = ""
            if user_id:
                user_filter = "AND user_id = %s"
                params.append(user_id)

            cursor.execute(
                f"""
                SELECT session_id
                FROM chat_sessions
                WHERE session_id = %s {user_filter}
                """,
                params,
            )
            session = cursor.fetchone()
            if not session:
                return {
                    "deleted": False,
                    "session_id": session_id,
                    "message": "채팅 세션을 찾을 수 없습니다.",
                }

            cursor.execute(
                """
                DELETE rs
                FROM retrieved_sources rs
                INNER JOIN chat_messages cm ON cm.message_id = rs.message_id
                WHERE cm.session_id = %s
                """,
                (session_id,),
            )
            sources_deleted = cursor.rowcount

            cursor.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
            messages_deleted = cursor.rowcount

            cursor.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))
            sessions_deleted = cursor.rowcount

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "deleted": sessions_deleted > 0,
        "session_id": session_id,
        "deleted_counts": {
            "chat_sessions": sessions_deleted,
            "chat_messages": messages_deleted,
            "retrieved_sources": sources_deleted,
        },
    }


def insert_chat_message(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    document_id: str | None = None,
):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            upsert_user(cursor, user_id)
            cursor.execute(
                """
                INSERT IGNORE INTO chat_sessions (session_id, user_id, title)
                VALUES (%s, %s, %s)
                """,
                (session_id, user_id, "Web RAG Chat"),
            )
            cursor.execute(
                """
                INSERT INTO chat_messages (session_id, user_id, document_id, role, content)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (session_id, user_id, document_id, role, content),
            )
            message_id = cursor.lastrowid
            cursor.execute(
                """
                UPDATE chat_sessions
                SET updated_at = CURRENT_TIMESTAMP,
                    title = CASE
                      WHEN (
                        title IS NULL OR title = '' OR title = 'Web RAG Chat'
                        OR title LIKE '문서 질의응답:%%'
                      ) AND %s = 'user'
                      THEN LEFT(%s, 255)
                      ELSE title
                    END
                WHERE session_id = %s
                """,
                (role, content, session_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return message_id


def insert_retrieved_sources(message_id: int, sources):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for source in sources:
                metadata = source.get("metadata", {})
                source_chunk_id = metadata.get("chunk_id") or None
                if source_chunk_id:
                    source_chunk_id = _chunk_number(source_chunk_id, 0) or None
                cursor.execute(
                    """
                    INSERT INTO retrieved_sources (
                      message_id, document_id, chunk_id, page_number, distance, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, CAST(%s AS JSON))
                    """,
                    (
                        message_id,
                        metadata.get("document_id") or metadata.get("document_uuid"),
                        source_chunk_id,
                        metadata.get("page_number") or None,
                        source.get("distance") or source.get("score"),
                        _json_dumps(metadata),
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_documents(user_id: str | None = None, limit: int = 50):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if user_id:
                cursor.execute(
                    """
                    SELECT document_id, user_id, original_filename, document_sector,
                           document_date, document_type, company, document_title,
                           status, chunk_count, page_count, created_at, updated_at
                    FROM documents
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT document_id, user_id, original_filename, document_sector,
                           document_date, document_type, company, document_title,
                           status, chunk_count, page_count, created_at, updated_at
                    FROM documents
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            return cursor.fetchall()
    finally:
        conn.close()


def delete_document(document_id: str):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT document_id, stored_path, txt_path, json_path
                FROM documents
                WHERE document_id = %s
                """,
                (document_id,),
            )
            document = cursor.fetchone()
            if not document:
                return {
                    "deleted": False,
                    "document_id": document_id,
                    "message": "문서를 찾을 수 없습니다.",
                    "counts": {},
                    "paths": {},
                }

            paths = {
                "stored_path": document.get("stored_path"),
                "txt_path": document.get("txt_path"),
                "json_path": document.get("json_path"),
            }
            counts = {}

            cursor.execute("DELETE FROM retrieved_sources WHERE document_id = %s", (document_id,))
            counts["retrieved_sources"] = cursor.rowcount

            cursor.execute(
                "UPDATE chat_messages SET document_id = NULL WHERE document_id = %s",
                (document_id,),
            )
            counts["chat_messages_unlinked"] = cursor.rowcount

            cursor.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))
            counts["document_chunks"] = cursor.rowcount

            cursor.execute("DELETE FROM documents WHERE document_id = %s", (document_id,))
            counts["documents"] = cursor.rowcount

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "deleted": counts.get("documents", 0) > 0,
        "document_id": document_id,
        "message": "삭제 완료",
        "counts": counts,
        "paths": paths,
    }
