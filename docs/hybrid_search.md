# Hybrid Search Design

## 결론

이 설계는 **RDB 검색 결과와 Vector DB 검색 결과를 병합하고, 병합된 후보를 rerank한 뒤 Gemini context로 전달하는 구조**다.

목표 흐름:

```text
question
-> MySQL keyword/metadata search
-> Chroma semantic search
-> merge results
-> normalize scores
-> rerank
-> top_k chunks
-> Gemini answer
```

현재 구현은 Chroma semantic search만 사용한다. Hybrid search 도입 후에는 MySQL이 정확 키워드와 메타데이터 기반 검색을 담당하고, Chroma가 의미 기반 검색을 담당한다.

## 현재 구조

현재 질문 검색은 Chroma 벡터 유사도 기반이다.

```text
question
-> text-multilingual-embedding-002
-> Chroma cosine vector search
-> top_k chunks
-> Gemini context
```

MySQL은 현재 문서 관리와 저장 용도다.

```text
- documents: 문서 메타데이터 저장
- document_chunks: 청크 번호, 페이지, Chroma ID 매핑
- chat_sessions/chat_messages: 채팅 기록 저장
- retrieved_sources: 답변 출처 저장
```

현재는 MySQL 검색 결과와 Chroma 검색 결과를 결합하지 않는다.

## 목표 구조

| 저장소 | 역할 |
| --- | --- |
| MySQL | keyword search, metadata search, 정확 문자열 후보 검색 |
| Chroma | semantic search, 의미 유사도 검색 |
| Reranker | 병합 후보 최종 재정렬 |
| Gemini | rerank된 top_k 청크를 근거로 답변 생성 |

전체 흐름:

```text
User question
  |
  +-- 1. Query analysis
  |      - 회사명, 섹터, 문서유형, 날짜, 숫자/금액/고유명사 후보 추출
  |
  +-- 2. MySQL search
  |      - metadata search
  |      - FULLTEXT/ngram keyword search
  |
  +-- 3. Chroma search
  |      - text-multilingual-embedding-002 query embedding
  |      - cosine vector similarity
  |
  +-- 4. Merge
  |      - document_id + chunk_id 기준으로 중복 제거
  |
  +-- 5. Score normalization
  |      - keyword_score_norm
  |      - vector_score_norm
  |      - metadata_boost
  |
  +-- 6. Rerank
  |      - 1차: weighted score rerank
  |      - 2차 선택: cross-encoder 또는 LLM rerank
  |
  +-- 7. Gemini answer
         - rerank된 top_k chunks를 context로 전달
```

## 저장 스키마 변경

keyword search를 하려면 MySQL에 청크 본문 전체가 필요하다. 현재 `document_chunks`에는 `text_preview`만 있으므로 `chunk_text`를 추가한다.

```sql
ALTER TABLE document_chunks
ADD COLUMN chunk_text LONGTEXT NULL AFTER text_preview;
```

FULLTEXT 인덱스:

```sql
ALTER TABLE document_chunks
ADD FULLTEXT INDEX ft_document_chunks_text (chunk_text);
```

한국어 검색 품질을 높이려면 ngram parser를 검토한다.

```sql
ALTER TABLE document_chunks
ADD FULLTEXT INDEX ft_document_chunks_text_ngram (chunk_text) WITH PARSER ngram;
```

metadata 검색용 인덱스는 이미 일부 존재하지만, 필요하면 다음을 추가한다.

```sql
CREATE INDEX idx_documents_sector ON documents (document_sector);
CREATE INDEX idx_documents_date ON documents (document_date);
CREATE INDEX idx_chunks_page ON document_chunks (page_number);
```

## 저장 파이프라인 변경

청킹 결과 저장 시 `chunk["chunk"]` 원문을 MySQL에도 저장한다.

수정 대상:

```text
src/database/db_store.py
- upsert_chunked_document()

src/main.py
- _store_chunks_to_mysql()
```

저장할 값:

```text
document_id
chunk_id
page_number
chroma_id
text_preview
chunk_text
metadata
```

Chroma에는 기존처럼 다음 값을 저장한다.

```text
id
embedding
document
metadata
```

## 검색 함수 설계

기존 함수:

```python
def _search_chunks(question: str, document_id: str | None = None, top_k: int = 4):
    ...
```

권장 구조:

```python
def _analyze_query(question: str, user_id: str) -> dict:
    """질문에서 metadata 조건과 keyword query를 추출한다."""


def _keyword_search_chunks(
    question: str,
    user_id: str,
    document_id: str | None = None,
    filters: dict | None = None,
    top_k: int = 12,
) -> list[dict]:
    """MySQL FULLTEXT/ngram 기반 keyword search."""


def _metadata_search_chunks(
    user_id: str,
    document_id: str | None = None,
    filters: dict | None = None,
    top_k: int = 12,
) -> list[dict]:
    """MySQL metadata 조건 기반 후보 search."""


def _semantic_search_chunks(
    question: str,
    document_id: str | None = None,
    filters: dict | None = None,
    top_k: int = 12,
) -> list[dict]:
    """Chroma semantic search."""


def _merge_search_results(
    keyword_results: list[dict],
    metadata_results: list[dict],
    semantic_results: list[dict],
) -> list[dict]:
    """document_id + chunk_id 기준 병합."""


def _rerank_results(
    question: str,
    merged_results: list[dict],
    top_k: int = 5,
) -> list[dict]:
    """weighted score 또는 reranker model로 최종 재정렬."""


def _hybrid_search_chunks(
    question: str,
    user_id: str,
    document_id: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    filters = _analyze_query(question, user_id)
    keyword_results = _keyword_search_chunks(question, user_id, document_id, filters)
    metadata_results = _metadata_search_chunks(user_id, document_id, filters)
    semantic_results = _semantic_search_chunks(question, document_id, filters)
    merged = _merge_search_results(keyword_results, metadata_results, semantic_results)
    return _rerank_results(question, merged, top_k=top_k)
```

`_answer_with_sources()`는 `_search_chunks()` 대신 `_hybrid_search_chunks()`를 호출한다.

## Query Analysis

1차 구현은 rule-based로 충분하다.

```python
SECTOR_KEYWORDS = {
    "은행": "bank",
    "카드": "card",
    "보험": "insurance",
    "투자": "stock",
}

DOCUMENT_TYPE_KEYWORDS = ["약관", "명세서", "신청서", "설명서", "안내장"]
EXACT_MARKERS = ["금액", "날짜", "계좌", "이율", "수수료", "한도", "조항", "고객명"]
```

분석 결과 예:

```python
{
    "sector": "bank",
    "company": "신한은행",
    "document_type": "신청서",
    "has_exact_marker": True,
    "keyword_query": "신한은행 고객 이름 신청서",
}
```

회사명은 MySQL에 저장된 회사 목록에서 추출한다.

```sql
SELECT DISTINCT company
FROM documents
WHERE user_id = %s
  AND company IS NOT NULL
  AND company <> '';
```

## MySQL Keyword Search

선택 문서 안에서 검색:

```sql
SELECT
  dc.document_id,
  dc.chunk_id,
  dc.page_number,
  dc.chroma_id,
  dc.chunk_text,
  d.company,
  d.document_type,
  d.document_sector,
  MATCH(dc.chunk_text) AGAINST (%s IN NATURAL LANGUAGE MODE) AS keyword_score
FROM document_chunks dc
JOIN documents d ON d.document_id = dc.document_id
WHERE d.user_id = %s
  AND dc.document_id = %s
  AND MATCH(dc.chunk_text) AGAINST (%s IN NATURAL LANGUAGE MODE)
ORDER BY keyword_score DESC
LIMIT %s;
```

전체 문서에서 검색:

```sql
SELECT
  dc.document_id,
  dc.chunk_id,
  dc.page_number,
  dc.chroma_id,
  dc.chunk_text,
  d.company,
  d.document_type,
  d.document_sector,
  MATCH(dc.chunk_text) AGAINST (%s IN NATURAL LANGUAGE MODE) AS keyword_score
FROM document_chunks dc
JOIN documents d ON d.document_id = dc.document_id
WHERE d.user_id = %s
  AND MATCH(dc.chunk_text) AGAINST (%s IN NATURAL LANGUAGE MODE)
ORDER BY keyword_score DESC
LIMIT %s;
```

FULLTEXT가 불가한 환경에서는 임시 fallback:

```sql
WHERE dc.chunk_text LIKE CONCAT('%', %s, '%')
```

단, `LIKE`는 성능과 품질이 낮으므로 운영 설계는 FULLTEXT/ngram 기준으로 둔다.

## MySQL Metadata Search

metadata search는 정확 필터 후보를 만든다.

```sql
SELECT
  dc.document_id,
  dc.chunk_id,
  dc.page_number,
  dc.chroma_id,
  dc.chunk_text,
  d.company,
  d.document_type,
  d.document_sector,
  1.0 AS metadata_score
FROM document_chunks dc
JOIN documents d ON d.document_id = dc.document_id
WHERE d.user_id = %s
  AND (%s IS NULL OR dc.document_id = %s)
  AND (%s IS NULL OR d.company LIKE CONCAT('%', %s, '%'))
  AND (%s IS NULL OR d.document_sector = %s)
  AND (%s IS NULL OR d.document_type LIKE CONCAT('%', %s, '%'))
ORDER BY d.updated_at DESC, dc.page_number ASC
LIMIT %s;
```

metadata 결과는 keyword/semantic 결과와 병합되며, `metadata_score` 또는 `metadata_boost`로 rerank에 반영한다.

## Chroma Semantic Search

Chroma 검색은 기존 구조를 유지한다.

```python
results = collection.query(
    query_embeddings=[embed_query(question)],
    n_results=top_k,
    where=where,
    include=["documents", "metadatas", "distances"],
)
```

vector score:

```python
vector_score = 1 - distance
```

선택 문서가 있을 때:

```python
where = {"document_id": document_id}
```

metadata filter를 같이 적용할 수 있으면 다음처럼 확장한다.

```python
where = {
    "$and": [
        {"document_id": document_id},
        {"sector": "bank"},
    ]
}
```

Chroma 버전에 따라 `$and`, `$in` 지원이 다를 수 있으므로 1차 구현은 `document_id` 필터를 우선한다.

## 결과 형태

세 검색 방식의 결과를 공통 형태로 맞춘다.

```python
{
    "text": "...",
    "metadata": {
        "document_id": "...",
        "chunk_id": "...",
        "page_number": 1,
        "company": "신한은행",
        "sector": "bank",
        "document_type": "신청서",
    },
    "keyword_score": 3.2,
    "metadata_score": 1.0,
    "vector_score": 0.82,
    "distance": 0.18,
    "retrieval_methods": ["keyword", "semantic"],
}
```

규칙:

```text
MySQL 결과의 text는 chunk_text 사용
Chroma 결과의 text는 Chroma document 사용
동일 청크가 여러 검색 방식에서 나오면 하나로 합침
retrieval_methods에 검색 출처를 누적
```

## 병합 기준

병합 키:

```python
key = (document_id, chunk_id)
```

병합 로직:

```python
def merge_result(target: dict, source: dict, method: str) -> dict:
    target["retrieval_methods"].add(method)

    for score_key in ("keyword_score", "metadata_score", "vector_score", "distance"):
        if source.get(score_key) is not None:
            target[score_key] = source[score_key]

    if not target.get("text") and source.get("text"):
        target["text"] = source["text"]

    target["metadata"].update(source.get("metadata") or {})
    return target
```

중요:

```text
동일 청크가 keyword와 semantic 양쪽에서 나오면 최종 점수가 올라가야 한다.
한쪽에만 나온 청크도 후보로 유지한다.
```

## 점수 정규화

검색 방식별 점수 범위가 다르므로 정규화한다.

```python
def normalize_scores(results: list[dict], score_key: str, normalized_key: str) -> None:
    scores = [r[score_key] for r in results if r.get(score_key) is not None]
    if not scores:
        for result in results:
            result[normalized_key] = 0.0
        return

    min_score = min(scores)
    max_score = max(scores)
    span = max_score - min_score

    for result in results:
        score = result.get(score_key)
        if score is None:
            result[normalized_key] = 0.0
        elif span == 0:
            result[normalized_key] = 1.0
        else:
            result[normalized_key] = (score - min_score) / span
```

정규화 대상:

```text
keyword_score -> keyword_score_norm
metadata_score -> metadata_score_norm
vector_score -> vector_score_norm
```

## 1차 Rerank: Weighted Score

1차 구현은 weighted score rerank를 사용한다.

기본 공식:

```python
final_score = (
    0.55 * vector_score_norm
    + 0.30 * keyword_score_norm
    + 0.10 * metadata_score_norm
    + 0.05 * multi_match_boost
)
```

`multi_match_boost`는 여러 검색 방식에서 동시에 발견된 청크에 주는 보너스다.

```python
multi_match_boost = min(len(retrieval_methods) - 1, 2) / 2
```

질문 유형에 따라 가중치를 바꿀 수 있다.

| 질문 유형 | vector | keyword | metadata | multi-match |
| --- | ---: | ---: | ---: | ---: |
| 일반 설명/요약 | 0.65 | 0.20 | 0.10 | 0.05 |
| 정확 문자열/금액/날짜 | 0.40 | 0.45 | 0.10 | 0.05 |
| 회사/문서유형 지정 | 0.45 | 0.25 | 0.25 | 0.05 |

간단한 가중치 선택:

```python
def choose_weights(filters: dict) -> dict:
    if filters.get("has_exact_marker"):
        return {"vector": 0.40, "keyword": 0.45, "metadata": 0.10, "multi": 0.05}
    if filters.get("company") or filters.get("document_type"):
        return {"vector": 0.45, "keyword": 0.25, "metadata": 0.25, "multi": 0.05}
    return {"vector": 0.65, "keyword": 0.20, "metadata": 0.10, "multi": 0.05}
```

## 2차 Rerank: 선택 기능

weighted score로 상위 20개 후보를 뽑은 뒤, 필요하면 reranker를 추가한다.

```text
merged candidates
-> weighted score top 20
-> reranker(question, chunk)
-> final top 5
```

우선순위:

```text
1. weighted score rerank 먼저 구현
2. 품질이 부족하면 cross-encoder reranker 추가
3. 모델 도입이 어렵다면 LLM rerank를 옵션으로 추가
```

Reranker 결과 필드:

```python
{
    "rerank_score": 0.91,
    "final_score": 0.88,
}
```

2차 reranker를 쓰는 경우 최종 정렬은 `rerank_score` 우선, 동점이면 `final_score`를 사용한다.

## Gemini Context 구성

rerank 후 상위 `top_k`만 context에 넣는다.

```text
범위: 현재 선택 문서 또는 DB 내 관련 문서
문서: {document_id}
페이지: {page_number}
회사: {company}
검색방식: keyword, semantic
점수: final_score={final_score}

{chunk text}
```

LLM에는 점수를 너무 길게 설명하지 말고, 출처 검증용 metadata로만 포함한다.

## retrieved_sources 저장

`retrieved_sources.metadata`에는 hybrid 검색 정보를 저장한다.

```json
{
  "retrieval_methods": ["keyword", "semantic"],
  "keyword_score": 3.2,
  "keyword_score_norm": 0.74,
  "vector_score": 0.82,
  "vector_score_norm": 0.91,
  "metadata_score": 1.0,
  "metadata_score_norm": 1.0,
  "final_score": 0.86,
  "rerank_score": 0.91
}
```

`retrieved_sources.distance`에는 기존 호환성을 위해 Chroma distance를 유지한다. keyword-only 결과는 `distance = NULL`로 둔다.

## Fallback 정책

각 검색 방식은 독립적으로 실패할 수 있다.

```text
MySQL keyword 실패 -> metadata + Chroma 결과만 사용
MySQL metadata 실패 -> keyword + Chroma 결과만 사용
Chroma 실패 -> MySQL keyword/metadata 결과만 사용
모두 실패 -> chunk JSON fallback 또는 404
```

fallback 원칙:

```text
하나의 검색 방식 실패가 전체 /api/ask 500으로 이어지면 안 된다.
검색 실패는 로그에 남기고 가능한 결과만 병합한다.
병합 결과가 0개일 때만 fallback 또는 404를 사용한다.
```

## 구현 순서

1. `document_chunks.chunk_text` 컬럼 추가
2. FULLTEXT 또는 ngram 인덱스 추가
3. `upsert_chunked_document()`에서 `chunk_text` 저장
4. `db_store.list_known_companies(user_id)` 추가
5. `db_store.keyword_search_chunks(...)` 추가
6. `db_store.metadata_search_chunks(...)` 추가
7. `main.py`에 `_analyze_query()` 추가
8. 기존 `_search_chunks()`를 `_semantic_search_chunks()`로 이름/역할 정리
9. `main.py`에 `_merge_search_results()` 추가
10. `main.py`에 `_normalize_scores()` 추가
11. `main.py`에 `_rerank_results()` 추가
12. `main.py`에 `_hybrid_search_chunks()` 추가
13. `_answer_with_sources()`가 `_hybrid_search_chunks()`를 사용하도록 변경
14. `retrieved_sources.metadata`에 hybrid score 정보 저장
15. 업로드 후 `/api/ask`, `/api/summary` 회귀 테스트

## 테스트 시나리오

정확 문자열:

```text
고객 이름이 뭐야?
문서에 나온 계좌번호가 있어?
2021년 4월 기준 문서야?
수수료 관련 조항을 찾아줘.
```

semantic:

```text
이 문서에서 고객이 신청한 업무를 요약해줘.
이 약관에서 사용자가 주의해야 할 부분은 뭐야?
금융사가 고객 정보를 어떻게 이용한다고 설명해?
```

metadata:

```text
신한은행 문서에서 고객 정보 관련 내용을 찾아줘.
카드 문서 중 할인 혜택을 알려줘.
보험 약관에서 보상하지 않는 손해를 찾아줘.
```

hybrid 효과 확인:

```text
박예은이 나온 신한은행 신청서 내용을 찾아줘.
현대카드O 할인 혜택을 알려줘.
한화손해보험 약관에서 면책 조항을 찾아줘.
```

검증 기준:

```text
- keyword-only로 찾히던 정확 문자열이 결과에 포함되는가
- vector-only에서 놓치던 고유명사가 포함되는가
- semantic-only로 잘 찾던 설명형 질문 품질이 떨어지지 않는가
- retrieval_methods와 final_score가 sources에 남는가
- 한 검색 방식 실패 시에도 500이 나지 않는가
```

## 최종 목표 구조

```text
User question
  |
  +-- MySQL keyword search
  |     - FULLTEXT/ngram
  |     - keyword_score
  |
  +-- MySQL metadata search
  |     - company, sector, document_type, date
  |     - metadata_score
  |
  +-- Chroma semantic search
  |     - text-multilingual-embedding-002
  |     - vector_score
  |
  +-- Merge
  |     - key: document_id + chunk_id
  |
  +-- Normalize
  |     - keyword_score_norm
  |     - metadata_score_norm
  |     - vector_score_norm
  |
  +-- Rerank
  |     - weighted final_score
  |     - optional cross-encoder/LLM rerank
  |
  +-- Gemini
        - top_k reranked chunks as context
```
