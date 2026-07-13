# Roadmap

## Fase 1 — Arquitectura (actual)
Cerrar este diseño, elegir nombre, crear repo en GitHub con estos docs, CI básico (lint + gitleaks) y estructura de carpetas.

## Fase 2 — Núcleo mínimo (MVP)
- Docker Compose: Ollama + Redis + Qdrant + gateway FastAPI.
- Orquestador con clasificador (Qwen3 1.7B) y un solo agente: **Coder** (Qwen2.5-Coder 14B).
- Identidad de Hefisty por prompt de sistema.
- Sesiones persistentes en SQLite: crear, listar y retomar (`hefisty sessions`).
- CLI simple (`hefisty ask "..."`). Cache L1 exacta.
- Criterio de salida: resolver tareas de código reales end-to-end en la máquina actual.

## Fase 3 — Conocimiento y multi-agente
- Pipeline RAG: ingesta de docs + indexado del repo propio (el "diccionario" del Coder).
- Herramientas de navegación de código: glob/grep, índice semántico del repo, edición por diff.
- Segundo y tercer agente (Revisor, Docs) + encadenamiento de subtareas con estado en la sesión.
- Cache semántica. Protecciones entrada/salida completas.

## Fase 4 — Roles dinámicos
- Paquetes de rol declarativos + comando de creación de rol.
- Flujo "no tengo ese rol → crearlo": esqueleto, ingesta de fuentes, alta en el clasificador.
- Dataset de correcciones por rol.

## Fase 5 — Especialización y distribución
- Entrenamiento QLoRA local de adaptadores por rol (Unsloth).
- LoRA de identidad para el clasificador (Hefisty consistente sin prompt largo).
- Sub-roles por lenguaje: adaptadores LoRA + diccionarios por lenguaje sobre el Coder base.
- Modelo de visión (Qwen2.5-VL 7B) para pistas visuales: captura → descripción → búsqueda en el repo.
- Instalador con detección de tier de hardware y perfiles automáticos.
- Imágenes publicadas en GHCR; pruebas en Windows nativo + Docker; luego Linux/macOS.

## Fase 6 — Escala local
- Backend vLLM opcional (tier ultimate, multi-usuario en LAN).
- Observabilidad completa (métricas VRAM/latencia, dashboard).
- API pública estable para integraciones (IDE, editores).
