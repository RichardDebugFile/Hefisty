import { useEffect, useState } from 'react';
import { getToken, setToken } from '../api/client';

export function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [value, setValue] = useState('');

  useEffect(() => {
    if (open) setValue(getToken());
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const save = () => {
    setToken(value.trim());
    onClose();
  };

  return (
    <div
      className="modal-backdrop"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape' || e.key === 'Enter') onClose();
      }}
    >
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h2 className="modal__title">Ajustes</h2>
        <label className="modal__label" htmlFor="token-input">
          Token de acceso
        </label>
        <p className="modal__hint">
          Se envía como <code>Authorization: Bearer &lt;token&gt;</code> en cada petición. Puede
          quedar vacío en desarrollo local. Se guarda en <code>localStorage</code>.
        </p>
        <input
          id="token-input"
          className="modal__input"
          type="password"
          value={value}
          placeholder="(vacío)"
          onChange={(e) => setValue(e.target.value)}
          autoComplete="off"
        />
        <div className="modal__actions">
          <button className="btn btn--ghost" onClick={onClose} type="button">
            Cancelar
          </button>
          <button className="btn" onClick={save} type="button">
            Guardar
          </button>
        </div>
      </div>
    </div>
  );
}
