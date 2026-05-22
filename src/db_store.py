import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

try:
    from scripts.chunking import split_text_with_langchain
except ModuleNotFoundError:
    from chunking import split_text_with_langchain


load_dotenv()


def mysql_enabled():
    return bool(os.getenv("MYSQL_HOST") and os.getenv("MYSQL_DATABASE"))


def get_connection(database: str = None):
    try:
        import pymysql
    except ImportError as error:
        raise ImportError("PyMySQL이 설치되어 있지 않습니다. `pip install pymysql`을 실행하세요.") from error

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


def execute_schema(schema_path: str = "db/schema.sql"):
    database = os.getenv("MYSQL_DATABASE")
    if not database:
        raise ValueError("MYSQL_DATABASE 환경변수가 필요합니다.")

    conn = get_connection(database=None)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "CREATE DATABASE IF NOT EXISTS `{}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci".format(
                    database.replace("`", "``")
                )
            )
            cursor.execute("USE `{}`".format(database.replace("`", "``")))
            statements = [
                statement.strip()
                for statement in Path(schema_path).read_text(encoding="utf-8").split(";")
                if statement.strip()
            ]
            for statement in statements:
                cursor.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def split_text_into_chunks(text: str, chunk_size: int = 800, chunk_overlap: int = 120):
    return split_text_with_langchain(
        text=text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        include_page_number=False,
    )


def read_legacy_text(document: dict, json_path: Path):
    source_evidence = document.get("source_evidence", [])
    if source_evidence and isinstance(source_evidence, list):
        source_txt = source_evidence[0].get("source_txt")
        if source_txt and Path(source_txt).exists():
            return Path(source_txt).read_text(encoding="utf-8")

    for candidate in [json_path.with_suffix(".txt"), json_path.with_suffix(".md")]:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")

    return document.get("full_text") or document.get("summary") or ""


def normalize_storage_document(document: dict, json_path: Path):
    document_id = document.get("document_id") or document.get("doc_id") or json_path.stem
    user_id = document.get("user_id") or document.get("customer_id") or document_id
    source_evidence = document.get("source_evidence", [])
    first_evidence = source_evidence[0] if source_evidence else {}
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}

    if not metadata:
        metadata = {
            "customer_name": document.get("customer_name", ""),
            "checked_items": document.get("checked_items", {}),
            "source_image": first_evidence.get("source_image", ""),
            "source_txt": first_evidence.get("source_txt", str(json_path.with_suffix(".txt"))),
            "ocr_model": first_evidence.get("model", ""),
            "ocr_status": first_evidence.get("status", ""),
            "error_message": first_evidence.get("error_message"),
            "created_at": first_evidence.get("created_at", ""),
        }

    full_text = document.get("full_text") or read_legacy_text(document, json_path)
    chunks = document.get("chunks") or split_text_into_chunks(full_text)

    return {
        "document_id": document_id,
        "user_id": user_id,
        "document_sector": document.get("document_sector") or document.get("sector") or "",
        "document_date": document.get("document_date") or "",
        "document_type": document.get("document_type") or document.get("doc_type") or "",
        "company": document.get("company") or "",
        "document_title": document.get("document_title") or document.get("title") or document_id,
        "page_count": document.get("page_count"),
        "chunks": chunks,
        "metadata": metadata,
    }


def upsert_user(cursor, user_id: str):
    cursor.execute(
        """
        INSERT INTO users (user_id)
        VALUES (%s)
        ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
        """,
        (user_id,),
    )


def column_exists(cursor, table_name: str, column_name: str):
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    return cursor.fetchone()["count"] > 0


def ensure_page_columns(cursor):
    columns = [
        (
            "documents",
            "page_count",
            "ALTER TABLE documents ADD COLUMN page_count INT NULL AFTER chunk_count",
        ),
        (
            "document_chunks",
            "page_number",
            "ALTER TABLE document_chunks ADD COLUMN page_number INT NULL AFTER chunk_id",
        ),
        (
            "retrieved_sources",
            "page_number",
            "ALTER TABLE retrieved_sources ADD COLUMN page_number INT NULL AFTER chunk_id",
        ),
    ]

    for table_name, column_name, ddl in columns:
        if not column_exists(cursor, table_name, column_name):
            cursor.execute(ddl)

    if column_exists(cursor, "retrieved_sources", "source_text"):
        cursor.execute("ALTER TABLE retrieved_sources DROP COLUMN source_text")


def upsert_document_from_json(
    json_path: str,
    original_filename: str = None,
    stored_path: str = None,
    status: str = "processed",
    error_message: str = None,
):
    json_path = Path(json_path)
    document = normalize_storage_document(
        json.loads(json_path.read_text(encoding="utf-8")),
        json_path,
    )

    document_id = document.get("document_id")
    user_id = document.get("user_id") or document_id
    metadata = document.get("metadata", {})
    chunks = document.get("chunks", [])
    txt_path = metadata.get("source_txt") or str(json_path.with_suffix(".txt"))

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            ensure_page_columns(cursor)
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
                  original_filename = VALUES(original_filename),
                  stored_path = VALUES(stored_path),
                  txt_path = VALUES(txt_path),
                  json_path = VALUES(json_path),
                  document_sector = VALUES(document_sector),
                  document_date = VALUES(document_date),
                  document_type = VALUES(document_type),
                  company = VALUES(company),
                  document_title = VALUES(document_title),
                  status = VALUES(status),
                  error_message = VALUES(error_message),
                  chunk_count = VALUES(chunk_count),
                  page_count = VALUES(page_count),
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document_id,
                    user_id,
                    original_filename,
                    stored_path or metadata.get("source_file"),
                    txt_path,
                    str(json_path),
                    document.get("document_sector"),
                    document.get("document_date"),
                    document.get("document_type"),
                    document.get("company"),
                    document.get("document_title"),
                    status,
                    error_message,
                    len(chunks),
                    document.get("page_count"),
                ),
            )

            for chunk in chunks:
                chunk_id = int(chunk.get("chunk_id"))
                page_number = chunk.get("page_number")
                chroma_id = f"{document_id}_chunk_{chunk_id:03d}"
                preview = (chunk.get("text") or "")[:500]
                cursor.execute(
                    """
                    INSERT INTO document_chunks (document_id, chunk_id, page_number, chroma_id, text_preview)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      page_number = VALUES(page_number),
                      chroma_id = VALUES(chroma_id),
                      text_preview = VALUES(text_preview)
                    """,
                    (document_id, chunk_id, page_number, chroma_id, preview),
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return document


def create_chat_session(user_id: str, title: str = None):
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


def ensure_chat_session(user_id: str, session_id: str = None, title: str = None):
    if session_id:
        return session_id
    return create_chat_session(user_id=user_id, title=title)


def list_chat_sessions(user_id: str = None, query: str = None, limit: int = 50):
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


def get_chat_messages(session_id: str, user_id: str = None):
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


def insert_chat_message(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    document_id: str = None,
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
            ensure_page_columns(cursor)
            for source in sources:
                metadata = source.get("metadata", {})
                cursor.execute(
                    """
                    INSERT INTO retrieved_sources (
                      message_id, document_id, chunk_id, page_number, distance, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, CAST(%s AS JSON))
                    """,
                    (
                        message_id,
                        metadata.get("document_id"),
                        metadata.get("chunk_id") or None,
                        metadata.get("page_number") or None,
                        source.get("distance"),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_documents(user_id: str = None, limit: int = 50):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if user_id:
                cursor.execute(
                    """
                    SELECT document_id, user_id, original_filename, document_sector,
                           document_date, document_type, company, document_title,
                           status, chunk_count, created_at, updated_at
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
                           status, chunk_count, created_at, updated_at
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

            cursor.execute(
                "DELETE FROM retrieved_sources WHERE document_id = %s",
                (document_id,),
            )
            counts["retrieved_sources"] = cursor.rowcount

            cursor.execute(
                "UPDATE chat_messages SET document_id = NULL WHERE document_id = %s",
                (document_id,),
            )
            counts["chat_messages_unlinked"] = cursor.rowcount

            cursor.execute(
                "DELETE FROM document_chunks WHERE document_id = %s",
                (document_id,),
            )
            counts["document_chunks"] = cursor.rowcount

            cursor.execute(
                "DELETE FROM documents WHERE document_id = %s",
                (document_id,),
            )
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
