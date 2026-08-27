@echo off
REM ============================================================
REM  Hefisty - arranque rapido (Windows)
REM  Levanta Redis + Qdrant (Docker) y el gateway.
REM  Requisitos previos (una sola vez): scripts\setup.ps1
REM ============================================================
setlocal
cd /d "%~dp0"

echo == Hefisty ==

REM 1. Ollama debe estar instalado y corriendo (el app de escritorio lo inicia solo).
where ollama >nul 2>nul
if errorlevel 1 (
    echo [!] Ollama no esta en PATH. Instalalo desde https://ollama.com/download
    pause
    exit /b 1
)

REM 2. Servicios en Docker (Redis + Qdrant).
echo == Levantando Redis + Qdrant (Docker) ==
docker compose -f docker\docker-compose.yml up -d
if errorlevel 1 (
    echo [!] Fallo docker compose. Docker Desktop debe estar corriendo.
    pause
    exit /b 1
)

REM 3. Gateway (http://127.0.0.1:8080). Ctrl+C para detener.
echo == Iniciando gateway en http://127.0.0.1:8080  (Ctrl+C para parar) ==
uv run hefisty serve

endlocal
