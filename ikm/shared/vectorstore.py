import os
import chromadb
from datetime import datetime, timezone
from . import config


def get_client() -> chromadb.PersistentClient:
    os.makedirs(config.CHROMA_DIR, exist_ok=True)
    return chromadb.PersistentClient(path=config.CHROMA_DIR)


def get_collection(client: chromadb.PersistentClient = None):
    if client is None:
        client = get_client()
    return client.get_or_create_collection(
        name=config.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunk(
    chunk_id: str,
    text: str,
    embedding: list[float],
    source: str,
    auditor: str,
    department: str,
):
    collection = get_collection()
    collection.upsert(
        ids=[chunk_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[
            {
                "source": source,
                "auditor": auditor,
                "department": department,
                "verified_date": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )


def query_knowledge(
    query_embedding: list[float],
    department: str = None,
    n_results: int = 5,
) -> dict:
    collection = get_collection()
    where = {"department": department} if department else None
    return collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"],
    )


def get_collection_count() -> int:
    try:
        collection = get_collection()
        return collection.count()
    except Exception:
        return 0
