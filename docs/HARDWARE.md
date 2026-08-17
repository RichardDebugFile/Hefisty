# Hardware

Cuatro tiers. El tier define qué modelos y cuántos agentes concurrentes soporta la instalación — el instalador debería detectar VRAM/RAM y elegir perfil automáticamente.

## Tabla de tiers

| | Mínimo | **Actual (dev)** | Recomendado | Ultimate |
|---|---|---|---|---|
| GPU | Ninguna o 6-8 GB | **RTX 5060 Ti 16 GB** | 16 GB VRAM (5060 Ti/4070 Ti S) | RTX 5090 32 GB o 2× GPU |
| CPU | 4 núcleos | **Ryzen 7 5700 (8c/16t)** | 8 núcleos modernos | 16+ núcleos |
| RAM | 16 GB | **48 GB (~30 GB libres)** | 32 GB | 64-128 GB |
| Disco | 50 GB SSD | NVMe, ~100 GB libres | NVMe 200 GB | NVMe 500 GB+ |
| Coder | 7B Q4 (lento en CPU) | gpt-oss:20b MoE @ ~60 tok/s | gpt-oss:20b (MoE) | 32B denso Q4, o MoE mayor |
| Agentes simultáneos | 1 | 1 grande + clasificador | 1 grande + 2 pequeños | 3-4 en paralelo |
| Runtime | Ollama (CPU/GPU) | Ollama | Ollama | vLLM (concurrencia 10-20×) |

## Notas por tier

**Mínimo (para que cualquiera lo pruebe):** todo en CPU o GPU pequeña. Solo el Coder 7B + clasificador. RAG completo funciona igual (Qdrant y Redis son ligeros). Experiencia lenta pero funcional — define el piso de compatibilidad del proyecto.

**Actual:** la 5060 Ti de 16 GB es un punto dulce real: gpt-oss:20b (MoE, ~13 GB pico) + clasificador 1.7B + embeddings caben juntos. Presupuesto de VRAM (medido en vivo con `/api/ps`, los tres modelos residentes a la vez = pico de un turno con delegación): coder 13.13 + frontal 1.77 + embeddings 0.55 = **15.46/16 GB, 100% GPU, sin offload a RAM**, dejando ~0.5 GB para SO/compositor — muy justo pero viable, y el MoE mantiene ~60 tok/s. Al terminar cada turno el Coder se descarga de VRAM (no retiene ~13 GB ocioso); recarga en 1-3 s desde el page cache de RAM en la siguiente tarea de código. Elegido tras un benchmark agéntico de modelos locales de código (gpt-oss:20b resolvió 10/15 tareas con 100% de tool_calls nativas; Qwen2.5-Coder 14B, 0/15 y 0% nativas). Los 48 GB de RAM (~30 GB libres) son una ventaja grande: varios modelos descargados de VRAM quedan cacheados en RAM (page cache), así el intercambio entre agentes tarda 1-3 s en vez de leer del disco; incluso permite correr un modelo secundario pequeño en CPU en paralelo si hiciera falta.

**Recomendado (lo que se pide a usuarios finales):** igual al actual. Es deliberado — desarrollar sobre el hardware recomendado garantiza que la experiencia publicada sea la real.

**Ultimate:** 32 GB de VRAM permiten Coder 32B (salto grande de calidad) o varios agentes de 7-14B residentes a la vez sin intercambio, con vLLM sirviendo peticiones concurrentes (multi-usuario en LAN). Con 2 GPUs: una fija para el Coder, otra para el resto de los agentes.

## Reglas de dimensionamiento

- Modelo **denso** Q4 ≈ 0.6 GB de VRAM por B de parámetros, + 2-4 GB de contexto/KV cache.
- Modelo **MoE**: la VRAM la fija el total de parámetros, no los activos (gpt-oss:20b ≈ 13 GB aunque active ~3.6B); la velocidad sí depende de los activos → alta.
- Dejar siempre ~1 GB de VRAM libre (SO/compositor en Windows).
- El clasificador y embeddings (~2 GB) son costo fijo; restarlos antes de elegir el Coder.
