import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listSessions,
  renameSession,
  resumeSession,
  streamChat,
} from './api/client';
import type {
  AgentName,
  ChainStep,
  ChatMessage,
  Role,
  Session,
  Source,
} from './api/types';
import { useHealth } from './hooks/useHealth';
import { useModels } from './hooks/useModels';
import { useRoles } from './hooks/useRoles';
import { Sidebar } from './components/Sidebar';
import { MessageBubble, type ContentPart } from './components/MessageBubble';
import { MessageInput } from './components/MessageInput';
import { AgentBadge } from './components/AgentBadge';
import { HealthDot } from './components/HealthDot';
import { LivePanel } from './components/LivePanel';
import { SettingsModal } from './components/SettingsModal';

interface UiMessage {
  id: string;
  role: Role;
  // Full plain text of the turn. Kept in sync while streaming so it can be
  // replayed to the backend as history on the next send.
  content: string;
  // Chain-aware slices for rendering (one per agent). User/resumed turns have
  // none and fall back to `content`.
  parts?: ContentPart[];
  cached?: boolean;
  sources?: Source[];
  // Feedback payload captured from the meta event(s) of this turn.
  cacheKey?: string;
  agent?: AgentName;
  model?: string;
  sessionId?: string;
}

let idSeq = 0;
function newId(): string {
  idSeq += 1;
  return `m${Date.now().toString(36)}-${idSeq}`;
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [agent, setAgent] = useState<AgentName | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [chain, setChain] = useState<ChainStep[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);

  const health = useHealth();
  const models = useModels();
  const roles = useRoles();
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const res = await listSessions();
      setSessions(res.sessions ?? []);
    } catch {
      // Sidebar simply stays empty if the gateway is unreachable.
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  // Auto-scroll to the latest content.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  const handleNewChat = useCallback(() => {
    abortRef.current?.abort();
    setActiveSessionId(null);
    setMessages([]);
    setAgent(null);
    setModel(null);
    setChain(null);
    setError(null);
  }, []);

  const handleSelectSession = useCallback(
    async (id: string) => {
      if (streaming) return;
      setError(null);
      setChain(null);
      try {
        const res = await resumeSession(id);
        setActiveSessionId(res.id);
        setAgent(res.active_agent ?? null);
        setModel(null);
        setMessages(
          (res.messages ?? []).map((m) => ({
            id: newId(),
            role: m.role,
            content: m.content,
            sessionId: res.id,
            agent: m.role === 'assistant' ? res.active_agent : undefined,
          })),
        );
      } catch (e) {
        setError(`No se pudo retomar la sesión: ${(e as Error).message}`);
      }
    },
    [streaming],
  );

  const handleRename = useCallback(
    async (id: string, title: string) => {
      // Optimistic update, then persist.
      setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title } : s)));
      try {
        await renameSession(id, title);
      } catch (e) {
        setError(`No se pudo renombrar: ${(e as Error).message}`);
        void refreshSessions();
      }
    },
    [refreshSessions],
  );

  const handleSend = useCallback(
    async (text: string) => {
      setError(null);
      // Each new turn starts without a chain; meta events refill it if the
      // turn ends up being chained.
      setChain(null);

      const userMsg: UiMessage = { id: newId(), role: 'user', content: text };
      const assistantMsg: UiMessage = {
        id: newId(),
        role: 'assistant',
        content: '',
        parts: [],
        sessionId: activeSessionId ?? undefined,
      };

      // History sent to the backend (OpenAI-compatible): the full visible
      // conversation plus the new user turn. The assistant placeholder is not
      // included.
      const history: ChatMessage[] = [...messages, userMsg].map((m) => ({
        role: m.role,
        content: m.content,
      }));

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      // Append streamed text to the current assistant turn: to `content` (for
      // history) and to the last chain part (for chain-aware rendering).
      const appendToAssistant = (piece: string) => {
        setMessages((prev) => {
          const copy = [...prev];
          const idx = copy.length - 1;
          const last = copy[idx];
          if (last?.role !== 'assistant') return prev;
          const parts = last.parts ? [...last.parts] : [];
          if (parts.length === 0) {
            parts.push({ text: piece });
          } else {
            const li = parts.length - 1;
            parts[li] = { ...parts[li], text: parts[li].text + piece };
          }
          copy[idx] = { ...last, content: last.content + piece, parts };
          return copy;
        });
      };

      try {
        await streamChat(
          history,
          activeSessionId ?? undefined,
          {
            onMeta: (meta) => {
              setAgent(meta.agent ?? null);
              setModel(meta.model ?? null);
              // Chained turns carry the live sub-task states; keep the latest.
              if (meta.chain) setChain(meta.chain);
              // A brand-new conversation gets its id from the first meta event.
              if (!activeSessionId && meta.session_id) {
                setActiveSessionId(meta.session_id);
              }
              setMessages((prev) => {
                const copy = [...prev];
                const idx = copy.length - 1;
                const last = copy[idx];
                if (last?.role !== 'assistant') return prev;

                // Chain handling: several meta events arrive per turn (one per
                // agent). When the active agent changes mid-answer we open a
                // new part so the bubble shows a "— revisor —" separator; if
                // the current part is still empty we just relabel it.
                const parts = last.parts ? [...last.parts] : [];
                const lastPart = parts[parts.length - 1];
                if (!lastPart) {
                  parts.push({ agent: meta.agent, text: '' });
                } else if (lastPart.agent !== meta.agent) {
                  if (lastPart.text.length === 0) {
                    parts[parts.length - 1] = { ...lastPart, agent: meta.agent };
                  } else {
                    parts.push({ agent: meta.agent, text: '' });
                  }
                }

                copy[idx] = {
                  ...last,
                  parts,
                  cached: meta.cached,
                  sources: meta.sources ?? last.sources,
                  cacheKey: meta.cache_key ?? last.cacheKey,
                  agent: meta.agent ?? last.agent,
                  model: meta.model ?? last.model,
                  sessionId: last.sessionId ?? meta.session_id,
                };
                return copy;
              });
            },
            onDelta: appendToAssistant,
          },
          controller.signal,
        );
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          setError(`Error en el stream: ${(e as Error).message}`);
          appendToAssistant('\n\n_(La respuesta se interrumpió por un error.)_');
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
        // Refresh sidebar so a new/renamed session and its timestamp appear.
        void refreshSessions();
      }
    },
    [messages, activeSessionId, refreshSessions],
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    setStreaming(false);
  }, []);

  const showEmptyState = messages.length === 0;

  // Running count of assistant turns, used as feedback `turn_index`.
  let assistantTurn = -1;

  return (
    <div className={`app${panelOpen ? ' app--panel' : ''}`}>
      <Sidebar
        sessions={sessions}
        activeId={activeSessionId}
        loading={sessionsLoading}
        onNewChat={handleNewChat}
        onSelect={handleSelectSession}
        onRename={handleRename}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="chat">
        <header className="chat__header">
          <div className="chat__title">
            <span className="chat__brand">Hefisty</span>
            <span className="chat__subtitle">forjadora de tus tareas</span>
          </div>
          <div className="chat__header-right">
            <AgentBadge agent={agent} model={model} streaming={streaming} />
            <HealthDot health={health} />
            <button
              className={`panel-toggle${panelOpen ? ' panel-toggle--on' : ''}`}
              type="button"
              onClick={() => setPanelOpen((v) => !v)}
              title={panelOpen ? 'Ocultar panel en vivo' : 'Mostrar panel en vivo'}
              aria-pressed={panelOpen}
            >
              ▦
            </button>
          </div>
        </header>

        {error && (
          <div className="chat__error" role="alert">
            {error}
            <button onClick={() => setError(null)} aria-label="Cerrar" type="button">
              ✕
            </button>
          </div>
        )}

        <div className="chat__scroll" ref={scrollRef}>
          {showEmptyState ? (
            <div className="empty">
              <div className="empty__logo" aria-hidden="true">
                🔨
              </div>
              <h1 className="empty__title">Hola, soy Hefisty</h1>
              <p className="empty__text">
                Tu orquestadora de IAs locales. Cuéntame qué necesitas y decidiré si te respondo yo
                o delego en un agente especializado.
              </p>
            </div>
          ) : (
            <div className="messages">
              {messages.map((m, i) => {
                if (m.role === 'assistant') assistantTurn += 1;
                const isLast = i === messages.length - 1;
                const isStreaming = streaming && isLast && m.role === 'assistant';
                // Offer feedback on finished assistant answers only.
                const canFeedback = m.role === 'assistant' && !isStreaming;
                return (
                  <MessageBubble
                    key={m.id}
                    role={m.role}
                    content={m.content}
                    parts={m.parts}
                    cached={m.cached}
                    sources={m.sources}
                    streaming={isStreaming}
                    feedback={
                      canFeedback
                        ? {
                            sessionId: m.sessionId ?? activeSessionId,
                            turnIndex: assistantTurn,
                            agent: m.agent,
                            model: m.model,
                            cacheKey: m.cacheKey,
                            sources: m.sources,
                          }
                        : undefined
                    }
                  />
                );
              })}
            </div>
          )}
        </div>

        <footer className="chat__footer">
          <MessageInput
            onSend={handleSend}
            onStop={handleStop}
            streaming={streaming}
            disabled={false}
          />
        </footer>
      </main>

      {panelOpen && (
        <LivePanel
          health={health}
          models={models}
          agent={agent}
          model={model}
          streaming={streaming}
          chain={chain}
          roles={roles}
          onClose={() => setPanelOpen(false)}
        />
      )}

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
