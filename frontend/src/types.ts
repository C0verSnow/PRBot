export const views = ['Issue', 'Pull Request'] as const;
export type View = typeof views[number];
export type Action = 'ready' | 'choose_workspace' | 'open_configuration' | 'start_service'
  | 'stop_service' | 'connections' | 'use_id_token' | 'submit_message' | 'history' | 'tasks' | 'retry_save';
export interface Draft { link: string; text: string; request_id: string }
export interface ServiceStatus {
  user?: string; loaded?: boolean; state: string; reported_at?: number;
  workspace?: string; configuration?: string;
}
export interface Task {
  id: string; conversation_id: string; mode: View; status: string; result?: string;
  created_at: number;
}
export interface Message {
  message_id: string; content: string; role: string; sequence: number; task_id: string;
}
export interface NativeResult {
  ok: boolean; message?: string | null; busy?: boolean; status?: ServiceStatus;
  connections?: { id_token: string; infisical: string; project: string };
  accepted?: Task; tasks?: Task[];
  history?: { mode: View; cursor: string; messages: Message[] };
}
export interface RequestData { text?: string; mode?: View; link?: string; request_id?: string; cursor?: string }
export type SendAction = (action: Action, data?: RequestData) => void;
declare global {
  interface Window {
    receiveNative?: (payload: NativeResult) => void;
    webkit?: { messageHandlers: { prbot?: { postMessage: (body: RequestData & { action: Action }) => void } } };
  }
}
