import { useEffect, useRef, useState } from 'react';
import { listModels } from '../api/client';
import type { ModelInfo } from '../api/types';

export interface ModelsState {
  models: ModelInfo[];
  // 'loading' until the first response resolves; 'down' when the last poll
  // failed; 'ok' otherwise.
  status: 'loading' | 'ok' | 'down';
}

/**
 * Polls GET /v1/models on an interval to show what is loaded in VRAM right now.
 *
 * Why polling instead of SSE: the set of models resident in VRAM is a
 * low-frequency, low-priority signal — it only changes when Ollama loads or
 * evicts a model (seconds-to-minutes apart), and a stale reading for a few
 * seconds is harmless. A short poll is far cheaper and simpler than keeping a
 * dedicated event stream open (extra connection, reconnection/backoff logic,
 * server push plumbing) for a value nobody is watching millisecond-to-
 * millisecond. SSE is reserved for the chat stream, where every token matters.
 */
export function useModels(intervalMs = 5000): ModelsState {
  const [state, setState] = useState<ModelsState>({ models: [], status: 'loading' });
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const poll = async () => {
      try {
        const res = await listModels(controller.signal);
        if (cancelled) return;
        setState({ models: res.models ?? [], status: 'ok' });
      } catch {
        if (cancelled) return;
        // Keep whatever we last showed; just flag the connection as down.
        setState((prev) => ({ models: prev.models, status: 'down' }));
      }
    };

    void poll();
    timer.current = window.setInterval(poll, intervalMs);

    return () => {
      cancelled = true;
      controller.abort();
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [intervalMs]);

  return state;
}
