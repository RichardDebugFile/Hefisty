# Hefisty

> En honor a Hefesto, el dios forjador que construyó autómatas para que trabajaran por él. Hefisty es su versión femenina: una identidad cercana y confiable como cara del sistema hacia el usuario.

Sistema de IAs locales orquestadas: un orquestador ligero delega tareas a agentes especializados (programación como rol principal), cada uno respaldado por modelos locales pequeños. Instalable en cualquier máquina compatible vía Docker.

## Idea central

En vez de un modelo gigante, varios modelos pequeños especializados. El orquestador clasifica la petición y la enruta al agente correcto. Un rol nuevo (ej. gestión económica) se agrega como **paquete de rol** sin reentrenar nada: prompt de sistema + conocimiento RAG ("diccionario") + herramientas, y opcionalmente un adaptador LoRA cuando el rol madure.

## Documentación

| Documento | Contenido |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Arquitectura completa, componentes y flujo |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Tiers de hardware: mínimo, actual, recomendado, ultimate |
| [docs/ROLES.md](docs/ROLES.md) | Cómo los agentes aprenden roles nuevos (RAG → LoRA) |
| [docs/SECURITY.md](docs/SECURITY.md) | Gestión de secretos y seguridad |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Fases del proyecto |

## Stack propuesto

- **Runtime de modelos:** Ollama (simple, multiplataforma, API estándar). Migración a vLLM en tier "ultimate" para concurrencia.
- **Router de modelos:** LiteLLM (un solo endpoint para N modelos, failover).
- **Orquestador:** Python 3.12 + FastAPI (capa propia, ligera).
- **Conocimiento (RAG):** Qdrant (vector DB, corre en Docker).
- **Cache y colas:** Redis (cache exacta + semántica, cola de tareas).
- **Distribución:** Docker Compose. Windows nativo vía instalador de Ollama + servicios en contenedores (WSL2).
- **Calidad:** pytest, ruff, gitleaks, GitHub Actions.

## Estado

Fase 1: definición de arquitectura. Ver [docs/ROADMAP.md](docs/ROADMAP.md).
