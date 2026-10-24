# Issue #19：工作区与指定任务停止

## 实现

统一 GitHub 导航通过 GitHub mode 调用原有 submit，后端用严格 HTTPS URL 解析映射为 Issue 或 Pull Request；旧 mode 调用仍保留原规则。桌面新任务使用 request UUID 作为独立 conversation_id，历史读取仍兼容旧的按身份和 mode 分组记录。Telegram 的分组与授权不变。

New 保留当前窗口非空草稿并生成新 UUID；Cancel 使用原生 HTML dialog 确认丢弃未提交输入。历史导航与任务执行分离，延迟的接受响应不能切走新会话。发送和回答沿用桥接幂等键及单操作锁，取消使用单独 cancel_task 动作和服务器确定的 local actor，不读取云连接凭据，也不被设置页面的耗时操作阻塞。

数字规则：每个非当前会话，若当前任务 status/changed_at 修订尚未在本窗口查看，显示 1；同一修订反复投递不加数。进入会话时确认最新修订。计数不是消息数，也不承诺跨窗口记住阅读进度。

## 停止链路

queued → save_pending(cancelled result) → cancelled；取消与执行器领取同用 SQLite BEGIN IMMEDIATE，已取消的任务不会被领取。

running → cancelling → 取消执行协程 → CodexAdapter finally 对该 worker 的独立进程组 SIGKILL 并 wait → save_pending(cancelled result) → cancelled。执行器持有内核锁直到清理和归档结束。多个服务实例通过同一持久化状态协调，不向数据库存下来的 PID 发信号。任务完成先写入 save_pending 后，迟到 Stop 只返回已有状态；取消先获胜时，后到结果不能覆盖 cancelling。

复用已有 CodexAdapter 进程组清理；回归测试会启动假的 worker 和子进程，验证取消后没有可运行的子进程，不调用真实模型。取消历史使用已有归档机制，保存失败只重试归档。旧任务、结果、消息及授权不做破坏性迁移。

## 验证

本地只进行源码阅读、Python AST 静态解析和 git diff --check。没有运行本地编译、构建、测试或冒烟脚本。远端结果待记录。

新增 tests/test_workspace_lifecycle.py 覆盖统一链接识别、独立会话、授权和重复取消、排队任务不再执行、运行取消等待清理、完成/取消竞态、归档失败重试、真实 OS worker/子进程回收和旧历史恢复。现有 ui-check 更新为在真实 WebKit 验证统一入口、New/Cancel、折叠侧栏、数字标记和 Send/Stop 生命周期，同时保留设置和窗口尺寸检查。

## 边界

草稿和阅读确认仅保留在当前窗口。Send 新建独立任务，原任务的澄清回答用 Continue task。历史加载不自动重建已经丢失的本地执行任务。Stop 不回滚已发生的文件或远端操作；强制终止整个宿主机进程或系统崩溃的恢复沿用现有 interrupted 处理。
