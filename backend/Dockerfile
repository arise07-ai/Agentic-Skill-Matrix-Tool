FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# requirements.lock (made with `pip freeze > requirements.lock` in your working .venv) gives exact, repeatable versions.
# Without it the build falls back to requirements.txt.
COPY requirements.tx[t] requirements.loc[k] ./
RUN if [ -f requirements.lock ]; then pip install --no-cache-dir -r requirements.lock; else pip install --no-cache-dir -r requirements.txt; fi
COPY app ./app

# Everything the app stores (database, uploads, vector index) lives in DATA_DIR. On Railway, attach ONE volume to this service
# and mount it at /app/data, otherwise all data is lost on every deploy.
ENV DATA_DIR=/app/data
EXPOSE 8000

# Railway sets PORT. HOST can be changed to "::" if the frontend cannot reach this service over the private network.
# Run exactly one instance: role-matching jobs and the login lock-out are kept in this process's memory.
CMD ["sh", "-c", "exec uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}"]
