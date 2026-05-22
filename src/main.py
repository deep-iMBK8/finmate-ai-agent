import json
import os
from pathlib import Path

import chromadb
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from google import genai
from sentence_transformers import SentenceTransformer


BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
QUERY_EMBED_MODEL = "BAAI/bge-m3"
CHROMA_DIR = BASE_DIR / "data" / "vectordb" / "chroma" / "stock"
CHROMA_COLLECTION_NAME = "stock_documents"

_query_embedder = None

app = FastAPI(title="finmate-ai-agent")


HTML_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FinMate AI Agent</title>
  <style>
    :root {
      --bg: #eef5ff;
      --panel: #ffffff;
      --line: #d7e6ff;
      --text: #18324a;
      --muted: #6b84a0;
      --accent: #2c6bed;
      --accent-dark: #1f56c7;
      --accent-soft: #eaf2ff;
      --user: #dff0ff;
      --bot: #f4f8ff;
      --shadow: 0 18px 38px rgba(44, 107, 237, 0.10);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans KR", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(135, 181, 255, 0.30) 0, transparent 24%),
        radial-gradient(circle at bottom right, rgba(92, 155, 255, 0.20) 0, transparent 22%),
        linear-gradient(180deg, #f9fcff 0%, var(--bg) 100%);
      color: var(--text);
    }

    .wrap {
      max-width: 1040px;
      margin: 32px auto;
      padding: 0 20px;
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 20px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
    }

    .side {
      padding: 20px;
      display: grid;
      gap: 18px;
      align-content: start;
    }

    h1 {
      margin: 0 0 8px;
      font-size: 30px;
      letter-spacing: 0;
    }

    h2 {
      margin: 0 0 10px;
      font-size: 18px;
    }

    .desc {
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
      font-size: 14px;
    }

    .upload-box {
      border: 1px dashed #b8d2ff;
      border-radius: 16px;
      padding: 16px;
      background: linear-gradient(180deg, #ffffff 0%, #f7fbff 100%);
    }

    .system-box {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px 16px;
      background: linear-gradient(180deg, #f7fbff 0%, #edf5ff 100%);
    }

    .system-box h3 {
      margin: 0 0 10px;
      font-size: 15px;
    }

    .system-list {
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      background: #dff0ff;
      color: #1d5fd1;
      font-size: 12px;
      font-weight: 700;
    }

    .upload-box input[type=file] {
      width: 100%;
    }

    .button {
      border: 0;
      border-radius: 12px;
      background: var(--accent);
      color: white;
      padding: 12px 16px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      box-shadow: 0 10px 20px rgba(44, 107, 237, 0.20);
      transition: background 0.18s ease, transform 0.18s ease, box-shadow 0.18s ease;
    }

    .button:hover {
      background: var(--accent-dark);
      transform: translateY(-1px);
      box-shadow: 0 14px 24px rgba(44, 107, 237, 0.24);
    }

    .file-list {
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 14px;
    }

    .chat-panel {
      overflow: hidden;
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 720px;
    }

    .chat-head {
      padding: 18px 20px 0;
      display: flex;
      justify-content: space-between;
      color: var(--muted);
      font-size: 13px;
    }

    .messages {
      padding: 20px;
      overflow-y: auto;
      display: grid;
      align-content: start;
      gap: 12px;
      min-height: 500px;
      background:
        linear-gradient(180deg, rgba(247, 251, 255, 0.96) 0%, rgba(255, 255, 255, 0.92) 100%);
    }

    .msg {
      width: fit-content;
      max-width: min(82%, 720px);
      padding: 14px 16px;
      border-radius: 16px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: keep-all;
      overflow-wrap: anywhere;
      border: 1px solid transparent;
    }

    .msg.user {
      margin-left: auto;
      background: var(--user);
      border-color: #c9e5ff;
    }

    .msg.bot {
      background: var(--bot);
      border-color: #dfeafb;
    }

    .composer {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      padding: 16px;
      border-top: 1px solid var(--line);
      background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
    }

    textarea {
      width: 100%;
      min-height: 68px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 14px;
      resize: vertical;
      font: inherit;
      color: var(--text);
      background: #ffffff;
      outline: none;
      transition: border-color 0.18s ease, box-shadow 0.18s ease;
    }

    textarea:focus,
    .upload-box input[type=file]:focus {
      border-color: #8db6ff;
      box-shadow: 0 0 0 4px rgba(44, 107, 237, 0.10);
    }

    .status {
      color: var(--muted);
      font-size: 13px;
    }

    @media (max-width: 900px) {
      .wrap {
        grid-template-columns: 1fr;
      }

      .chat-panel {
        min-height: 640px;
      }
    }
  </style>
</head>
<body>
  <main class="wrap">
    <section class="panel side">
      <div>
        <h1>FinMate</h1>
        <p class="desc">문서를 업로드하고, Chroma 검색으로 관련 내용을 찾은 뒤 Gemini가 답변하는 금융문서 챗봇입니다.</p>
      </div>

      <div class="system-box">
        <h3>현재 파이프라인</h3>
        <div class="status-pill">Chroma 검색 기반</div>
        <ul class="system-list" style="margin-top: 10px;">
          <li>벡터 검색: ChromaDB</li>
          <li>응답 생성: Gemini 유지</li>
          <li>현재 상태: UI 연결 완료, 검색 결과 기반 응답 연결</li>
        </ul>
      </div>

      <div>
        <h2>파일 업로드</h2>
        <form id="upload-form" class="upload-box">
          <input id="file-input" type="file" name="files" multiple accept=".pdf,.json,.png,.jpg,.jpeg,.html">
          <div style="height: 12px"></div>
          <button class="button" type="submit">업로드</button>
        </form>
        <p id="upload-status" class="status">업로드된 파일이 없습니다.</p>
        <ul id="uploaded-files" class="file-list"></ul>
      </div>
    </section>

    <section class="panel chat-panel">
      <div class="chat-head">
        <span>Chat API · Chroma + Gemini</span>
        <span id="chat-status">Status: ready</span>
      </div>

      <div id="messages" class="messages">
        <div class="msg bot">안녕하세요. 현재 챗봇은 Chroma 검색 결과를 바탕으로 Gemini가 응답하는 구조입니다. 질문을 입력하면 저장된 금융문서 임베딩에서 관련 내용을 찾은 뒤 답변합니다.</div>
      </div>

      <form id="chat-form" class="composer">
        <textarea id="question" placeholder="예: 업로드한 문서에서 투자위험 관련 내용을 알려줘"></textarea>
        <button class="button" type="submit">질문 보내기</button>
      </form>
    </section>
  </main>

  <script>
    const uploadForm = document.getElementById("upload-form");
    const fileInput = document.getElementById("file-input");
    const uploadStatus = document.getElementById("upload-status");
    const uploadedFiles = document.getElementById("uploaded-files");

    const chatForm = document.getElementById("chat-form");
    const questionInput = document.getElementById("question");
    const messages = document.getElementById("messages");
    const chatStatus = document.getElementById("chat-status");

    function appendMessage(role, text) {
      const div = document.createElement("div");
      div.className = `msg ${role}`;
      div.textContent = text;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }

    function renderUploadedFiles(files) {
      uploadedFiles.innerHTML = "";
      files.forEach((name) => {
        const li = document.createElement("li");
        li.textContent = name;
        uploadedFiles.appendChild(li);
      });
    }

    uploadForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const files = fileInput.files;
      if (!files.length) {
        uploadStatus.textContent = "업로드할 파일을 선택해주세요.";
        return;
      }

      const formData = new FormData();
      for (const file of files) {
        formData.append("files", file);
      }

      uploadStatus.textContent = "업로드 중...";

      try {
        const response = await fetch("/api/upload", {
          method: "POST",
          body: formData,
        });
        const data = await response.json();

        if (data.files) {
          uploadStatus.textContent = `${data.files.length}개 파일 업로드 완료`;
          renderUploadedFiles(data.files.map((item) => item.saved_name));
        } else {
          uploadStatus.textContent = "업로드 응답을 확인하지 못했습니다.";
        }
      } catch (error) {
        uploadStatus.textContent = "업로드 중 오류가 발생했습니다.";
      }
    });

    questionInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        chatForm.requestSubmit();
      }
    });

    chatForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const question = questionInput.value.trim();
      if (!question) return;

      appendMessage("user", question);
      questionInput.value = "";
      chatStatus.textContent = "Status: thinking";

      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        });

        const data = await response.json();
        appendMessage("bot", data.answer || "응답을 불러오지 못했습니다.");
      } catch (error) {
        appendMessage("bot", "서버와 통신하는 중 문제가 발생했습니다.");
      } finally {
        chatStatus.textContent = "Status: ready";
      }
    });
  </script>
</body>
</html>
"""


def list_uploaded_files() -> list[Path]:
    if not UPLOAD_DIR.exists():
        return []
    return [path for path in UPLOAD_DIR.iterdir() if path.is_file()]


def get_query_embedder() -> SentenceTransformer:
    global _query_embedder
    if _query_embedder is None:
        _query_embedder = SentenceTransformer(QUERY_EMBED_MODEL)
    return _query_embedder


def get_chroma_collection():
    if not CHROMA_DIR.exists():
        raise FileNotFoundError(f"Chroma directory not found: {CHROMA_DIR}")

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_collection(CHROMA_COLLECTION_NAME)


def retrieve_chroma_context(question: str, top_k: int = 5) -> tuple[str, list[dict]]:
    embedder = get_query_embedder()
    query_embedding = embedder.encode(
        question,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).tolist()

    collection = get_chroma_collection()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]

    if not documents:
        return "", []

    context_parts = []
    sources = []

    for doc, metadata, chunk_id in zip(documents, metadatas, ids):
        company = metadata.get("company", "알 수 없음")
        title = metadata.get("document_title", "제목 없음")
        page_number = metadata.get("page_number", "")
        chunk_index = metadata.get("chunk_index", "")

        context_parts.append(
            f"[문서] 회사명: {company} | 제목: {title} | 페이지: {page_number} | 청크: {chunk_index}\n{doc}"
        )
        sources.append(
            {
                "chunk_id": chunk_id,
                "company": company,
                "document_title": title,
                "page_number": page_number,
                "chunk_index": chunk_index,
            }
        )

    return "\n\n".join(context_parts).strip(), sources


def ask_gemini(question: str, context: str, sources: list[dict]) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return (
            "GEMINI_API_KEY가 설정되지 않았습니다.\n"
            "터미널에서 환경변수를 설정한 뒤 다시 실행해주세요."
        )

    client = genai.Client(api_key=api_key)
    source_lines = []
    for source in sources:
        source_lines.append(
            f"- {source['company']} | {source['document_title']} | 페이지 {source['page_number']} | 청크 {source['chunk_index']}"
        )
    source_text = "\n".join(source_lines) if source_lines else "없음"

    if context:
        prompt = f"""
당신은 금융문서 기반 챗봇입니다.
반드시 제공된 문맥을 우선 참고해서 한국어로 답변하세요.
문맥에 없는 내용은 추정이라고 분명히 밝히세요.
가능하면 답변 말미에 참고한 문서 범위를 짧게 요약하세요.

[검색된 문서]
{source_text}

[문맥]
{context}

[질문]
{question}
"""
    else:
        prompt = f"""
당신은 금융문서 기반 챗봇입니다.
현재 검색된 문서 문맥이 없습니다.
문서 근거가 없어 일반적인 설명만 가능하다는 점을 먼저 밝히고, 조심스럽게 한국어로 답변하세요.

[질문]
{question}
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    if hasattr(response, "text") and response.text:
        return response.text.strip()

    return "응답을 생성하지 못했습니다."


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return HTML_TEMPLATE


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)) -> JSONResponse:
    saved_files = []

    for file in files:
        target_path = UPLOAD_DIR / file.filename
        content = await file.read()
        target_path.write_bytes(content)

        saved_files.append(
            {
                "original_name": file.filename,
                "saved_name": target_path.name,
                "size": len(content),
            }
        )

    return JSONResponse({"message": "upload complete", "files": saved_files})


@app.post("/api/chat")
async def chat(payload: dict) -> JSONResponse:
    question = (payload.get("question") or "").strip()

    if not question:
        return JSONResponse({"error": "question is required"}, status_code=400)

    try:
        context, sources = retrieve_chroma_context(question)
        answer = ask_gemini(question, context, sources)
    except Exception as exc:
        return JSONResponse(
            {
                "error": "chat_pipeline_failed",
                "detail": str(exc),
            },
            status_code=500,
        )

    return JSONResponse(
        {
            "answer": answer,
            "sources": sources,
        }
    )
