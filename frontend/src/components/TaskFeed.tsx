import { useLayoutEffect, useRef } from 'react';
import type { AnswerDraft, SendAction, Task } from '../types';
import { taskLabel } from './TaskStatus';
import { TaskResult } from './TaskResult';
export function TaskFeed({ task, send, busy, archived = false, history = false, answer, onAnswerChange }: { answer: AnswerDraft; onAnswerChange: (text: string) => void; task: Task; send: SendAction; busy: boolean; archived?: boolean; history?: boolean }) {
  const feed = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useLayoutEffect(() => {
    if (follow.current && feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [task.events, task.result]);
  return <article className="message-row py-4">
    <p className="mb-3 text-xs">{new Date(task.created_at * 1000).toLocaleString()} · {taskLabel(task.status)}</p>
    <div ref={feed} className="execution-feed space-y-3" onScroll={() => {
      const node = feed.current;
      if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
    }}>
      {!history && task.events?.map(event => <p key={event.sequence} className="whitespace-pre-wrap break-words text-xs leading-6 text-muted"><time>{new Date(event.created * 1000).toLocaleTimeString()}</time> · {event.text}</p>)}
      {task.result && !archived && <TaskResult content={task.result} send={send} busy={busy} />}
    </div>
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
