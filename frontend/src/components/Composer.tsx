import type { Draft, View } from '../types';
interface Props { draft: Draft; mode: View; onChange: (value: Draft) => void; busy: boolean; submitting: boolean; onSend: () => void }
export function validLink(link: string, mode: View) {
  return mode === 'Pull Request'
    ? /^https:\/\/github\.com\/[\w-]+\/[\w.-]+\/pull\/[1-9]\d*\/?$/.test(link)
    : /^https:\/\/github\.com\/[\w-]+\/[\w.-]+(?:\/issues\/[1-9]\d*)?\/?$/.test(link);
}
export function Composer({ draft, mode, onChange, busy, submitting, onSend }: Props) {
  const reason = !validLink(draft.link, mode) ? `Enter a GitHub ${mode === 'Issue' ? 'repository or issue' : 'pull request'} link.`
    : !draft.text.trim() ? 'Describe the required change.'
    : draft.text.length > 12000 ? 'Use up to 12000 characters.' : '';
  return <form id="composer" className="composer rounded-[17px] border border-line bg-white p-4 shadow-sm" onSubmit={event => { event.preventDefault(); if (!reason && !busy) onSend(); }}>
    <label htmlFor="target-link" className="mb-2 block text-xs font-medium">{mode === 'Issue' ? 'Repository or issue link' : 'Pull request link'}</label>
    <input id="target-link" type="url" value={draft.link} onChange={e => onChange({ ...draft, link: e.target.value })} placeholder={`https://github.com/owner/repo${mode === 'Pull Request' ? '/pull/123' : ''}`} className="mb-4 w-full rounded-lg border border-line px-3 py-2" />
    <label htmlFor="draft" className="sr-only">Describe the change</label>
    <textarea id="draft" value={draft.text} onChange={e => onChange({ ...draft, text: e.target.value })} maxLength={12000} placeholder="Describe the change you want to make…" className="min-h-[90px] w-full resize-y bg-transparent leading-7 outline-none" />
    <div className="mt-3 flex items-center justify-between gap-4"><p id="input-help" className="text-xs text-muted">{reason || 'Runs directly with Codex · Full Access'}</p><button id="send" type="submit" disabled={busy || !!reason} aria-describedby="input-help" className="rounded-lg bg-accent px-5 py-2 text-white">{submitting ? 'Submitting…' : 'Send'}</button></div>
  </form>;
}
