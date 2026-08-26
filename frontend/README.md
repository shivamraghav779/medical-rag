# Clinical RAG Frontend

React web UI for the Multi-Agent Clinical RAG platform.

## Stack

- React 18 + TypeScript + Vite
- Tailwind CSS (Techtattava-inspired dark theme)
- Inter font (ChatGPT-style typography)

## Screens

| Route | Description |
|-------|-------------|
| `/chat` | ChatGPT-style clinical chat with SSE streaming, citations, agent pipeline, disclaimer & context modals |
| `/documents` | PDF upload (with metadata), document library, delete |
| `/analytics` | Query types, drug mentions, emergency log, faithfulness |
| `/status` | Redis/Pinecone health + model info |

## Development

```bash
# From repo root — start the API first
uvicorn api.main:app --reload --port 8000

# Frontend (proxies /api and /health to :8000)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Production build

```bash
cd frontend
npm run build
npm run preview
```

Serve `frontend/dist` behind your reverse proxy, or configure the API to serve static files.
