import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listSessions,
  renameSession,
  resumeSession,
  streamChat,
} from './api/client';
import type { AgentName, ChatMessage, Role, Session } from './api/types';
import { useHealth } from './hooks/useHealth';
import { Sidebar } from './components/Sidebar';
import { MessageBubble } from './components/MessageBubble';
import { MessageInput } from './components/MessageInput';
import { AgentBadge } from './components/AgentBadge';
import { HealthDot } from './components/HealthDot';
import { SettingsModal } from './components/SettingsModal';

interface UiMessage {
  id: string;
  role: Role;
  content: string;
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
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const health = useHealth();
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
    setError(null);
  }, []);

  const handleSelectSession = useCallback(
    async (id: string) => {
      if (streaming) return;
      setError(null);
      try {
        const res = await resumeSession(id);
        setActiveSessionId(res.id);
        setAgent(res.active_agent ?? null);
        setMessages(
          (res.messages ?? []).map((m) => ({ id: newId(), role: m.role, content: m.content })),
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

      const userMsg: UiMessage = { id: newId(), role: 'user', content: text };
      const assistantMsg: UiMessage = { id: newId(), role: 'assistant', content: '' };

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

      const appendToAssistant = (piece: string) => {
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          if (last && last.role === 'assistant') {
            copy[copy.length - 1] = { ...last, content: last.content + piece };
          }
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
              // A brand-new conversation gets its id from the first meta event.
              if (!activeSessionId && meta.session_id) {
                setActiveSessionId(meta.session_id);
              }
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

  return (
    <div className="app">
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
              {messages.map((m, i) => (
                <MessageBubble
                  key={m.id}
                  role={m.role}
                  content={m.content}
                  streaming={
                    streaming && i === messages.length - 1 && m.role === 'assistant'
                  }
                />
              ))}
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

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
