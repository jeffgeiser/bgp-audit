"""
Streamlit Auditor Dashboard for Zenlayer IKM.

PMs and department heads review, edit, and approve knowledge chunks
before they're promoted to the production vector store.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from openai import OpenAI
from shared import config
from shared.db import init_db, get_chunks, get_chunk, update_chunk, get_stats, get_department_stats
from shared.vectorstore import upsert_chunk, get_collection_count

st.set_page_config(
    page_title="Zenlayer IKM Auditor",
    page_icon="🔍",
    layout="wide",
)

# --- Embedding helper ---


@st.cache_resource
def get_embed_client():
    return OpenAI(api_key=config.QWEN_API_KEY, base_url=config.QWEN_API_BASE)


def embed_text(text: str) -> list[float]:
    client = get_embed_client()
    response = client.embeddings.create(input=[text], model=config.EMBEDDING_MODEL)
    return response.data[0].embedding


# --- Init ---
init_db()

# --- Sidebar ---
st.sidebar.title("IKM Auditor")
st.sidebar.caption("Review and approve knowledge for the Zenlayer AI assistant.")

# Stats
stats = get_stats()
total_pending = stats.get("Pending", 0)
total_approved = stats.get("Approved", 0)
total_rejected = stats.get("Rejected", 0)
vector_count = get_collection_count()

st.sidebar.metric("Pending Review", total_pending)
st.sidebar.metric("Approved", total_approved)
st.sidebar.metric("In Vector Store", vector_count)

st.sidebar.divider()

# Filters
filter_status = st.sidebar.selectbox("Status", ["Pending", "Approved", "Rejected", "All"])
filter_dept = st.sidebar.selectbox("Department", ["All"] + config.DEPARTMENTS)

auditor_name = st.sidebar.text_input("Your Name", placeholder="e.g. Jane Doe")

# --- Main Content ---
st.title("Knowledge Chunk Review")

if not auditor_name:
    st.warning("Enter your name in the sidebar before approving chunks.")

# Fetch chunks
status_filter = None if filter_status == "All" else filter_status
dept_filter = None if filter_dept == "All" else filter_dept
chunks = get_chunks(status=status_filter, department=dept_filter, limit=50)

if not chunks:
    st.info("No chunks match the current filters.")
    st.stop()

st.caption(f"Showing {len(chunks)} chunks")

for chunk in chunks:
    with st.expander(
        f"{'🟡' if chunk['status'] == 'Pending' else '🟢' if chunk['status'] == 'Approved' else '🔴'} "
        f"#{chunk['id']} — {chunk['source'][:60]} — {chunk['department']}",
        expanded=chunk["status"] == "Pending",
    ):
        col1, col2 = st.columns([3, 1])

        with col1:
            edited_content = st.text_area(
                "Content",
                value=chunk["content"],
                height=200,
                key=f"content_{chunk['id']}",
            )

            notes = st.text_input(
                "Auditor Notes",
                value=chunk.get("auditor_notes", "") or "",
                key=f"notes_{chunk['id']}",
            )

        with col2:
            st.caption(f"**Source:** {chunk['source']}")
            st.caption(f"**Status:** {chunk['status']}")
            st.caption(f"**Department:** {chunk['department']}")
            if chunk.get("auditor"):
                st.caption(f"**Auditor:** {chunk['auditor']}")
            st.caption(f"**Updated:** {chunk['updated_at'][:10]}")

            new_dept = st.selectbox(
                "Department",
                config.DEPARTMENTS,
                index=config.DEPARTMENTS.index(chunk["department"])
                if chunk["department"] in config.DEPARTMENTS
                else 0,
                key=f"dept_{chunk['id']}",
            )

            # Action buttons
            if chunk["status"] == "Pending":
                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button("Approve", key=f"approve_{chunk['id']}", type="primary"):
                        if not auditor_name:
                            st.error("Enter your name first.")
                        else:
                            # Update staging DB
                            update_chunk(
                                chunk["id"],
                                content=edited_content,
                                department=new_dept,
                                status="Approved",
                                auditor=auditor_name,
                                auditor_notes=notes,
                            )
                            # Embed and push to ChromaDB
                            embedding = embed_text(edited_content)
                            upsert_chunk(
                                chunk_id=f"chunk_{chunk['id']}",
                                text=edited_content,
                                embedding=embedding,
                                source=chunk["source"],
                                auditor=auditor_name,
                                department=new_dept,
                            )
                            st.success(f"Approved and embedded by {auditor_name}")
                            st.rerun()

                with btn_col2:
                    if st.button("Reject", key=f"reject_{chunk['id']}"):
                        update_chunk(
                            chunk["id"],
                            status="Rejected",
                            auditor=auditor_name or "Unknown",
                            auditor_notes=notes,
                        )
                        st.rerun()
            else:
                if st.button("Save Edits", key=f"save_{chunk['id']}"):
                    update_chunk(
                        chunk["id"],
                        content=edited_content,
                        department=new_dept,
                        auditor_notes=notes,
                    )
                    # Re-embed if already approved
                    if chunk["status"] == "Approved":
                        embedding = embed_text(edited_content)
                        upsert_chunk(
                            chunk_id=f"chunk_{chunk['id']}",
                            text=edited_content,
                            embedding=embedding,
                            source=chunk["source"],
                            auditor=auditor_name or chunk.get("auditor", "Unknown"),
                            department=new_dept,
                        )
                    st.success("Saved")
                    st.rerun()
