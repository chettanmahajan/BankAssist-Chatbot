"""RAG pipeline: embeddings, FAISS retriever, and the Groq chain."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from langchain.chains import ConversationalRetrievalChain
from langchain.prompts import PromptTemplate
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings

from .ingestion import ingest

load_dotenv()
logger = logging.getLogger(__name__)


# ----- configuration --------------------------------------------------------

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# Env var name kept as CHROMA_DIR for backwards-compat with existing .env files;
# the path now holds a FAISS index (index.faiss + index.pkl).
INDEX_DIR = os.getenv("VECTORSTORE_DIR") or os.getenv("CHROMA_DIR", "./faiss_index")
DATA_DIR = os.getenv("DATA_DIR", "./data")

RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "5"))
RETRIEVAL_FETCH_K = int(os.getenv("RETRIEVAL_FETCH_K", "15"))
RETRIEVAL_LAMBDA = float(os.getenv("RETRIEVAL_LAMBDA", "0.6"))
LOW_SCORE_THRESHOLD = float(os.getenv("LOW_SCORE_THRESHOLD", "0.0"))

FALLBACK_ANSWER = (
    "I don't have specific information on that in my knowledge base. "
    "Please contact your bank's customer care at 1800-XXX-XXXX or visit your nearest branch."
)


# ----- banking prompt -------------------------------------------------------

BANKING_PROMPT_TEMPLATE = """You are a knowledgeable Banking Support Assistant for Indian banking customers. You answer questions about any banking topic: accounts, loans, credit cards, deposits, investments, insurance, digital banking, NEFT/RTGS/UPI, RBI rules, KYC, definitions, acronyms, processes, eligibility, and more.

You have TWO sources of information:
1. RETRIEVED DOCUMENTS below — specific policies, rates, fees, and procedures from your bank's knowledge base.
2. Your own established knowledge of Indian banking — RBI guidelines, common industry practices, definitions of banking terms and acronyms, and general financial concepts.

RULES:
1. If the retrieved documents directly answer the question, base your answer on them and prefer their specific numbers, fees, and procedures.
2. If the documents only partially answer or don't cover the question, use your own banking knowledge to give a complete, helpful answer. Prefix such answers with "Based on general banking knowledge:" so the user knows the source.
3. For specific interest rates, fees, or charges from the documents, state the number, then add "Please confirm current rates with your bank as these are subject to change."
4. NEVER invent specific bank policies, account numbers, rates, or fees that aren't in the documents. For numbers not in the docs, give realistic ranges (e.g., "savings rates typically range from 2.5% to 4% p.a.") and tell the user to confirm with their bank.
5. For personal financial advice (which loan, which investment): give a factual comparison, then say "For personalized advice, please consult your bank's relationship manager."
6. Answer common definitions, acronyms (FAQ, KYC, NEFT, RTGS, IFSC, CIBIL, EMI, etc.), and conceptual questions confidently from general banking knowledge — these are standard, factual.
7. If the question is clearly NOT about banking, finance, or related personal/business money topics (e.g., cooking, weather, sports), politely decline: "I'm a banking support assistant — I can only help with banking and financial queries."
8. Keep answers structured: short paragraphs or bullet points. Be concise but complete.
9. Maintain conversational context — if the user asked about home loans earlier and now says "what about its tenure?", interpret "its" as home loan.

CONTEXT FROM DOCUMENTS:
{context}

CHAT HISTORY:
{chat_history}

USER QUESTION: {question}

ANSWER:"""

BANKING_PROMPT = PromptTemplate(
    template=BANKING_PROMPT_TEMPLATE,
    input_variables=["context", "chat_history", "question"],
)

# Default condense prompt + minimal customisation
CONDENSE_QUESTION_TEMPLATE = """Given the following conversation and a follow-up question, rephrase the follow-up to be a standalone question in the same language. If the follow-up is already standalone, return it unchanged.

Chat History:
{chat_history}

Follow-Up Input: {question}
Standalone question:"""

CONDENSE_QUESTION_PROMPT = PromptTemplate.from_template(CONDENSE_QUESTION_TEMPLATE)


# ----- embeddings + vectorstore ---------------------------------------------

_embeddings_singleton: HuggingFaceEmbeddings | None = None


def get_embeddings() -> HuggingFaceEmbeddings:
    """Singleton — loading the model is ~5-10s and consumes RAM."""
    global _embeddings_singleton
    if _embeddings_singleton is None:
        logger.info("loading embeddings model: %s", EMBEDDING_MODEL)
        _embeddings_singleton = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    return _embeddings_singleton


def build_vectorstore(data_dir: str = DATA_DIR, persist_dir: str = INDEX_DIR) -> FAISS:
    """One-shot: ingest documents, embed, persist."""
    Path(persist_dir).mkdir(parents=True, exist_ok=True)

    logger.info("ingesting docs from %s ...", data_dir)
    chunks: list[Document] = ingest(data_dir)
    logger.info("got %d chunks", len(chunks))

    logger.info("building FAISS index at %s ...", persist_dir)
    vs = FAISS.from_documents(documents=chunks, embedding=get_embeddings())
    vs.save_local(persist_dir)
    logger.info("indexed %d chunks", len(chunks))
    return vs


def load_vectorstore(persist_dir: str = INDEX_DIR) -> FAISS:
    index_file = Path(persist_dir) / "index.faiss"
    if not index_file.exists():
        raise FileNotFoundError(
            f"vector store not found at {persist_dir}. Run `python -m backend.build_index` first."
        )
    # allow_dangerous_deserialization is required because FAISS persists the
    # docstore as pickle; we trust our own index that we just built.
    return FAISS.load_local(
        folder_path=persist_dir,
        embeddings=get_embeddings(),
        allow_dangerous_deserialization=True,
    )


def count_documents(vs: FAISS) -> int:
    try:
        return int(vs.index.ntotal)
    except Exception:
        return 0


# ----- retrieval + chain ----------------------------------------------------


def build_llm(streaming: bool = False) -> ChatGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set; populate backend/.env from .env.example")
    return ChatGroq(
        model=GROQ_MODEL,
        temperature=0.2,
        max_tokens=800,
        streaming=streaming,
        api_key=api_key,
    )


def build_retriever(vs: FAISS):
    return vs.as_retriever(
        search_type="mmr",
        search_kwargs={
            "k": RETRIEVAL_K,
            "fetch_k": RETRIEVAL_FETCH_K,
            "lambda_mult": RETRIEVAL_LAMBDA,
        },
    )


def build_chain(vs: FAISS, memory, streaming: bool = False) -> ConversationalRetrievalChain:
    """Build a chain that returns answer + source documents, scoped to a memory."""
    llm = build_llm(streaming=streaming)
    retriever = build_retriever(vs)
    return ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        memory=memory,
        condense_question_prompt=CONDENSE_QUESTION_PROMPT,
        combine_docs_chain_kwargs={"prompt": BANKING_PROMPT},
        return_source_documents=True,
        output_key="answer",
        verbose=False,
    )


def low_score_fallback(vs: FAISS, query: str, threshold: float = LOW_SCORE_THRESHOLD) -> bool:
    """Return True when the best retrieval is too weak to bother calling the LLM.

    FAISS (IndexFlatL2) returns squared L2 distance. For unit-normalised
    embeddings: d² = 2 - 2·cos_sim, so cos_sim = 1 - d²/2. We treat
    cos_sim < threshold as 'no useful match'.
    """
    try:
        results = vs.similarity_search_with_score(query, k=1)
    except Exception as exc:
        logger.warning("similarity check failed (%s); skipping fallback", exc)
        return False
    if not results:
        return True
    _doc, distance = results[0]
    similarity = 1.0 - float(distance) / 2.0
    logger.info("top similarity %.3f (d=%.3f) for query=%r", similarity, float(distance), query[:60])
    return similarity < threshold


def format_sources(source_docs: Iterable[Document]) -> list[dict]:
    """Compact source representation for API responses."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for d in source_docs:
        meta = d.metadata or {}
        filename = meta.get("source", "unknown")
        category = meta.get("category", "uncategorised")
        snippet = (d.page_content or "")[:240].strip()
        key = (filename, snippet[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "filename": filename,
                "category": category,
                "snippet": snippet,
            }
        )
    return out
