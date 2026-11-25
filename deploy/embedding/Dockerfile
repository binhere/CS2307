# Purpose: the CPU embedding service (Ollama) with its model baked in, so the cluster never downloads
#   a model at runtime and every replica serves the exact same weights.
# Input: build arguments EMBEDDING_MODEL (Ollama model tag) and EMBEDDING_MODEL_MANIFEST_SHA256 (the
#   digest `ollama list` shows for that tag). The build fails if the registry now serves different
#   content under the tag: catalog vectors are only valid for the weights that produced them.
# Output: Ollama on port 11434 (OpenAI-compatible /v1/embeddings), running as the unprivileged user
#   10001 with a read-only root filesystem; only /tmp needs to be writable.
#
# Changing the model, its digest or the Ollama version changes the vectors, so the live catalog must
# be re-embedded before such an image is deployed (see deploy/README.md).

ARG OLLAMA_IMAGE=ollama/ollama:0.19.0

FROM ${OLLAMA_IMAGE} AS model

ARG EMBEDDING_MODEL=qwen3-embedding:0.6b
ARG EMBEDDING_MODEL_MANIFEST_SHA256=ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d
ENV OLLAMA_MODELS=/opt/ollama/models \
    HOME=/home/ollama

# `ollama serve` creates its identity key under $HOME on first start; creating it here means the
# runtime never has to write it. The GPU runtimes (cuda_*, ~4.8 GB) are removed: the server has no GPU.
RUN mkdir -p "$OLLAMA_MODELS" "$HOME" \
    && (ollama serve > /tmp/ollama-build.log 2>&1 &) \
    && for attempt in $(seq 60); do ollama list > /dev/null 2>&1 && break; sleep 1; done \
    && ollama pull "$EMBEDDING_MODEL" \
    && model_name="${EMBEDDING_MODEL%%:*}" && model_tag="${EMBEDDING_MODEL##*:}" \
    && echo "$EMBEDDING_MODEL_MANIFEST_SHA256  $OLLAMA_MODELS/manifests/registry.ollama.ai/library/$model_name/$model_tag" \
       | sha256sum --check --strict - \
    && echo "$EMBEDDING_MODEL" > /opt/ollama/EMBEDDING_MODEL \
    && find /usr/lib/ollama -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +

FROM ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b

ENV OLLAMA_MODELS=/opt/ollama/models \
    HOME=/home/ollama \
    OLLAMA_HOST=0.0.0.0:11434 \
    # Never delete model blobs on start (the directory is read-only at runtime).
    OLLAMA_NOPRUNE=1 \
    # One request at a time and one model: memory use stays flat and predictable on CPU.
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    # Keep the model loaded; the default unloads it after 5 idle minutes and the next search pays the reload.
    OLLAMA_KEEP_ALIVE=-1

COPY --from=model /usr/bin/ollama /usr/bin/ollama
COPY --from=model /usr/lib/ollama /usr/lib/ollama
COPY --from=model /opt/ollama /opt/ollama
COPY --from=model --chown=10001:10001 /home/ollama /home/ollama

USER 10001:10001
EXPOSE 11434
ENTRYPOINT ["/usr/bin/ollama"]
CMD ["serve"]
