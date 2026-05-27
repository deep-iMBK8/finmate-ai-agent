# [iM DiGital Banker 8기 DL 프로젝트] FinMate | 당신 곁의 금융 메이트

복잡한 금융 문서를 딥러닝 기반으로 검색·분석해주는 금융 문서 인텔리전스 에이전트 챗봇입니다.  
AI-powered financial document intelligence agent for managing, searching, and understanding complex finance documents.

## 시스템 아키텍처 및 기술 스택

<img width="6315" height="2983" alt="finmate_아키텍처" src="https://github.com/user-attachments/assets/1117cd3c-fb0a-41fd-bd1a-291345d298d0" />

### 개발 환경

python 3.10  
Anaconda

### 텍스트 추출 및 파싱

gemini 3.1, PuMuPDF, pdfminer, BeautifulSoup, pdfplumber

### RAG

LangChain, text-multilingual-embedding-002 (Vertex AI)

### LLM

Gemini 3.1

### lint/formatting

black/isort/flake8

### 협업 도구

GitHub, Notion

## 프로젝트 구조

```
finmate-ai-agent/
│
├── data/                           # 데이터 저장소 - DB, 청크, 처리된 JSON 등 (.gitignore)
│   ├── chroma_db/
│   ├── chunks/
│   ├── processed/
│   └── raw/
│
├── scripts/                        # DB 초기화 등 실행용 관리 스크립트
│   ├── init_mysql.py
│   └── schema.sql
│
├── src/                            # 메인 소스 코드
│   ├── config/                     # 경로 등의 설정 관리
│   │   └── paths.py                # 디렉토리 경로 상수
│   ├── database/                   # DB 연결 및 데이터 저장 로직
│   │   └── db_store.py
│   ├── preprocessing/              # 문서 파싱 및 OCR 로직
│   │   ├── pdf_parsers/            # 금융 섹터별 PDF 파서
│   │   │   ├── bank_parser.py
│   │   │   ├── card_parser.py
│   │   │   ├── insurance_parser.py
│   │   │   └── stock_parser.py
│   │   ├── image_ocr_engine.py     # 이미지용 OCR 엔진
│   │   └── pdf_router.py
│   ├── rag/                        # RAG 관련 (청킹 및 임베딩/인덱싱)
│   │   ├── chunking.py
│   │   └── indexing.py
│   ├── services/                   # 비즈니스 로직
│   │   ├── chat_service.py
│   │   ├── document_service.py
│   │   └── gemini_service.py
│   ├── static/                     # 정적 리소스 - JS, CSS (모노리틱 배포 방식 사용)
│   │   ├── app.js
│   │   └── style.css
│   ├── templates/                  # HTML 템플릿
│   │   └── index.html
│   ├── utils/                      # 유틸 함수
│   │   ├── chunk_helpers.py
│   │   └── docs_helpers.py
│   └── main.py                     # FastAPI 서버 실행 파일 (API 엔드포인트)
│
├── notebooks/                      # 프로토타이핑/사전 확인용 Jupyter Notebook (.gitignore)
├── tests/                          # 테스트 코드 (추후 디벨롭 예정)
├── .env                            # 환경 변수 (API KEY 등)
├── .env.example                    # 환경 변수 키 정의
├── .gitignore
├── pyproject.toml                  # 의존성 관리
└── requirements.txt
```
