# [iM DiGital Banker 8기 DL 프로젝트] FinMate | 당신 곁의 금융 메이트

RAG 의미 검색 기반으로 복잡한 금융 문서를 설명·분석해주는 금융 문서 인텔리전스 에이전트 챗봇입니다.  
AI-powered financial document intelligence agent based on RAG semantic search, for managing, searching, and understanding complex finance documents.

## 시스템 아키텍처 및 기술 스택

<img width="6315" height="2983" alt="finmate_아키텍처" src="https://github.com/user-attachments/assets/1117cd3c-fb0a-41fd-bd1a-291345d298d0" />

### 개발 환경

![Python](https://img.shields.io/badge/Python-3.10-blue?style=flat-square&logo=python)
![Anaconda](https://img.shields.io/badge/Anaconda-44A833?style=flat-square&logo=Anaconda&logoColor=white")

### 텍스트 추출 & 파싱

![Gemini](https://img.shields.io/badge/Gemini-3.1-8E75B2?style=flat-square&logo=googlegemini)
![PuMuPDF](https://img.shields.io/badge/PuMuPDF-black?style=flat-square)
![pdfminer](https://img.shields.io/badge/pdfminer-black?style=flat-square)
![BeautifulSoup](https://img.shields.io/badge/BeautifulSoup-black?style=flat-square)
![pdfplumber](https://img.shields.io/badge/pdfplumber-black?style=flat-square)

### RAG & LLM

![LangChain](https://img.shields.io/badge/LangChain-7FC8FF?style=flat-square&logo=langchain)
![Vertex AI](https://img.shields.io/badge/VertexAI-black?style=flat-square) (text-multilingual-embedding-002)

### 데이터베이스

![MySQL](https://img.shields.io/badge/MySQL-4479A1?style=flat-square&logo=mysql)
![ChromaDB](https://img.shields.io/badge/ChromaDB-black?style=flat-square)

### 웹 프론트엔드

![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=flat-square&logo=html5&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=flat-square&logo=javascript&logoColor=black)
![CSS3](https://img.shields.io/badge/CSS3-1572B6?style=flat-square&logo=css3&logoColor=white)

### 배포 환경

![GCP](https://img.shields.io/badge/GoogleCloud-4285F4?style=flat-square&logo=googlecloud&logoColor=white)

### 코드 포맷팅

![black](https://img.shields.io/badge/black-black?style=flat-square)
![flake8](https://img.shields.io/badge/flake8-black?style=flat-square)
![isort](https://img.shields.io/badge/isort-black?style=flat-square)

### 협업 도구

![GitHub](https://img.shields.io/badge/GitHub-black?style=flat-square&logo=github)
![Notion](https://img.shields.io/badge/Notion-grey?style=flat-square&logo=notion)

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
