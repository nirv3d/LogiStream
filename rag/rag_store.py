"""
rag_store.py - RAG over docs\\gold_definitions.md (the business rules document).

What it does:
  build   cut the document into section chunks, turn each chunk into numbers
          (an "embedding") with a small local model, and store them in Chroma
  search  turn a question into numbers the same way and return the closest chunks
  test    run the definition questions and show which chunks come back

Usage (from D:\\LogiStream):
  python rag\\rag_store.py build
  python rag\\rag_store.py search "What counts as a late shipment?"
  python rag\\rag_store.py test
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import chromadb
from chromadb.config import Settings

ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = ROOT / "docs" / "gold_definitions.md"
DB_PATH = ROOT / "rag" / "chroma_db"
GT_PATH = ROOT / "eval" / "ground_truth.json"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
COLLECTION = "gold_definitions"
MAX_CHARS = 1200  # about 300 tokens per chunk
TOP_K = 5         # how many chunks a search returns


# ------------------------------------------------------------- 1. chunking
def chunk_markdown(text, max_chars=MAX_CHARS):
    """Split by markdown headings. Every chunk starts with its heading trail
    (for example 'Hub daily table > Origin hub') so it still makes sense on its own."""
    sections = []  # (trail, body_lines)
    stack = []     # [(level, title)]
    body = []

    def flush():
        if any(line.strip() for line in body):
            trail = " > ".join(t for _, t in stack) or "Introduction"
            sections.append((trail, "\n".join(body).strip()))

    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            title = s[level:].strip()
            if title:
                flush()
                body = []
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                continue
        body.append(line)
    flush()

    chunks = []
    for trail, body_text in sections:
        for piece in _split_long(body_text, max_chars):
            chunks.append({"section": trail, "text": f"Section: {trail}\n{piece}"})
    return chunks


def _split_long(body, max_chars):
    """Keep a short section whole; otherwise pack paragraphs (then lines) up to max_chars."""
    if len(body) <= max_chars:
        return [body]
    pieces, current = [], ""
    for block in body.split("\n\n"):
        for part in ([block] if len(block) <= max_chars else block.split("\n")):
            if current and len(current) + len(part) + 2 > max_chars:
                pieces.append(current.strip())
                current = ""
            current += part + "\n\n"
    if current.strip():
        pieces.append(current.strip())
    return pieces


# ------------------------------------------------------------ 2. embedding
def embed(texts, prefix):
    """Ask the local Ollama server for embeddings. nomic-embed-text works best when
    documents start with 'search_document: ' and questions with 'search_query: '."""
    out = []
    for i in range(0, len(texts), 16):
        batch = [prefix + t for t in texts[i:i + 16]]
        data = json.dumps({"model": EMBED_MODEL, "input": batch}).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL + "/api/embed", data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                out.extend(json.load(r)["embeddings"])
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Ollama said: {e.read().decode('utf-8', 'replace')}\n"
                f"Did you run:  ollama pull {EMBED_MODEL}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Cannot reach Ollama at {OLLAMA_URL}. Is the Ollama app running?") from e
    return out


# --------------------------------------------------------------- 3. storage
def _client():
    return chromadb.PersistentClient(
        path=str(DB_PATH), settings=Settings(anonymized_telemetry=False))


def build():
    text = DOC_PATH.read_text(encoding="utf-8")
    chunks = chunk_markdown(text)
    client = _client()
    try:
        client.delete_collection(COLLECTION)  # full rebuild, so no stale chunks
    except Exception:
        pass
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    vectors = embed([c["text"] for c in chunks], "search_document: ")
    col.add(
        ids=[f"c{i:03d}" for i in range(len(chunks))],
        documents=[c["text"] for c in chunks],
        embeddings=vectors,
        metadatas=[{"section": c["section"]} for c in chunks],
    )
    sizes = [len(c["text"]) for c in chunks]
    print(f"Read {DOC_PATH}")
    print(f"Stored {len(chunks)} chunks (shortest {min(sizes)}, longest {max(sizes)} characters)")
    print(f"Database: {DB_PATH}")


def search(question, k=TOP_K):
    """Return the k closest chunks as dicts: section, text, distance (lower = closer)."""
    col = _client().get_collection(COLLECTION)
    q = embed([question], "search_query: ")
    res = col.query(query_embeddings=q, n_results=k)
    return [
        {"section": m["section"], "text": d, "distance": round(dist, 4)}
        for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def format_context(results):
    """The text block that gets pasted into the model's prompt."""
    return "\n\n---\n\n".join(r["text"] for r in results)


# ------------------------------------------------------------------- 4. test
def test():
    questions = []
    try:
        gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
        questions += [(q["id"], q["question"]) for q in gt["questions"]
                      if q["category"] in ("definition", "unanswerable")]
    except FileNotFoundError:
        pass
    questions += [
        ("x1", "How is hub processing time defined?"),
        ("x2", "Which city is used for the cancellation rate by city?"),
        ("x3", "What does 'today' mean in this data?"),
        ("x4", "Are scanned events used?"),
    ]
    for qid, question in questions:
        print(f"\n[{qid}] {question}")
        for rank, r in enumerate(search(question), 1):
            first = r["text"].split("\n", 1)[1].replace("\n", " ")[:110]
            print(f"   {rank}. dist={r['distance']:.3f}  {r['section']}")
            print(f"      {first}")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # Windows consoles can choke on odd characters
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        build()
    elif cmd == "search" and len(sys.argv) > 2:
        for r in search(" ".join(sys.argv[2:])):
            print(f"\n--- dist={r['distance']:.3f}  {r['section']}\n{r['text']}")
    elif cmd == "test":
        test()
    else:
        print(__doc__)