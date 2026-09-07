# BrandMuse AI

Generates marketing copy for film and TV titles in each title's own voice.

Built for the Agentic Cinema hackathon — Parallel track.

---

## The problem

- A studio markets many titles at once. Each has its own voice.
- A prestige drama does not sound like a teen comedy.
- Today that voice lives in style guides and in people's heads.
- Writers redraft AI output by hand before it can ship.
- The voice drifts between titles and between writers.

## What this does

- You upload a title's approved past material: press releases, social captions, trailer copy, talent bios.
- The system reads it and builds a profile of that title's voice. This is called the Brand Brain.
- You ask for a new piece of copy.
- Four agents draft it, score it against the Brand Brain, and revise it until it passes.
- You get a draft that already sounds like the title.

One Brand Brain per title. Not one house style for the whole studio.

---

## The four agents

Content passes through each in order.

**1. Researcher**
- Pulls relevant passages from the title's own uploaded material using vector search.
- If web search is on, also calls the Parallel Search API for current context: reception, comparable titles, what is being said now.

**2. Writer**
- Writes the draft.
- Gets the research, the Brand Brain, and past reviewer feedback.
- On a revision pass, also gets the Enforcer's specific notes.

**3. Enforcer**
- Scores the draft on four things: style, tone, structure, signature phrases.
- Runs deterministic checks first. If the title never uses exclamation marks and the draft has one, it fails without calling the model.
- Runs a hallucination check against a list of claims the title is allowed to make.
- Below the score threshold, it sends the draft back to the Writer with notes.
- Content with invented claims is never approved, even at the last iteration.

**4. Deployer**
- Saves the approved content.
- Stores it for future pattern analysis.
- Sends a webhook if one is configured.
- High-scoring content feeds back into the Brand Brain.

The Writer and Enforcer loop up to 3 times.

---

## Content types

- Press release
- Premiere / social campaign
- Trailer / campaign copy
- Talent bio
- Show / title synopsis
- Behind-the-scenes post
- Pitch / greenlight deck

Each has its own chunking strategy.

---

## Hackathon requirements

**Google Cloud.** Every model call runs on Vertex AI — generation via
`ChatVertexAI` and retrieval embeddings via `VertexAIEmbeddings`, both against
a Google Cloud project with Application Default Credentials or a service
account. See `model.py` and `embedding_stategy.py`. No non-Google model is used
anywhere in the pipeline.

`LLM_PROVIDER` selects the backend: `vertex_ai` (default for deployment) or
`ai_studio`, which runs the same Gemini models through an API key instead. Both
paths are supported; the tradeoff between them is set out under Caching below.

The LangGraph pipeline can additionally be deployed to Agent Platform Runtime —
see `deploy/` and the section below — though the graph runs in-process without
it.

**Parallel.** The Researcher calls `client.search(...)` from the official
`parallel-web` SDK at runtime. See `search.py`. Web search is additive: RAG over
the brand's own documents always runs, and Parallel supplies external context on
top when `use_search` is on.

---

## Stack

| Layer | What | Why |
|---|---|---|
| API | FastAPI | Async, dependency injection |
| Agents | LangGraph | Stateful graph with conditional routing |
| Model | Gemini 2.5 Flash | All four agents, different temperature each |
| Web search | Parallel Search API | Current context for the Researcher |
| Vector search | LlamaIndex + pgvector | Retrieval over uploaded documents |
| Embeddings | Gemini `text-embedding-004` | Google model, 768 dims, same API key as generation |
| Queue | Celery on Redis | Generation takes 30s+, so it runs async. Redis brokers the queues as well as holding the cache, result backend and stream — one service instead of two |
| Database | PostgreSQL 16 + pgvector | Storage and similarity search |
| Cache | Redis | Brand Brain cache, live generation streaming |
| Auth | JWT + Argon2 | Token auth, hashed passwords |
| Frontend | React + TypeScript + Vite | Single-page app |
| Deploy | Docker Compose | All services |

---

## Architecture

```
      FastAPI  ->  Redis (broker)  ->  workers (generation x2, feedback, rag)
         |                              |
         |                    Researcher -> Writer -> Enforcer -> Deployer
         |                              |
         +------ Redis (cache, stream) -+
                 PostgreSQL + pgvector
```

---

## Running it

You need Docker, a Google API key, and a Parallel API key.

```bash
cp .env.example .env     # then fill in the keys
docker compose up -d
```

| Service | Port |
|---|---|
| API | 8000 |
| PostgreSQL | 5433 |
| Redis | 6379 |
| Flower | 5555 |

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` to the public API URL before building for production. It is baked in at build time.

---

## API

All content endpoints need a bearer token.

| Method | Path | Auth | What |
|---|---|---|---|
| GET | `/health` | no | Health check |
| POST | `/users/create` | no | Register |
| POST | `/users/login` | no | Get a token |
| GET | `/users/me` | yes | Current user |
| POST | `/conversation/generate/stream` | yes | Generate, streamed |
| POST | `/conversation/feedback` | yes | Submit review |
| GET | `/conversation/patterns/{business_id}/{content_type}` | yes | Learned patterns |
| POST | `/documents/top-performing` | yes | Upload material |
| DELETE | `/documents/{document_id}` | yes | Delete a document |

A user can only reach their own `business_id`.

---

## Design decisions

**Four agents, not one prompt.** Each can be tuned and tested on its own. The Enforcer can reject without re-running research. Costs more latency. Buys consistency.

**A synthesized Brand Brain, not raw chunks.** Raw chunks contradict each other. The Brain is one distilled profile, cached and reused. Costs an upfront synthesis step. If the synthesis is wrong, everything downstream inherits it, so new documents and high-scoring output keep updating it.

**Two retrieval paths.** Vector search gives facts. The Brand Brain gives voice. Keeping them apart stops facts from diluting style. Costs a second system to maintain.

**Async generation.** Generation takes 30s+. Holding an HTTP connection that long does not work. The API returns an ID and streams over Redis. Costs the client polling logic.

**Separate queue per worker type.** Generation cannot starve feedback or RAG refresh. Each scales on its own. Costs memory, since each pool loads the app.

**Google embeddings.** Retrieval runs on Gemini `text-embedding-004` (768 dims), using the same API key as generation, so every model in the pipeline is Google's. A local third-party encoder would be cheaper per call, but it puts a non-Google model in the middle of the retrieval path. `EmbeddingPort` keeps that swappable: `FastEmbedEmbedding` remains available as an offline fallback, and the vector table name carries the embedding dimension so the two never collide.

**Human review is optional.** Content auto-approves above the threshold. A human can reject with notes, which triggers a regeneration and is stored for later. If nobody reviews, the system still runs on its own synthesis.

---

## Caching, and what each layer actually saves

Three different caches sit in this system. They are often conflated, so it is
worth being precise about which cost each one removes — and which one we give
up by running on Vertex AI.

| Layer | Where | Removes | Status |
|---|---|---|---|
| Brand Brain cache | Redis (+ Postgres) | Re-synthesizing a brand's voice profile on every request | **Active** |
| LLM response cache | Redis | The entire API call, for a byte-identical prompt | **Active** |
| Context (prompt) cache | Google, server-side | Input-token billing on a repeated prompt *prefix* | **Not active on Vertex** |

**Brand Brain cache — the one that matters.** The voice profile is synthesized
once per `(business_id, content_type)` and reused. Measured on this corpus:
**5,561 tokens per generation avoided, about 23% of total cost.** It is an
architectural property rather than an optimization: voice is a property of the
brand, not of the request, so it should be computed per brand and not per
request. Invalidation is soft — a superseded profile keeps serving while its
replacement is rebuilt, so an upload never makes the next generation pay a
synchronous re-synthesis. Ten uploads at once cost one rebuild, not ten,
because synthesis is debounced.

**LLM response cache — real, but narrow.** Redis memoizes the full response
for an identical prompt. That helps repeated demo runs and any unchanged
prompt, and it does nothing at all once a single character differs, which is
every genuinely new generation. Note when reading benchmarks: a re-run of the
same script serves from this cache, which makes elapsed times collapse (a
90-second generation reporting 6 seconds) while still reporting token counts.
Vary the topic to measure honestly.

**Context caching — the tradeoff we accepted.** Gemini can cache a prompt
*prefix* server-side and bill those input tokens at a discount. The prompts
here are deliberately ordered for it: universal static instructions first, then
the brand-stable block, then per-call material last, which is why the Enforcer
prompt has ~14,000 characters of cacheable prefix ahead of its first variable
slot.

It works on the AI Studio backend. Measured, three calls sharing a
4,030-token prefix:

```
call 1   prompt=4030  cached=0
call 2   prompt=4030  cached=3055     <- 76% served from cache
call 3   prompt=4030  cached=3055
```

The same prefix on Vertex AI reports `cached_content_token_count: 0` on every
call, well above the minimum token threshold. Implicit caching does not apply
there.

So there is a real choice, and we made it deliberately:

|  | Context caching | Runs on Google Cloud | Billing |
|---|---|---|---|
| AI Studio (`LLM_PROVIDER=ai_studio`) | Yes, ~76% of prefix | API key, not a Cloud service | Separate AI Studio wallet |
| Vertex AI (`LLM_PROVIDER=vertex_ai`) | Not observed | Yes, a Cloud service | Cloud billing |

**We run on Vertex and forgo the caching discount.** Running the models as a
Google Cloud service is worth more than the token saving, and the Brand Brain
cache — the larger and more defensible saving — is unaffected either way. The
choice is one environment variable, so the other path stays available.

**Redis cannot substitute for context caching.** It is tempting to think a
local cache could cover the gap. It cannot: context caching is server-side,
where Google stores the prefix's attention state and discounts those tokens.
Redis sits on our side of the wire, so a prompt that differs by one character
is still transmitted and billed in full. The two caches solve different
problems, and we already run the one Redis can do.

**If the discount is wanted on Vertex**, the route is *explicit* caching rather
than implicit: `vertexai.caching.CachedContent` plus
`ChatVertexAI(cached_content=...)`, creating one cached resource per
`(brand, content_type)` over the stable prefix. Both are available in the
pinned SDK. It is not implemented here because it adds a resource lifecycle to
manage — creation, TTL, storage billed per token-hour, and invalidation
whenever the Brand Brain changes — and it only pays off above a reuse rate we
have not yet measured.

---

## Deploying the graph to Agent Platform Runtime

Optional. Without it the graph runs in-process and needs no GCP credentials.

```bash
pip install "google-cloud-aiplatform[agent_engines,langgraph]"
gcloud auth application-default login

export PROJECT_ID=your-project
export LOCATION=us-central1
export STAGING_BUCKET=gs://your-bucket
# plus GOOGLE_API_KEY, PARALLEL_API_KEY, POSTGRES_URI, REDIS_URL

python deploy/deploy_agent.py    # prints a resource name
python deploy/smoke_test.py      # confirms the deployed agent round-trips
```

Set the printed resource name as `AGENT_ENGINE_RESOURCE_NAME`. `celery_task._run_pipeline()` then routes generation there instead of running in-process.

`rag`, `analyzer`, and `memory` resolve per call from `business_id` on the graph state — see `graph/deps.py`. That is why one compiled graph can serve every request, which is what Agent Platform expects.

---

## Testing

```bash
pytest tests/            # unit and eval tests
python evalution.py      # DeepEval suite
```

The eval suite measures voice consistency, retrieval quality, and faithfulness to retrieved context.

`chaos_engineering/` has four resilience experiments: worker crash, Redis restart, data integrity under failure, and latency injection. Each needs the stack running.

`scripts/` holds manual checks that need live services. They are not part of the test suite.

---

## Layout

```
main.py              FastAPI app
database.py          SQLAlchemy models
model.py             Gemini client and per-task routing
brand_rag.py         Vector search over uploaded documents
brand_metrics.py     Brand Brain extraction and synthesis
celery_task.py       Task definitions and queues
learning_memory.py   Feedback storage and pattern analysis
human_loop.py        Regeneration after rejection
search.py            Parallel Search API
auth.py              JWT and access checks
graph/               LangGraph wiring, state, per-call deps
nodes/               researcher, writer, enforcer, deployer
prompts/             Prompt templates
routers/             API endpoints
deploy/              Agent Platform Runtime deploy and smoke test
frontend/            React app
tests/               Test suite
scripts/             Manual dev checks
chaos_engineering/   Resilience experiments
```

---

## License

MIT. See [LICENSE](./LICENSE).
