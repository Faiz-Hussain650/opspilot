"""OpsPilot chat UI (Streamlit).

Docker / k8s:      UI_BACKEND=api    (default) - talks to the FastAPI service
Streamlit Cloud:   UI_BACKEND=direct - runs the RAG pipeline in-process
"""

from __future__ import annotations

import os

import streamlit as st
from backends import ApiBackend, DirectBackend

st.set_page_config(page_title="OpsPilot", page_icon=":material/emergency:", layout="wide")

EXAMPLES = [
    "checkout-service is returning 502s and logs say ERR_POOL_TIMEOUT",
    "Redis is rejecting writes with OOM command not allowed",
    "payments are timing out against the card processor, what do I do?",
    "a pod is stuck in CrashLoopBackOff after a deploy",
    "users are getting 401s right after we rotated signing keys",
]


@st.cache_resource(show_spinner="Loading models and connecting to the knowledge base...")
def get_backend():
    mode = os.getenv("UI_BACKEND", "api").lower()
    if mode == "direct":
        return DirectBackend()
    return ApiBackend(os.getenv("API_URL", "http://localhost:8000"))


def confidence_badge(resp: dict) -> str:
    if resp.get("blocked"):
        return ":red-background[blocked by guardrail]"
    badge = ":blue-background[cached]" if resp.get("cached") else ""
    conf = resp.get("confidence", 0.0)
    if not resp.get("grounded"):
        badge += " :orange-background[needs review - not fully backed by sources]"
    elif resp.get("escalate"):
        badge += f" :orange-background[escalate - confidence {conf:.0%}]"
    else:
        badge += f" :green-background[grounded - confidence {conf:.0%}]"
    return f"{badge}  ·  {resp.get('latency_ms', 0) / 1000:.1f}s"


def render_review_note(resp: dict) -> None:
    """Explain *why* an answer was flagged, instead of a generic warning."""
    if resp.get("blocked") or not resp.get("escalate"):
        return
    claims = resp.get("unsupported_claims") or []
    if not resp.get("grounded"):
        note = "The fact-check couldn't find every statement in the sources. Double-check these before acting:"
        if claims:
            note += "\n" + "\n".join(f"- {c}" for c in claims[:5])
        else:
            note = "The fact-check couldn't verify this answer against the sources - double-check before acting."
        st.warning(note)
    elif not resp.get("sources"):
        st.warning("Nothing in the knowledge base covers this - escalate to the owning team.")
    else:
        st.warning("Low confidence - treat this as a starting point and escalate.")


def render_sources(resp: dict) -> None:
    sources = resp.get("sources") or []
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})"):
        for s in sources:
            mark = "**cited**" if s.get("cited") else "not cited"
            score = s.get("rerank_score")
            score_txt = f" · rerank {score:.2f}" if score is not None else ""
            st.markdown(
                f"**[{s['ref']}] {s['title']}**  \n"
                f"`{s['source']}` · {s['doc_type']} · {s['service']}{score_txt} · {mark}"
            )
            if s.get("section"):
                st.caption(s["section"])
            st.text(s["snippet"])
    if resp.get("rewritten_query"):
        st.caption(f"Search was rewritten to: _{resp['rewritten_query']}_")


backend = get_backend()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("OpsPilot")
    st.caption("Agentic RAG copilot for on-call engineers")

    stats = backend.stats()
    if stats:
        st.markdown(
            f"**Mode:** {stats['mode']}  \n"
            f"**LLM:** {stats['llm_provider']} / `{stats['llm_model']}`  \n"
            f"**Indexed chunks:** {stats['chunks']}"
        )
        services = stats.get("services") or []
        doc_types = stats.get("doc_types") or []
    else:
        st.error("Can't reach the OpsPilot API. Is `docker compose up` running?")
        services, doc_types = [], []

    st.subheader("Filters")
    service = st.selectbox("Service", ["(any)"] + services)
    doc_type = st.selectbox("Document type", ["(any)"] + doc_types)
    use_cache = st.toggle("Use semantic cache", value=True)

    st.subheader("Add knowledge")
    uploaded = st.file_uploader("Runbook / postmortem", type=["md", "txt", "pdf"])
    up_service = st.text_input("Service tag", placeholder="e.g. checkout-service")
    up_type = st.selectbox("Type", ["runbook", "postmortem", "ticket", "other"])
    if uploaded and st.button("Index document", width="stretch"):
        with st.spinner("Chunking and embedding..."):
            try:
                result = backend.upload(
                    uploaded.name, uploaded.getvalue(), up_type, up_service or None
                )
                st.success(f"Indexed {result['chunks']} chunks from {uploaded.name}")
            except Exception as exc:
                st.error(f"Upload failed: {exc}")

    if st.button("Clear semantic cache", width="stretch"):
        backend.clear_cache()
        st.toast("Cache cleared")

    if isinstance(backend, ApiBackend):
        st.subheader("Dashboards")
        st.markdown(
            "[API docs](http://localhost:8000/docs) · [Grafana](http://localhost:3000) · "
            "[Prometheus](http://localhost:9090) · [MLflow](http://localhost:5001)"
        )

# --------------------------------------------------------------------- chat
st.header("What's broken?")
st.caption("Describe the alert, error or symptom. Answers cite the runbooks they come from.")

if "history" not in st.session_state:
    st.session_state.history = []

if not st.session_state.history:
    cols = st.columns(len(EXAMPLES))
    for col, example in zip(cols, EXAMPLES, strict=True):
        if col.button(example, width="stretch"):
            st.session_state.pending = example
            st.rerun()

for turn in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        st.markdown(turn["response"]["answer"])
        st.caption(confidence_badge(turn["response"]))
        render_review_note(turn["response"])
        render_sources(turn["response"])

question = st.chat_input("e.g. checkout 502s, ERR_POOL_TIMEOUT in the logs")
question = question or st.session_state.pop("pending", None)

if question:
    with st.chat_message("user"):
        st.markdown(question)

    payload = {
        "question": question,
        "service": None if service == "(any)" else service,
        "doc_type": None if doc_type == "(any)" else doc_type,
        "use_cache": use_cache,
    }

    with st.chat_message("assistant"):
        status = st.status("Thinking...", expanded=False)
        placeholder = st.empty()
        streamed = ""
        final = None

        for event in backend.stream_chat(payload):
            if event["type"] == "step":
                status.write(event["data"])
            elif event["type"] == "token":
                streamed += event["data"]
                placeholder.markdown(streamed + " ▌")
            elif event["type"] == "final":
                final = event["data"]
            elif event["type"] == "error":
                status.update(label="Failed", state="error")
                placeholder.error(event["data"])

        if final:
            status.update(label="Done", state="complete")
            # The final answer may differ from the streamed text (secret redaction).
            placeholder.markdown(final["answer"])
            st.caption(confidence_badge(final))
            render_review_note(final)
            render_sources(final)
            st.session_state.history.append({"question": question, "response": final})
