# FinMate 프로젝트 코드 흐름 정리

## 1. 프로젝트 한 줄 요약

FinMate는 금융 문서(PDF, 이미지)를 업로드하면 문서를 파싱하고, 청크로 나눈 뒤, ChromaDB에 벡터로 저장하고, MySQL에는 문서/청크/채팅 기록 메타데이터를 저장하는 금융 문서 RAG 챗봇입니다.

사용자는 웹 화면에서 문서를 업로드하거나 저장된 문서를 선택한 뒤 질문할 수 있습니다. 질문 시 현재 선택 문서를 우선 검색하고, DB에 저장된 다른 관련 문서도 함께 검색해서 Gemini가 답변을 생성합니다.

## 2. 전체 실행 흐름

### 문서 업로드 흐름

1. 사용자가 웹 화면에서 문서 파일을 업로드합니다.
2. `src/front/components/app.js`가 `/api/upload`로 파일과 메타데이터를 전송합니다.
3. `src/main.py`의 `/api/upload`가 내부적으로 `/api/parse` 처리 로직을 호출합니다.
4. 업로드 파일 확장자에 따라 처리기가 나뉩니다.
   - PDF: `src/preprocessing/pdf_router.py`
   - 이미지: `src/preprocessing/image_ocr_engine.py`
5. 파싱 결과는 공통 JSON 구조로 정리됩니다.
6. `src/rag/chunking.py`가 파싱 JSON을 검색용 청크로 나눕니다.
7. 파싱 JSON과 청크 JSON이 `data/processed` 아래에 저장됩니다.
8. `mysql/db_store.py`가 문서와 청크 메타데이터를 MySQL에 저장합니다.
9. `src/rag/embedding.py`가 청크를 Vertex AI 임베딩으로 변환하고 ChromaDB에 저장합니다.
10. 업로드가 끝나면 프론트는 해당 문서를 현재 선택 문서로 설정합니다.

### 질문 답변 흐름

1. 사용자가 선택된 문서에 대해 질문합니다.
2. `src/front/components/app.js`가 `/api/ask`로 `document_id`, `question`, `user_id`, `session_id`를 보냅니다.
3. `src/main.py`의 `/api/ask`가 `_answer_with_sources()`를 실행합니다.
4. `_answer_with_sources()`는 먼저 선택 문서의 청크를 검색합니다.
5. 이어서 ChromaDB 전체에서 질문과 관련된 다른 문서 청크도 검색합니다.
6. 중복 청크를 제거하고 검색 결과를 Gemini 프롬프트 문맥으로 구성합니다.
7. `src/services/gemini_service.py`가 Gemini API를 호출해 한국어 답변을 생성합니다.
8. `mysql/db_store.py`가 사용자 메시지, assistant 메시지, 검색 근거를 MySQL에 저장합니다.
9. 프론트가 답변을 채팅 UI에 표시합니다.

### 문서 요약 흐름

1. 사용자가 요약 버튼을 누릅니다.
2. `src/front/components/app.js`가 `/api/summary`를 호출합니다.
3. `src/main.py`는 선택 문서의 청크만 검색합니다.
4. Gemini가 선택 문서 기준으로 요약 답변을 생성합니다.

일반 질문은 선택 문서와 DB 내 관련 문서를 함께 참고하지만, 요약은 선택 문서 기준으로 동작합니다.

## 3. 주요 폴더별 역할

## `src/main.py`

현재 FastAPI 서버의 실제 진입점입니다.

주요 역할:

- 웹 프론트 정적 파일 제공
- 문서 업로드 API 제공
- PDF/이미지 파싱 라우팅
- 파싱 결과 JSON 저장
- 문서 청킹 실행
- MySQL 문서/청크 메타데이터 저장
- ChromaDB 임베딩 저장
- 문서 목록 조회
- 채팅 세션 목록 및 메시지 조회
- 문서 삭제
- 질문 답변 RAG 처리
- 문서 요약 처리
- 헬스체크 제공

주요 API:

- `GET /`: 웹 UI 반환
- `POST /api/upload`: 웹에서 사용하는 문서 업로드 API
- `POST /api/parse`: 문서 파싱, 청킹, 저장, 임베딩 처리
- `POST /api/index`: 기존 청크 JSON을 ChromaDB에 다시 인덱싱
- `GET /api/documents`: MySQL에 저장된 문서 목록 조회
- `GET /api/chat/sessions`: 채팅 세션 목록 조회
- `GET /api/chat/sessions/{session_id}`: 특정 채팅 세션 메시지 조회
- `DELETE /api/documents/{document_id}`: 문서, 청크, Chroma 벡터, 로컬 파일 삭제
- `POST /api/ask`: RAG 기반 질문 답변
- `POST /api/summary`: 선택 문서 요약
- `GET /api/health`: 서버 상태 확인

## `src/front/components`

웹 UI를 구성하는 프론트엔드 파일들이 있습니다.

### `index.html`

채팅 화면, 문서 업로드 폼, 저장 문서 선택 영역, 채팅 세션 목록 등 전체 화면 구조를 정의합니다.

### `app.js`

브라우저에서 동작하는 핵심 프론트 로직입니다.

주요 역할:

- 업로드 폼 제출 처리
- `/api/upload` 호출
- 저장된 문서 목록 조회
- 문서 선택 처리
- 문서 삭제 요청
- 질문 전송
- 요약 요청
- 채팅 세션 목록 조회
- 이전 채팅 불러오기
- 화면 상태, 진행 상태, 답변 버블 표시

현재 질문 전송 시 `document_id`를 백엔드로 보내며, 백엔드는 이 문서를 우선 검색하고 DB 내 관련 문서도 함께 검색합니다.

### `style.css`

웹 UI 스타일을 담당합니다.

## `src/preprocessing`

문서 원본을 RAG에 사용할 수 있는 공통 JSON 구조로 변환하는 전처리 영역입니다.

### `pdf_router.py`

PDF 문서를 업권별 파서로 라우팅합니다.

지원 업권:

- `bank`
- `card`
- `insurance`
- `stock`

입력받은 `sector`에 따라 `pdf_parsers` 아래의 실제 파서 함수를 호출합니다.

### `pdf_parsers`

업권별 PDF 파서가 들어 있습니다.

- `bank_parser.py`: 은행 문서 PDF 파싱
- `card_parser.py`: 카드 문서 PDF 파싱
- `insurance_parser.py`: 보험 문서 PDF 파싱
- `stock_parser.py`: 투자/증권 문서 PDF 파싱

각 파서는 PDF에서 텍스트, 페이지, 표 등 정보를 추출해 공통 문서 JSON 형태로 반환하는 역할을 합니다.

### `image_ocr_engine.py`

이미지 문서 OCR 처리기입니다.

주요 흐름:

1. Gemini Vision으로 이미지 안의 텍스트와 표를 OCR 추출합니다.
2. 추출된 OCR 텍스트를 다시 Gemini에 전달해 RAG용 계층형 JSON으로 정리합니다.
3. 원본 OCR 텍스트는 TXT로 저장합니다.
4. 구조화된 문서 JSON도 저장합니다.

이미지 파일은 단일 페이지 문서로 처리됩니다.

### `dart_parser.py`

DART 공시 문서를 수집하고 파싱하는 스크립트입니다.

현재 웹 업로드 흐름의 핵심 진입점은 아니고, 별도 공시 수집/전처리 용도에 가깝습니다.

## `src/rag`

검색 증강 생성(RAG)을 위한 청킹, 임베딩, 검색 관련 코드가 있습니다.

### `chunking.py`

파싱된 문서 JSON을 검색에 적합한 청크 리스트로 나눕니다.

주요 역할:

- 문서 유형, 회사, 페이지 수에 따라 청크 크기와 overlap 동적 결정
- 페이지 텍스트 정리
- 표 데이터를 Markdown 형태로 변환해 별도 청크로 포함
- 각 청크에 `chunk_id`, `document_uuid`, `page_number`, `document_type`, `company` 같은 메타데이터 부여

청크 결과는 이후 MySQL의 `document_chunks`와 ChromaDB 저장에 사용됩니다.

### `embedding.py`

청크를 임베딩하고 ChromaDB에 저장하거나, 질문을 임베딩해 검색할 때 사용합니다.

주요 역할:

- Vertex AI 임베딩 모델 초기화
- ChromaDB collection 생성 및 접근
- 문서 청크 임베딩 생성
- ChromaDB에 청크 텍스트, 벡터, 메타데이터 저장
- 질문 임베딩 생성
- 임베딩 완료 후 MySQL 청크 상태 갱신

현재 collection 이름은 `financial_documents`입니다.

### `fixed_embedder.py`

기존 청크 JSON 파일들을 일괄로 다시 임베딩해 ChromaDB에 넣는 보조 스크립트입니다.

### `image_rag.py`

이미지 기반 RAG를 위한 별도/실험성 코드로 보입니다. 현재 메인 웹 흐름은 `src/main.py`와 `embedding.py` 중심입니다.

## `src/services`

LLM 호출이나 간단한 서비스성 헬퍼가 있습니다.

### `gemini_service.py`

Gemini API 호출을 담당합니다.

주요 역할:

- `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY` 로드
- 문서 문맥이 있을 때는 문서 기반 답변 프롬프트 구성
- 문서 문맥이 없을 때는 일반 금융 지식 기반 답변 프롬프트 구성
- Gemini 모델 호출 후 텍스트 답변 반환

기본 모델은 환경변수 `GEMINI_MODEL`이 없으면 `gemini-2.5-flash-lite`입니다.

### `chat_service.py`

파싱된 문서 데이터를 직접 문맥으로 만들어 Gemini 답변을 생성하는 보조 코드입니다.

현재 메인 웹 RAG 흐름은 `src/main.py`의 ChromaDB 검색 기반 로직을 사용합니다.

### `document_service.py`

외부에서 FastAPI `/api/parse`를 호출해 문서를 파싱하는 클라이언트성 코드입니다.

현재 웹 UI는 브라우저에서 직접 `/api/upload`를 호출합니다.

## `mysql`

MySQL 스키마와 DB 접근 코드가 있습니다.

### `schema.sql`

프로젝트에서 사용하는 MySQL 테이블 정의입니다.

주요 테이블:

- `users`: 사용자
- `documents`: 업로드/파싱된 문서 메타데이터
- `document_chunks`: 문서 청크와 Chroma ID 매핑
- `chat_sessions`: 채팅 세션
- `chat_messages`: 채팅 메시지
- `retrieved_sources`: 답변 생성에 사용된 검색 근거

### `db_store.py`

MySQL CRUD 로직을 담당합니다.

주요 역할:

- MySQL 연결 생성
- 스키마 실행
- 사용자 upsert
- 문서 메타데이터 저장
- 청크 메타데이터 저장
- 임베딩 완료 상태 갱신
- 채팅 세션 생성 및 조회
- 채팅 메시지 저장 및 조회
- 답변 근거 저장
- 문서 삭제 시 관련 DB 데이터 삭제

### `init_mysql.py`

MySQL 스키마 초기화용 스크립트입니다.

### `database_schema.md`

DB 구조를 사람이 읽기 쉽게 설명한 문서입니다.

## `src/config`

프로젝트 공통 경로 설정이 있습니다.

### `paths.py`

루트 기준 데이터 저장 경로를 정의합니다.

주요 경로:

- `data/raw/pdf`: 업로드된 PDF 원본
- `data/raw/image`: 업로드된 이미지 원본
- `data/processed/json`: 파싱된 문서 JSON
- `data/processed/txt`: OCR 원문 TXT
- `data/processed/chunking`: 청크 JSON
- `data/chroma_db`: ChromaDB 영속 저장소

## `src/utils`

전처리와 청킹에서 사용하는 작은 유틸 함수들이 있습니다.

### `docs_helpers.py`

파일명 안전 처리 등 문서 저장 관련 헬퍼를 제공합니다.

### `chunk_helpers.py`

청킹 과정에서 사용하는 텍스트 정리, 계층 복원, 표 유효성 검사, Markdown 표 변환 등의 헬퍼를 제공합니다.

## 4. 데이터 저장 구조

### 로컬 파일 저장

업로드 및 처리 결과는 주로 `data` 폴더 아래에 저장됩니다.

- 원본 PDF: `data/raw/pdf/{sector}/`
- 원본 이미지: `data/raw/image/{sector}/`
- 파싱 JSON: `data/processed/json/`
- 청크 JSON: `data/processed/chunking/`
- OCR TXT: `data/processed/txt/`
- ChromaDB: `data/chroma_db/`

### MySQL 저장

MySQL은 원문 벡터 자체를 저장하지 않습니다. 문서 관리, 청크 메타데이터, 채팅 기록, 검색 근거를 저장합니다.

대표적으로:

- `documents`: 문서 ID, 파일명, 회사, 문서 종류, 경로, 상태
- `document_chunks`: 문서별 청크 번호, 페이지 번호, Chroma ID, 텍스트 preview
- `chat_sessions`: 채팅방 단위
- `chat_messages`: 사용자/AI 메시지
- `retrieved_sources`: AI 답변에 사용된 Chroma 검색 근거

### ChromaDB 저장

ChromaDB에는 실제 검색용 데이터가 들어갑니다.

저장 내용:

- 청크 텍스트
- 임베딩 벡터
- `document_id`
- `chunk_id`
- `company`
- `document_type`
- `page_number`
- `sector`

질문 시 ChromaDB에서 유사 청크를 검색하고, 그 결과가 Gemini 프롬프트 문맥으로 들어갑니다.

## 5. 핵심 RAG 동작 방식

현재 `/api/ask`는 다음 방식으로 검색합니다.

1. 선택된 `document_id`로 ChromaDB 검색
2. 같은 질문으로 ChromaDB 전체 검색
3. 이미 선택 문서 검색 결과에 포함된 청크는 중복 제거
4. 검색 결과마다 `selected_document` 또는 `related_document` 범위를 표시
5. Gemini 프롬프트에 다음 형태로 문맥 삽입

```text
[범위: 현재 선택 문서, 문서: ..., 페이지: ..., 회사: ...]
청크 내용

---

[범위: DB 내 관련 문서, 문서: ..., 페이지: ..., 회사: ...]
청크 내용
```

이 구조 때문에 사용자는 현재 문서에 대해 질문하면서도 DB에 저장된 다른 관련 문서 정보를 함께 받을 수 있습니다.

## 6. 환경변수

코드에서 사용하는 주요 환경변수는 다음과 같습니다.

- `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY`: Gemini 답변 생성용 API 키
- `GEMINI_MODEL`: Gemini 답변 생성 모델명
- `PROJECT_ID` 또는 `GOOGLE_CLOUD_PROJECT`: Vertex AI 임베딩/OCR용 GCP 프로젝트
- `GOOGLE_CLOUD_LOCATION`: Gemini OCR용 위치
- `ACCESS_TOKEN`: Vertex AI 인증에 사용할 access token
- `MYSQL_HOST`: MySQL 호스트
- `MYSQL_PORT`: MySQL 포트
- `MYSQL_USER`: MySQL 사용자
- `MYSQL_PASSWORD`: MySQL 비밀번호
- `MYSQL_DATABASE`: MySQL DB 이름

`MYSQL_HOST`와 `MYSQL_DATABASE`가 없으면 MySQL 기능은 비활성화된 것으로 처리됩니다.

## 7. 실행 진입점

현재 메인 서버는 다음 방식으로 실행하는 구조입니다.

```bash
uvicorn src.main:app --host 127.0.0.1 --port 8080 --reload
```

또는 `src/main.py`를 직접 실행하면 내부에서 같은 FastAPI 앱을 띄웁니다.

```bash
python -m src.main
```

## 8. 현재 코드에서 주의할 점

- `src/main.py`가 현재 웹 서버의 중심입니다.
- `src/services/chat_service.py`, `src/services/document_service.py`는 현재 메인 웹 UI 흐름에서는 핵심 경로가 아닙니다.
- `fixed_embedder.py`, `dart_parser.py`, `image_rag.py`는 보조/배치/실험성 성격이 강합니다.
- 질문 답변은 ChromaDB 인덱싱이 성공해야 제대로 동작합니다.
- MySQL은 벡터 본문 저장소가 아니라 문서 관리와 기록 저장소입니다.
- Gemini는 전달된 문서 문맥을 우선 참고하지만, 문맥에 없는 내용은 일반 지식으로 답할 수 있으므로 답변 신뢰도를 높이려면 검색 근거 표시 UI를 추가하는 것이 좋습니다.
