"""Build the FAISS index from data/. Run: python -m backend.build_index"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .rag_pipeline import DATA_DIR, INDEX_DIR, build_vectorstore, count_documents


def main() -> int:
    parser = argparse.ArgumentParser(description="Build/refresh the FAISS vector store.")
    parser.add_argument("--data-dir", default=DATA_DIR, help="Path to documents directory")
    parser.add_argument("--persist-dir", default=INDEX_DIR, help="Path to vector index directory")
    parser.add_argument("--clean", action="store_true", help="Delete existing index first")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("build_index")

    persist_path = Path(args.persist_dir)
    if args.clean and persist_path.exists():
        log.info("removing existing index at %s", persist_path)
        shutil.rmtree(persist_path)

    log.info("building vector store ...")
    vs = build_vectorstore(data_dir=args.data_dir, persist_dir=args.persist_dir)
    total = count_documents(vs)
    log.info("done — %d chunks indexed at %s", total, persist_path.resolve())
    print(f"\nVector store built. {total} chunks indexed at {persist_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
