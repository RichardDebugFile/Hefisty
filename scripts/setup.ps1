#Requires -Version 5.1
<#
    Setup de Hefisty (Fase 2).
    Verifica Ollama, descarga los modelos necesarios y levanta Redis + Qdrant en Docker.
    Uso:  pwsh -File scripts/setup.ps1  [-SkipModels] [-SkipDocker]
#>
param(
    [switch]$SkipModels,
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$models = @("qwen3:1.7b", "gpt-oss:20b", "nomic-embed-text")

Write-Host "== Hefisty setup ==" -ForegroundColor Cyan

# 1. Ollama nativo en el host.
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Warning "Ollama no esta instalado. Instalalo desde https://ollama.com/download y reejecuta."
    exit 1
}
Write-Host "Ollama detectado: $((& ollama --version) -join ' ')"

# 2. Modelos.
if (-not $SkipModels) {
    $installed = (& ollama list | Out-String)
    foreach ($m in $models) {
        if ($installed -match [regex]::Escape($m)) {
            Write-Host "  [ok] $m ya instalado"
        }
        else {
            Write-Host "  [..] descargando $m (puede tardar)" -ForegroundColor Yellow
            & ollama pull $m
        }
    }
}

# 3. Servicios Docker (Redis + Qdrant).
if (-not $SkipDocker) {
    $compose = Join-Path $PSScriptRoot "..\docker\docker-compose.yml"
    Write-Host "Levantando Redis + Qdrant..."
    & docker compose -f $compose up -d
}

Write-Host ""
Write-Host "Listo. Copia .env.example a .env y arranca el gateway:" -ForegroundColor Green
Write-Host "  hefisty serve    # gateway con la config" -ForegroundColor Green
Write-Host "  hefisty status   # verifica que todo este verde" -ForegroundColor Green
