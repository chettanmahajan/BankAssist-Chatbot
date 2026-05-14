"""Document ingestion: load PDF/TXT files from data/, clean and chunk them."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

_PAGE_PATTERN = re.compile(r"\bpage\s+\d+\s+of\s+\d+\b", re.IGNORECASE)
_STANDALONE_PAGE_NO = re.compile(r"^\s*-?\s*\d{1,4}\s*-?\s*$")
_MULTI_WS = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def _clean_text(text: str) -> str:
    """Strip headers/footers and normalise whitespace; keep numbers and symbols intact."""
    if not text:
        return ""

    lines = text.splitlines()
    cleaned: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            cleaned.append("")
            continue
        if _STANDALONE_PAGE_NO.match(line):
            continue
        line = _PAGE_PATTERN.sub("", line)
        line = _MULTI_WS.sub(" ", line)
        cleaned.append(line)

    text = "\n".join(cleaned)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def _category_from_path(path: Path, data_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(data_root.resolve())
    except ValueError:
        return "uncategorised"
    parts = rel.parts
    return parts[0] if len(parts) > 1 else "uncategorised"


def _load_single_file(path: Path, data_root: Path) -> list[Document]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            docs = PyPDFLoader(str(path)).load()
            file_type = "pdf"
        elif suffix == ".txt":
            docs = TextLoader(str(path), encoding="utf-8").load()
            file_type = "txt"
        else:
            return []
    except Exception as exc:
        logger.warning("failed to load %s: %s", path, exc)
        return []

    cleaned: list[Document] = []
    category = _category_from_path(path, data_root)
    for doc in docs:
        text = _clean_text(doc.page_content)
        if len(text) < 50:
            continue
        metadata = {
            "source": path.name,
            "category": category,
            "file_type": file_type,
            "path": str(path.relative_to(data_root.parent)) if data_root.parent in path.parents else str(path),
        }
        if isinstance(doc.metadata, dict):
            page = doc.metadata.get("page")
            if page is not None:
                metadata["page"] = page
        cleaned.append(Document(page_content=text, metadata=metadata))
    return cleaned


def load_documents(data_dir: str | os.PathLike) -> list[Document]:
    """Walk data_dir recursively and return cleaned LangChain Documents."""
    root = Path(data_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"data directory not found: {root}")

    files: list[Path] = sorted(p for p in root.rglob("*") if p.suffix.lower() in {".pdf", ".txt"})
    if not files:
        raise RuntimeError(f"no .pdf or .txt files under {root}")

    # Avoid double-indexing when both filename.txt and filename.pdf exist:
    # prefer TXT (source of truth), drop matching PDF.
    by_stem: dict[Path, dict[str, Path]] = {}
    for p in files:
        by_stem.setdefault(p.parent / p.stem, {})[p.suffix.lower()] = p

    selected: list[Path] = []
    for _stem, variants in by_stem.items():
        if ".txt" in variants:
            selected.append(variants[".txt"])
        else:
            selected.append(variants[".pdf"])

    docs: list[Document] = []
    for path in selected:
        loaded = _load_single_file(path, root)
        if loaded:
            docs.extend(loaded)
            logger.info("loaded %s -> %d docs", path.name, len(loaded))
    if not docs:
        raise RuntimeError(f"no usable documents under {root} (all were < 50 chars after cleaning)")
    return docs


def chunk_documents(docs: Iterable[Document], chunk_size: int = 600, overlap: int = 120) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", "? ", "! ", " ", ""],
    )
    return splitter.split_documents(list(docs))


def ingest(data_dir: str | os.PathLike, chunk_size: int = 600, overlap: int = 120) -> list[Document]:
    """Convenience: load + chunk in one call."""
    docs = load_documents(data_dir)
    chunks = chunk_documents(docs, chunk_size=chunk_size, overlap=overlap)
    return chunks


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    chunks = ingest("data")
    print(f"\nTotal chunks produced: {len(chunks)}")
    print("Sample chunk:\n---")
    print(chunks[0].page_content[:400])
    print("\nMetadata:", chunks[0].metadata)
