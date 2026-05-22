import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


DEFAULT_CHROMA_DIR = "data/chroma_db"
DEFAULT_COLLECTION_NAME = "financial_documents"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_LLM_MODEL = "gemini-3.5-flash"
DEFAULT_LLM_MAX_RETRIES = 4
DEFAULT_LLM_RETRY_SLEEP = 10.0


load_dotenv()


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("GEMINI_API_KEY가 없습니다. .env 파일을 확인하세요.")

    return genai.Client(api_key=api_key)


def embed_query(client, query: str, model_name: str):
    response = client.models.embed_content(
        model=model_name,
        contents=[query],
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
        ),
    )

    return response.embeddings[0].values


def build_where(args):
    where = {}

    if args.document_type:
        where["document_type"] = args.document_type
    if args.company:
        where["company"] = args.company

    if not where:
        return None
    if len(where) == 1:
        return where

    return {"$and": [{key: value} for key, value in where.items()]}


def search_chunks(
    query: str,
    where,
    top_k: int,
    chroma_dir: str,
    collection_name: str,
    embedding_model: str,
    gemini_client,
):
    import chromadb

    query_embedding = embed_query(
        client=gemini_client,
        query=query,
        model_name=embedding_model,
    )

    chroma_client = chromadb.PersistentClient(path=chroma_dir)
    collection = chroma_client.get_collection(name=collection_name)
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    for index, (document, metadata, distance) in enumerate(
        zip(documents, metadatas, distances),
        start=1,
    ):
        hits.append(
            {
                "rank": index,
                "distance": distance,
                "metadata": metadata,
                "text": document,
            }
        )

    return hits


def format_context(hits):
    blocks = []

    for hit in hits:
        metadata = hit["metadata"]
        source = (
            f"source_id: {hit['rank']}\n"
            f"document_id: {metadata.get('document_id', '')}\n"
            f"user_id: {metadata.get('user_id', '')}\n"
            f"document_type: {metadata.get('document_type', '')}\n"
            f"company: {metadata.get('company', '')}\n"
            f"document_title: {metadata.get('document_title', '')}\n"
            f"chunk_id: {metadata.get('chunk_id', '')}\n"
            f"distance: {hit['distance']}\n"
            f"text:\n{hit['text']}"
        )
        blocks.append(source)

    return "\n\n---\n\n".join(blocks)


def generate_content_with_retry(
    client,
    model_name: str,
    prompt: str,
    max_retries: int,
    retry_sleep: float,
):
    retry_markers = ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED"]

    for attempt in range(1, max_retries + 1):
        try:
            return client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                ),
            )
        except Exception as error:
            message = str(error)
            should_retry = any(marker in message for marker in retry_markers)

            if not should_retry or attempt >= max_retries:
                raise

            wait_seconds = retry_sleep * attempt
            print(
                f"LLM 호출 일시 실패. {wait_seconds:.0f}초 후 재시도합니다. "
                f"({attempt}/{max_retries})",
                flush=True,
            )
            time.sleep(wait_seconds)


def strip_source_id_footer(text: str) -> str:
    lines = text.strip().splitlines()

    while lines and lines[-1].strip().lower().startswith(
        (
            "source_id",
            "source id",
            "sources:",
            "source:",
            "근거 source_id",
            "사용한 근거",
        )
    ):
        lines.pop()

    return "\n".join(lines).strip()


def generate_answer(
    client,
    model_name: str,
    question: str,
    hits,
    max_retries: int,
    retry_sleep: float,
):
    context = format_context(hits)
    prompt = f"""
너는 금융 문서 RAG 질의응답 챗봇이다.
아래 [검색된 문서 근거]만 사용해서 답변해라.

규칙:
- 근거에 없는 내용은 추측하지 말고 "문서에서 확인되지 않습니다."라고 답해라.
- 금액, 날짜, 비율, 회사명, 문서명은 근거에 있는 그대로 답해라.
- source_id, chunk_id, distance 같은 내부 검색 식별자는 답변에 출력하지 마라.
- 한국어로 간결하게 답해라.

[검색된 문서 근거]
{context}

[질문]
{question}
""".strip()

    response = generate_content_with_retry(
        client=client,
        model_name=model_name,
        prompt=prompt,
        max_retries=max_retries,
        retry_sleep=retry_sleep,
    )

    return strip_source_id_footer(response.text or "")


def generate_summary(
    client,
    model_name: str,
    hits,
    summary_type: str,
    max_retries: int,
    retry_sleep: float,
):
    context = format_context(hits)

    if summary_type == "brief":
        instruction = "문서 내용을 5개 이내 bullet로 요약해라."
    else:
        instruction = """
다음 항목으로 문서를 요약해라.
1. 문서 개요
2. 핵심 수치/조건
3. 주요 조항 또는 유의사항
4. 고객/회사 관련 핵심 정보
5. 확인이 필요한 부분
""".strip()

    prompt = f"""
너는 금융 문서 요약 전문가이다.
아래 [검색된 문서 근거]만 사용해서 요약해라.
근거에 없는 내용은 만들지 마라.

[요약 방식]
{instruction}

[검색된 문서 근거]
{context}
""".strip()

    response = generate_content_with_retry(
        client=client,
        model_name=model_name,
        prompt=prompt,
        max_retries=max_retries,
        retry_sleep=retry_sleep,
    )

    return (response.text or "").strip()


def print_sources(hits):
    print("\n[검색 근거]")
    for hit in hits:
        metadata = hit["metadata"]
        preview = hit["text"].replace("\n", " ")[:180]
        print(
            f"- source_id={hit['rank']} "
            f"document_id={metadata.get('document_id', '')} "
            f"chunk_id={metadata.get('chunk_id', '')} "
            f"distance={hit['distance']:.4f} "
            f"preview={preview}"
        )


def save_chat_log(path: str, payload: dict):
    if not path:
        return

    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Chroma RAG 검색 결과를 Gemini LLM에 전달해 답변 또는 요약을 생성합니다."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask_parser = subparsers.add_parser("ask", help="질문에 답변합니다.")
    ask_parser.add_argument("question", help="사용자 질문")

    summary_parser = subparsers.add_parser("summary", help="문서를 요약합니다.")
    summary_parser.add_argument(
        "--summary-type",
        choices=["brief", "detailed"],
        default="detailed",
        help="요약 방식"
    )

    for subparser in [ask_parser, summary_parser]:
        subparser.add_argument(
            "--user-id",
            default=None,
            help="호환용 인자입니다. RAG 검색 범위 제한에는 사용하지 않습니다.",
        )
        subparser.add_argument(
            "--document-id",
            default=None,
            help="호환용 인자입니다. RAG 검색 범위 제한에는 사용하지 않습니다.",
        )
        subparser.add_argument("--document-type", default=None)
        subparser.add_argument("--company", default=None)
        subparser.add_argument("--chroma-dir", default=DEFAULT_CHROMA_DIR)
        subparser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
        subparser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
        subparser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
        subparser.add_argument("--llm-max-retries", type=int, default=DEFAULT_LLM_MAX_RETRIES)
        subparser.add_argument("--llm-retry-sleep", type=float, default=DEFAULT_LLM_RETRY_SLEEP)
        subparser.add_argument("--top-k", type=int, default=6)
        subparser.add_argument("--show-sources", action="store_true")
        subparser.add_argument(
            "--chat-log",
            default=None,
            help="JSONL 형태로 질문/답변/근거를 저장할 경로"
        )

    args = parser.parse_args()

    gemini_client = get_gemini_client()
    where = build_where(args)

    if args.command == "ask":
        query = args.question
    else:
        query_parts = ["문서의 핵심 내용, 주요 수치, 조건, 유의사항을 요약"]
        if args.document_type:
            query_parts.append(args.document_type)
        if args.company:
            query_parts.append(args.company)
        query = " ".join(query_parts)

    hits = search_chunks(
        query=query,
        where=where,
        top_k=args.top_k,
        chroma_dir=args.chroma_dir,
        collection_name=args.collection,
        embedding_model=args.embedding_model,
        gemini_client=gemini_client,
    )

    if not hits:
        print("검색된 문서 근거가 없습니다. 필터 조건이나 인덱싱 상태를 확인하세요.")
        return

    if args.command == "ask":
        answer = generate_answer(
            client=gemini_client,
            model_name=args.llm_model,
            question=args.question,
            hits=hits,
            max_retries=args.llm_max_retries,
            retry_sleep=args.llm_retry_sleep,
        )
        print(answer)
        log_payload = {
            "command": "ask",
            "question": args.question,
            "answer": answer,
            "sources": hits,
        }
    else:
        answer = generate_summary(
            client=gemini_client,
            model_name=args.llm_model,
            hits=hits,
            summary_type=args.summary_type,
            max_retries=args.llm_max_retries,
            retry_sleep=args.llm_retry_sleep,
        )
        print(answer)
        log_payload = {
            "command": "summary",
            "summary_type": args.summary_type,
            "answer": answer,
            "sources": hits,
        }

    if args.show_sources:
        print_sources(hits)

    save_chat_log(args.chat_log, log_payload)


if __name__ == "__main__":
    main()
