import streamlit as st

from services.chat_service import generate_chat_answer


def render_chat_box(sector: str) -> None:
    st.subheader("금융 문서 챗봇")

    parsed_data = st.session_state.get("last_parsed_data")

    chat_box = st.container(height=520, border=True)

    with chat_box:
        if not st.session_state.messages:
            st.info("문서를 업로드한 뒤 질문을 입력해보세요.")
        else:
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

    with st.form("chat_form", clear_on_submit=True):
        question = st.text_area(
            "질문하기",
            placeholder="예: 이 문서의 핵심 위험요인을 요약해줘",
            label_visibility="collapsed",
            height=80,
        )
        submitted = st.form_submit_button("질문 보내기", use_container_width=True)

    if submitted and question.strip():
        prompt = question.strip()
        st.session_state.messages.append({"role": "user", "content": prompt})

        try:
            answer = generate_chat_answer(
                question=prompt,
                sector=sector,
                parsed_data=parsed_data,
            )
        except Exception as exc:
            answer = f"Gemini 호출 실패: {exc}"

        st.session_state.messages.append({"role": "assistant", "content": answer})
        st.rerun()
