import os

DATA_DIR = os.environ.get("IKM_DATA_DIR", "/app/data")
STAGING_DB = os.path.join(DATA_DIR, "staging.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chromadb")
GAPS_LOG = os.path.join(DATA_DIR, "gaps.csv")

QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "not-needed")
QWEN_API_BASE = os.environ.get("QWEN_API_BASE", "http://10.1.0.251:18010/v1")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-plus")

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-v3")
CHROMA_COLLECTION = "zenlayer_knowledge"

SIMILARITY_THRESHOLD = 0.7

DEPARTMENTS = [
    "Bare Metal",
    "Cloud Networking",
    "HR",
    "DevOps",
    "Sales",
    "Finance",
    "General",
]
