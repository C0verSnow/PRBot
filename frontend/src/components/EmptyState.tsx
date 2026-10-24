import type { View } from '../types';
import { Icon } from './Icon';
const copy = {
  Issue: ['Turn GitHub issues into actionable changes.', 'Provide a repository or issue link, then describe the changes you want Codex to make.'],
  'Pull Request': ['Review and refine pull requests.', 'Provide a pull request link and describe the updates or review tasks to perform.'],
  'Chat History': ['Continue your conversations.', 'Browse saved conversations or ask Codex a private question. Chat is never automatically published to GitHub.'],
};
export function EmptyState({ view }: { view: View }) {
  return <div id="empty-view" className="mb-8 text-center"><Icon name="brand" className="mx-auto size-10 text-accent" />
    <h1 id="greeting" className="mt-4 font-serif text-[30px] leading-tight">{copy[view][0]}</h1>
    <p className="mt-3 text-sm leading-6 text-muted">{copy[view][1]}</p></div>;
}
