# Isolated service -- never installed into cinemagraph's own venv/image.
# CPU-capable by default (torch's PyPI wheel); for real GPU use, swap the
# base image / torch install for a CUDA-matched build, same as any other
# torch project (see ACE-Step's own Dockerfile for a GPU-oriented example).
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml app.py ./
RUN pip install --no-cache-dir .

# Model weights download from the HF Hub on first request (or at container
# start, since load happens in a startup hook) and are cached in this
# volume across restarts -- mount it, or every restart re-downloads.
ENV HF_HOME=/cache/huggingface
# huggingface_hub auto-uses its "Xet" fast-transfer backend for repos that
# support it whenever the transitively-installed hf_xet package is present
# (pulled in via transformers/huggingface_hub, not something this project
# depends on directly) -- and that backend's own CDN requests have a real,
# observed failure mode here (t5-base download: "CAS Client Error: Request
# middleware error: error sending request for url ..." against
# us.aws.cdn.hf.co, container exits 3, startup never completes). Forcing
# the plain HTTPS downloader instead is more battle-tested and sidesteps
# that failure path entirely -- see huggingface_hub's own docs for this
# env var. Revisit if a future hf_xet release fixes the underlying issue.
ENV HF_HUB_DISABLE_XET=1
VOLUME ["/cache"]

EXPOSE 8003
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003"]
