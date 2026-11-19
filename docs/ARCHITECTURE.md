# How OpsPilot works (and why it's built this way)

This document explains every concept in the project in plain language, the design decisions
behind it, and the questions an interviewer is likely to ask. Read it alongside the code: each
section names the file where the idea lives.

---

## 1. RAG in one paragraph

An LLM only knows what it saw in training. It has never seen *your* runbooks. **Retrieval-Augmented
Generation** fixes that at question time: search your documents for the passages most related to the
question, paste them into the prompt, and tell the model to answer *only* from them. The model
becomes a reader and summariser of your knowledge, not a source of it. Two things decide quality:
**did we retrieve the right passages**, and **did the model stay faithful to them**. Everything
in OpsPilot is about improving or measuring one of those two.

---

## 2. Ingestion: from files to searchable chunks

`src/opspilot/ingestion/`

**Loading** (`loaders.py`). Markdown, text and PDF. Markdown files carry YAML front matter
(`service`, `doc_type`, `severity`, `tags`), which becomes **metadata** stored next to each chunk
so we can filter ("only payments-service runbooks").

**Chunking** (`chunker.py`). We can't embed a whole document: one vector for 3 pages would blur
everything together, and the LLM's context window is limited. So documents are split into chunks.
The naive way, every N characters, cuts procedures in half. Runbooks are organised by headings,
so we split on headings first and keep a section whole if it fits (1200 characters). Long sections
are split on paragraph boundaries with a 150-character **overlap**, so a sentence that straddles
the cut appears in both pieces. Code blocks are respected (a `#` inside a bash block is a comment,
not a heading).

**Contextual chunk headers**. Before embedding, every chunk is prefixed with
`"<document title> > <section path>"`. A chunk that just says "Run `CONFIG SET maxmemory-policy
volatile-lru`" becomes "Redis out of memory > Mitigation > Run CONFIG SET...", which is much easier
to match to a question about Redis.

**Idempotency**. Chunk ids are `uuid5(source + index)`, so re-ingesting a file overwrites its
points instead of duplicating them. We also delete a source's old chunks first, in case the document got shorter.

---

## 3. Embeddings: dense *and* sparse

`src/opspilot/embeddings.py`

**Dense embeddings** (`BAAI/bge-small-en-v1.5`, 384 dimensions) turn text into a vector so that
similar *meanings* are close together. "Database is overloaded" lands near "connection pool
exhausted" even though they share no words. Weakness: exact identifiers. `ERR_POOL_TIMEOUT`,
`INC-1150` or `kid not found` are just noise to a semantic model.

**Sparse embeddings** (BM25) are classic keyword search turned into a vector: one dimension per
word, weighted by how rare the word is (IDF). Exact error codes match perfectly. Weakness: no
understanding of synonyms.

On-call questions are full of both ("checkout is slow, ERR_POOL_TIMEOUT in logs"), so we use both.

**Why FastEmbed** instead of sentence-transformers or Ollama embeddings? It runs ONNX models
without PyTorch, so the Docker image is hundreds of MB smaller. It behaves identically on a Mac,
in a Linux container and on Streamlit Cloud, and it's maintained by Qdrant, so its BM25 output
is exactly what Qdrant's IDF modifier expects.

---

## 4. Hybrid search with Reciprocal Rank Fusion

`src/opspilot/vectorstore.py` -> `hybrid_search()`

Each Qdrant point stores two named vectors: `dense` and `sparse`. A single `query_points` call
runs both searches (`prefetch`) and merges them with **Reciprocal Rank Fusion**:

```
score(doc) = sum over result lists of 1 / (k + rank_in_that_list)
```

RRF only uses *ranks*, not raw scores, which is why it works: cosine similarities (0 to 1) and
BM25 scores (0 to 30+) aren't comparable, but "ranked 2nd" and "ranked 5th" are. A document
ranked high by both searches wins. It all happens inside Qdrant in one round trip.

Metadata filters (`service`, `doc_type`) are applied inside both prefetches, using payload
indexes so filtering stays fast as the collection grows.

---

## 5. Reranking with a cross-encoder

`src/opspilot/retrieval.py`, `embeddings.py -> Reranker`

The embedding models are **bi-encoders**: they encode the question and each passage
*separately* and compare vectors. Fast, but shallow. A **cross-encoder** reads the question and
passage *together* in one forward pass and outputs a relevance score, which is much more
accurate but too slow to run on every document.

So: hybrid search fetches 20 candidates cheaply, the cross-encoder (`ms-marco-MiniLM-L-6-v2`,
~80 MB) re-scores those 20, and we keep the top 5. Classic **retrieve-then-rerank**. Turn it off
with `RERANK_ENABLED=false` and compare the MLflow runs to see what it's worth.

---

## 6. The agent: corrective RAG with LangGraph

`src/opspilot/agent/`

A plain RAG pipeline is a straight line: retrieve -> generate. If retrieval misses, the LLM
either hallucinates or gives a vague answer, and nobody notices. OpsPilot is a **state machine**
that checks itself:

| Node | What it does | Why |
|---|---|---|
| `guard_input` | Regex check for prompt injection | Cheap, deterministic first line of defence |
| `retrieve` | Hybrid search + rerank | |
| `grade_documents` | One LLM call: "which of these passages are actually relevant?" | Rerank scores say "similar"; the grader says "useful for *this* problem" |
| `rewrite_query` | LLM rewrites the search query using SRE vocabulary, drops filters | Users describe symptoms ("site is slow"); runbooks use component names |
| `generate` | Answer with citations, fixed format | |
| `check_grounding` | LLM fact-checks the answer against the passages, returns confidence | Catches hallucinated commands before an engineer runs them |
| `no_context` | Honest "I don't know, escalate" | Better than a confident wrong answer at 3am |

Routing (`routing.py`): after grading, if any passage is relevant go to `generate`; otherwise
rewrite and retry, up to `MAX_QUERY_REWRITES` (2); then give up. These routing functions are pure
Python, so they're unit tested without an LLM.

**Why LangGraph** and not a plain Python function? Explicit state, conditional edges and loops
are first-class. Every node's input/output is visible (we stream them to the UI as "steps"). The
same graph shows up as a nested trace in Langfuse. And token streaming from inside a node works
out of the box (`stream_mode=["updates", "messages"]`).

**Structured outputs**. The grader, rewriter and checker don't return free text: they return
Pydantic models (`DocumentGrades`, `RewrittenQuery`, `GroundingCheck`) via
`with_structured_output()`. Small models occasionally produce bad JSON, so every structured call
has a fallback: if the grader fails we fall back to the reranker's scores, and if the grounding check
fails we mark the answer unverified.

**Batching LLM calls**. The grader judges all 5 passages in *one* call instead of five. On a local
7B model each call costs seconds, so this matters more than any other optimisation.

---

## 7. Semantic cache

`src/opspilot/cache.py`

During an incident many people ask the same thing in different words. We embed the question
and look in a separate Qdrant collection for a previous question with cosine similarity
≥ 0.92 *and* the same filters *and* younger than 24 h. A hit returns the stored answer in
milliseconds and costs no LLM calls.

Only **grounded, non-escalated** answers are cached, so a bad answer is never replayed. The
threshold is a trade-off: too low and "Redis OOM on cart" might return the answer for "Redis OOM
on sessions"; too high and the cache rarely hits.

---

## 8. Guardrails

`src/opspilot/guardrails.py`

**Input**: regexes for known injection phrasings ("ignore previous instructions", "reveal your
system prompt"...). Blocked requests never reach the LLM. Rules miss clever attacks, but they're
free, instant and deterministic, and the system prompt + grounding check are a second layer.

**Output**: real runbooks and tickets contain secrets people pasted in a hurry. Before an answer
leaves the service, passwords, AWS keys, GitHub/Groq tokens, JWTs and private keys are replaced
with `[REDACTED_...]`. Ticket INC-1042 in the knowledge base contains a fake leaked password to
demonstrate this.

Known limitation: with streaming, tokens reach the browser *before* the final redaction pass.
The UI replaces the streamed text with the redacted final answer, but a determined client could
read the raw stream. A production system would also redact at ingestion time (so secrets never
enter the index) or buffer the stream until each line passes the filter. Being able to name the
limits of your own design is a good interview signal.

---

## 9. Serving: FastAPI + Server-Sent Events

`src/opspilot/api/main.py`, `src/opspilot/service.py`

`ChatService` is the single place that knows the full flow (cache -> graph -> redaction ->
metrics -> cache write). The API and the Streamlit "direct" mode both call it, so behaviour is
identical everywhere.

`/chat/stream` uses **Server-Sent Events**: a plain HTTP response that stays open and sends
`event: step`, `event: token` and finally `event: final` messages. SSE is simpler than WebSockets
for one-way server-to-client streaming and works through proxies.

All blocking work (ONNX inference, Qdrant client calls) runs in `asyncio.to_thread` so the
event loop stays free to serve other requests while one is waiting on the LLM.

`/health` (liveness: process is up) and `/ready` (readiness: Qdrant and the LLM are reachable)
are deliberately different, which is the distinction Kubernetes probes are built around.

---

## 10. Observability

`src/opspilot/metrics.py`, `monitoring/`, `src/opspilot/tracing.py`

**Metrics** (Prometheus): counters and histograms for requests, end-to-end answer time,
LLM time per agent step, retrieval time, cache hits, rewrites, escalations, guardrail blocks
and answer confidence. Prometheus scrapes `/metrics/` every 10 s; Grafana's provisioned dashboard
reads them. Metrics answer "is the system healthy and fast?"

**Traces** (Langfuse): every LLM call inside the graph, with the exact prompt, output, tokens
and latency, nested under one trace per question. Traces answer "why did *this* answer go wrong?"

You need both: metrics show *that* p95 latency doubled; traces show it's because the grader
started rejecting everything and every question now does two rewrites.

---

## 11. Evaluation and the CI gate

`evaluation/`, `.github/workflows/eval-gate.yml`

You can't improve what you don't measure, and "it looked fine when I tried three questions"
isn't a measurement. The golden dataset has 20 questions with the documents that *should* be
retrieved and a reference answer.

- **Retrieval metrics** (deterministic): hit rate, recall, MRR.
- **Generation metrics** (LLM-as-judge): faithfulness, relevancy, correctness, each 0 to 1.
- **Behaviour**: out-of-scope questions must escalate; injections must be blocked.

Every run is logged to **MLflow** with its parameters (model, top-k, chunk size, reranker on/off,
prompt version), so experiments are comparable and reproducible.

The **evaluation gate** runs a subset on every pull request in GitHub Actions (Groq as the LLM,
Qdrant as a service container) and fails the PR if quality drops below thresholds. A prompt tweak
that breaks retrieval for payments questions gets caught before merge, not by an engineer at 3am.

**Caveat worth mentioning in interviews**: LLM judges are noisy and biased (they favour longer
answers, and a model judging itself is lenient). Mitigations used here: a separate judge model,
temperature 0, strict rubric, and deterministic retrieval metrics alongside.

---

## 12. Packaging and deployment

**Docker** (`docker/`): the API image installs dependencies in a separate layer (fast rebuilds),
bakes the ONNX models in (containers start without downloading anything), runs as a non-root
user and has a healthcheck. The UI image only needs Streamlit + httpx (~200 MB).

**Compose**: one command for six services, with Ollama on the host for GPU access.

**Kubernetes** (`k8s/helm/opspilot`): Deployments for API and UI, a StatefulSet with a
PersistentVolumeClaim for Qdrant, ConfigMap + Secret injected with `envFrom`, startup/liveness/
readiness probes, an init container that waits for Qdrant, checksum annotations so config
changes roll the pods, and an optional HPA.

**CI/CD** (`.github/workflows`): lint, tests, image build, Trivy scan, eval gate, multi-arch
release to GHCR. Trivy runs from its pinned official image rather than the `trivy-action` tag:
in March 2026 attackers force-pushed most of that action's version tags to a credential stealer,
a good real-world lesson in why third-party actions should be pinned to a commit SHA.

**Cloud**: Streamlit Community Cloud runs the UI with the pipeline in-process (`UI_BACKEND=direct`);
Groq and Qdrant Cloud replace the local services; Terraform for Cloud Run is the optional extra.

---

## 13. Design decisions and trade-offs

| Decision | Alternative | Why this one |
|---|---|---|
| Qdrant | Chroma, pgvector, Pinecone | Native hybrid search + RRF in one query, payload indexes, free cloud tier, same engine local and cloud |
| FastEmbed (ONNX) | sentence-transformers | No PyTorch: smaller images, identical everywhere |
| Ollama native, not in Docker | Ollama container | Docker on macOS has no GPU access |
| qwen2.5:7b | llama3.1:8b, mistral | Strong instruction following and JSON output at 7B; fits 16 GB with room for Docker |
| LangGraph | Plain functions, LlamaIndex | Explicit state machine with loops, streaming, tracing |
| One grader call for all passages | One call per passage | 5x fewer LLM calls; local inference is the bottleneck |
| Regex guardrails | LLM classifier | Free, instant, testable; the grounding check is the second layer |
| SSE | WebSockets | One-way stream, plain HTTP |
| Streamlit | React | Python-only, fast to build; the API is UI-agnostic anyway |

## 14. What I'd do next in production

- Authentication and per-team document access (filter by the user's groups at query time).
- Ingest straight from Confluence / Notion / PagerDuty / Jira, with incremental sync.
- Feedback buttons (thumbs up/down) feeding new eval cases.
- A bigger, separate judge model, and tracking eval scores over time.
- Answer caching keyed on the knowledge-base version, invalidated on re-index.

## 15. Interview questions you should be able to answer

1. Why hybrid search instead of only vector search? *(exact identifiers vs. meaning; RRF uses ranks)*
2. What's the difference between a bi-encoder and a cross-encoder? *(separate vs. joint encoding; speed vs. accuracy)*
3. How do you stop the model from hallucinating? *(answer-only-from-context prompt, grading, grounding check, escalation, eval faithfulness)*
4. How did you choose the chunk size? *(structure-aware, then measured with `make eval` at 600 vs 1200)*
5. How do you know a change made it better? *(golden dataset, MLflow comparison, CI gate)*
6. What happens when the LLM returns invalid JSON? *(structured output + fallbacks per node)*
7. Why doesn't Ollama run in Docker on your Mac? *(no Metal GPU in the Docker VM)*
8. What's the latency breakdown of a request? *(Grafana: retrieval tens of ms, LLM steps seconds)*
9. How would you scale it to 100 engineers? *(stateless API behind HPA, hosted LLM, Qdrant cluster, cache)*
10. What are the limits of LLM-as-judge? *(noise, bias, self-preference; mitigations)*
