ARG NODE_BASE_IMAGE=node:22-alpine@sha256:8ea2348b068a9544dae7317b4f3aafcdc032df1647bb7d768a05a5cad1a7683f
FROM ${NODE_BASE_IMAGE}

WORKDIR /app/frontend

COPY package*.json /app/frontend/
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV NEXT_TELEMETRY_DISABLED=1
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm config set audit false \
    && npm config set fund false \
    && npm config set fetch-retries 5 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 120000 \
    && npm ci --no-audit --no-fund --prefer-offline --loglevel=warn

COPY . /app/frontend
ARG NEXT_PUBLIC_ANVIL_GATEWAY_URL=http://backend:18000
ENV NEXT_PUBLIC_ANVIL_GATEWAY_URL=${NEXT_PUBLIC_ANVIL_GATEWAY_URL}
ARG NEXT_PUBLIC_LANGSMITH_URL=https://smith.langchain.com
ENV NEXT_PUBLIC_LANGSMITH_URL=${NEXT_PUBLIC_LANGSMITH_URL}

RUN npm config delete script-shell --location=project \
    && npm run build

EXPOSE 13200

CMD ["sh", "-c", "npm run start -- --hostname 0.0.0.0 --port 13200"]
