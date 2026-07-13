import type { HealthState } from '../hooks/useHealth';

const LABELS: Record<HealthState['status'], string> = {
  ok: 'Sistema operativo',
  degraded: 'Sistema degradado',
  down: 'Sin conexión con el gateway',
  unknown: 'Comprobando estado…',
};

export function HealthDot({ health }: { health: HealthState }) {
  const { status, detail } = health;
  const models = detail?.loaded_models ?? [];

  const services = detail
    ? [
        `Ollama: ${detail.ollama ? 'ok' : 'off'}`,
        `Redis: ${detail.redis ? 'ok' : 'off'}`,
        `Qdrant: ${detail.qdrant ? 'ok' : 'off'}`,
        models.length ? `Modelos: ${models.join(', ')}` : 'Modelos: ninguno',
      ].join('\n')
    : '';

  const title = services ? `${LABELS[status]}\n${services}` : LABELS[status];

  return (
    <div className="health" title={title} aria-label={LABELS[status]}>
      <span className={`health__dot health__dot--${status}`} />
      <span className="health__label">{LABELS[status]}</span>
    </div>
  );
}
