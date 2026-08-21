# Isolated service -- never installed into cinemagraph's own venv/image.
# CPU-capable by default (torch's PyPI wheel); for real GPU use, swap the
# base image / torch install for a CUDA-matched build, same as any other
# torch project (see ACE-Step's own Dockerfile for a GPU-oriented example).
FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml app.py ./
RUN pip install --no-cache-dir .

# Model weights download from the HF Hub on first request (or at container
# start, since load happens in a startup hook) and are cached in this
# volume across restarts -- mount it, or every restart re-downloads.
ENV HF_HOME=/cache/huggingface
VOLUME ["/cache"]

EXPOSE 8004
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8004"]
