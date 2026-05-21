import argparse
import os
from pathlib import Path

import chromadb
from google import genai
from google.genai import types

BASE_DIR    = Path(__file__).resolve().parent.parent.parent
ENV_PATH    = BASE_DIR / ".env"
CHROMA_DIR  = BASE_DIR / "data" / "vectordb" / "image_chroma"
COLLECTION  = "image_docs"
EMBED_MODEL = "text-embedding-004"
GEN_MODEL   = "gemini-3.1-flash-lite-preview"


# ── 환경변수 로드 ──────────────────────────────────────────────────────────────

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# ── 검색 ──────────────────────────────────────────────────────────────────────

def search(
    query: str,
    ai_client: genai.Client,
    collection,
    top_k: int = 5,
) -> list[dict]:
    """질문을 임베딩해서 ChromaDB에서 유사한 청크 검색"""

    # 질문 임베딩
    response = ai_client.models.embed_content(
        model=EMBED_MODEL,
        contents=[query],
    )
    query_embedding = response.embeddings[0].values

    # ChromaDB 검색
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({
            "text":     doc,
            "metadata": meta,
            "score":    round(1 - dist, 4),
        })

    return chunks


# ── 답변 생성 ──────────────────────────────────────────────────────────────────

def generate_answer(
    query: str,
    chunks: list[dict],
    ai_client: genai.Client,
) -> str:
    """검색된 청크를 참고해서 Gemini로 답변 생성"""

    context_parts = []
    for c in chunks:
        m = c["metadata"]
        header = f"[{m.get('company', '-')} / {m.get('document_type', '-')} / {m.get('document_date', '-')}]"
        context_parts.append(f"{header}\n{c['text']}")

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""당신은 금융 문서 전문 AI 어시스턴트입니다.
아래 참고 문서를 바탕으로 사용자의 질문에 한국어로 답변하세요.

규칙:
- 참고 문서에 근거해서 답변하세요.
- 참고 문서에 없는 내용은 추측하지 마세요.
- 관련 회사명, 문서 종류를 언급해주세요.
- 간결하고 이해하기 쉽게 작성하세요.

[참고 문서]
{context}

[질문]
{query}

[답변]"""

    response = ai_client.models.generate_content(
        model=GEN_MODEL,
        contents=prompt,
    )
    return (response.text or "답변을 생성할 수 없습니다.").strip()


# ── 일반 대화 (이미지 없을 때) ────────────────────────────────────────────────

def chat(query: str, chat_history: list[dict], gen_client: genai.Client) -> str:
    """이미지 없이 일반 대화 + 대화 기록 유지"""

    history_str = ""
    if chat_history:
        lines = []
        for msg in chat_history[-6:]:
            role = "사용자" if msg["role"] == "user" else "AI"
            lines.append(f"{role}: {msg['content']}")
        history_str = "\n".join(lines)

    prompt = f"""당신은 금융 전문 AI 어시스턴트 FinMate입니다.
사용자와 자연스럽게 대화하며, 금융 관련 질문에는 전문적으로 답변하세요.
이전 대화 내용을 기억하고 자연스럽게 이어가세요.

[이전 대화]
{history_str if history_str else "없음"}

[현재 질문]
{query}

[답변]"""

    response = gen_client.models.generate_content(
        model=GEN_MODEL,
        contents=prompt,
    )
    return (response.text or "죄송해요, 다시 질문해주세요.").strip()


# ── 클라이언트 생성 헬퍼 ──────────────────────────────────────────────────────

def _make_clients():
    load_dotenv(ENV_PATH)
    project  = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    embed_client = genai.Client(vertexai=True, project=project, location="us-central1")
    gen_client   = genai.Client(vertexai=True, project=project, location=location)
    return embed_client, gen_client


# ── 실시간 이미지 OCR ──────────────────────────────────────────────────────────

def ocr_image_bytes(image_bytes: bytes, mime_type: str, gen_client: genai.Client) -> str:
    """업로드된 이미지 바이트 → OCR 텍스트"""
    prompt = """
    이 이미지는 한국어 금융 문서입니다. OCR만 수행하세요.
    보이는 텍스트를 원문 그대로 추출하고, 표는 줄바꿈으로 정리하세요.
    없는 내용은 추측하지 말고, 읽기 어려운 글자는 [불명확]으로 표시하세요.
    """
    response = gen_client.models.generate_content(
        model=GEN_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
    )
    return (response.text or "").strip()


# ── 업로드 이미지 기반 Q&A (대화 기록 포함) ───────────────────────────────────

def answer_from_text(
    query: str,
    ocr_text: str,
    chat_history: list[dict],
    gen_client: genai.Client,
) -> str:
    """OCR 텍스트 + 대화 기록 → Gemini 답변"""

    # 대화 기록 문자열 생성
    history_str = ""
    if chat_history:
        lines = []
        for msg in chat_history[-6:]:   # 최근 6턴만
            role = "사용자" if msg["role"] == "user" else "AI"
            lines.append(f"{role}: {msg['content']}")
        history_str = "\n".join(lines)

    prompt = f"""당신은 금융 문서 전문 AI 어시스턴트입니다.
사용자가 업로드한 금융 문서 내용을 바탕으로 질문에 한국어로 답변하세요.

규칙:
- 문서 내용에 근거해서 답변하세요.
- 문서에 없는 내용은 추측하지 마세요.
- 이전 대화 내용을 참고해서 자연스럽게 답변하세요.
- 간결하고 이해하기 쉽게 작성하세요.

[업로드 문서 내용]
{ocr_text}

[이전 대화]
{history_str if history_str else "없음"}

[현재 질문]
{query}

[답변]"""

    response = gen_client.models.generate_content(
        model=GEN_MODEL,
        contents=prompt,
    )
    return (response.text or "답변을 생성할 수 없습니다.").strip()


# ── RAG 통합 함수 ─────────────────────────────────────────────────────────────

def rag(query: str, top_k: int = 5) -> dict:
    """질문 → 검색 → 답변 (외부에서 import해서 쓸 수 있음)"""
    load_dotenv(ENV_PATH)
    project  = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

    # 임베딩: us-central1 / 답변 생성: 원래 location(global)
    embed_client = genai.Client(vertexai=True, project=project, location="us-central1")
    gen_client   = genai.Client(vertexai=True, project=project, location=location)

    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection   = chroma_client.get_collection(COLLECTION)

    chunks = search(query, embed_client, collection, top_k=top_k)
    answer = generate_answer(query, chunks, gen_client)

    return {"answer": answer, "sources": chunks}


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="이미지 문서 RAG 질의응답")
    parser.add_argument("--query",  required=True, help="질문 입력")
    parser.add_argument("--top-k",  type=int, default=5, help="참고 문서 수")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    project  = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

    if not project:
        raise RuntimeError(".env에 GOOGLE_CLOUD_PROJECT=프로젝트ID 를 넣어주세요.")

    # 임베딩: us-central1 / 답변 생성: 원래 location(global)
    embed_client  = genai.Client(vertexai=True, project=project, location="us-central1")
    gen_client    = genai.Client(vertexai=True, project=project, location=location)
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection    = chroma_client.get_collection(COLLECTION)

    print(f"\n질문: {args.query}\n")

    # 검색
    chunks = search(args.query, embed_client, collection, top_k=args.top_k)

    print(f"── 참고 문서 {len(chunks)}건 ──")
    for i, c in enumerate(chunks, 1):
        m = c["metadata"]
        print(f"  {i}. [{m.get('company', '-')}] {m.get('document_type', '-')} | 유사도: {c['score']}")
    print()

    # 답변 생성
    answer = generate_answer(args.query, chunks, gen_client)

    print("── 답변 ──")
    print(answer)


if __name__ == "__main__":
    main()


'''
임베딩 (text-embedding-004) → us-central1만 됨
답변 생성 (gemini-3.1-flash-lite-preview) → global만 됨
'''