import type { AgentName, ChainState, ChainStep } from '../api/types';
import type { HealthState } from '../hooks/useHealth';
import type { ModelsState } from '../hooks/useModels';
import type { RolesState } from '../hooks/useRoles';
import { RolesList } from './RolesList';

const HEALTH_LABELS: Record<HealthState['status'], string> = {
  ok: 'Operativo',
  degraded: 'Degradado',
  down: 'Sin conexión',
  unknown: 'Comprobando…',
};

// Glyph + CSS modifier per chain-step state. Unknown states fall back to a
// neutral pending look so the panel never breaks on new backend states.
const CHAIN_STATE: Record<string, { glyph: string; mod: string; label: string }> = {
  pendiente: { glyph: '○', mod: 'pendiente', label: 'pendiente' },
  en_curso: { glyph: '◐', mod: 'en_curso', label: 'en curso' },
  hecha: { glyph: '✓', mod: 'hecha', label: 'hecha' },
  fallida: { glyph: '✕', mod: 'fallida', label: 'fallida' },
};

function chainMeta(state: ChainState) {
  return CHAIN_STATE[state] ?? { glyph: '○', mod: 'pendiente', label: String(state) };
}

function prettyAgent(agent: AgentName | null): string {
  if (!agent) return '—';
  return agent.charAt(0).toUpperCase() + agent.slice(1);
}

export function LivePanel({
  health,
  models,
  agent,
  model,
  streaming,
  chain,
  roles,
  onClose,
}: Readonly<{
  health: HealthState;
  models: ModelsState;
  agent: AgentName | null;
  model: string | null;
  streaming: boolean;
  chain: ChainStep[] | null;
  roles: RolesState;
  onClose: () => void;
}>) {
  const detail = health.detail;

  return (
    <aside className="panel" aria-label="Panel en vivo">
      <div className="panel__top">
        <span className="panel__title">Panel en vivo</span>
        <button
          className="panel__close"
          type="button"
          onClick={onClose}
          title="Ocultar panel"
          aria-label="Ocultar panel"
        >
          ✕
        </button>
      </div>

      <div className="panel__scroll">
        {/* Estado del sistema (health) */}
        <section className="panel__section">
          <h3 className="panel__heading">Estado</h3>
          <div className="panel__status">
            <span className={`health__dot health__dot--${health.status}`} />
            <span className="panel__status-label">{HEALTH_LABELS[health.status]}</span>
          </div>
          {detail && (
            <ul className="panel__services">
              <li>
                Ollama <b className={detail.ollama ? 'ok' : 'off'}>{detail.ollama ? 'ok' : 'off'}</b>
              </li>
              <li>
                Redis <b className={detail.redis ? 'ok' : 'off'}>{detail.redis ? 'ok' : 'off'}</b>
              </li>
              <li>
                Qdrant <b className={detail.qdrant ? 'ok' : 'off'}>{detail.qdrant ? 'ok' : 'off'}</b>
              </li>
            </ul>
          )}
        </section>

        {/* Turno en curso */}
        <section className="panel__section">
          <h3 className="panel__heading">Turno actual</h3>
          <div className="panel__turn">
            <span
              className={`panel__turn-dot${streaming ? ' panel__turn-dot--on' : ''}`}
              aria-hidden="true"
            />
            <div className="panel__turn-text">
              <span className="panel__turn-agent">{prettyAgent(agent)}</span>
              <span className="panel__turn-model">{model ?? 'inactivo'}</span>
            </div>
          </div>
        </section>

        {/* Estado de la cadena */}
        {chain && chain.length > 0 && (
          <section className="panel__section">
            <h3 className="panel__heading">Cadena de la respuesta</h3>
            <ol className="chain">
              {chain.map((step, i) => {
                const meta = chainMeta(step.state);
                return (
                  <li className={`chain__step chain__step--${meta.mod}`} key={`${step.agent}-${i}`}>
                    <span className="chain__icon" aria-hidden="true">
                      {meta.glyph}
                    </span>
                    <span className="chain__agent">{step.agent}</span>
                    <span className="chain__state">{meta.label}</span>
                  </li>
                );
              })}
            </ol>
          </section>
        )}

        {/* Modelos en VRAM */}
        <section className="panel__section">
          <h3 className="panel__heading">
            Modelos en VRAM
            {models.status === 'down' && (
              <span className="panel__stale" title="Última lectura falló">
                sin conexión
              </span>
            )}
          </h3>
          {models.status === 'loading' ? (
            <p className="panel__empty">Consultando…</p>
          ) : models.models.length === 0 ? (
            <p className="panel__empty">Ningún modelo cargado.</p>
          ) : (
            <ul className="vram">
              {models.models.map((m) => (
                <li className="vram__item" key={m.name}>
                  <span className="vram__name" title={m.name}>
                    {m.name}
                  </span>
                  <span className="vram__size">{m.vram}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Roles instalados */}
        <section className="panel__section">
          <h3 className="panel__heading">Roles instalados</h3>
          <RolesList state={roles} />
        </section>
      </div>
    </aside>
  );
}
