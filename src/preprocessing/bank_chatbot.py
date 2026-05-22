import os
import json
import sqlite3
import glob
import chainlit as cl
from functools import lru_cache
from pydantic import BaseModel, Field

from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# API 키 설정
os.environ["GOOGLE_API_KEY"] = "AIzaSyDp9-8sYzjUEXVT1lAmZSFUFYJqdhZD2QA"

def setup_rdb(json_folder="src/chunking"):
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE documents (document_uuid TEXT, company TEXT, document_type TEXT, document_date TEXT)')
    inserted_uuids = set()
    for file_path in glob.glob(os.path.join(json_folder, "*.json")):
        with open(file_path, 'r', encoding='utf-8') as f:
            chunks = json.load(f)
            if not isinstance(chunks, list): continue
            for chunk in chunks:
                meta = chunk.get("metadata", {})
                uuid = meta.get("document_uuid")
                if uuid and uuid not in inserted_uuids:
                    cursor.execute('INSERT INTO documents VALUES (?, ?, ?, ?)', (uuid, meta.get("company"), meta.get("document_type"), meta.get("document_date")))
                    inserted_uuids.add(uuid)
    conn.commit()
    return conn

def extract_metadata_and_query_rdb(query, db_conn):
    cursor = db_conn.cursor()
    target_company = None
    if "하나은행" in query: target_company = "하나은행"
    elif "토스" in query or "bankbook" in query: target_company = "bankbook"
    elif "아이엠뱅크" in query: target_company = "아이엠뱅크"
    elif "신한은행" in query: target_company = "신한은행"
    
    if target_company: cursor.execute("SELECT document_uuid FROM documents WHERE company = ?", (target_company,))
    else: cursor.execute("SELECT document_uuid FROM documents")
    return [row[0] for row in cursor.fetchall()]

def perform_hybrid_search(query, target_uuids, vector_db, json_folder="src/chunking", top_k=5):
    vector_results = vector_db.similarity_search(query=query, k=top_k * 2, filter={"document_uuid": {"$in": target_uuids}})
    target_docs = []
    for file_path in glob.glob(os.path.join(json_folder, "*.json")):
        with open(file_path, 'r', encoding='utf-8') as f:
            chunks = json.load(f)
            for chunk in chunks:
                if chunk.get("metadata", {}).get("document_uuid") in target_uuids:
                    text_content = chunk.get("page_content", "").strip()
                    if text_content: target_docs.append(Document(page_content=text_content, metadata=chunk.get("metadata", {})))
    tokenized_corpus = [doc.page_content.split() for doc in target_docs]
    bm25 = BM25Okapi(tokenized_corpus)
    bm25_scores = bm25.get_scores(query.split())
    bm25_results = [target_docs[i] for i in sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[:top_k*2]]
    
    fused_scores = {}
    doc_map = {}
    def add_to_rrf(docs, weight=1.0):
        for rank, doc in enumerate(docs, 1):
            doc_id = doc.page_content 
            doc_map[doc_id] = doc
            fused_scores[doc_id] = fused_scores.get(doc_id, 0) + weight * (1 / (rank + 60))
    add_to_rrf(vector_results, 0.5); add_to_rrf(bm25_results, 0.5)
    return [doc_map[doc_id] for doc_id, _ in sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]]

def rerank_results(query, retrieved_docs, top_k=3):
    if not retrieved_docs: return []
    reranker_model = CrossEncoder('BAAI/bge-reranker-v2-m3', device='cpu')
    scores = reranker_model.predict([[query, doc.page_content] for doc in retrieved_docs])
    return [doc for doc, _ in sorted(zip(retrieved_docs, scores), key=lambda x: x[1], reverse=True)[:top_k]]

def generate_rag_answer(query, retrieved_docs, category):
    context_text = "\n\n---\n\n".join([doc.page_content for doc in retrieved_docs])
    
    # 카테고리별로 AI의 역할을 동적으로 변경하여 퀄리티 상승
    system_prompt = f"""당신은 [{category}] 분야의 최고 금융 전문가 'FinMate'입니다. 
    아래 제공된 [검색된 문서]만을 바탕으로 사용자의 질문에 상세하고 친절하게 답변하세요.
    보기 좋게 글머리 기호를 사용하여 정리해주세요.
    
    [검색된 문서]
    {{context}}"""
    
    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("human", "{question}")])
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", temperature=0.1)
    return (prompt | llm | StrOutputParser()).invoke({"context": context_text, "question": query})


def search_bank_product(query: str, rdb_conn, vector_db) -> str:
    uuids = extract_metadata_and_query_rdb(query, rdb_conn)
    hybrid_docs = perform_hybrid_search(query, uuids, vector_db)
    final_docs = rerank_results(query, hybrid_docs)
    return generate_rag_answer(query, final_docs, "은행 상품 및 예적금/대출")

def search_insurance_product(query: str, rdb_conn, vector_db) -> str:
    uuids = extract_metadata_and_query_rdb(query, rdb_conn)
    hybrid_docs = perform_hybrid_search(query, uuids, vector_db)
    final_docs = rerank_results(query, hybrid_docs)
    return generate_rag_answer(query, final_docs, "보험 및 보장 분석")

def search_stock_info(query: str, rdb_conn, vector_db) -> str:
    uuids = extract_metadata_and_query_rdb(query, rdb_conn)
    hybrid_docs = perform_hybrid_search(query, uuids, vector_db)
    final_docs = rerank_results(query, hybrid_docs)
    return generate_rag_answer(query, final_docs, "주식 및 펀드/증권")

def search_card_benefit(query: str, rdb_conn, vector_db) -> str:
    uuids = extract_metadata_and_query_rdb(query, rdb_conn)
    hybrid_docs = perform_hybrid_search(query, uuids, vector_db)
    final_docs = rerank_results(query, hybrid_docs)
    return generate_rag_answer(query, final_docs, "신용/체크카드 혜택")

def handle_general_chat(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite")
    return llm.invoke(query).content

class IntentClassification(BaseModel):
    category: str = Field(description="은행, 보험, 주식, 카드, 일반 중 하나를 선택하세요.")

def classify_intent_with_llm(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", temperature=0)
    structured_llm = llm.with_structured_output(IntentClassification)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """당신은 금융 사용자의 질문 의도를 분류하는 라우터입니다.
        다음 중 가장 적절한 카테고리 하나만 선택하세요: [은행, 보험, 주식, 카드, 일반]
        - 예적금, 대출, 청약, 환전 -> 은행
        - 실비, 종신, 자동차보험 -> 보험
        - 펀드, 주가, 증권 -> 주식
        - 할인, 포인트, 신용카드 -> 카드
        - 인사, 단순 대화 -> 일반"""),
        ("human", "{question}")
    ])
    
    return (prompt | structured_llm).invoke({"question": query}).category

@lru_cache(maxsize=1)
def get_db_and_model():
    return Chroma(persist_directory="data/chroma_db", embedding_function=HuggingFaceEmbeddings(model_name="BAAI/bge-m3"), collection_name="finmate_bank_docs")

@cl.on_chat_start
async def start():
    cl.user_session.set("rdb_conn", setup_rdb())
    cl.user_session.set("vector_db", get_db_and_model())
    
    await cl.ChatSettings([
        cl.input_widget.Select(
            id="search_domain",
            label="검색 분야 선택 (의도 명확화)",
            values=["자동 분류 (AI 추천)", "은행/예적금", "보험/보장", "주식/펀드", "카드/혜택"],
            initial_index=0,
        )
    ]).send()
    
    cl.user_session.set("search_domain", "자동 분류 (AI 추천)")
    
    await cl.Message(
        content="안녕하세요! 금융 비서 **FinMate**입니다.\n"
                "좌측 설정 메뉴에서 검색 분야를 직접 지정하시거나, "
                "그냥 질문해주시면 AI가 알아서 알맞은 문서를 찾아 답변해 드립니다!"
    ).send()

@cl.on_settings_update
async def setup_agent(settings):
    cl.user_session.set("search_domain", settings["search_domain"])

@cl.on_message
async def main(message: cl.Message):
    query = message.content
    rdb_conn = cl.user_session.get("rdb_conn")
    vector_db = cl.user_session.get("vector_db")
    selected_domain = cl.user_session.get("search_domain")
    
    category = ""
    
    # [라우팅 1단계] 사용자 명시적 선택 확인
    if selected_domain != "자동 분류 (AI 추천)":
        if "은행" in selected_domain: category = "은행"
        elif "보험" in selected_domain: category = "보험"
        elif "주식" in selected_domain: category = "주식"
        elif "카드" in selected_domain: category = "카드"
        
        await cl.Message(content=f"*(사용자 지정 모드: **{category}** 파이프라인 가동)*").send()

    # [라우팅 2단계] 자동 분류 모드일 경우 LLM 의도 파악
    else:
        with cl.Step(name=" AI 의도 분석기 가동") as step:
            category = classify_intent_with_llm(query)
            step.output = f"분석 결과: **'{category}'** 관련 질문으로 판단됨."
    
    # [라우팅 3단계] 결정된 카테고리에 맞춰 실제 RAG 검색 실행
    final_answer = ""
    
    with cl.Step(name=f" {category} 전용 검색 및 답변 생성") as step:
        if category == "은행":
            final_answer = search_bank_product(query, rdb_conn, vector_db)
        elif category == "보험":
            final_answer = search_insurance_product(query, rdb_conn, vector_db)
        elif category == "주식":
            final_answer = search_stock_info(query, rdb_conn, vector_db)
        elif category == "카드":
            final_answer = search_card_benefit(query, rdb_conn, vector_db)
        else: 
            final_answer = handle_general_chat(query)
            
        step.output = "완료되었습니다."

    await cl.Message(content=final_answer).send()