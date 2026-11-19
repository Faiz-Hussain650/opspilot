# LinkedIn post

At 3 a.m., the fix is almost always written down somewhere. Finding it is the hard part.

So I built OpsPilot, an AI copilot for on-call engineers. 🚨

𝗦𝗶𝘁𝘂𝗮𝘁𝗶𝗼𝗻
When production breaks, engineers lose valuable minutes searching through runbooks, old postmortems and past tickets. Generic AI chatbots don't really help here. They don't know your systems, and they sound just as confident when they're guessing.

𝗧𝗮𝘀𝗸
Build an assistant that answers only from a team's own documentation, shows where every step comes from, and admits when it doesn't know. And run it like a real production service, without spending anything.

𝗔𝗰𝘁𝗶𝗼𝗻
🔎 Hybrid search in Qdrant (meaning + exact error codes like ERR_POOL_TIMEOUT), with a reranker on top
🔁 A LangGraph agent that grades its own search results and rewrites the query when they're weak
✅ A fact-check step that compares every answer with the sources and lists anything it can't verify
🛡️ Guardrails against prompt injection and leaked passwords or API keys
📊 Evaluation tracked in MLflow, a GitHub Actions gate that blocks changes that make answers worse, and Prometheus + Grafana monitoring
🐳 Docker, Kubernetes (Helm) and a live cloud deployment

𝗥𝗲𝘀𝘂𝗹𝘁
• A live demo anyone can try, running entirely on free tiers (Groq, Qdrant Cloud, Streamlit)
• Answers with citations in about 5 seconds
• 40 automated tests and CI on every push
• My favourite moment: the fact-check caught the AI inventing a kubectl command with the wrong namespace, and flagged it before anyone could run it

The biggest lesson? Getting the AI to answer was the easy part. Making it trustworthy, measured and honest about what it doesn't know is where the real engineering happens.

👉 Try it: https://opspilot-faiz.streamlit.app
💻 Code: https://github.com/Faiz-Hussain650/opspilot

Built with Python, FastAPI, LangGraph, Qdrant, Groq, Ollama, Streamlit, MLflow, Docker, Kubernetes and GitHub Actions.

I'd love to hear your feedback, especially from anyone who has been on call! 🙌

#AIEngineering #RAG #LLM #MLOps #GenerativeAI #LangGraph #DevOps

---

## Images (in this order)

1. `docs/images/opspilot-pipeline.png`: explains the project at a glance
2. A screenshot of the live app with a green "grounded" answer and the Sources panel open
