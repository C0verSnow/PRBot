import { useContext } from 'react';
import { MessageText } from './MessageText';
import { DeliveryVerificationContext } from './DeliveryVerification';
import type { SendAction } from '../types';
export function TaskResult({ content, send, busy, taskId }: { content: string; send: SendAction; busy: boolean; taskId?: string }) {
  const { verification, verifying } = useContext(DeliveryVerificationContext);
  const checked = verification?.task_id === taskId ? verification : undefined;
  const checking = !!taskId && verifying === taskId;
  let result: { summary?: unknown; validation?: unknown; pr_url?: unknown; delivery?: { code?: string; pr?: string; issue?: string; ci?: string } } | undefined;
  try {
    const parsed: unknown = JSON.parse(content);
    if (parsed && typeof parsed === 'object') result = parsed;
  } catch { /* Older plain-text messages remain readable. */ }
  if (result?.delivery && (typeof result.delivery !== 'object' || Array.isArray(result.delivery))) result = { ...result, delivery: undefined };
  if (!result || typeof result.summary !== 'string') return <MessageText content={content} />;
  const url = typeof result.pr_url === 'string' && /^https:\/\/github\.com\/[\w-]+\/[\w.-]+\/pull\/[1-9]\d*\/?$/.test(result.pr_url) ? result.pr_url : '';
  return <div className="space-y-3 text-sm leading-7"><MessageText content={result.summary} />
    {!result.delivery && typeof result.validation === 'string' && result.validation && <p className="whitespace-pre-wrap text-xs text-muted">{result.validation}</p>}
    {result.delivery && (result.delivery.ci === 'failed' || result.delivery.pr === 'invalid') && <p role="alert" className="text-xs text-red-700">Delivery needs attention. Open the pull request or review delivery details.</p>}
    {result.delivery && <details className="text-xs text-muted"><summary>Saved delivery details</summary><p>Snapshot from this response; use Verify delivery for a current check.</p><dl aria-label="Delivery facts">
      <div><dt className="inline">Code: </dt><dd className="inline">{result.delivery.code === 'completed_by_codex' ? 'Completed (reported by Codex)' : 'Not confirmed by Codex'}</dd></div>
      <div><dt className="inline">Pull request: </dt><dd className="inline">{typeof result.delivery.pr === 'string' ? result.delivery.pr : 'unknown'}</dd></div>
      <div><dt className="inline">Target issue: </dt><dd className="inline">{result.delivery.issue === 'not_applicable' ? 'Not applicable' : typeof result.delivery.issue === 'string' ? result.delivery.issue : 'unknown'}</dd></div>
      <div><dt className="inline">Remote CI: </dt><dd className="inline">{typeof result.delivery.ci === 'string' ? result.delivery.ci : 'unknown'}</dd></div>
    </dl>{typeof result.validation === 'string' && <MessageText content={result.validation} />}</details>}
    {url && <div className="flex flex-wrap gap-2"><button className="secondary" disabled={busy} onClick={() => send('open_github', { text: url })}>Open pull request</button>
      {taskId && <button className="secondary verify-delivery" disabled={verifying !== null} aria-busy={checking} onClick={() => send('verify_delivery', { task_id: taskId })}>{checking ? 'Verifying…' : checked?.error ? 'Retry verification' : 'Verify delivery'}</button>}
    </div>}
    {checking && <p role="status" className="text-xs text-muted">Checking the pull request and remote checks…</p>}
    {!checking && checked && <div role={checked.error ? 'alert' : 'status'} className="verification-result text-xs text-muted">
      <p>{checked.error || checked.detail || 'Verification finished.'}</p>
      {checked.delivery && <p>Pull request: {checked.delivery.pr} · Issue: {checked.delivery.issue} · Remote CI: {checked.delivery.ci}</p>}
      {checked.checked_at && <p>Checked {new Date(checked.checked_at * 1000).toLocaleString()}. This does not change conversation completion.</p>}
    </div>}
  </div>;
}
