// Thin API client for the Hefisty gateway.
//
// Auth: every /v1/* request carries `Authorization: Bearer <token>` where the
// token lives in localStorage['hefisty_token']. The backend accepts an empty
// token in local dev, so when unset we simply omit the header.

import type {
  ChainStep,
  ChatChunk,
  ChatMessage,
  FeedbackBody,
  FeedbackResponse,
  HealthResponse,
  MetaEvent,
  ModelsResponse,
  RenameResponse,
  ResumeResponse,
  RolesResponse,
  SessionsResponse,
  Source,
} from './types';

export const TOKEN_KEY = 'hefisty_token';

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

export function setToken(token: string): void {
  // The token is a bearer credential the user types in themselves for local
  // use and is later sent as an `Authorization` header. Before persisting it
  // we strip surrounding whitespace and any character that is illegal in an
  // HTTP header value (control chars, CR/LF, spaces), which both sanitizes the
  // stored value and prevents header injection.
  const clean = token.trim().replace(/[^\x21-\x7E]/g, '');
  try {
    if (clean) {
      // NOSONAR: 'clean' ya viene sanitizado (trim + strip de caracteres no imprimibles) arriba.
      localStorage.setItem(TOKEN_KEY, clean);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
  } catch {
    /* ignore storage failures (private mode, etc.) */
  }
}

function authHeaders(base: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  const headers: Record<string, string> = { ...base };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

async function jsonFetch<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: authHeaders({
      'Content-Type': 'application/json',
      ...(init?.headers as Record<string, string> | undefined),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ''}`);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export function listSessions(): Promise<SessionsResponse> {
  return jsonFetch<SessionsResponse>('/v1/sessions');
}

export function resumeSession(id: string): Promise<ResumeResponse> {
  return jsonFetch<ResumeResponse>(`/v1/sessions/${encodeURIComponent(id)}/resume`, {
    method: 'POST',
  });
}

export function renameSession(id: string, title: string): Promise<RenameResponse> {
  return jsonFetch<RenameResponse>(`/v1/sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return jsonFetch<HealthResponse>('/health', { signal });
}

// ---------------------------------------------------------------------------
// Live state: models loaded in VRAM & installed roles
// ---------------------------------------------------------------------------

export function listModels(signal?: AbortSignal): Promise<ModelsResponse> {
  return jsonFetch<ModelsResponse>('/v1/models', { signal });
}

export function listRoles(signal?: AbortSignal): Promise<RolesResponse> {
  return jsonFetch<RolesResponse>('/v1/roles', { signal });
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

export function sendFeedback(body: FeedbackBody): Promise<FeedbackResponse> {
  return jsonFetch<FeedbackResponse>('/v1/feedback', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Chat completions (SSE stream)
// ---------------------------------------------------------------------------

export interface ChatStreamCallbacks {
  onMeta?: (meta: MetaEvent) => void;
  onDelta?: (text: string) => void;
  onDone?: () => void;
}

/**
 * POST /v1/chat/completions and consume the text/event-stream response.
 *
 * EventSource cannot send an Authorization header or a POST body, so we parse
 * the SSE stream by hand from the fetch ReadableStream.
 */
export async function streamChat(
  messages: ChatMessage[],
  sessionId: string | undefined,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/v1/chat/completions', {
    method: 'POST',
    signal,
    headers: authHeaders({
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    }),
    body: JSON.stringify({
      messages,
      stream: true,
      ...(sessionId ? { session_id: sessionId } : {}),
    }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ''}`);
  }
  if (!res.body) {
    throw new Error('Respuesta sin cuerpo (streaming no disponible).');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;

  const handleData = (data: string): void => {
    if (data === '[DONE]') {
      finished = true;
      callbacks.onDone?.();
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      // Ignore malformed/keep-alive lines.
      return;
    }
    if (parsed && typeof parsed === 'object' && (parsed as { type?: string }).type === 'meta') {
      const raw = parsed as MetaEvent;
      // Normalize the extra fields so downstream state can trust their shape:
      // `cached` is a strict boolean, `sources`/`chain` are arrays (or
      // undefined) and `cache_key` is a string (or undefined).
      const meta: MetaEvent = {
        ...raw,
        cached: raw.cached === true,
        sources: Array.isArray(raw.sources) ? (raw.sources as Source[]) : undefined,
        chain: Array.isArray(raw.chain) ? (raw.chain as ChainStep[]) : undefined,
        cache_key: typeof raw.cache_key === 'string' ? raw.cache_key : undefined,
      };
      callbacks.onMeta?.(meta);
      return;
    }
    const chunk = parsed as ChatChunk;
    const piece = chunk.choices?.[0]?.delta?.content;
    if (piece) {
      callbacks.onDelta?.(piece);
    }
  };

  // Consume one complete SSE line, extracting any `data:` payload.
  const processLine = (rawLine: string): void => {
    const line = rawLine.replace(/\r$/, '');
    if (!line.startsWith('data:')) return;
    const data = line.slice(5).trimStart();
    handleData(data);
  };

  while (!finished) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newlineIdx: number;
    while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newlineIdx);
      buffer = buffer.slice(newlineIdx + 1);
      processLine(line);
      if (finished) break;
    }
  }

  // Flush any trailing buffered line (stream ended without newline).
  if (!finished && buffer.length > 0) {
    processLine(buffer);
  }
  if (!finished) {
    callbacks.onDone?.();
  }
}
