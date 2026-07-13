import type { AgentName } from '../api/types';

function prettyAgent(agent: AgentName | null): string {
  if (!agent) return 'Hefisty';
  const known: Record<string, string> = { hefisty: 'Hefisty', coder: 'Coder' };
  return known[agent] ?? agent.charAt(0).toUpperCase() + agent.slice(1);
}

export function AgentBadge({
  agent,
  model,
  streaming,
}: {
  agent: AgentName | null;
  model: string | null;
  streaming: boolean;
}) {
  return (
    <div className="agent-badge" title="Agente activo y modelo cargado">
      <span className={`agent-badge__pulse${streaming ? ' agent-badge__pulse--on' : ''}`} />
      <span className="agent-badge__agent">{prettyAgent(agent)}</span>
      {model && <span className="agent-badge__model">{model}</span>}
    </div>
  );
}
