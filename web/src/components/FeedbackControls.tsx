import { useState } from 'react';
import { sendFeedback } from '../api/client';
import type { FeedbackVote, Source } from '../api/types';

export interface FeedbackTarget {
  sessionId: string | null;
  turnIndex: number;
  agent?: string;
  model?: string;
  cacheKey?: string;
  sources?: Source[];
}

type Phase = 'idle' | 'commenting' | 'sending' | 'done' | 'error';

/**
 * 👍 / 👎 controls shown under a finished assistant answer.
 *
 * 👍 sends immediately. 👎 first reveals an optional comment field, then sends.
 * After a successful vote the buttons stay disabled and a subtle confirmation
 * ("gracias") is shown; a 👎 that invalidated the cached answer says so.
 */
export function FeedbackControls({ target }: Readonly<{ target: FeedbackTarget }>) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [vote, setVote] = useState<FeedbackVote | null>(null);
  const [comment, setComment] = useState('');
  const [note, setNote] = useState<string>('');

  const disabled = phase === 'sending' || phase === 'done';

  const submit = async (chosen: FeedbackVote, withComment: string) => {
    if (!target.sessionId) {
      setNote('sin sesión');
      setPhase('error');
      return;
    }
    setPhase('sending');
    try {
      const res = await sendFeedback({
        vote: chosen,
        session_id: target.sessionId,
        turn_index: target.turnIndex,
        agent: target.agent,
        model: target.model,
        cache_key: target.cacheKey,
        sources: target.sources,
        ...(withComment.trim() ? { comment: withComment.trim() } : {}),
      });
      setPhase('done');
      setNote(res.invalidated_cache ? 'gracias · cache invalidada' : 'gracias');
    } catch {
      setPhase('error');
      setNote('no se pudo enviar');
    }
  };

  const onUp = () => {
    setVote('up');
    void submit('up', '');
  };

  const onDown = () => {
    setVote('down');
    setPhase('commenting');
  };

  return (
    <div className="feedback">
      <div className="feedback__row">
        <button
          type="button"
          className={`feedback__btn${vote === 'up' ? ' feedback__btn--active' : ''}`}
          onClick={onUp}
          disabled={disabled || phase === 'commenting'}
          title="Respuesta útil"
          aria-label="Voto positivo"
        >
          👍
        </button>
        <button
          type="button"
          className={`feedback__btn${vote === 'down' ? ' feedback__btn--active' : ''}`}
          onClick={onDown}
          disabled={disabled}
          title="Respuesta poco útil"
          aria-label="Voto negativo"
        >
          👎
        </button>
        {phase === 'done' && <span className="feedback__note feedback__note--ok">{note}</span>}
        {phase === 'error' && <span className="feedback__note feedback__note--err">{note}</span>}
        {phase === 'sending' && <span className="feedback__note">enviando…</span>}
      </div>

      {phase === 'commenting' && (
        <div className="feedback__comment">
          <textarea
            className="feedback__textarea"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="¿Qué falló? (opcional)"
            rows={2}
            autoFocus
          />
          <div className="feedback__comment-actions">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                setPhase('idle');
                setVote(null);
              }}
            >
              Cancelar
            </button>
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => void submit('down', comment)}
            >
              Enviar
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
