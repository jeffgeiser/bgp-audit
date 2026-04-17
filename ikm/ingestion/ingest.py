"""
Ingestion pipeline for Zenlayer IKM.

Crawls URLs or processes local files, chunks the content into Markdown,
and stages chunks in SQLite for auditor review.

Usage:
    python ingest.py --url https://docs.zenlayer.com/some-page --department "Bare Metal"
    python ingest.py --file /path/to/document.pdf --department "HR"
    python ingest.py --urls-file urls.txt --department "Cloud Networking"
"""

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config
from shared.db import init_db, insert_chunk, get_connection


def chunk_markdown(text: str, max_chunk_size: int = 1500, overlap: int = 200) -> list[str]:
    """Split markdown into chunks, preferring section boundaries."""
    # Split on headers first
    sections = re.split(r'\n(?=#{1,4}\s)', text)

    chunks = []
    current = ""

    for section in sections:
        section = section.strip()
        if not section:
            continue

        if len(current) + len(section) < max_chunk_size:
            current = current + "\n\n" + section if current else section
        else:
            if current:
                chunks.append(current.strip())
            # If a single section exceeds max, split by paragraphs
            if len(section) > max_chunk_size:
                paragraphs = section.split("\n\n")
                sub = ""
                for para in paragraphs:
                    if len(sub) + len(para) < max_chunk_size:
                        sub = sub + "\n\n" + para if sub else para
                    else:
                        if sub:
                            chunks.append(sub.strip())
                        sub = para
                current = sub
            else:
                current = section

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if len(c) > 50]  # Skip trivially small chunks


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def is_duplicate(content: str) -> bool:
    """Check if chunk content already exists in staging."""
    h = content_hash(content)
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM chunks WHERE content = ? OR content LIKE ? LIMIT 1",
        (content, content[:100] + "%"),
    ).fetchone()
    conn.close()
    return row is not None


async def crawl_url(url: str) -> str:
    """Crawl a URL using Crawl4AI and return markdown content."""
    try:
        from crawl4ai import AsyncWebCrawler

        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            return result.markdown if result.success else ""
    except ImportError:
        # Fallback: use Crawl4AI REST API if running as a service
        import aiohttp

        crawl4ai_url = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
        async with aiohttp.ClientSession() as session:
            payload = {"urls": [url], "word_count_threshold": 50}
            async with session.post(f"{crawl4ai_url}/crawl", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    return results[0].get("markdown", "") if results else ""
        return ""


def process_pdf(filepath: str) -> str:
    """Extract markdown from a PDF using Docling."""
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(filepath)
        return result.document.export_to_markdown()
    except ImportError:
        print("[Ingest] Docling not installed, skipping PDF processing")
        return ""


def ingest_markdown(markdown: str, source: str, department: str) -> int:
    """Chunk markdown and insert into staging DB. Returns count of chunks inserted."""
    chunks = chunk_markdown(markdown)
    inserted = 0
    for chunk in chunks:
        if not is_duplicate(chunk):
            insert_chunk(content=chunk, source=source, department=department)
            inserted += 1
    return inserted


async def ingest_url(url: str, department: str) -> int:
    """Crawl a URL and ingest its content."""
    print(f"[Ingest] Crawling: {url}")
    markdown = await crawl_url(url)
    if not markdown:
        print(f"[Ingest] No content from: {url}")
        return 0
    count = ingest_markdown(markdown, source=url, department=department)
    print(f"[Ingest] Staged {count} chunks from: {url}")
    return count


def ingest_file(filepath: str, department: str) -> int:
    """Process a local file and ingest its content."""
    print(f"[Ingest] Processing file: {filepath}")
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".pdf":
        markdown = process_pdf(filepath)
    elif ext in (".md", ".txt"):
        with open(filepath, "r") as f:
            markdown = f.read()
    else:
        print(f"[Ingest] Unsupported file type: {ext}")
        return 0

    if not markdown:
        print(f"[Ingest] No content from: {filepath}")
        return 0

    count = ingest_markdown(markdown, source=os.path.basename(filepath), department=department)
    print(f"[Ingest] Staged {count} chunks from: {filepath}")
    return count


async def main():
    parser = argparse.ArgumentParser(description="Ingest content into IKM staging")
    parser.add_argument("--url", help="Single URL to crawl")
    parser.add_argument("--urls-file", help="File with one URL per line")
    parser.add_argument("--file", help="Local file to process (PDF, MD, TXT)")
    parser.add_argument("--department", default="General", help="Department category")
    args = parser.parse_args()

    init_db()

    total = 0
    if args.url:
        total += await ingest_url(args.url, args.department)
    elif args.urls_file:
        with open(args.urls_file) as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        for url in urls:
            total += await ingest_url(url, args.department)
    elif args.file:
        total += ingest_file(args.file, args.department)
    else:
        parser.print_help()
        return

    print(f"\n[Ingest] Done. Total chunks staged: {total}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
