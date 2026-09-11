FROM python:3.12-slim

ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG IMAGE_TAG=unknown
# Pin official CLI for repro; bump deliberately after Adapter 验收
ARG LARK_CLI_VERSION=1.0.94

LABEL org.opencontainers.image.source="geekpark_mesh" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      mesh.image.tag="${IMAGE_TAG}"

ENV MESH_BUILD_GIT_SHA=${GIT_SHA} \
    MESH_BUILD_DATE=${BUILD_DATE} \
    MESH_IMAGE_TAG=${IMAGE_TAG} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LARKSUITE_CLI_BRAND=feishu

WORKDIR /srv/mesh

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Official lark-cli (headless env credentials — no interactive login in hot path)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && curl -fsSL "https://nodejs.org/dist/v20.18.1/node-v20.18.1-linux-x64.tar.xz" \
         | tar -xJ -C /usr/local --strip-components=1 \
    && npm install -g "@larksuite/cli@${LARK_CLI_VERSION}" \
    && npx --yes "@larksuite/cli@${LARK_CLI_VERSION}" install \
    && command -v lark-cli \
    && lark-cli --version \
    && apt-get purge -y curl xz-utils \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.npm /tmp/*

# App code + prompts baked into image (do NOT volume-override prompts on tmesh for repro)
COPY app ./app
COPY eval/emit_repro_baseline.py ./eval/emit_repro_baseline.py
COPY deploy/docker-entry.sh /docker-entry.sh
RUN sed -i 's/\r$//' /docker-entry.sh && chmod +x /docker-entry.sh && mkdir -p data/raw eval/reports

EXPOSE 8080
CMD ["/docker-entry.sh"]
