export const views = ['New', 'Projects', 'Artifacts', 'Code', 'Pinned', 'Chats and tasks'] as const;
export type View = typeof views[number];
export type Action = '打开配置目录' | '选择工作路径' | '配置任务' | '导入 Codex 登录'
  | '配置 Token' | '启动服务' | '停止服务' | '重启服务' | '开启登录自启动'
  | '关闭登录自启动' | '查看状态' | 'ready' | 'draft';
export interface ServiceStatus {
  user?: string;
  loaded?: boolean;
  autostart?: boolean;
  state: string;
  workspace?: string;
}
export interface NativeResult {
  ok: boolean;
  message?: string | null;
  busy?: boolean;
  status?: ServiceStatus;
}
export type SendAction = (action: Action, text?: string) => void;

declare global {
  interface Window {
    receiveNative?: (payload: NativeResult) => void;
    webkit?: { messageHandlers: { prbot?: { postMessage: (body: { action: Action; text?: string }) => void } } };
  }
}
