import type { SendAction } from '../types';
export function TaskResult({ content, send, busy }: { content: string; send: SendAction; busy: boolean }) {
  let result: { summary?: unknown; validation?: unknown; pr_url?: unknown } | undefined;
  try {
    const parsed: unknown = JSON.parse(content);
    if (parsed && typeof parsed === 'object') result = parsed;
  } catch { /* Older plain-text messages remain readable. */ }
  if (!result || typeof result.summary !== 'string') return <p className="whitespace-pre-wrap break-words text-sm leading-7">{content}</p>;
  const url = typeof result.pr_url === 'string' && /^https:\/\/github\.com\/[\w-]+\/[\w.-]+\/pull\/[1-9]\d*\/?$/.test(result.pr_url) ? result.pr_url : '';
  return <div className="space-y-3 text-sm leading-7"><p className="whitespace-pre-wrap break-words">{result.summary}</p>
    {typeof result.validation === 'string' && result.validation && <p className="whitespace-pre-wrap text-xs text-muted">Validation: {result.validation}</p>}
    {url && <button className="secondary" disabled={busy} onClick={() => send('open_github', { text: url })}>Open pull request</button>}
  </div>;
}
