import os

import streamlit as st
from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")


def init_chat_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []


def build_context_from_parsed_data(parsed_data: dict) -> str:
    if not parsed_data:
        return ""

    company = parsed_data.get("company", "")
    title = parsed_data.get("document_title", "")
    pages = parsed_data.get("pages", [])

    context_parts = []
    if company or title:
        context_parts.append(f"[문서 정보] 회사명: {company} / 제목: {title}")

    for page in pages[:3]:
        page_number = page.get("page_number", "")
        text = (page.get("text") or "").strip()
        if text:
            context_parts.append(f"[페이지 {page_number}] {text[:1500]}")

    return "\n\n".join(context_parts).strip()


def ask_gemini(question: str, sector: str, parsed_data: dict | None = None) -> str:
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEY 또는 GOOGLE_API_KEY가 설정되지 않았습니다."

    client = genai.Client(api_key=GEMINI_API_KEY)
    context = build_context_from_parsed_data(parsed_data or {})

    if context:
        prompt = f"""
당신은 금융 문서 기반 챗봇입니다.
반드시 제공된 문서 문맥을 우선 참고해서 한국어로 답변하세요.
문서에 없는 내용은 추정이라고 분명히 밝히세요.

[업권]
{sector}

[문서 문맥]
{context}

[질문]
{question}
""".strip()
    else:
        prompt = f"""
당신은 금융 문서 기반 챗봇입니다.
현재 문서 문맥이 충분하지 않을 수 있습니다.
일반적인 금융 지식으로 답하되, 문서 근거가 없으면 그 점을 밝혀주세요.

[업권]
{sector}

[질문]
{question}
""".strip()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    text = getattr(response, "text", None)
    if not text:
        return "응답을 생성하지 못했습니다."

    return text.strip()


def render_chatbot(sector: str):
    st.subheader("금융 문서 챗봇")
    init_chat_state()

    parsed_data = st.session_state.get("last_parsed_data")

    # 위: 대화창
    chat_box = st.container(height=520, border=True)

    with chat_box:
        if not st.session_state.messages:
            st.info("문서를 업로드한 뒤 질문을 입력해보세요.")
        else:
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

    # 아래: 입력창
    prompt = st.chat_input("예: 이 문서의 핵심 위험요인을 요약해줘")

    if prompt:
        st.session_state.messages.append(
            {"role": "user", "content": prompt}
        )

        try:
            answer = ask_gemini(
                question=prompt,
                sector=sector,
                parsed_data=parsed_data,
            )
        except Exception as e:
            answer = f"Gemini 호출 실패: {e}"

        st.session_state.messages.append(
            {"role": "assistant", "content": answer}
        )

        st.rerun()