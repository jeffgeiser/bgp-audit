# ZenBrain - Internal Knowledge MCP

## Stack
- **Backend:** FastAPI
- **Templates:** Jinja2
- **Frontend:** Alpine.js / Tailwind CSS
- **Vector Store:** ChromaDB (local embeddings via sentence-transformers)
- **LLM:** Qwen (Qwen3.5-35B-A3B) via OpenAI-compatible API at 10.1.0.251:18010

## Architecture
- Running in Docker behind Nginx at the `/zenbrain` subpath
- IKM chat at `/zenbrain/`, dashboard at `/zenbrain/dash`
- Legacy BGP audit available at `/zenbrain/bgp`

## Constraints

### Root Path Configuration
Keep `app = FastAPI(root_path="/zenbrain")` and ensure all internal URLs are relative or respect the root path.

### Design Elements
Do not change any existing design elements.
