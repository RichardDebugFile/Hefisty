# Seguridad

## Qué se sube al repo y qué no

Política de publicación (implementada en `.gitignore` desde el commit 1):

| Se sube | No se sube |
|---|---|
| Código fuente y tests | `.env` planos, claves privadas age |
| Documentación (`docs/`) | Modelos y pesos (`.gguf`, `.safetensors` — se descargan) |
| Manifiestos de roles (`role.yaml`, prompts) | Datos de usuario: sesiones, historial, logs |
| Secretos cifrados (`*.enc.*`) y `.sops.yaml` | Colecciones Qdrant (los "diccionarios" indexados) |
| Adaptadores LoRA ya entrenados (identidad, lenguajes) | Datasets de entrenamiento — TODOS: correcciones del usuario y el dataset de identidad de Hefisty (`roles/hefisty/dataset/`). Se publica la IA entrenada, nunca su material fuente |
| CI, Docker Compose, instalador | Cualquier ruta o dato personal de la máquina |

Regla editorial: la documentación pública describe el sistema tal como es, sin referencias a material de trabajo interno (bocetos, conversaciones de diseño, decisiones personales) que el lector no tiene.

## Secretos

Estrategia: **SOPS + age**, secretos cifrados dentro del repo.

- **age** genera un par de claves; la privada vive fuera del repo (`~/.config/sops/age/` y como secret de GitHub Actions). La pública va en `.sops.yaml`.
- **SOPS** cifra los valores de `.env`/YAML dejando las claves legibles (diffs útiles). Los archivos `*.enc.*` sí se commitean.
- En runtime: `sops exec-file` descifra a memoria — el plaintext no toca disco.
- En contenedores: **Docker Secrets** (montados en `/run/secrets/`, invisibles en `docker inspect` y variables de entorno) en lugar de `.env` planos.
- Rotación: editar recipients en `.sops.yaml` + `sops updatekeys`.

Reglas fijas: nunca `.env` plano en git (`.gitignore` desde el commit 1); **gitleaks** en pre-commit y en CI para bloquear fugas; los modelos nunca reciben secretos en el contexto (la protección de salida del gateway además escanea respuestas por patrones de credenciales).

## Superficie de ataque local

- El gateway escucha solo en `127.0.0.1` por defecto; exponer en LAN es opt-in con token.
- Protección de entrada: límites de tamaño, detección de prompt injection (reglas + clasificador pequeño) sobre todo para contenido que entra vía RAG (documentos de terceros pueden traer instrucciones maliciosas).
- Herramientas de agentes con permisos declarados en el manifiesto del rol: el Coder ejecuta código solo en sandbox (contenedor efímero sin red), escritura de archivos limitada al workspace.
- Modelos: descargar solo de registries verificados (Ollama library / HF con checksum).

## CI/CD (GitHub Actions)

Pipeline en `.github/workflows/ci.yml`:

1. **Lint + formato:** ruff.
2. **Secret scanning:** gitleaks (falla el build si detecta credenciales).
3. **Tests:** pytest con modelos mockeados (CI no tiene GPU); tests de integración con un modelo diminuto (Qwen 0.5B en CPU) en job nightly opcional.
4. **Build:** imagen Docker multi-arch (amd64 primero), publicada a GHCR en tags `v*`.
5. **Release:** tag semántico → GitHub Release con changelog generado.

Ramas: `main` protegida, PRs con CI verde obligatorio. Dependabot para dependencias.
