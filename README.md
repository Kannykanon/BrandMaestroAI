# BrandMaestro AI

Writes blog posts, ad copy, proposals, scripts and press releases in your brand's own voice — on whichever LLM you choose.

---

## The problem

- A brand's voice lives in style guides and in people's heads.
- Generic AI drafts sound like generic AI, so writers redraft them by hand.
- The voice drifts between writers, channels and content types.

## What this does

- You upload approved past writing: blog posts, ads, proposals, scripts, press releases.
- The system reads it and builds a profile of how the brand writes. This is called the Brand Brain.
- You can also upload product documents (briefs, fact sheets) for the facts to write from.
- You ask for a new piece.
- Four agents research it, draft it, score it against the Brand Brain, and revise it until it passes.
- You get a draft that already sounds like the brand.

One Brand Brain per business and content type, so a brand's ads can sound different from its proposals.

---

## Content types

| Key | What it writes | Chunking |
|---|---|---|
| `blog` | Long-form blog articles | Paragraph-aware, large chunks |
| `ad` | Ad copy: headline, body, call to action | Sentence-level, short chunks |
| `proposal` | Business proposals | Section-aware, large overlapping chunks |
| `script` | Video, audio and presentation scripts | Scene- and line-aware |
| `press_release` | Press releases | Section-aware |

The list lives in `schema.CONTENT_TYPES`; chunking in `chunking_stategy.CHUNKING_REGISTRY`.

---

## The four agents

**1. Researcher** — pulls relevant passages from the brand's uploaded product documents (vector search), strips internal planning notes out of them, and, when web research is on, adds current context from the Parallel Search API.

**2. Writer** — plans, drafts and edits the piece against the Brand Brain, the research, and past reviewer feedback. On a revision pass it also gets the Enforcer's notes.

**3. Enforcer** — runs deterministic checks first (punctuation the brand never uses, measured writing rates, unfilled placeholders, invented quotes and contact details, copied source passages), then scores style, tone, structure and signature phrases with the model. Below threshold, it sends the draft back with notes. Content with invented claims is never approved.

**4. Deployer** — saves the approved content, sends a webhook if configured, and feeds high-scoring output back into memory.

The Writer and Enforcer loop up to `MAX_REVISION_ITERATIONS` times.

---

## Model providers

`model.py` uses a plug-and-adapter design. `LLMProvider` is an abstract adapter; each provider is one subclass that builds its LangChain chat model. `LLMSingleton` is the switch: it reads `LLM_PROVIDER`, plugs in the matching adapter, and hands out one chat model per task mode. Nodes only ever call `LLMSingleton.get(mode).invoke(prompt)`.

| `LLM_PROVIDER` | Adapter | Chat model | Credential | Default model |
|---|---|---|---|---|
| `vertex_ai` (default) | `VertexAIProvider` | `ChatVertexAI` | `PROJECT_ID` + ADC | `gemini-2.5-flash` |
| `groq` | `GroqProvider` | `ChatGroq` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| `claude` | `ClaudeProvider` | `ChatAnthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5` |

```bash
LLM_PROVIDER=claude
LLM_MODEL=claude-sonnet-5      # optional; the adapter's default otherwise
```

Each task mode (`extraction`, `enforcement`, `synthesis`, `generation`) gets its own temperature, output cap and timeout from `LLMSingleton`; `LLM_TEMPERATURE_<MODE>` and `LLM_MAX_TOKENS_<MODE>` override them. Claude models that reject `temperature` (Opus 4.7+, Sonnet 5) are detected by `ClaudeProvider` and it is not sent. The API refuses to start if the selected adapter's credential is missing.

**Adding a provider:** subclass `LLMProvider`, set `name`, `default_model` and `required_env`, implement `to_langchain()`, and register the class in `LLMSingleton.PROVIDERS`.

```python
class MyProvider(LLMProvider):
    name = "my_provider"
    default_model = "my-model"
    required_env = ("MY_API_KEY",)

    def to_langchain(self, callbacks=None):
        from langchain_myprovider import ChatMyProvider
        return ChatMyProvider(model=self.model, temperature=self.temperature,
                              max_tokens=self.max_tokens, callbacks=callbacks or [])
```

### Embeddings

Retrieval over product documents uses the same design in `embedding_stategy.py`: an abstract `EmbeddingProvider`, three adapters, and `EmbeddingSingleton` as the switch. Groq and Anthropic have no embedding models of their own, so each LLM provider is paired with a backend:

| `LLM_PROVIDER` | Embeddings adapter | Model | Dimensions | Credential |
|---|---|---|---|---|
| `vertex_ai` | `VertexEmbedding` | `text-embedding-004` | 768 | `PROJECT_ID` + ADC |
| `claude` | `VoyageEmbedding` (Anthropic's recommended partner) | `voyage-4` | 1024 | `VOYAGE_API_KEY` |
| `groq` | `LocalEmbedding` (FastEmbed, in-process) | `BAAI/bge-small-en-v1.5` | 384 | none |

`EMBEDDING_PROVIDER=vertex_ai|voyage|local` overrides the pairing, e.g. Claude for writing with Vertex for retrieval. `EMBEDDING_MODEL` picks another model the adapter knows; `EMBEDDING_DIMENSIONS` sets Voyage's vector size (256, 512 or 1024). The local model downloads on first use into a Docker volume, so it survives redeploys.

The vector table name includes the dimension, so switching backend starts a fresh index — re-upload product documents afterwards.

---

## Stack

| Layer | What |
|---|---|
| API | FastAPI, serving the UI from `static/` |
| Agents | LangGraph |
| Models | Vertex AI, Groq or Claude via provider adapters |
| Web search | Parallel Search API |
| Vector search | LlamaIndex + pgvector |
| Queue | Celery on Redis |
| Database | PostgreSQL 16 + pgvector |
| Cache | Redis: Brand Brain cache, LLM response cache, generation stream |
| Auth | JWT + Argon2 |
| Tracing | Opik (optional) |

```
      FastAPI  ->  Redis (broker)  ->  workers (generation x2, feedback, retraining, rag)
         |                              |
         |                    Researcher -> Writer -> Enforcer -> Deployer
         |                              |
         +------ Redis (cache, stream) -+
                 PostgreSQL + pgvector
```

---

## Running it

You need Docker, credentials for your chosen model provider (Vertex AI, Groq or Claude), and a Parallel API key.

```bash
cp .env.example .env     # set LLM_PROVIDER, its key, EMBEDDING_PROVIDER, PARALLEL_API_KEY, SECRET_KEY
docker compose up -d --build
```

Open http://localhost:8000.

| Service | Port |
|---|---|
| API + UI | 8000 |
| PostgreSQL | 5433 |
| Redis | 6379 |
| Flower | 5555 |

Pushes to `main` deploy to the production VM through `.github/workflows/deploy.yml`. `setup-vm.sh` prepares a fresh VM.

---

## API

All content endpoints need a bearer token. A user can only reach their own `business_id`.

| Method | Path | What |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/users/create` | Register |
| POST | `/users/login` | Get a token |
| GET | `/users/me` | Current user |
| POST | `/conversation/generate/stream` | Generate, streamed |
| POST | `/conversation/feedback` | Submit review |
| GET | `/conversation/patterns/{business_id}/{content_type}` | Learned patterns |
| POST | `/documents/brand-voice` | Upload past published writing (builds the Brand Brain) |
| POST | `/documents/product` | Upload a product document (indexed for retrieval) |
| GET | `/documents` | List documents |
| GET | `/documents/brand-brain/{content_type}` | Read the Brand Brain |
| DELETE | `/documents/brand-brain/{content_type}` | Reset the Brand Brain |
| DELETE | `/documents/{document_id}` | Delete a document |

Example generation request:

```json
{
  "business_id": "…",
  "content_type": "proposal",
  "topic": "Managed analytics rollout for a 40-store retail chain",
  "format_type": "business proposal",
  "research_mode": "both"
}
```

`research_mode` is `both`, `rag` (product documents only) or `web` (Parallel only).

---

## Design decisions

**Four agents, not one prompt.** Each can be tuned and tested on its own, and the Enforcer can reject without re-running research. Costs latency; buys consistency.

**A synthesized Brand Brain, not raw chunks.** Raw examples contradict each other. The Brain is one distilled profile per business and content type, cached in Redis and Postgres. Invalidation is soft — a superseded profile keeps serving while its replacement is rebuilt — and synthesis is debounced, so ten uploads cost one rebuild.

**Two intake paths.** Past writing teaches voice and goes to the Brand Brain. Product documents supply facts and go to the vector index. Keeping them apart stops facts from diluting style.

**Deterministic gates before model scoring.** A scoring model can approve an empty-brain draft on one sample and reject it on the next. Rules that must hold — no invented quotes, no unfilled placeholders, no punctuation the brand never uses — are enforced in code.

**Providers as adapters.** Nodes never import a provider SDK; each provider is one `LLMProvider` subclass and `LLMSingleton` is the only switch. Responses are normalized to plain text in one place, so models that return content blocks (e.g. Claude with thinking) need no special handling downstream.

**Async generation.** Generation takes 30s+. The API returns an ID and streams progress over Redis. Separate queues per worker type stop generation starving feedback or RAG refresh.

**Caching.** The Brand Brain cache removes re-synthesis on every request — the largest saving. The Redis LLM response cache only helps byte-identical prompts. Prompts are ordered static-first, so providers with automatic prompt caching can discount the repeated prefix.

---

## Testing

```bash
uv sync --group dev
uv run pytest tests/
```

The suite needs no live services or API keys.

---

## Layout

```
main.py              FastAPI app and startup checks
model.py             LLM provider adapters and LLMSingleton switch
embedding_stategy.py Embedding adapters and EmbeddingSingleton switch
schema.py            Request models and content types
database.py          SQLAlchemy models
brand_rag.py         Vector search over product documents
brand_metrics.py     Brand Brain extraction and synthesis
chunking_stategy.py  Chunking per content type
celery_task.py       Task definitions and queues
learning_memory.py   Reviewer feedback storage and pattern analysis
human_loop.py        Regeneration after rejection
search.py            Parallel Search API
observability.py     Opik tracing
auth.py              JWT and access checks
graph/               LangGraph wiring, state, per-call deps
nodes/               researcher, writer, enforcer, deployer
prompts/             Prompt templates
routers/             API endpoints
utils/               Brand profile parsing and enforcement checks
static/              Web UI
tests/               Test suite
```

---

## License

MIT. See [LICENSE](./LICENSE).
