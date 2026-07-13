# Roles: cómo un agente aprende un trabajo nuevo

Principio (respaldado por la práctica actual): **RAG para conocimiento, LoRA para comportamiento, y en ese orden**. La secuencia de especialización es: Prompt → RAG → LoRA → (destilar). Nunca se empieza entrenando.

## Paquete de rol (unidad de extensión)

Un rol es una carpeta declarativa. Crear el rol "gestión económica" = crear esto, sin tocar código:

```yaml
# roles/finanzas/role.yaml
name: finanzas
description: "Gestión económica: presupuestos, análisis de gastos, facturación"
triggers: ["presupuesto", "gasto", "factura", "flujo de caja"]  # ayudan al clasificador
model: qwen3:8b            # modelo base genérico
system_prompt: prompts/finanzas.md
knowledge:                  # el "diccionario"
  collection: finanzas      # colección en Qdrant
  sources: [docs/finanzas/**.pdf, docs/finanzas/**.md]
tools: [calculadora, leer_csv, generar_reporte]
lora: null                  # se agrega cuando el rol madura
```

## Ciclo de vida de un rol

1. **Detección:** el clasificador no encuentra agente adecuado → el orquestador responde "no tengo ese rol, ¿quieres crearlo?" y genera el esqueleto del paquete.
2. **Diccionario (RAG):** el usuario aporta fuentes (docs, manuales, ejemplos); un pipeline las trocea, embebe y sube a la colección del rol. Desde ese momento el rol funciona con el modelo genérico + retrieval. Días, no semanas.
3. **Refinamiento:** cada corrección del usuario se guarda como par (tarea, respuesta buena) en un dataset del rol.
4. **LoRA (opcional, rol maduro):** cuando hay ~500-1000 ejemplos buenos, se entrena un adaptador QLoRA sobre el modelo base (factible en la RTX 5060 Ti 16 GB para modelos 7-8B, con Unsloth o axolotl). El adaptador pesa MBs y se versiona en el repo del rol. LoRA aporta estilo, formato y patrones; el conocimiento factual sigue en RAG (actualizable sin reentrenar).

## El rol Coder (principal)

Mismo esquema pero con modelo dedicado (Qwen2.5-Coder 14B) y diccionario más rico: documentación de lenguajes/frameworks, convenciones propias del usuario, y **el propio repo indexado** (re-embebido incrementalmente al hacer commit). Herramientas: ejecutar código en sandbox, leer/escribir archivos, git, búsqueda estructural y semántica sobre el repo (ver ARCHITECTURE.md).

Es jerárquico: delega en **sub-roles por lenguaje** (Python, JS/TS, etc.), cada uno con su diccionario y adaptador LoRA propios sobre el mismo modelo base — especialización sin duplicar modelos en VRAM.

## La identidad de Hefisty

El modelo pequeño del orquestador es "la cara" del sistema: recibe todo y responde por todos. Es **conversacional antes que enrutadora**: puede mantener una charla, pedir aclaraciones y solo delega en un agente cuando hay una tarea de trabajo definida — la elección de herramienta es automática, invisible para el usuario. Observación clave: debe **saber quién es** — su nombre, su rol de coordinadora, su tono.

- **Fase inicial:** identidad por prompt de sistema (nombre, personalidad, cómo derivar a los agentes). Suficiente para arrancar y gratis.
- **Fase madura:** LoRA de identidad sobre el clasificador (dataset pequeño: saludos, presentaciones, derivaciones con su tono). Fine-tunear un 1.7B con QLoRA es trivial en la GPU actual y hace la identidad consistente sin gastar tokens de prompt en cada petición.
- El resto de agentes no necesita identidad propia: son "las manos" de Hefisty; ella habla siempre en primera persona.

## Por qué no entrenar cada rol desde el inicio

- Un 7-8B genérico + RAG bien construido cubre la mayoría de roles nuevos con calidad aceptable desde el día 1.
- Fine-tuning para conocimiento es un antipatrón: el conocimiento caduca y reentrenar cuesta; en RAG se actualiza reemplazando documentos.
- La tendencia 2026 confirma el enfoque: modelos pequeños (1-8B) especializados con adaptadores finos igualan a modelos mucho mayores en su nicho, con ~10× menos costo de inferencia.
