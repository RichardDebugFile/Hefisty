# Hefisty — Web SPA

Interfaz de chat para **Hefisty**, el sistema de IAs locales orquestadas. SPA en
**React 18 + Vite + TypeScript**, tema oscuro, sin frameworks de CSS (estilos propios,
autocontenidos). Se compila a estáticos y los sirve el propio gateway.

## Requisitos

- **Node** ≥ 18 (probado con Node 22).
- **pnpm** ≥ 10 (gestor de paquetes del proyecto). Si no lo tienes:

  ```bash
  corepack enable pnpm    # viene incluido con Node
  # o bien:  npm i -g pnpm
  ```

  El campo `packageManager` de `package.json` fija la versión; corepack la respetará.

## Puesta en marcha

```bash
pnpm install     # instala dependencias (genera pnpm-lock.yaml reproducible)
pnpm dev         # servidor de desarrollo en http://localhost:5173
pnpm build       # type-check + build de producción → web/dist
pnpm preview     # sirve el build de web/dist para revisarlo
```

### Desarrollo

`pnpm dev` levanta Vite en el puerto **5173** y hace **proxy** de `/v1` y `/health`
hacia el gateway en `http://127.0.0.1:8080`. Arranca el backend en ese puerto y la UI
hablará con él sin configuración extra ni problemas de CORS.

### Producción

`pnpm build` genera `web/dist/` con `base: './'` (rutas de assets relativas), de modo
que el gateway puede servir esos estáticos desde su raíz. `node_modules/` y `dist/`
están en el `.gitignore` del repo; `pnpm-lock.yaml` **sí** se versiona.

## Endurecimiento de la cadena de suministro (pnpm)

- **`.npmrc`** define `minimum-release-age=1440`: pnpm rechaza versiones publicadas hace
  menos de 1 día, mitigando releases maliciosos recién publicados.
- En pnpm v10+ los *lifecycle scripts* (`postinstall`) están bloqueados por defecto.
  `esbuild` (usado por Vite) necesita el suyo, así que está permitido explícitamente en
  `package.json` → `pnpm.onlyBuiltDependencies`. Si al compilar algún otro paquete se
  queja por un script bloqueado, añádelo a esa lista.

## Configuración de acceso

La UI envía `Authorization: Bearer <token>` en cada petición `/v1/*`. El token se guarda
en `localStorage['hefisty_token']` y se edita desde el icono de ajustes (⚙) en la barra
lateral. En desarrollo local puede quedar vacío (el gateway lo permite).

## Contrato de API

El gateway sirve en `http://127.0.0.1:8080`:

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/v1/chat/completions` | Chat OpenAI-compatible, respuesta SSE (streaming) |
| `GET` | `/v1/sessions` | Lista de sesiones |
| `POST` | `/v1/sessions/{id}/resume` | Retomar una sesión (carga mensajes) |
| `PATCH` | `/v1/sessions/{id}` | Renombrar sesión |
| `GET` | `/health` | Estado del sistema (ollama/redis/qdrant/modelos) |

El stream SSE se parsea a mano con `fetch` + `ReadableStream` (no `EventSource`, que no
admite cabeceras `Authorization` ni cuerpos `POST`). Secuencia de eventos:

```
data: {"type":"meta","session_id":"…","agent":"hefisty|coder","model":"…"}
data: {"choices":[{"delta":{"content":"…"}}]}
…
data: [DONE]
```

El evento `meta` fija el `session_id` de las conversaciones nuevas y alimenta el
indicador de **agente activo + modelo**.

## Estructura

```
web/
├─ index.html
├─ vite.config.ts        # base './', proxy /v1 y /health, salida a dist
├─ tsconfig.json         # TypeScript estricto
├─ .npmrc                # minimum-release-age (supply-chain)
└─ src/
   ├─ main.tsx
   ├─ App.tsx            # estado, sesiones, envío/streaming
   ├─ api/
   │  ├─ client.ts       # fetch + auth + parser SSE + endpoints
   │  └─ types.ts        # tipos del contrato
   ├─ hooks/useHealth.ts # polling de /health
   ├─ utils/time.ts      # tiempo relativo
   ├─ components/        # Sidebar, ChatWindow (bubbles/input), badges, modal
   └─ styles/index.css   # tema oscuro autocontenido
```
