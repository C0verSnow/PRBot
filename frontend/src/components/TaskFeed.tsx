import type { AnswerDraft, SendAction, Task } from '../types';
import { taskLabel } from './TaskStatus';
import { MessageCard } from './MessageCard';
export function TaskFeed({ task, send, busy, archived = false, history = false, answer, onAnswerChange }: { answer: AnswerDraft; onAnswerChange: (text: string) => void; task: Task; send: SendAction; busy: boolean; archived?: boolean; history?: boolean }) {
  return <article className="message-row">
    <div role="log" aria-label="Execution log" className="execution-feed space-y-3">
      {!history && task.events?.map(event => <MessageCard key={event.sequence} role={['text', 'interaction'].includes(event.kind) ? 'assistant' : 'system'} source={task.source} created={event.created} content={event.text} send={send} busy={busy} />)}
    </div>
    {task.result && !archived && <div className="mt-3"><MessageCard role="assistant" source={task.source} created={task.changed_at || task.created_at} content={task.result} status={taskLabel(task.status)} taskId={task.id} send={send} busy={busy} /></div>}
    {task.status === 'needs_clarification' && task.source === 'telegram' && <p className="mt-3 text-sm">Reply to this task in Telegram to continue.</p>}
    {task.status === 'needs_clarification' && task.source !== 'telegram' && <form className="mt-4" onSubmit={event => {
      event.preventDefault(); if (answer.text.trim() && answer.request_id && !busy) send('answer', { task_id: task.id, request_id: answer.request_id, text: answer.text });
    }}><label className="block text-sm" htmlFor={`answer-${task.id}`}>Answer Codex to continue this task</label>
      <textarea id={`answer-${task.id}`} value={answer.text} maxLength={12000} onChange={event => onAnswerChange(event.target.value)} className="my-2 min-h-20 w-full rounded-lg border border-line p-3" />
      <button className="secondary" disabled={busy || !answer.text.trim()}>Continue task</button>
    </form>}
    {task.status === 'save_pending' && task.source !== 'telegram' && <button className="secondary mt-3" disabled={busy} onClick={() => send('retry_save', { text: task.id })}>Message not saved. Retry.</button>}
  </article>;
}
