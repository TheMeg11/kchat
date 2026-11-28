FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && useradd --create-home --uid 10001 hamchat \
    && chown hamchat:hamchat /app
COPY --chown=hamchat:hamchat app ./app
COPY --chown=hamchat:hamchat webadmin ./webadmin
USER hamchat
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=8)"
CMD ["python", "-m", "app.main"]
