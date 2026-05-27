from __future__ import annotations

from services.gemini_service import ask_gemini


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


def generate_chat_answer(question: str, sector: str, parsed_data: dict | None = None) -> str:
    context = build_context_from_parsed_data(parsed_data or {})
    return ask_gemini(question=question, sector=sector, context=context)
