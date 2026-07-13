import { useLayoutEffect, useRef, useState } from 'react';

export function MessageInput({
  onSend,
  onStop,
  streaming,
  disabled,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
}) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea up to a max height.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || streaming || disabled) return;
    onSend(text);
    setValue('');
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer">
      <textarea
        ref={textareaRef}
        className="composer__input"
        value={value}
        placeholder="Escribe un mensaje para Hefisty…  (Enter para enviar, Shift+Enter salto de línea)"
        rows={1}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={disabled}
      />
      {streaming ? (
        <button className="composer__btn composer__btn--stop" onClick={onStop} type="button">
          Detener
        </button>
      ) : (
        <button
          className="composer__btn"
          onClick={submit}
          type="button"
          disabled={disabled || value.trim().length === 0}
        >
          Enviar
        </button>
      )}
    </div>
  );
}
