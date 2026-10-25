# Issue #21：模型选择、统一消息卡片、滚动空白

## 改了什么

模型链路：Composer → Draft.model → 原生 submit_message → pending_submissions / requests.payload.model → CodexAdapter.invoke(model=...) → worker → Codex.thread_start(model=...)。

选项通过 bundled SDK 的 Codex.models(include_hidden=False) 读取，使用 model 字段作为真实参数，过滤 hidden、非文本模型和重复项。不硬编码模型名。目录读取不依赖 Redis 或 Telegram；失败时有重试入口，默认任务不依赖目录。当前 SDK 没有公开目录翻页参数，若返回下一页游标则明确报目录不完整，不静默展示截断列表。

请求参数只在受理时固定。原请求的并发提交、归档重试、重启后执行和澄清回答都使用原 payload。改选模型会创建新的草稿请求编号。旧 payload 没有 model 的任务继续使用原默认方式。执行前再次核对目录；模型不可用或参数无效不会回退。模型调用失败或者收到 model/rerouted 通知时停止，向用户返回可理解的安全错误，不把原始 RPC 错误或凭据带到界面。

MessageCard 和 MessageText 统一历史与实时卡片：角色、渠道、时间、留白、换行、长文本、fenced code。TaskResult 仅负责结构化结果正文，保留 summary、validation、delivery 和经过校验的 PR 按钮。异常 delivery 字段不会把对象直接交给 React。日志有自己的滚动区，完整最终结果放在日志外，使用主内容滚动查看。

## 滚动问题的实际证据

旧界面 + 回归脚本的远端 Actions：
https://github.com/C0verSnow/PRBot/actions/runs/40157636359

前置 TypeScript、构建、Ruff、282 项原 Python 测试通过。真实 WebKit 完成既有设置、会话切换、任务状态及日志交互后，新增断言失败：

```json
{"ok": false, "detail": "failed:Blank document scroll: top=155, height=915"}
```

窗口内容高 760 像素。失败截图显示侧栏和主区一起向上移动，底部出现约 155 像素的整条白带。问题在文档级滚动层，不能仅凭执行日志的 scrollTop 推断。最初的短页面断言曾把正常底部留白算成失败，已修正；这里引用的是修正断言后真实的文档高度越界，不能把早期夹具误判当根因证据。

修复将 body 固定在视口，使用 html overflow:clip 禁止文档滚动，并约束 #root 和 flex 应用外壳到窗口高度，主区和侧栏的 flex 子项允许缩小并各自滚动。overscroll-behavior 隔离各滚动区，避免滚动链传到文档；textarea 仍能在合理高度内调整，长输入在内部滚动。完整回复、历史、长代码和设置表单都保留真实滚动入口。ResizeObserver 只在日志已经跟随底部时保持跟随，查看旧日志时不改位置。

## 远端验收

全部运行由现有 macOS Actions 执行，本机只做 AST、Ruff 和差异检查，不运行编译、构建或测试。

- tests/test_model_selection.py：默认和旧 payload 兼容；选定模型经过受理、归档失败重试、重启和回答保持一致；拒绝不在目录的模型；控制器到 worker 的参数；SDK 真实方法签名；worker thread_start 参数及不可用、失败和改道停止。
- ui-check：模型目录渲染、选定模型提交、提交中锁定、重试保留、New 恢复默认；桌面 / Telegram / Codex 卡片、时间、代码和异常结构化数据；短内容与长任务的真实滚动高度；原生像素滚轮经过 WebKit responder，验证确实滚动后才检查边界；用户查看旧日志不跳转、回到底部继续跟随；最小窗口设置表单能滚到保存按钮。
- 源码 WebKit、DMG 安装、打包 WebKit 及 SDK 内置运行时沿用已有 CI。

最终业务代码为 `dd11890`，远端 [Actions 40158089013](https://github.com/C0verSnow/PRBot/actions/runs/40158089013) 全部必需检查通过：

- 297 项 Python 测试，Ruff、TypeScript 和前端构建通过。
- 源码和安装包内 WebKit 均通过默认及 760×580 双尺寸验收，UI JSON 都为 `ok:true`。原生像素滚轮实际推动主内容后验证边界，日志滚到末端不带动主内容；最小窗口仍能访问设置保存按钮。已实际查看首页、任务页和最小窗口截图。
- DMG 校验、挂载复制、内置 SDK 运行时及工作区配置检查通过。
- [源码截图与验收 JSON](https://github.com/C0verSnow/PRBot/actions/runs/40158089013/artifacts/12585578143)、[安装包内截图与验收 JSON](https://github.com/C0verSnow/PRBot/actions/runs/40158089013/artifacts/12585263855)、[DMG 下载](https://github.com/C0verSnow/PRBot/actions/runs/40158089013/artifacts/12585428648)。

Issue #21 已关闭，交付 PR 为 [#22](https://github.com/C0verSnow/PRBot/pull/22)，未自动合并。最后文档收尾不修改业务代码，最新分支状态以 PR Checks 为准。

## 明确的边界

[OpenAI 官方说明](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)指出 model/list 可能来自内置或缓存目录，不能单凭它保证账号有推理权限。因此界面每个选项明确标记 SDK catalog，并提示实际调用时确认账号访问权。PRBot 不会为列出目录而对每个模型发起收费推理，也不宣称所有目录项已通过真实账号调用。目录校验和实际推理拒绝是两种不同检查；调用拒绝会明确失败，不静默换模型。

本次常规 CI 使用 SDK 夹具和本机 WebKit，不会向真实提供商发送收费任务。鼠标像素滚轮由原生事件回归覆盖；实体触控板的手势阶段、惯性手感仍需硬件人工体验，不能由合成事件保证。
