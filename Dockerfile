# The sheet and the pipeline in one image.
#
# They ship together because the web layer runs `main.py` as a subprocess: the
# browser never evaluates a rule, so the rules engine has to be on the same
# filesystem. Splitting them would mean inventing a protocol between two halves
# of one program.
#
# Node is the base and Python is installed into a venv rather than the other way
# round. Copying a node binary into a Python image is the shorter Dockerfile and
# the more fragile one — it depends on the two bases sharing shared libraries.
# The venv also sidesteps PEP 668, which refuses `pip install` into a Debian
# system Python.
#
# NOT included: San Francisco. Apple's font licence does not permit
# redistributing it, so every letterform check reports unavailable in a
# container rather than guessing. That rule carries weight 0 today, so no
# verdict changes; a deployer entitled to the font can mount it and set
# SF_FONT_PATH.

FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build && npm prune --omit=dev


FROM node:22-bookworm-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=8080 \
    # One vision call at a time. The free tier is the constraint, not the CPU.
    EXAMINE_CONCURRENCY=1 \
    EXAMINE_MAX_QUEUE=12 \
    VERIFIER_ROOT=/app \
    VERIFIER_PYTHON=/opt/venv/bin/python

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The pipeline, its knowledge base, and its prompt. Reference photographs under
# knowledge/real_* and knowledge/fake are excluded by .dockerignore and must
# never enter an image: they carry real serial numbers.
COPY *.py ./
COPY knowledge/ ./knowledge/
COPY PROMPTS.md REQUIREMENTS.md ./

COPY --from=web /build/web/dist ./web/dist
COPY --from=web /build/web/node_modules ./web/node_modules
COPY --from=web /build/web/package.json ./web/package.json

RUN useradd --system --uid 10001 sheet && chown -R sheet /app
USER sheet

EXPOSE 8080

# A container with no API key still starts, serves the sheet, and answers every
# examination with a notice saying the backend is unconfigured. That is the
# right failure, and it is why the health check asks the queue endpoint rather
# than running an examination.
HEALTHCHECK --interval=30s --timeout=4s --start-period=15s \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||8080)+'/api/examine').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "./web/dist/server/entry.mjs"]
