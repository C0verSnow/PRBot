import { MessageText } from './MessageText';
import type { SendAction } from '../types';
export function TaskResult({ content, send, busy }: { content: string; send: SendAction; busy: boolean }) {
  let result: { summary?: unknown; validation?: unknown; pr_url?: unknown; delivery?: { code?: string; pr?: string; issue?: string; ci?: string } } | undefined;
  try {
    const parsed: unknown = JSON.parse(content);
    if (parsed && typeof parsed === 'object') result = parsed;
  } catch { /* Older plain-text messages remain readable. */ }
  if (result?.delivery && (typeof result.delivery !== 'object' || Array.isArray(result.delivery))) result = { ...result, delivery: undefined };
  if (!result || typeof result.summary !== 'string') return <MessageText content={content} />;
  const url = typeof result.pr_url === 'string' && /^https:\/\/github\.com\/[\w-]+\/[\w.-]+\/pull\/[1-9]\d*\/?$/.test(result.pr_url) ? result.pr_url : '';
  return <div className="space-y-3 text-sm leading-7"><MessageText content={result.summary} />
    {typeof result.validation === 'string' && result.validation && <p className="whitespace-pre-wrap text-xs text-muted">Validation: {result.validation}</p>}
    {result.delivery && <dl aria-label="Delivery facts" className="text-xs text-muted">
      <div><dt className="inline">Code: </dt><dd className="inline">{result.delivery.code === 'completed_by_codex' ? 'Completed (reported by Codex)' : 'Not confirmed by Codex'}</dd></div>
      <div><dt className="inline">Pull request: </dt><dd className="inline">{typeof result.delivery.pr === 'string' ? result.delivery.pr : 'unknown'}</dd></div>
      <div><dt className="inline">Target issue: </dt><dd className="inline">{result.delivery.issue === 'not_applicable' ? 'Not applicable' : typeof result.delivery.issue === 'string' ? result.delivery.issue : 'unknown'}</dd></div>
      <div><dt className="inline">Remote CI: </dt><dd className="inline">{typeof result.delivery.ci === 'string' ? result.delivery.ci : 'unknown'}</dd></div>
    </dl>}
    {url && <button className="secondary" disabled={busy} onClick={() => send('open_github', { text: url })}>Open pull request</button>}
  </div>;
}
