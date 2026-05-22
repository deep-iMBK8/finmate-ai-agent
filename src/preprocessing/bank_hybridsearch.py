import os
import glob
import json
import sqlite3
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

def setup_rdb(json_folder="src/chunking"):
    print("1. RDB(SQLite) 초기화 및 메타데이터 적재 중...")
    conn = sqlite3.connect(':memory:') 
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE documents (
            document_uuid TEXT,
            company TEXT,
            document_type TEXT,
            document_date TEXT
        )
    ''')
    
    inserted_uuids = set()
    
    for file_path in glob.glob(os.path.join(json_folder, "*.json")):
        with open(file_path, 'r', encoding='utf-8') as f:
            chunks = json.load(f)
            if not isinstance(chunks, list): continue
            
            for chunk in chunks:
                meta = chunk.get("metadata", {})
                uuid = meta.get("document_uuid")
                
                if uuid and uuid not in inserted_uuids:
                    cursor.execute('''
                        INSERT INTO documents (document_uuid, company, document_type, document_date)
                        VALUES (?, ?, ?, ?)
                    ''', (uuid, meta.get("company"), meta.get("document_type"), meta.get("document_date")))
                    inserted_uuids.add(uuid)
                    
    conn.commit()
    return conn

def extract_metadata_and_query_rdb(query, db_conn):
    print("\n2. [RDB 검색] 질의 의도 파악 및 SQL 필터링 실행...")
    cursor = db_conn.cursor()
    
    target_company = None
    if "하나은행" in query: target_company = "하나은행"
    elif "토스" in query or "bankbook" in query: target_company = "bankbook"
    elif "아이엠뱅크" in query: target_company = "아이엠뱅크"
    elif "신한은행" in query: target_company = "신한은행"

    if target_company:
        print(f"   -> 감지된 조건: 기업명 = '{target_company}'")
        cursor.execute("SELECT document_uuid FROM documents WHERE company = ?", (target_company,))
    else:
        print("   -> 감지된 조건 없음: 전체 문서 대상 검색")
        cursor.execute("SELECT document_uuid FROM documents")
        
    result_uuids = [row[0] for row in cursor.fetchall()]
    print(f"   -> RDB 필터링 결과: 총 {len(result_uuids)}개의 원본 문서 ID 확보")
    return result_uuids

def perform_hybrid_search(query, target_uuids, vector_db, json_folder="src/chunking", top_k=5):
    if not target_uuids:
        return []

    print(f"\n3. [Vector 검색] 필터링된 문서 내에서 의미(Semantic) 검색 진행...")
    
    vector_results = vector_db.similarity_search(
        query=query, 
        k=top_k * 2, 
        filter={"document_uuid": {"$in": target_uuids}}
    )

    print(f"4. [BM25 검색] 정확한 키워드 매칭 검색 진행...")
    target_docs = []
    for file_path in glob.glob(os.path.join(json_folder, "*.json")):
        with open(file_path, 'r', encoding='utf-8') as f:
            chunks = json.load(f)
            for chunk in chunks:
                if chunk.get("metadata", {}).get("document_uuid") in target_uuids:
                    text_content = chunk.get("page_content", "").strip()
                    if text_content:
                        target_docs.append(Document(page_content=text_content, metadata=chunk.get("metadata", {})))

    tokenized_corpus = [doc.page_content.split() for doc in target_docs]
    bm25 = BM25Okapi(tokenized_corpus)
    tokenized_query = query.split()
    bm25_scores = bm25.get_scores(tokenized_query)
    
    bm25_top_indices = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[:top_k*2]
    bm25_results = [target_docs[i] for i in bm25_top_indices]

    print(f"5. [Score 결합] RRF 알고리즘으로 Vector와 BM25 결과 앙상블...")
    fused_scores = {}
    doc_map = {}
    
    def add_to_rrf(docs, weight=1.0):
        for rank, doc in enumerate(docs, 1):
            doc_id = doc.page_content 
            doc_map[doc_id] = doc
            if doc_id not in fused_scores:
                fused_scores[doc_id] = 0
            fused_scores[doc_id] += weight * (1 / (rank + 60))

    add_to_rrf(vector_results, weight=0.5)
    add_to_rrf(bm25_results, weight=0.5)
    
    sorted_docs = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    return [doc_map[doc_id] for doc_id, score in sorted_docs[:top_k]]

def rerank_results(query, retrieved_docs, top_k=3):
    if not retrieved_docs:
        return []
        
    print(f"\n6. [Reranking] Cross-Encoder 모델을 통한 최종 순위 정밀 재조정...")
    
    reranker_model = CrossEncoder('BAAI/bge-reranker-v2-m3', device='cpu')
    
    sentence_pairs = [[query, doc.page_content] for doc in retrieved_docs]
    scores = reranker_model.predict(sentence_pairs)
    
    scored_docs = list(zip(retrieved_docs, scores))
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    
    return [doc for doc, score in scored_docs[:top_k]]

def generate_rag_answer(query, retrieved_docs, api_key):
    if not retrieved_docs:
        return "관련 문서를 찾을 수 없어 답변을 생성할 수 없습니다."
        
    print(f"\n7. [Generation] 검색된 문서를 바탕으로 Gemini LLM이 답변을 생성 중입니다...")

    context_text = "\n\n---\n\n".join([doc.page_content for doc in retrieved_docs])
    
    system_prompt = """
    당신은 금융 및 은행 문서를 바탕으로 고객의 질문에 답변하는 전문 AI 어시스턴트 'FINMATE'입니다.
    반드시 아래 제공된 [검색된 문서 내용]만을 바탕으로 사용자의 질문에 답변하세요.
    제공된 문서에 없는 내용이라면, 절대 지어내지 말고 "제공된 문서에서는 해당 정보를 찾을 수 없습니다."라고 솔직하게 답변하세요.
    답변은 가독성 좋게 글머리 기호와 줄바꿈을 활용하여 친절하게 작성해주세요.
    
    [검색된 문서 내용]
    {context}
    """
    
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{question}")
    ])

    os.environ["GOOGLE_API_KEY"] = api_key
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", temperature=0.1)
    
    rag_chain = prompt_template | llm | StrOutputParser()
    
    try:
        answer = rag_chain.invoke({
            "context": context_text,
            "question": query
        })
        return answer
    except Exception as e:
        return f"답변 생성 중 오류가 발생했습니다: {e}"

if __name__ == "__main__":

    GEMINI_API_KEY = "AIzaSyDp9-8sYzjUEXVT1lAmZSFUFYJqdhZD2QA" 


    print("0. 기존 벡터 DB (Chroma) 로드 중...")
    embedding_model = HuggingFaceEmbeddings(
        model_name="BAAI/bge-m3",
        model_kwargs={'device': 'cpu'},
        encode_kwargs={'normalize_embeddings': True}
    )
    vector_db = Chroma(
        persist_directory="data/chroma_db", 
        embedding_function=embedding_model,
        collection_name="finmate_bank_docs"
    )


    rdb_conn = setup_rdb()
    

    user_query = "주택청약 1순위가 되려면 어떻게 해야 하나요?"
    print(f"\n=============================================")
    print(f" 사용자 질문: '{user_query}'")
    print(f"=============================================")

    # Step 1~3: RDB 필터링
    target_uuids = extract_metadata_and_query_rdb(user_query, rdb_conn)
    
    # Step 4~5: 하이브리드 검색 (Vector + BM25)
    hybrid_docs = perform_hybrid_search(user_query, target_uuids, vector_db, top_k=5)
    
    # Step 6: Reranker 기반 최종 재정렬
    final_docs = rerank_results(user_query, hybrid_docs, top_k=3)

    # [결과 출력: 검색된 문서 확인용]
    print(f"\n 최종 검색 결과 (Top {len(final_docs)}):")
    for i, doc in enumerate(final_docs, 1):
        meta = doc.metadata
        print(f"\n[{i}위] 출처: {meta.get('company')} - {meta.get('document_type')} ({meta.get('page_number')}p)")
        print(f"내용: {doc.page_content[:150]}...")

# Step 7: LLM RAG 답변 생성 및 최종 출력
    if GEMINI_API_KEY:  # 키가 존재하기만 하면 무조건 실행하도록 변경
        final_answer = generate_rag_answer(user_query, final_docs, GEMINI_API_KEY)
        
        print("\n=============================================")
        print(" [FINMATE AI의 답변]")
        print("=============================================")
        print(final_answer)
        
        print("\n[ 참고한 출처]")
        for i, doc in enumerate(final_docs, 1):
            meta = doc.metadata
            print(f"- {meta.get('company')} {meta.get('document_type')} ({meta.get('page_number')}페이지)")
    else:
        print("\n[알림] 구글 API 키가 입력되지 않아 LLM 답변 생성 단계를 건너뜁니다.")
        print("코드 하단의 GEMINI_API_KEY 변수에 실제 키를 입력해주세요.")