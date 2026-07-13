import type { Role } from '../api/types';

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

export function MessageBubble({
  role,
  content,
  streaming,
}: {
  role: Role;
  content: string;
  streaming?: boolean;
}) {
  const isUser = role === 'user';
  const segments = splitSegments(content);

  return (
    <div className={`msg msg--${isUser ? 'user' : 'assistant'}`}>
      <div className="msg__avatar" aria-hidden="true">
        {isUser ? 'Tú' : '🔨'}
      </div>
      <div className="msg__body">
        <div className="msg__author">{isUser ? 'Tú' : 'Hefisty'}</div>
        <div className="msg__content">
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
          {streaming && <span className="msg__caret" aria-hidden="true" />}
        </div>
      </div>
    </div>
  );
}
