# syntax=docker/dockerfile:1
# 검색 서버(MCP), 인덱서 워커, 시드가 함께 쓰는 이미지. 운영 구성은 deploy/compose.yaml이다.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /bin/uv
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
# 파이썬은 새로 받지 않고 이미지의 3.12를 쓴다. 모델은 HF_HOME 아래에 둔다
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH HF_HOME=/opt/huggingface

# 의존성 층. 코드가 바뀌어도 torch 등을 다시 설치하지 않게 프로젝트보다 먼저 설치한다(리눅스는 CPU 전용 torch, pyproject.toml)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# 모델 층. 고정 커밋의 bge-m3(2.3GB)를 이미지에 넣어 컨테이너가 뜰 때 밖에서 받지 않게 한다. 이름과 커밋은
# config.py에서 읽어 한 곳에서만 고정한다. 불러오는 파일만 받도록 실제로 모델을 한 번 만든다
COPY src/wiki_rag_mcp/config.py /tmp/config.py
RUN DISABLE_SAFETENSORS_CONVERSION=1 python -c "\
import runpy; from sentence_transformers import SentenceTransformer; \
c = runpy.run_path('/tmp/config.py'); \
SentenceTransformer(c['EMBEDDING_MODEL'], revision=c['EMBEDDING_REVISION'], device='cpu')"

COPY pyproject.toml uv.lock ./
COPY src src
# wiki-rag-seed가 위키로 옮길 가상 위키
COPY data/wiki data/wiki
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

USER app
ENV HF_HUB_OFFLINE=1
