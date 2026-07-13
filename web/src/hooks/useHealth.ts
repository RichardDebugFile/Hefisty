import { useEffect, useRef, useState } from 'react';
import { getHealth } from '../api/client';
import type { HealthResponse } from '../api/types';

export type HealthStatus = 'ok' | 'degraded' | 'down' | 'unknown';

export interface HealthState {
  status: HealthStatus;
  detail: HealthResponse | null;
}

/**
 * Polls GET /health on an interval. Returns 'ok' when the gateway reports
 * status "ok", 'degraded' when reachable but not ok, 'down' when unreachable.
 */
export function useHealth(intervalMs = 15000): HealthState {
  const [state, setState] = useState<HealthState>({ status: 'unknown', detail: null });
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const poll = async () => {
      try {
        const detail = await getHealth(controller.signal);
        if (cancelled) return;
        setState({ status: detail.status === 'ok' ? 'ok' : 'degraded', detail });
      } catch {
        if (cancelled) return;
        setState({ status: 'down', detail: null });
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
