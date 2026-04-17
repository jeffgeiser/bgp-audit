"""
FastAPI router for Zenlayer IKM.

Provides:
- /           → Chat interface (served by main.py root route)
- /dash       → Auditor dashboard with persona system
- /api/ikm/*  → API endpoints for chat and auditor actions
"""

import os
import sys
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared import config
from shared.db import init_db, get_chunks, get_chunk, update_chunk, insert_chunk, get_stats
from shared.vectorstore import upsert_chunk, query_knowledge, get_collection_count
from shared.gaps import log_gap
from shared.personas import init_personas_db, get_personas, get_persona, create_persona, update_persona, delete_persona

router = APIRouter()

# Init databases on import
init_db()
init_personas_db()


# --- Pydantic models ---

class ChatRequest(BaseModel):
    message: str
    department: Optional[str] = None


class ChunkApproval(BaseModel):
    content: str
    department: str
    auditor: str
    auditor_notes: Optional[str] = ""


class ChunkReject(BaseModel):
    auditor: str
    auditor_notes: Optional[str] = ""


class IngestRequest(BaseModel):
    content: str
    source: str
    department: str = "General"


class PersonaCreate(BaseModel):
    name: str
    role: str
    departments: list[str]


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    departments: Optional[list[str]] = None


# --- Page routes ---

@router.get("/dash", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    return templates.TemplateResponse("ikm_auditor.html", {
        "request": request,
        "departments": config.DEPARTMENTS,
    })


# --- Chat API ---

@router.post("/api/ikm/chat")
async def ikm_chat(req: ChatRequest):
    """Query the knowledge base and return an AI response with sources."""
    import httpx

    results = query_knowledge(
        query_text=req.message,
        department=req.department,
        n_results=5,
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    context_parts = []
    sources = []

    for doc, meta, dist in zip(documents, metadatas, distances):
        similarity = 1 - dist
        if similarity < config.SIMILARITY_THRESHOLD:
            continue
        context_parts.append(doc)
        sources.append({
            "source": meta.get("source", "Unknown"),
            "auditor": meta.get("auditor", "Unknown"),
            "department": meta.get("department", ""),
            "verified_date": meta.get("verified_date", "")[:10],
            "similarity": round(similarity, 3),
        })

    if not sources:
        best_score = 0.0
        if distances:
            best_score = 1 - min(distances)
        log_gap(req.message, best_score)
        return {
            "response": "I don't have verified information on that topic yet. Your question has been logged for the documentation team.",
            "sources": [],
            "gap_logged": True,
        }

    context = "\n\n---\n\n".join(context_parts)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{config.QWEN_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {config.QWEN_API_KEY}"},
            json={
                "model": config.QWEN_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are the Zenlayer Internal Knowledge Assistant. "
                            "Answer questions using ONLY the provided context. "
                            "Be concise and accurate. Do not make up information."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Context:\n{context}\n\nQuestion: {req.message}",
                    },
                ],
                "temperature": 0.3,
                "max_tokens": 2048,
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to get response from Qwen")

    data = resp.json()
    answer = data["choices"][0]["message"]["content"]

    return {
        "response": answer,
        "sources": sources,
        "gap_logged": False,
    }


@router.post("/api/ikm/chat/stream")
async def ikm_chat_stream(req: ChatRequest):
    """Stream a chat response from Qwen."""
    import httpx
    import json

    results = query_knowledge(
        query_text=req.message,
        department=req.department,
        n_results=5,
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    context_parts = []
    sources = []

    for doc, meta, dist in zip(documents, metadatas, distances):
        similarity = 1 - dist
        if similarity < config.SIMILARITY_THRESHOLD:
            continue
        context_parts.append(doc)
        sources.append({
            "source": meta.get("source", "Unknown"),
            "auditor": meta.get("auditor", "Unknown"),
            "department": meta.get("department", ""),
            "verified_date": meta.get("verified_date", "")[:10],
        })

    if not sources:
        best_score = 0.0
        if distances:
            best_score = 1 - min(distances)
        log_gap(req.message, best_score)

        async def no_results():
            payload = {
                "type": "complete",
                "response": "I don't have verified information on that topic yet. Your question has been logged for the documentation team.",
                "sources": [],
            }
            yield f"data: {json.dumps(payload)}\n\n"

        return StreamingResponse(no_results(), media_type="text/event-stream")

    context = "\n\n---\n\n".join(context_parts)

    async def stream_response():
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{config.QWEN_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {config.QWEN_API_KEY}"},
                json={
                    "model": config.QWEN_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are the Zenlayer Internal Knowledge Assistant. "
                                "Answer questions using ONLY the provided context. "
                                "Be concise and accurate. Do not make up information."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Context:\n{context}\n\nQuestion: {req.message}",
                        },
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2048,
                    "stream": True,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        chunk_data = line[6:]
                        if chunk_data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(chunk_data)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue

        yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")


# --- Auditor/Chunk API ---

@router.get("/api/ikm/chunks")
async def list_chunks(
    status: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    return get_chunks(status=status, department=department, limit=limit, offset=offset)


@router.get("/api/ikm/stats")
async def ikm_stats():
    return {
        "staging": get_stats(),
        "vector_count": get_collection_count(),
    }


@router.post("/api/ikm/chunks/{chunk_id}/approve")
async def approve_chunk(chunk_id: int, approval: ChunkApproval):
    chunk = get_chunk(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    update_chunk(
        chunk_id,
        content=approval.content,
        department=approval.department,
        status="Approved",
        auditor=approval.auditor,
        auditor_notes=approval.auditor_notes,
    )

    upsert_chunk(
        chunk_id=f"chunk_{chunk_id}",
        text=approval.content,
        source=chunk["source"],
        auditor=approval.auditor,
        department=approval.department,
    )

    return {"status": "approved", "chunk_id": chunk_id}


@router.post("/api/ikm/chunks/{chunk_id}/reject")
async def reject_chunk(chunk_id: int, rejection: ChunkReject):
    chunk = get_chunk(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    update_chunk(
        chunk_id,
        status="Rejected",
        auditor=rejection.auditor,
        auditor_notes=rejection.auditor_notes,
    )

    return {"status": "rejected", "chunk_id": chunk_id}


@router.put("/api/ikm/chunks/{chunk_id}")
async def edit_chunk(chunk_id: int, data: dict):
    chunk = get_chunk(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    update_chunk(chunk_id, **data)

    if chunk["status"] == "Approved" and "content" in data:
        upsert_chunk(
            chunk_id=f"chunk_{chunk_id}",
            text=data.get("content", chunk["content"]),
            source=chunk["source"],
            auditor=data.get("auditor", chunk.get("auditor", "Unknown")),
            department=data.get("department", chunk["department"]),
        )

    return {"status": "updated", "chunk_id": chunk_id}


@router.post("/api/ikm/ingest")
async def ingest_content(req: IngestRequest):
    """Manually ingest a content chunk into staging."""
    chunk_id = insert_chunk(
        content=req.content,
        source=req.source,
        department=req.department,
    )
    return {"status": "staged", "chunk_id": chunk_id}


# --- Persona API ---

@router.get("/api/ikm/personas")
async def list_personas():
    return get_personas()


@router.get("/api/ikm/personas/{persona_id}")
async def get_persona_detail(persona_id: int):
    persona = get_persona(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@router.post("/api/ikm/personas")
async def create_persona_endpoint(data: PersonaCreate):
    persona_id = create_persona(
        name=data.name,
        role=data.role,
        departments=data.departments,
    )
    return {"status": "created", "id": persona_id}


@router.put("/api/ikm/personas/{persona_id}")
async def update_persona_endpoint(persona_id: int, data: PersonaUpdate):
    updates = {k: v for k, v in data.dict().items() if v is not None}
    if not updates:
        return {"status": "no changes"}
    update_persona(persona_id, **updates)
    return {"status": "updated", "id": persona_id}


@router.delete("/api/ikm/personas/{persona_id}")
async def delete_persona_endpoint(persona_id: int):
    delete_persona(persona_id)
    return {"status": "deleted", "id": persona_id}
