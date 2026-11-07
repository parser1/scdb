# Control plane image.
# Target "fake" serves the UI with no Azure calls.
# Target "azure" is the same app plus the Azure CLI, which the worker shells out to.

FROM python:3.12-slim-bookworm AS fake

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    REPO_ROOT=/app \
    VAR_DIR=/var/lib/scylla-dbaas \
    API_KEY=dev \
    CLOUD=fake

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn>=0.32" \
        "jinja2>=3.1" \
        "pyyaml>=6"

COPY versions.env ./
COPY api ./api
COPY worker ./worker
COPY clients ./clients
COPY infra ./infra
COPY ops ./ops
COPY ui ./ui

RUN mkdir -p /var/lib/scylla-dbaas

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM fake AS azure

ENV CLOUD=azure \
    AZURE_CONFIG_DIR=/root/.azure

# az deployment reads the Bicep files. ssh-keygen satisfies Azure's Linux login requirement.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
        lsb-release \
        apt-transport-https \
        openssh-client \
        libicu72 \
    && curl -sL https://aka.ms/InstallAzureCLIDeb | bash \
    && az bicep install \
    && rm -rf /var/lib/apt/lists/*
