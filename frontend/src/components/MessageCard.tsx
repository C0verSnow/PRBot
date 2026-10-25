import type { SendAction } from '../types';
import { MessageText } from './MessageText';
import { TaskResult } from './TaskResult';

export function MessageCard({ role, source, created, content, send, busy, pending = false }: {
  role: string; source?: string; created: number; content: string; send: SendAction; busy: boolean; pending?: boolean;
}) {
  const date = new Date(created * 1000);
  const validDate = Number.isFinite(date.getTime());
  return <article className="message-card history-message" data-role={role} data-source={source || 'desktop'}>
    <header className="message-meta">
      <strong>{role === 'assistant' ? 'Codex' : role === 'user' ? 'You' : 'System'}</strong>
      <span>{source === 'telegram' ? 'Telegram' : 'Desktop'}</span>
      <time dateTime={validDate ? date.toISOString() : undefined}>{validDate ? date.toLocaleString() : 'Time unavailable'}</time>
      {pending && <span>Submitting…</span>}
    </header>
    <div className="message-body">{role === 'assistant'
      ? <TaskResult content={content} send={send} busy={busy} />
      : <MessageText content={content} />}</div>
  </article>;
}
