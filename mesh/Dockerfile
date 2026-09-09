FROM python:3.12-slim

ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG IMAGE_TAG=unknown

LABEL org.opencontainers.image.source="geekpark_mesh" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      mesh.image.tag="${IMAGE_TAG}"

ENV MESH_BUILD_GIT_SHA=${GIT_SHA} \
    MESH_BUILD_DATE=${BUILD_DATE} \
    MESH_IMAGE_TAG=${IMAGE_TAG} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/mesh

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + prompts baked into image (do NOT volume-override prompts on tmesh for repro)
COPY app ./app
COPY eval/emit_repro_baseline.py ./eval/emit_repro_baseline.py
COPY deploy/docker-entry.sh /docker-entry.sh
RUN sed -i 's/\r$//' /docker-entry.sh && chmod +x /docker-entry.sh && mkdir -p data/raw eval/reports

EXPOSE 8080
CMD ["/docker-entry.sh"]
