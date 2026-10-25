import type { Draft, ModelCatalog, Task, Workspace } from '../types';
import { Icon } from './Icon';
interface Props { catalog?: ModelCatalog; onRefreshModels: () => void; stopPending: boolean; draft: Draft; mode: Workspace; task?: Task; onChange: (value: Draft) => void; busy: boolean; submitting: boolean; onSend: () => void; onStop: () => void; onNew: () => void; onCancel: () => void }
export function validLink(link: string, mode: Workspace) {
  if (mode === 'Chat History') return true;
  return /^https:\/\/github\.com\/[\w-]+\/(?!\.{1,2}(?:\/|$))[\w.-]+(?:\/(?:issues|pull)\/[1-9]\d*)?\/?$/i.test(link.trim());
}
export function Composer({ draft, mode, task, onChange, busy, submitting, onSend, onStop, onNew, onCancel, stopPending, catalog, onRefreshModels }: Props) {
  const stopping = task?.status === 'cancelling';
  const active = !!task && ['queued', 'running', 'cancelling'].includes(task.status);
  const unknownModel = !!draft.model && !!catalog && !catalog.models.some(model => model.id === draft.model);
  const reason = unknownModel ? 'Selected model is no longer listed. Refresh models or choose another model.' : !validLink(draft.link, mode) ? 'Enter a GitHub repository, issue or pull request link.'
    : !draft.text.trim() ? 'Describe the required change.'
    : draft.text.length > 12000 ? 'Use up to 12000 characters.' : '';
  const label = stopping ? 'Stopping…' : active ? 'Stop task' : submitting ? 'Submitting…' : 'Send';
  return <form id="composer" className="composer rounded-[17px] border border-line bg-white p-4 shadow-sm" onSubmit={event => { event.preventDefault(); if (!reason && !busy && !active) onSend(); }}>
    {mode === 'GitHub' && <><label htmlFor="target-link" className="mb-2 block text-xs font-medium">Repository, issue or pull request link</label>
      <input id="target-link" type="url" value={draft.link} disabled={active || submitting} onChange={e => onChange({ ...draft, link: e.target.value })} placeholder="https://github.com/owner/repo/issues/123" className="mb-4 w-full rounded-lg border border-line px-3 py-2" /></>}
    <label htmlFor="draft" className="sr-only">Describe the change</label>
    <textarea id="draft" value={draft.text} disabled={active || submitting} onChange={e => onChange({ ...draft, text: e.target.value })} maxLength={12000} placeholder={mode === 'Chat History' ? 'Ask Codex a question…' : 'Describe the task for this repository, issue or pull request…'} className="min-h-[90px] w-full resize-y bg-transparent leading-7 outline-none" />
    <div className="model-picker mt-3">
      <label htmlFor="model">Model</label>
      <select id="model" value={draft.model || ''} disabled={active || submitting} onChange={event => onChange({ ...draft, model: event.target.value || undefined, request_id: crypto.randomUUID() })} aria-describedby="model-help">
        <option value="">Codex default</option>
        {unknownModel && <option value={draft.model} disabled>{draft.model} · unavailable</option>}
        {catalog?.models.map(model => <option key={model.id} value={model.id}>{model.name} · SDK catalog</option>)}
      </select>
      <button id="refresh-models" type="button" className="secondary" disabled={active || submitting} onClick={onRefreshModels}>Refresh models</button>
      <p id="model-help" role={catalog?.error ? 'alert' : 'status'} className="text-xs text-muted">{catalog?.error || (!catalog ? 'Loading models… Default remains available.' : 'SDK catalog; account access is checked on Send. No automatic fallback.')}</p>
      {task && <p className="text-xs text-muted">Task model: {task.model || 'Codex default'}</p>}
    </div>
    <div className="mt-3 flex flex-wrap items-center gap-3"><button id="new-session" type="button" className="secondary" onClick={onNew}>New</button>
      <button id="cancel-draft" type="button" className="secondary" disabled={submitting || (!draft.link && !draft.text)} onClick={onCancel}>Cancel</button>
      <p id="input-help" className="flex-1 text-xs text-muted">{active ? (stopping ? 'Stop requested. Waiting for process cleanup.' : 'Task accepted. Stop affects only this task; New keeps it running.') : reason || (mode === 'Chat History' ? 'Private conversation · saved to history' : 'Codex · Full Access')}</p>
      <button id="send" type={active ? 'button' : 'submit'} onClick={active ? onStop : undefined} disabled={(active ? stopPending : busy) || stopping || (!active && !!reason) || task?.source === 'telegram'} aria-label={label} title={label} aria-describedby="input-help" className="send-icon rounded-lg bg-accent text-white"><Icon name={active ? 'stop' : 'arrow'} /></button>
    </div>
  </form>;
}
