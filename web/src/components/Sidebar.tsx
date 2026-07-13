import { useEffect, useRef, useState } from 'react';
import type { Session } from '../api/types';
import { relativeTime } from '../utils/time';

function SessionItem({
  session,
  active,
  onSelect,
  onRename,
}: {
  session: Session;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  useEffect(() => {
    setDraft(session.title);
  }, [session.title]);

  const commit = () => {
    const next = draft.trim();
    setEditing(false);
    if (next && next !== session.title) {
      onRename(next);
    } else {
      setDraft(session.title);
    }
  };

  return (
    <div
      className={`session${active ? ' session--active' : ''}`}
      onClick={() => !editing && onSelect()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (!editing && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="session__main">
        {editing ? (
          <input
            ref={inputRef}
            className="session__edit"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commit();
              } else if (e.key === 'Escape') {
                setDraft(session.title);
                setEditing(false);
              }
            }}
          />
        ) : (
          <span
            className="session__title"
            title="Doble clic para renombrar"
            onDoubleClick={(e) => {
              e.stopPropagation();
              setEditing(true);
            }}
          >
            {session.title || 'Sin título'}
          </span>
        )}
        <span className="session__meta">
          {session.active_agent && (
            <span className="session__agent">{session.active_agent}</span>
          )}
          <span className="session__time">{relativeTime(session.updated_at)}</span>
        </span>
      </div>
      {!editing && (
        <button
          className="session__rename"
          title="Renombrar"
          aria-label="Renombrar sesión"
          onClick={(e) => {
            e.stopPropagation();
            setEditing(true);
          }}
          type="button"
        >
          ✎
        </button>
      )}
    </div>
  );
}

export function Sidebar({
  sessions,
  activeId,
  loading,
  onNewChat,
  onSelect,
  onRename,
  onOpenSettings,
}: {
  sessions: Session[];
  activeId: string | null;
  loading: boolean;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onOpenSettings: () => void;
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__logo" aria-hidden="true">
          🔨
        </span>
        <div className="sidebar__brandtext">
          <span className="sidebar__name">Hefisty</span>
          <span className="sidebar__tagline">IA local orquestada</span>
        </div>
        <button
          className="sidebar__settings"
          title="Ajustes"
          aria-label="Ajustes"
          onClick={onOpenSettings}
          type="button"
        >
          ⚙
        </button>
      </div>

      <button className="sidebar__new" onClick={onNewChat} type="button">
        + Nueva conversación
      </button>

      <div className="sidebar__list">
        {loading && sessions.length === 0 ? (
          <p className="sidebar__empty">Cargando sesiones…</p>
        ) : sessions.length === 0 ? (
          <p className="sidebar__empty">Aún no hay conversaciones.</p>
        ) : (
          sessions.map((s) => (
            <SessionItem
              key={s.id}
              session={s}
              active={s.id === activeId}
              onSelect={() => onSelect(s.id)}
              onRename={(title) => onRename(s.id, title)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
