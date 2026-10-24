import type { Task } from '../types';
export function TaskStatus({ task, submitting = false }: { task?: Task; submitting?: boolean }) {
  const state = submitting ? 'submitting' : task?.status || 'idle';
  const labels: Record<string, string> = {
    idle: 'Idle', submitting: 'Submitting…', cancelling: 'Stopping · waiting for process cleanup', cancelled: 'Cancelled', queued: 'Queued', running: 'Codex is working',
    needs_clarification: 'Waiting for your answer', completed: 'Completed',
    failed: 'Failed', interrupted: 'Interrupted', timed_out: 'Timed out',
    save_pending: 'Result waiting to be saved', delivered_unverified: 'Delivered · verification incomplete',
  };
  return <div className="task-status my-5 flex items-center gap-5" role="status" aria-live="polite" data-state={state}>
    <span aria-hidden="true" className={`service-core task-core ${state}`} />
    <div><p className="text-sm">{labels[state] || state}</p><p className="mt-1 break-all text-xs text-muted">{task ? `Codex SDK · ${task.id}` : 'Codex SDK · No task selected'}</p></div>
  </div>;
}
