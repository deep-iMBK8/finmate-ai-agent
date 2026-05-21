import mimetypes
import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent / "vectordb"))
from image_rag import _make_clients, answer_from_text, chat, ocr_image_bytes

# ── 페이지 설정 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="FinMate AI",
    page_icon="",
    layout="wide",
)

# ── 커스텀 CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.stApp { background-color: #0f1117; color: #ffffff; }
.stMarkdown, .stMarkdown p, .stMarkdown li { color: #ffffff !important; }
[data-testid="stChatMessageContent"] p { color: #ffffff !important; }
[data-testid="stChatMessageContent"] li { color: #ffffff !important; }
[data-testid="stSidebar"] { background-color: #1a1d27; border-right: 1px solid #2a2d3e; }
.main-title {
    font-size: 2rem; font-weight: 700;
    background: linear-gradient(135deg, #00d4ff, #7b61ff);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.sub-caption { color: #9ca3af; font-size: 0.9rem; margin-bottom: 1.5rem; }
.ocr-box {
    background-color: #12151f; border: 1px solid #2a2d3e;
    border-radius: 10px; padding: 12px 16px; margin-top: 8px;
    color: #e0e0e0; font-size: 0.82rem; line-height: 1.6;
    max-height: 200px; overflow-y: auto;
}
.doc-status {
    background: linear-gradient(135deg, #00d4ff22, #7b61ff22);
    border: 1px solid #00d4ff44; border-radius: 10px;
    padding: 10px 14px; font-size: 0.85rem; color: #00d4ff;
    margin-bottom: 8px;
}
hr { border-color: #2a2d3e !important; }
</style>
""", unsafe_allow_html=True)

# ── 헤더 ──────────────────────────────────────────────────────────────────────
st.markdown('<div class="main-title"> FinMate AI</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-caption">금융 문서를 업로드하거나 자유롭게 대화하세요</div>', unsafe_allow_html=True)

# ── 세션 초기화 ───────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "ocr_texts" not in st.session_state:
    st.session_state.ocr_texts = {}   # {파일명: ocr텍스트}
if "processed_files" not in st.session_state:
    st.session_state.processed_files = set()

# ── 사이드바 ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⬆ 문서 업로드")
    st.caption("금융 문서 이미지를 올리면 내용을 읽고 답변해드려요")

    uploaded_files = st.file_uploader(
        "이미지 업로드",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploaded_files:
        _, gen_client = _make_clients()
        for uploaded_file in uploaded_files:
            if uploaded_file.name not in st.session_state.processed_files:
                with st.spinner(f"📄 {uploaded_file.name} 읽는 중..."):
                    image_bytes = uploaded_file.read()
                    mime_type   = mimetypes.guess_type(uploaded_file.name)[0] or "image/jpeg"
                    ocr_result  = ocr_image_bytes(image_bytes, mime_type, gen_client)
                st.session_state.ocr_texts[uploaded_file.name] = ocr_result
                st.session_state.processed_files.add(uploaded_file.name)
                # 대화 기록 유지 (초기화 안 함)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"📎 **{uploaded_file.name}** 문서를 읽었어요! 이 문서에 대해 질문해보세요.",
                })

        for uploaded_file in uploaded_files:
            st.image(uploaded_file, caption=uploaded_file.name, use_container_width=True)
            if uploaded_file.name in st.session_state.ocr_texts:
                with st.expander(f"📄 {uploaded_file.name} 인식 텍스트"):
                    st.markdown(
                        f'<div class="ocr-box">{st.session_state.ocr_texts[uploaded_file.name]}</div>',
                        unsafe_allow_html=True,
                    )

    st.divider()

    if st.button("🗑️ 대화 초기화", use_container_width=True):
        st.session_state.messages = []
        st.session_state.ocr_texts = {}
        st.session_state.processed_files = set()
        st.rerun()

    st.divider()
    st.markdown('<div style="color:#3d4158; font-size:0.75rem; text-align:center;">Powered by Vertex AI</div>',
                unsafe_allow_html=True)

# ── 문서 업로드 상태 표시 ──────────────────────────────────────────────────────
if st.session_state.ocr_texts:
    names = ", ".join(st.session_state.ocr_texts.keys())
    st.markdown(
        f'<div class="doc-status">📎 업로드된 문서: <b>{names}</b></div>',
        unsafe_allow_html=True,
    )

# ── 빈 화면 안내 ──────────────────────────────────────────────────────────────
if not st.session_state.messages:
    st.markdown("""
    <div style="text-align:center; padding:60px 0; color:#3d4158;">
        <div style="font-size:3rem;">💬</div>
        <div style="font-size:1rem; margin-top:12px; color:#6b7280;">
            안녕하세요! 금융 문서에 대해 궁금한 점을 물어보세요.<br>
            <span style="font-size:0.85rem;">왼쪽에서 문서를 업로드하면 그 내용을 바탕으로 답변해드려요.</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# ── 이전 메시지 표시 ───────────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── 사용자 입력 ────────────────────────────────────────────────────────────────
if query := st.chat_input("메시지를 입력하세요..."):
    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.spinner("💭 생각 중..."):
            _, gen_client = _make_clients()
            history = st.session_state.messages[:-1]

            if st.session_state.ocr_texts:
                # 문서 업로드된 경우 → 문서 기반 Q&A (여러 파일 합치기)
                combined_ocr = "\n\n---\n\n".join(
                    f"[{fname}]\n{text}"
                    for fname, text in st.session_state.ocr_texts.items()
                )
                answer = answer_from_text(
                    query=query,
                    ocr_text=combined_ocr,
                    chat_history=history,
                    gen_client=gen_client,
                )
            else:
                # 문서 없으면 → 일반 대화
                answer = chat(
                    query=query,
                    chat_history=history,
                    gen_client=gen_client,
                )

        st.markdown(answer)

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
    })