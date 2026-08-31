# syntax=docker/dockerfile:1

# --- Stage 1: build del front (SPA) con pnpm ---
FROM node:20-alpine AS web
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml web/.npmrc ./
RUN pnpm install --frozen-lockfile  # NOSONAR: lock fija versiones; vite/esbuild requieren postinstall
COPY web/ ./
RUN pnpm build

# --- Stage 2: runtime del gateway ---
FROM python:3.12-slim AS runtime
WORKDIR /app

# Instalación editable: el código deja `roles/` y `web/dist` junto al paquete, y
# config.py resuelve el REPO_ROOT relativo a /app (layout del repo preservado).
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY roles/ ./roles/
RUN pip install --no-cache-dir -e .  # NOSONAR: instala el propio paquete; deps resueltas de pyproject

# Front compilado que el gateway sirve en '/'.
COPY --from=web /web/dist ./web/dist

# Usuario no-root.
RUN useradd -m -u 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8080
# Ollama corre en el host (host.docker.internal); Redis/Qdrant vía compose.
# En producción define HEFISTY_API_TOKEN (el gateway avisa si expone sin token).
ENV HEFISTY_HOST=0.0.0.0
CMD ["hefisty", "serve"]
