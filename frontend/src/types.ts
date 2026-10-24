export const views = ['Issue', 'Pull Request', 'Chat History'] as const;
export type View = typeof views[number];
export type Action = 'ready' | 'open_github' | 'refresh_service' | 'choose_workspace' | 'open_configuration' | 'start_service'
  | 'stop_service' | 'connections' | 'use_id_token' | 'fetch_codex_id_token' | 'configuration' | 'save_configuration'
  | 'answer' | 'submit_message' | 'history' | 'tasks' | 'retry_save';
export interface InfisicalFields {
  INFISICAL_URL: string; INFISICAL_CLIENT_ID: string; INFISICAL_CLIENT_SECRET: string;
  INFISICAL_PROJECT_ID: string; INFISICAL_ENVIRONMENT: string; INFISICAL_SECRET_PATH: string;
}
export interface InfisicalConfiguration {
  values: Omit<InfisicalFields, 'INFISICAL_CLIENT_SECRET'>;
  client_secret_set: boolean; environment_overrides: string[]; path: string;
}
export interface Draft { link: string; text: string; request_id: string }
export interface ServiceStatus {
  user?: string; loaded?: boolean; state: string; reported_at?: number; control_verified?: boolean; detail?: string;
  workspace?: string; configuration?: string;
}
export interface Task {
  id: string; conversation_id: string; mode: View; source?: 'desktop' | 'telegram'; status: string; result?: string;
  created_at: number;
  events?: { sequence: number; kind: string; text: string; created: number }[];
}
export interface Message {
  conversation_id?: string; source?: string; logical_order?: number; message_id: string; content: string; role: string; sequence: number; task_id: string;
}
export interface NativeResult {
  ok: boolean; bridge_id?: number; message?: string | null; busy?: boolean; completed_action?: Action; status?: ServiceStatus;
  connections?: { id_token: string; infisical: string; project: string };
  id_token_result?: { token: string; applied: boolean };
  configuration?: InfisicalConfiguration;
  accepted?: Task; tasks?: Task[]; tasks_revision?: number;
  history?: { reset?: boolean; mode: View; cursor: string; messages: Message[] };
}
export interface RequestData {
  bridge_id?: number; task_id?: string; text?: string; mode?: View; link?: string; request_id?: string; cursor?: string;
  configuration?: InfisicalFields; configuration_path?: string;
}
export type SendAction = (action: Action, data?: RequestData) => void;
declare global {
  interface Window {
    receiveNative?: (payload: NativeResult) => void;
    webkit?: { messageHandlers: { prbot?: { postMessage: (body: RequestData & { action: Action }) => void } } };
  }
}
