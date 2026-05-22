import re


DEFAULT_CHUNK_SEPARATORS = [
    "\n[----- TABLE START -----]\n",
    "\n[----- TABLE END -----]\n",
    "\n\n",
    "\n",
    ". ",
    " ",
    "",
]


def clean_chunk_source(text: str) -> str:
    return (text or "").strip()


def page_number_at_offset(text: str, offset: int):
    page_markers = list(re.finditer(r"\bPage\s+(\d+)\b", text or "", flags=re.IGNORECASE))
    if not page_markers:
        return None

    page_number = 1
    for match in page_markers:
        if match.start() > offset:
            break
        page_number = int(match.group(1))

    return page_number


def _load_recursive_text_splitter():
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as exc:
        raise ImportError(
            "LangChain 청킹을 사용하려면 langchain-text-splitters 패키지가 필요합니다. "
            "`pip install -r requirements.txt`를 실행하세요."
        ) from exc

    return RecursiveCharacterTextSplitter


def split_text_with_langchain(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
    page_number=None,
    include_page_number: bool = True,
):
    clean_text = clean_chunk_source(text)
    if not clean_text:
        return []

    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap은 chunk_size보다 작아야 합니다.")

    RecursiveCharacterTextSplitter = _load_recursive_text_splitter()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=DEFAULT_CHUNK_SEPARATORS,
        add_start_index=True,
    )

    documents = splitter.create_documents([clean_text])
    chunks = []

    for document in documents:
        chunk_text = document.page_content.strip()
        if not chunk_text:
            continue

        start_index = document.metadata.get("start_index", 0)
        chunk = {
            "chunk_id": len(chunks) + 1,
            "text": chunk_text,
        }

        if include_page_number:
            chunk["page_number"] = page_number if page_number is not None else page_number_at_offset(clean_text, start_index)

        chunks.append(chunk)

    return chunks
