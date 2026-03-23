"""
OpenPill Proxy – OpenAI-compatible proxy with automatic context injection.

Sits between any chat client and the LLM provider. For every chat completion
request, it searches MongoDB for relevant memory entries, injects them into
the system prompt, forwards the enriched request to the upstream LLM, and
optionally extracts new knowledge from the response.

This is the "invisible infrastructure" layer that makes your knowledge base
available in ANY chat tool that supports a custom API base URL (Open WebUI,
TypingMind, CLI tools, scripts, etc.) -- without the AI or the user needing
to do anything special.

Usage:
    python proxy.py                           # port 4000
    PROXY_PORT=5000 python proxy.py           # custom port
    PROXY_AUTO_EXTRACT=true python proxy.py   # auto-extract from responses

Client config:
    API Base URL: http://localhost:4000/v1
    API Key:      (your real provider key -- proxy forwards it)

Environment:
    PROXY_PORT            Listen port (default: 4000)
    PROXY_MAX_PILLS       Max pills to inject per request (default: 5)
    PROXY_MIN_SIMILARITY  Min similarity to include a pill (default: 0.70)
    PROXY_AUTO_EXTRACT    Auto-extract pills from responses (default: false)
    PROXY_EXPAND_NEIGHBORS  Append 1-hop related pills after semantic hits (default: false)
    PROXY_NEIGHBOR_LIMIT    Max extra neighbor pills to add (default: 5)
    EMBEDDING_MODEL       Embedding model for semantic search
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from litellm import acompletion

from db import close, get_collection
from embeddings import cosine_similarity, get_embedding
from pill_relations import expand_semantic_neighbors

PROXY_PORT = int(os.getenv("PROXY_PORT", "4000"))
MAX_PILLS = int(os.getenv("PROXY_MAX_PILLS", "5"))
MIN_SIMILARITY = float(os.getenv("PROXY_MIN_SIMILARITY", "0.70"))
AUTO_EXTRACT = os.getenv("PROXY_AUTO_EXTRACT", "false").lower() in ("true", "1", "yes")
EXPAND_NEIGHBORS = os.getenv("PROXY_EXPAND_NEIGHBORS", "false").lower() in ("true", "1", "yes")
NEIGHBOR_LIMIT = int(os.getenv("PROXY_NEIGHBOR_LIMIT", "5"))

PILL_INJECTION_HEADER = (
    "[KNOWLEDGE CONTEXT — auto-injected from your personal knowledge base]\n"
    "The following facts may be relevant to this conversation:\n\n"
)
PILL_INJECTION_FOOTER = "\n[END KNOWLEDGE CONTEXT]\n"


# ---------------------------------------------------------------------------
# Pill retrieval
# ---------------------------------------------------------------------------


async def find_relevant_pills(query: str, max_pills: int, min_sim: float) -> list[dict]:
    """Semantic search for pills relevant to the user's message."""
    col = await get_collection()

    try:
        query_embedding = await get_embedding(query)
    except (OSError, ValueError):
        return []

    filter_doc = {"status": "active", "embedding": {"$exists": True, "$ne": None}}
    candidates = []

    async for doc in col.find(filter_doc, {"embedding": 1, "title": 1, "content": 1, "category": 1, "tags": 1}):
        score = cosine_similarity(query_embedding, doc["embedding"])
        if score >= min_sim:
            candidates.append({
                "_id": str(doc["_id"]),
                "title": doc["title"],
                "content": doc["content"],
                "category": doc.get("category", ""),
                "tags": doc.get("tags", []),
                "similarity": score,
            })

    candidates.sort(key=lambda d: d["similarity"], reverse=True)
    seeds = candidates[:max_pills]
    if EXPAND_NEIGHBORS and NEIGHBOR_LIMIT > 0 and seeds:
        return await expand_semantic_neighbors(col, seeds, NEIGHBOR_LIMIT)
    return seeds


def build_pill_message(pills: list[dict]) -> str:
    """Format pills into a system message string."""
    lines = [PILL_INJECTION_HEADER]
    for i, pill in enumerate(pills, 1):
        tags = ", ".join(pill["tags"]) if pill["tags"] else ""
        lines.append(f"{i}. **{pill['title']}** [{pill['category']}]")
        lines.append(f"   {pill['content']}")
        if tags:
            lines.append(f"   Tags: {tags}")
        lines.append("")
    lines.append(PILL_INJECTION_FOOTER)
    return "\n".join(lines)


def extract_user_query(messages: list[dict]) -> str:
    """Extract the latest user message for semantic search."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "") for part in content if part.get("type") == "text"
                )
    return ""


def inject_pills_into_messages(messages: list[dict], pill_message: str) -> list[dict]:
    """Prepend a knowledge-context system message to the conversation."""
    injection = {"role": "system", "content": pill_message}

    if messages and messages[0].get("role") == "system":
        enriched = [messages[0], injection] + messages[1:]
    else:
        enriched = [injection] + messages

    return enriched


# ---------------------------------------------------------------------------
# Auto-extraction (optional)
# ---------------------------------------------------------------------------


async def maybe_extract(response_text: str) -> None:
    """If auto-extract is enabled, run the extractor on the response."""
    if not AUTO_EXTRACT or not response_text or len(response_text) < 100:
        return

    try:
        from extractor import run_extraction
        await run_extraction(
            text=response_text,
            source_reference="proxy:auto-extract",
            dry_run=False,
            min_confidence=0.7,
            max_pills=10,
        )
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    print("\n  OpenPill Proxy")
    print(f"  port: {PROXY_PORT}  |  max pills: {MAX_PILLS}  |  min similarity: {MIN_SIMILARITY}")
    print(f"  auto-extract: {AUTO_EXTRACT}")
    print("  Forwarding to upstream LLM via LiteLLM\n")
    yield
    await close()


app = FastAPI(
    title="OpenPill Proxy",
    description="OpenAI-compatible proxy with automatic knowledge pill injection.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions endpoint with pill injection."""
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(
            content={"error": {"message": f"Invalid JSON body: {e}", "type": "invalid_request_error"}},
            status_code=400,
        )
    messages = body.get("messages", [])
    model = body.get("model", "gpt-4o-mini")
    stream = body.get("stream", False)

    user_query = extract_user_query(messages)

    pills = []
    if user_query:
        try:
            pills = await find_relevant_pills(user_query, MAX_PILLS, MIN_SIMILARITY)
        except Exception as e:
            return JSONResponse(
                content={"error": {"message": f"Pill lookup failed: {e}", "type": "internal_error"}},
                status_code=500,
            )

    if pills:
        pill_message = build_pill_message(pills)
        messages = inject_pills_into_messages(messages, pill_message)
        body["messages"] = messages

    try:
        if stream:
            return await _handle_streaming(body, model)
        return await _handle_non_streaming(body, model)
    except Exception as e:
        return JSONResponse(
            content={"error": {"message": str(e), "type": "internal_error"}},
            status_code=500,
        )


async def _handle_non_streaming(body: dict, model: str) -> dict:
    """Forward a non-streaming request and return the response."""
    body.pop("stream", None)
    response = await acompletion(model=model, **{k: v for k, v in body.items() if k != "model"})

    response_dict = response.model_dump()

    assistant_text = ""
    choices = response_dict.get("choices", [])
    if choices:
        assistant_text = choices[0].get("message", {}).get("content", "")

    await maybe_extract(assistant_text)

    return response_dict


async def _handle_streaming(body: dict, model: str) -> StreamingResponse:
    """Forward a streaming request and yield SSE chunks."""
    body["stream"] = True
    collected_text = []

    async def generate():
        response = await acompletion(model=model, **{k: v for k, v in body.items() if k != "model"})
        async for chunk in response:
            chunk_dict = chunk.model_dump()
            delta = chunk_dict.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                collected_text.append(content)
            yield f"data: {json.dumps(chunk_dict)}\n\n"
        yield "data: [DONE]\n\n"

        full_text = "".join(collected_text)
        await maybe_extract(full_text)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/v1/models")
async def list_models():
    """Minimal /v1/models endpoint for client compatibility."""
    return {
        "object": "list",
        "data": [
            {"id": "ollama/llama3", "object": "model", "owned_by": "proxy"},
            {"id": "gpt-4o-mini", "object": "model", "owned_by": "proxy"},
            {"id": "gpt-4o", "object": "model", "owned_by": "proxy"},
            {"id": "claude-3-5-sonnet-20241022", "object": "model", "owned_by": "proxy"},
        ],
    }


# Minimal OpenAPI 3.0 spec so clients (e.g. Open WebUI) can discover the API.
OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "OpenPill Proxy", "version": "1.0.0"},
    "paths": {
        "/v1/chat/completions": {
            "post": {
                "summary": "Chat completions (with pill injection)",
                "operationId": "createChatCompletion",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "model": {"type": "string"},
                                    "messages": {"type": "array", "items": {"type": "object"}},
                                    "stream": {"type": "boolean"},
                                },
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "Completion response"}},
            }
        },
        "/v1/models": {
            "get": {
                "summary": "List models",
                "operationId": "listModels",
                "responses": {"200": {"description": "List of models"}},
            }
        },
    },
}


@app.get("/v1/openapi.json")
async def openapi_spec():
    """OpenAPI spec for connection validation and discovery (e.g. Open WebUI)."""
    return OPENAPI_SPEC


# ---------------------------------------------------------------------------
# Browser test UI
# ---------------------------------------------------------------------------

_PROXY_CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>OpenPill Proxy – Test</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; }
    h1 { font-size: 1.25rem; }
    label { display: block; margin-top: 1rem; font-weight: 500; }
    textarea { width: 100%; min-height: 80px; padding: 0.5rem; font: inherit; }
    select { padding: 0.5rem; font: inherit; }
    button { margin-top: 1rem; padding: 0.5rem 1rem; cursor: pointer; }
    #reply { white-space: pre-wrap; margin-top: 1rem; padding: 1rem; background: #f0f0f0; border-radius: 6px; min-height: 60px; }
    .error { color: #c00; }
    .meta { font-size: 0.875rem; color: #666; margin-top: 0.5rem; }
  </style>
</head>
<body>
  <h1>OpenPill Proxy – Browser Test</h1>
  <p>Your message is sent to the proxy; it injects relevant pills and forwards to the LLM.</p>
  <label for="model">Model</label>
  <select id="model">
    <option value="ollama/llama3">ollama/llama3</option>
    <option value="gpt-4o-mini">gpt-4o-mini</option>
  </select>
  <label for="msg">Message</label>
  <textarea id="msg" placeholder="e.g. How does Python handle concurrency?">How does Python handle concurrency?</textarea>
  <button id="send">Send</button>
  <div id="reply"></div>
  <script>
    const model = document.getElementById('model');
    const msg = document.getElementById('msg');
    const send = document.getElementById('send');
    const reply = document.getElementById('reply');
    send.addEventListener('click', async () => {
      reply.textContent = 'Waiting for response…';
      reply.classList.remove('error');
      send.disabled = true;
      try {
        const res = await fetch('/v1/chat/completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: model.value,
            messages: [{ role: 'user', content: msg.value.trim() }]
          })
        });
        const text = await res.text();
        let data;
        try { data = text ? JSON.parse(text) : {}; } catch (_) {
          reply.innerHTML = '<span class="error">' + (res.ok ? 'Invalid response' : 'Error ' + res.status + ': ' + (text || res.statusText)) + '</span>';
          send.disabled = false;
          return;
        }
        if (!res.ok) {
          reply.innerHTML = '<span class="error">' + (data.error?.message || data.detail || text || res.statusText) + '</span>';
          return;
        }
        if (data.choices && data.choices[0] && data.choices[0].message)
          reply.textContent = data.choices[0].message.content;
        else
          reply.innerHTML = '<span class="error">' + (data.error?.message || JSON.stringify(data)) + '</span>';
      } catch (e) {
        reply.innerHTML = '<span class="error">' + e.message + '</span>';
      }
      send.disabled = false;
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def proxy_chat_ui():
    """Simple browser UI to test the proxy: send a message and see the LLM reply (with pills injected)."""
    return _PROXY_CHAT_HTML


@app.get("/health")
async def health():
    return {"status": "ok", "proxy": True, "auto_extract": AUTO_EXTRACT}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT)
