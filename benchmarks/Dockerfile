FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential && rm -rf /var/lib/apt/lists/*
# Independent CPU-only evaluation runtime; never install these into the CUDA environment.
RUN pip install --no-cache-dir torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir evalplus==0.3.1 swebench==3.0.15
ENV XDG_CACHE_HOME=/opt/cache HF_HOME=/opt/cache/huggingface TOKENIZERS_PARALLELISM=false
COPY worker.py /opt/worker.py
RUN python /opt/worker.py warm-human
WORKDIR /tmp
ENTRYPOINT ["python", "/opt/worker.py"]
