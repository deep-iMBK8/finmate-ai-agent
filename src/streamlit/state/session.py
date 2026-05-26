import streamlit as st


def init_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "last_parsed_data" not in st.session_state:
        st.session_state.last_parsed_data = None
