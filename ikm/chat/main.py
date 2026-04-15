"""
Chainlit Chat Interface for Zenlayer IKM.

Queries only verified (approved) knowledge from ChromaDB.
Every response cites the source URL and the auditor who verified it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chainlit as cl
from openai import OpenAI
from shared import config
from shared.vectorstore import query_knowledge
from shared.gaps import log_gap

client = OpenAI(api_key=config.QWEN_API_KEY, base_url=config.QWEN_API_BASE)

SYSTEM_PROMPT = """You are the Zenlayer Internal Knowledge Assistant. You answer questions using ONLY the verified knowledge base provided in the context below.

Rules:
1. Only use information from the provided context to answer questions.
2. If the context doesn't contain enough information, say "I don't have verified information on that topic yet. Your question has been logged for the documentation team."
3. Always cite your sources at the end of your response in a "Sources" section.
4. For each source, include who verified it (the auditor name).
5. Be concise, accurate, and professional.
6. If multiple sources cover the topic, synthesize them but cite all relevant ones.
"""


def embed_query(text: str) -> list[float]:
    response = client.embeddings.create(input=[text], model=config.EMBEDDING_MODEL)
    return response.data[0].embedding


def build_context(results: dict) -> tuple[str, list[dict]]:
    """Build context string and source list from ChromaDB results."""
    if not results["documents"] or not results["documents"][0]:
        return "", []

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    context_parts = []
    sources = []

    for doc, meta, dist in zip(documents, metadatas, distances):
        similarity = 1 - dist  # ChromaDB cosine distance -> similarity
        if similarity < config.SIMILARITY_THRESHOLD:
            continue
        context_parts.append(doc)
        sources.append({
            "source": meta.get("source", "Unknown"),
            "auditor": meta.get("auditor", "Unknown"),
            "department": meta.get("department", ""),
            "verified_date": meta.get("verified_date", ""),
            "similarity": similarity,
        })

    return "\n\n---\n\n".join(context_parts), sources


@cl.on_chat_start
async def start():
    await cl.Message(
        content=(
            "Welcome to the **Zenlayer Knowledge Assistant**. "
            "I can answer questions about internal processes, product specs, and SOPs "
            "using our verified knowledge base.\n\n"
            "What would you like to know?"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    query = message.content

    # Embed the query
    query_embedding = embed_query(query)

    # Search ChromaDB
    results = query_knowledge(query_embedding=query_embedding, n_results=5)

    # Build context
    context, sources = build_context(results)

    # Check for gaps
    if not sources:
        # Log the gap
        best_score = 0.0
        if results["distances"] and results["distances"][0]:
            best_score = 1 - min(results["distances"][0])
        log_gap(query, best_score)

        await cl.Message(
            content=(
                "I don't have verified information on that topic yet. "
                "Your question has been logged so our documentation team can address it."
            )
        ).send()
        return

    # Build the prompt
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {query}",
        },
    ]

    # Stream the response
    msg = cl.Message(content="")
    await msg.send()

    stream = client.chat.completions.create(
        model=config.QWEN_MODEL,
        messages=messages,
        stream=True,
        temperature=0.3,
        max_tokens=2048,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            await msg.stream_token(delta.content)

    # Append sources
    source_block = "\n\n---\n**Sources:**\n"
    seen = set()
    for s in sources:
        key = s["source"]
        if key in seen:
            continue
        seen.add(key)
        source_block += (
            f"- {s['source']} — *Verified by {s['auditor']}*"
            f" ({s['department']}, {s['verified_date'][:10]})\n"
        )

    await msg.stream_token(source_block)
    await msg.update()
