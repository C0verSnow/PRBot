import type { View } from '../types';
export function EmptyState({ view }: { view: View }) {
  return <div id="empty-view" className="mb-8 text-center"><span aria-hidden="true" className="text-4xl text-accent">✳</span>
    <h1 id="greeting" className="mt-4 font-serif text-[34px]">{view === 'Issue' ? 'What shall we build next?' : 'Move your pull request forward.'}</h1>
    <p className="mt-3 text-sm text-muted">{view === 'Issue' ? 'Start with a repository or issue link.' : 'Review or update an existing pull request.'}</p></div>;
}
