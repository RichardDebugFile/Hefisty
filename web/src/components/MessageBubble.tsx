import type { AgentName, Role, Source } from '../api/types';
import { FeedbackControls, type FeedbackTarget } from './FeedbackControls';

// One contiguous slice of an assistant answer produced by a single agent.
// Chained turns yield several parts (coder, then revisor, …); a plain turn
// yields one. Separators between parts mark where the active agent changed.
export interface ContentPart {
  agent?: AgentName;
  text: string;
}

interface Segment {
  type: 'text' | 'code';
  content: string;
  lang?: string;
}

// Minimal, dependency-free splitter for triple-backtick fenced code blocks.
function splitSegments(text: string): Segment[] {
  const segments: Segment[] = [];
  const fence = /```([\w+-]*)\n?([\s\S]*?)```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = fence.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: text.slice(lastIndex, match.index) });
    }
    segments.push({ type: 'code', lang: match[1] || undefined, content: match[2].replace(/\n$/, '') });
    lastIndex = fence.lastIndex;
  }
  if (lastIndex < text.length) {
    segments.push({ type: 'text', content: text.slice(lastIndex) });
  }
  if (segments.length === 0) {
    segments.push({ type: 'text', content: text });
  }
  return segments;
}

function PartBody({ text }: { text: string }) {
  const segments = splitSegments(text);
  return (
    <>
      {segments.map((seg, i) =>
        seg.type === 'code' ? (
          <pre className="code-block" key={i}>
            {seg.lang && <span className="code-block__lang">{seg.lang}</span>}
            <code>{seg.content}</code>
          </pre>
        ) : (
          <p className="msg__text" key={i}>
            {seg.content}
          </p>
        ),
      )}
    </>
  );
}

export function MessageBubble({
  role,
  content,
  parts,
  streaming,
  cached,
  sources,
  feedback,
}: {
  role: Role;
  content: string;
  parts?: ContentPart[];
  streaming?: boolean;
  cached?: boolean;
  sources?: Source[];
  feedback?: FeedbackTarget;
}) {
  const isUser = role === 'user';
  // Chain-aware turns render as parts; everything else as a single block.
  const displayParts: ContentPart[] =
    parts && parts.length > 0 ? parts : [{ text: content }];

  return (
    <div className={`msg msg--${isUser ? 'user' : 'assistant'}`}>
      <div className="msg__avatar" aria-hidden="true">
        {isUser ? 'Tú' : '🔨'}
      </div>
      <div className="msg__body">
        <div className="msg__author">
          <span>{isUser ? 'Tú' : 'Hefisty'}</span>
          {!isUser && cached && (
            <span className="msg__cache" title="Respuesta servida desde la cache">
              cache
            </span>
          )}
        </div>
        <div className="msg__content">
          {displayParts.map((part, pi) => (
            <div className="msg__part" key={pi}>
              {pi > 0 && (
                <div className="msg__chain-sep" role="separator">
                  <span>— {part.agent ?? 'agente'} —</span>
                </div>
              )}
              <PartBody text={part.text} />
            </div>
          ))}
          {streaming && <span className="msg__caret" aria-hidden="true" />}
        </div>
        {!isUser && sources && sources.length > 0 && (
          <details className="msg__sources">
            <summary className="msg__sources-summary">Fuentes ({sources.length})</summary>
            <ul className="msg__sources-list">
              {sources.map((s, i) => (
                <li className="msg__source" key={`${s.source}-${i}`}>
                  <span className="msg__source-file">{s.source}</span>
                  <span className="msg__source-sep" aria-hidden="true">
                    ·
                  </span>
                  <span className="msg__source-section">{s.section}</span>
                  <span className="msg__source-score">{s.score.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {feedback && <FeedbackControls target={feedback} />}
      </div>
    </div>
  );
}
