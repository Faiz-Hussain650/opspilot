# OpsPilot Streamlit UI. In Docker it only talks to the API over HTTP,
# so it needs just streamlit + httpx (a ~200 MB image instead of ~1 GB).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN pip install "streamlit>=1.50" "httpx>=0.27"

COPY ui/app.py ui/backends.py ./

RUN useradd --create-home --uid 1000 opspilot
USER opspilot

ENV UI_BACKEND=api \
    API_URL=http://api:8000
EXPOSE 8501

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
