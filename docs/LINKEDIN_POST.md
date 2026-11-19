# LinkedIn post (draft)

Fill in the `[brackets]` with your own numbers from `make eval` before posting.

---

**I built an AI copilot for on-call engineers, and it runs 100% free.**

When production breaks at 3am, the hardest part isn't the fix. It's finding the right runbook,
the postmortem from last time, and the ticket where someone already solved it.

So I built **OpsPilot**: paste an alert or error message, and it answers with step-by-step fixes
and cites the exact runbook section each step comes from.

What makes it more than a chatbot:

🔎 **Hybrid search**: semantic vectors + BM25 keywords fused with RRF, so it matches both "database overloaded" and `ERR_POOL_TIMEOUT`
🎯 **Cross-encoder reranking** of the top 20 results
🔁 **Self-correcting agent** (LangGraph): it grades its own search results and rewrites the query when they're weak
✅ **Grounding check**: a second pass fact-checks every answer against the sources; low confidence = "escalate", never a guess
⚡ **Semantic cache**: repeated questions in different words come back in milliseconds
🛡️ **Guardrails**: blocks prompt injection, redacts leaked passwords and API keys

And the MLOps side:

📊 Evaluation suite with LLM-as-judge, tracked in **MLflow**: hit rate [0.xx], faithfulness [0.xx]
🚦 A **GitHub Actions gate** that fails any pull request that makes answers worse
📈 **Prometheus + Grafana** dashboard, **Langfuse** tracing
🐳 Docker Compose, ☸️ Kubernetes with Helm, CI with Trivy scanning, images on GHCR

Stack: Python · FastAPI · LangGraph · Qdrant · FastEmbed · Ollama (Qwen 2.5 7B on my MacBook M3) · Groq (Llama 3.3 70B) · Streamlit · MLflow · Prometheus · Grafana · Docker · Kubernetes · GitHub Actions

Total cloud bill: **$0**, no credit card anywhere.

The most interesting lesson: measuring changed everything. Turning the reranker off dropped
[metric] from [x] to [y]. I'd never have guessed that from trying a few questions by hand.

🔗 Live demo: [streamlit link]
💻 Code: [github link]

I come from customer support, where troubleshooting under pressure is the job. This project is
that experience, automated. Feedback very welcome!

#AIEngineering #MLOps #RAG #LLM #LangGraph #GenerativeAI #DevOps #Python #OpenToWork

---

## Tips for the post

- **Attach a 30-60 s screen recording** (macOS: `Cmd+Shift+5`) showing a question, the agent
  steps expanding, the streamed answer with citations, the cached re-ask, and the Grafana dashboard.
  Video posts get far more reach than text.
- Add 2-3 images: the architecture diagram from the README (screenshot it from GitHub),
  the MLflow comparison view, the Grafana dashboard.
- Put the **GitHub link in the first comment** as well as in the post.
- Pin the repo on your GitHub profile and add the live demo link to the repo's "About" section.
- Post on a weekday morning, and reply to every comment in the first hour.
