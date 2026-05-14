# BankAssist Chatbot

BankAssist Chatbot is a RAG-powered banking support assistant for Indian banking queries. It uses a FastAPI backend, a Streamlit chat interface, LangChain, FAISS vector search, Hugging Face embeddings, and Groq's Llama model to answer questions from curated banking documents.

## Features

- Chat interface for banking questions
- Retrieval-augmented answers grounded in local PDF/TXT documents
- Source snippets shown with responses
- Streaming responses from the backend to the UI
- Session-based conversation memory
- Optional Redis cache for repeated queries
- FAISS vector index built from the `data/` knowledge base
- Deployment assets for Nginx, systemd, and GitHub Actions

## Tech Stack

- Python
- FastAPI
- Streamlit
- LangChain
- FAISS
- Hugging Face Sentence Transformers
- Groq Llama 3.3
- Redis optional cache

## Project Structure

```text
BankAssist-Chatbot/
|-- backend/
|   |-- app.py              # FastAPI API server
|   |-- build_index.py      # Builds the local FAISS vector index
|   |-- ingestion.py        # Loads and chunks PDF/TXT documents
|   |-- rag_pipeline.py     # Embeddings, retrieval, and LLM chain
|   `-- requirements.txt
|-- frontend/
|   |-- streamlit_app.py    # Streamlit chat UI
|   `-- requirements.txt
|-- data/                   # Banking knowledge base documents
|-- docs/                   # Architecture notes
|-- deployment/             # Deployment configs
|-- .env.example            # Example environment variables
`-- .gitignore
```

## Prerequisites

Install these before running the project:

- Python 3.10 or newer
- Git
- A Groq API key
- Redis optional, only needed for response caching

## Setup

Clone the repository:

```bash
git clone https://github.com/chettanmahajan/BankAssist-Chatbot.git
cd BankAssist-Chatbot
```

Create and activate a virtual environment.

On Windows:

```bash
python -m venv venv
venv\Scripts\activate
```

On macOS/Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install backend and frontend dependencies:

```bash
pip install -r backend/requirements.txt
pip install -r frontend/requirements.txt
```

## Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set your Groq API key:

```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
CHROMA_DIR=./chroma_db
DATA_DIR=./data
API_URL=http://127.0.0.1:8000
```

Note: `CHROMA_DIR` is kept as an environment variable name for compatibility, but the current code stores a FAISS index in that folder.

Do not commit `.env`. It contains secrets and is already ignored by Git.

## Build the Vector Index

Build the FAISS vector store from the documents in `data/`:

```bash
python -m backend.build_index --clean
```

Run this command again whenever documents inside `data/` are changed.

## Run the Project

Start the backend API:

```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

In a second terminal, start the frontend:

```bash
streamlit run frontend/streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Open the app in your browser:

```text
http://127.0.0.1:8501
```

Backend health check:

```text
http://127.0.0.1:8000/health
```

Expected healthy response:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "vectorstore_loaded": true,
  "docs_indexed": 853
}
```

## API Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/health` | Check backend status and vector index loading |
| POST | `/chat` | Send a normal chat request |
| POST | `/chat/stream` | Send a chat request and receive streamed tokens |

Example request:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"What documents are required for a personal loan?\"}"
```

## Redis Cache

Redis is optional. If Redis is not running, the backend continues working and treats every request as a cache miss.

Default Redis settings:

```env
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_TTL_SECONDS=3600
```

## Troubleshooting

### Backend says vector store not loaded

Build the vector index:

```bash
python -m backend.build_index --clean
```

### Frontend says API unreachable

Make sure the backend is running:

```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Also confirm this value in `.env`:

```env
API_URL=http://127.0.0.1:8000
```

### Missing Groq API key

Set `GROQ_API_KEY` in `.env`.

### Redis connection warning

This is not fatal. Start Redis if you want caching, or ignore the warning during local development.

## Deployment

The `deployment/` folder contains example Nginx and systemd configuration files. The GitHub Actions workflow in `.github/workflows/deploy.yml` is designed for EC2 deployment using repository secrets:

- `EC2_HOST`
- `EC2_USER`
- `EC2_SSH_KEY`

See `docs/architecture.md` for the request flow and deployment topology.

## License

This project is licensed under the terms in the `LICENSE` file.
