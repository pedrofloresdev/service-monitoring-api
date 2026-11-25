FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Start period (spec 003d RF-9): worst-case startup with default settings is the lock wait
# (60 s) + migrations (30 s) + seed validation (2 s) + 003c's write drain (5 s) + 003c's
# reset bound (under 60 s), about 157 s. Fixed at 180 s; raise it by hand if those settings
# are raised.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:${PORT:-8000}/health').raise_for_status()"

# Exec form: the start command is the main process and receives stop signals itself. It
# runs the migrations, then the server (spec 003d RF-1).
CMD ["python", "-m", "app.serve"]
