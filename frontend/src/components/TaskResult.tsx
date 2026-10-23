import type { SendAction } from '../types';
export function TaskResult({ content, send, busy }: { content: string; send: SendAction; busy: boolean }) {
  let result: { summary?: unknown; validation?: unknown; pr_url?: unknown; delivery?: { code?: string; pr?: string; issue?: string; ci?: string } } | undefined;
  try {
    const parsed: unknown = JSON.parse(content);
    if (parsed && typeof parsed === 'object') result = parsed;
  } catch { /* Older plain-text messages remain readable. */ }
  if (!result || typeof result.summary !== 'string') return <p className="whitespace-pre-wrap break-words text-sm leading-7">{content}</p>;
  const url = typeof result.pr_url === 'string' && /^https:\/\/github\.com\/[\w-]+\/[\w.-]+\/pull\/[1-9]\d*\/?$/.test(result.pr_url) ? result.pr_url : '';
  return <div className="space-y-3 text-sm leading-7"><p className="whitespace-pre-wrap break-words">{result.summary}</p>
    {typeof result.validation === 'string' && result.validation && <p className="whitespace-pre-wrap text-xs text-muted">Validation: {result.validation}</p>}
    {result.delivery && <dl aria-label="Delivery facts" className="text-xs text-muted">
      <div><dt className="inline">Code: </dt><dd className="inline">{result.delivery.code === 'completed_by_codex' ? 'Completed (reported by Codex)' : 'Not confirmed by Codex'}</dd></div>
      <div><dt className="inline">Pull request: </dt><dd className="inline">{result.delivery.pr || 'unknown'}</dd></div>
      <div><dt className="inline">Target issue: </dt><dd className="inline">{result.delivery.issue === 'not_applicable' ? 'Not applicable' : result.delivery.issue || 'unknown'}</dd></div>
      <div><dt className="inline">Remote CI: </dt><dd className="inline">{result.delivery.ci || 'unknown'}</dd></div>
    </dl>}
    {url && <button className="secondary" disabled={busy} onClick={() => send('open_github', { text: url })}>Open pull request</button>}
  </div>;
}
