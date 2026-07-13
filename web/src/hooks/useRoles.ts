import { useEffect, useState } from 'react';
import { listRoles } from '../api/client';
import type { RoleInfo } from '../api/types';

export interface RolesState {
  roles: RoleInfo[];
  status: 'loading' | 'ok' | 'down';
}

/**
 * Loads the installed roles once on mount. Roles are read-only in this phase
 * (creating/editing is a later phase) and change rarely, so a single fetch —
 * no polling — is enough.
 */
export function useRoles(): RolesState {
  const [state, setState] = useState<RolesState>({ roles: [], status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    (async () => {
      try {
        const res = await listRoles(controller.signal);
        if (cancelled) return;
        setState({ roles: res.roles ?? [], status: 'ok' });
      } catch {
        if (cancelled) return;
        setState({ roles: [], status: 'down' });
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  return state;
}
