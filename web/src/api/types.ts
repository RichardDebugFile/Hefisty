// Shared API types mirroring the Hefisty gateway contract.

export type Role = 'user' | 'assistant' | 'system';

export type AgentName = 'hefisty' | 'coder' | string;

export interface ChatMessage {
  role: Role;
  content: string;
}

export interface Session {
  id: string;
  title: string;
  updated_at: string;
  active_agent: AgentName;
}

export interface SessionsResponse {
  sessions: Session[];
}

export interface ResumeResponse {
  id: string;
  title: string;
  active_agent: AgentName;
  messages: ChatMessage[];
}

export interface RenameResponse {
  id: string;
  title: string;
}

export interface HealthResponse {
  status: string;
  ollama: boolean;
  redis: boolean;
  qdrant: boolean;
  loaded_models: string[];
}

// A single RAG fragment retrieved from the knowledge dictionary.
export interface Source {
  source: string;
  section: string;
  score: number;
}

// SSE meta event emitted first in a chat stream.
export interface MetaEvent {
  type: 'meta';
  session_id: string;
  agent: AgentName;
  model: string;
  // Whether this turn was served from cache.
  cached?: boolean;
  // RAG fragments that grounded the answer.
  sources?: Source[];
}

// OpenAI-style streamed chunk.
export interface ChatChunk {
  choices?: Array<{
    delta?: { content?: string; role?: Role };
    finish_reason?: string | null;
  }>;
}
