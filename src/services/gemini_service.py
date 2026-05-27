import os

from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")


def _vertex_enabled() -> bool:
    value = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower()
    return value in {"1", "true", "yes", "y"}


def _make_client(force_api_key: bool = False):
    if (force_api_key or not _vertex_enabled()) and GEMINI_API_KEY:
        return genai.Client(api_key=GEMINI_API_KEY)

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if project_id:
        return genai.Client(
            vertexai=True,
            project=project_id,
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
        )

    if GEMINI_API_KEY:
        return genai.Client(api_key=GEMINI_API_KEY)

    raise RuntimeError("GEMINI_API_KEY 또는 GOOGLE_CLOUD_PROJECT 환경변수가 필요합니다.")


def ask_gemini(question: str, sector: str, context: str = "") -> str:
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

    try:
        client = _make_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
    except Exception:
        if _vertex_enabled() and GEMINI_API_KEY:
            client = _make_client(force_api_key=True)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
        else:
            raise

    return (getattr(response, "text", None) or "응답을 생성하지 못했습니다.").strip()
