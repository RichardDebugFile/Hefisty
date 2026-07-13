import type { RolesState } from '../hooks/useRoles';

/**
 * Read-only list of installed roles (name, description, model, collection).
 * Creating/editing roles is a later phase and deliberately not implemented.
 */
export function RolesList({ state }: { state: RolesState }) {
  const { roles, status } = state;

  if (status === 'loading') {
    return <p className="panel__empty">Cargando roles…</p>;
  }
  if (status === 'down') {
    return <p className="panel__empty">No se pudieron cargar los roles.</p>;
  }
  if (roles.length === 0) {
    return <p className="panel__empty">No hay roles instalados.</p>;
  }

  return (
    <ul className="roles">
      {roles.map((r) => (
        <li className="role" key={r.name}>
          <div className="role__head">
            <span className="role__name">{r.name}</span>
            <span className="role__model" title="Modelo del rol">
              {r.model}
            </span>
          </div>
          {r.description && <p className="role__desc">{r.description}</p>}
          <div className="role__meta">
            <span className="role__collection" title="Colección de conocimiento">
              📚 {r.collection}
            </span>
            {r.tools && r.tools.length > 0 && (
              <span className="role__tools" title={r.tools.join(', ')}>
                🛠 {r.tools.length} {r.tools.length === 1 ? 'herramienta' : 'herramientas'}
              </span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
