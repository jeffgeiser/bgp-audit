import os

DATA_DIR = os.environ.get("IKM_DATA_DIR", os.environ.get("DATA_DIR", "."))
STAGING_DB = os.path.join(DATA_DIR, "ikm_staging.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chromadb")
GAPS_LOG = os.path.join(DATA_DIR, "gaps.csv")

# Qwen chat completions (no auth needed for local vLLM)
QWEN_API_BASE = os.environ.get("QWEN_API_BASE", "http://10.1.0.251:18010/v1")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "not-needed")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "Qwen/Qwen3.5-35B-A3B")

# Embeddings are handled locally by ChromaDB's built-in model (all-MiniLM-L6-v2)
# No external embedding API needed
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
