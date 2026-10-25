export const views = ['Issue', 'Pull Request', 'Chat History'] as const;
export type View = typeof views[number];
export type Workspace = 'GitHub' | 'Chat History';
export type HistoryMode = View | 'All';
export interface HistorySession { id: string; title: string; mode: View; source: string; created_at: number }
export type Action = 'ready' | 'open_github' | 'refresh_service' | 'choose_workspace' | 'open_configuration' | 'start_service'
  | 'stop_service' | 'connections' | 'use_id_token' | 'fetch_codex_id_token' | 'configuration' | 'save_configuration'
  | 'models' | 'verify_delivery' | 'answer' | 'submit_message' | 'history' | 'tasks' | 'retry_save' | 'cancel_task';
export interface InfisicalFields {
  INFISICAL_URL: string; INFISICAL_CLIENT_ID: string; INFISICAL_CLIENT_SECRET: string;
  INFISICAL_PROJECT_ID: string; INFISICAL_ENVIRONMENT: string; INFISICAL_SECRET_PATH: string;
}
export interface InfisicalConfiguration {
  values: Omit<InfisicalFields, 'INFISICAL_CLIENT_SECRET'>;
  client_secret_set: boolean; environment_overrides: string[]; path: string;
}
export interface AnswerDraft { text: string; request_id: string; revision?: string }
export interface ModelOption { id: string; name: string; default: boolean }
export interface ModelCatalog { models: ModelOption[]; error?: string }
export interface DeliveryVerification {
  task_id: string; checked_at?: number; detail?: string; error?: string;
  delivery?: { pr?: string; issue?: string; ci?: string };
}
export interface Draft { model?: string; created_at?: number; workspace?: Workspace; link: string; text: string; request_id: string }
export interface ServiceStatus {
  user?: string; loaded?: boolean; state: string; reported_at?: number; control_verified?: boolean; detail?: string;
  workspace?: string; configuration?: string; database?: string; workspace_error?: string; executor?: string;
}
export interface Task {
  id: string; conversation_id: string; mode: View; source?: 'desktop' | 'telegram'; status: string; result?: string;
  model?: string | null; created_at: number; changed_at?: number; title?: string; user_text?: string; link?: string;
  events?: { sequence: number; kind: string; text: string; created: number }[];
}
export interface Message {
  conversation_id?: string; mode?: View; source?: string; logical_order?: number; message_id: string; content: string; role: string; sequence: number; task_id: string;
}
export interface NativeResult {
  ok: boolean; bridge_id?: number; message?: string | null; busy?: boolean; completed_action?: Action; status?: ServiceStatus;
  model_catalog?: ModelCatalog;
  delivery_verification?: DeliveryVerification;
  connections?: { id_token: string; infisical: string; project: string };
  id_token_result?: { token: string; applied: boolean };
  configuration?: InfisicalConfiguration;
  task_update?: Task; accepted?: Task; tasks?: Task[]; tasks_revision?: number;
  history?: { request_id?: string; session?: string; reset?: boolean; mode: HistoryMode; cursor: string; messages: Message[]; sessions?: HistorySession[]; cached?: boolean; warning?: string };
  history_error?: { request_id?: string; message: string };
}
export interface RequestData {
  reset?: boolean;
  model?: string; bridge_id?: number; task_id?: string; text?: string; mode?: HistoryMode | 'GitHub'; session?: string; link?: string; request_id?: string; cursor?: string;
  configuration?: InfisicalFields; configuration_path?: string;
}
export type SendAction = (action: Action, data?: RequestData) => void;
declare global {
  interface Window {
    receiveNative?: (payload: NativeResult) => void;
    webkit?: { messageHandlers: { prbot?: { postMessage: (body: RequestData & { action: Action }) => void } } };
  }
}
