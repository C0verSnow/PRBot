# Issue #25：模型刷新与连续对话

## 用户能看到的变化

- 点 Refresh models 后马上显示 Refreshing models…，按钮暂时禁用，仍然可以写草稿。成功时显示模型数量，即使列表没有变化也有反馈。
- 读取 SDK 模型目录的全部分页，按模型 ID 去重；空目录不再悄悄只留默认项。登录、网络、超时、发送失败都有可读原因与重试按钮。失败保留上次列表；应用新 ID token 时清空旧账户列表再加载。
- 模型仍由当前登录的 Codex 目录提供，发送时验证实际访问权限。目录不等于账户权限保证，不虚构模型，也不自动换模型。
- 历史、实时消息和最终回复放在同一条对话里，用页面本身滚动。时间、来源、投递状态附在消息上，不再用任务头分开两块滚动区域。向上读旧消息时不会强拉到底部。
- 对话完成状态使用 Codex 返回结果，不再自动读取 GitHub 后降级为 verification incomplete。旧任务保留原数据，但显示为 Delivered。
- 用户点 Verify delivery 才查 GitHub；等待、成功、失败、重试都在对应回复旁边显示。结果含查询时间、PR、Issue 和 CI 状态。查询只读，不改任务状态、不重发消息、不再次运行 Codex。已知 CI 失败或无效投递仍直接提示；旧快照默认折叠。主动查询得到新结果后，以新结果判断异常，旧失败警告不会覆盖最新成功状态。

## 原因与实现选择

固定的 openai-codex 0.163.0 中，Codex.models() 只传 includeHidden，没有 cursor 参数。旧代码看到 next_cursor 就抛出错误，丢弃已经读到的模型；空目录却返回成功。前端把刷新当后台操作，没有专门的加载状态，又让普通轮询清除全局提示，看起来像按钮没反应。

现在第一页仍走已有便利方法，后续页通过同一个已认证连接的 CodexClient.request 与同一响应类型读取。这个兼容点涉及 Codex._client，限定在一个函数内，增加固定 SDK 的契约检查。重复游标和超长分页会报错，不显示部分目录为完整结果。worker 只输出安全的错误类别，前端区分空目录、分页、登录和网络问题。

聊天原来是 HistoryBrowser 后接 TaskFeed，后者有 max-height 和独立滚动条。现在 TaskFeed 属于同一个历史区，滚动跟随由历史区观察主页面位置；最终回复只出现一次。

交付检查沿用已有 GitHub 只读查询，但移到显式 verify_delivery 原生请求。服务端重新校验任务可见范围、终止状态和 GitHub 任务类型，使用任务原来的连接配置，限定 45 秒。返回独立查询快照，不写入任务结果或历史。旧的判断函数只负责这次主动查询，不参与对话完成判定。

## 开源检索与借鉴

先在 GitHub 检索了 codex app-server 同类实现，核对维护状态、许可证与近期问题响应，没有复制外部实现。

| 候选 | 实现与依赖 | 维护、已知限制及取舍 |
| --- | --- | --- |
| [OpenAI Codex](https://github.com/openai/codex/tree/main/sdk/python)，Apache-2.0 | Rust app-server 提供 model/list；Python SDK 依赖 Pydantic、packaging 和固定 CLI runtime；游标与结果类型已经定义 | 2026-10-23 仍有提交，10-25 有 Issue 分类与跟进；本项目已经依赖它。便利方法缺少分页，选择复用底层请求，不引入新协议或依赖。 |
| [Agmente](https://github.com/rebornix/Agmente)，MIT | Swift iOS 客户端，通过 ACP/Codex WebSocket 展示历史和工具消息，需要 Xcode 与 Swift 包 | 最近推送 2026-05-31，近期维护证据不足，排除，不作为设计来源。 |
| [openclaw-codex-app-server](https://github.com/pwrdrvr/openclaw-codex-app-server)，MIT | OpenClaw 的 Codex 接入插件，需要另一套宿主环境 | 最近推送 2026-08-23；没有足够近期人工 Issue 响应证据，排除。 |
| [CodexBridge](https://github.com/Gan-Xing/CodexBridge) | 独立桥接层 | 仓库接口未返回明确许可证，排除，不复用代码。 |

采用 OpenAI Codex 已验证的模型 slug、分页和类型校验设计；兼容代码自行实现并在函数注释注明来源。参考[官方 model/list 文档](https://developers.openai.com/codex/app-server#list-models-modellist)，实际接口以固定 0.163.0 wheel 源码为准。

## 验收与证据

本地只读代码、解析 Python AST 与检查差异，没有执行构建、编译、测试或冒烟脚本。本机未安装 ruff，lint 也交给远端 CI。

修复前先新增回归场景并提交：[远端失败记录](https://github.com/C0verSnow/PRBot/actions/runs/40187826488)，提交 bd3187f。三个失败对应：第二页直接报目录不完整、空目录不报错、重复游标未被识别。

远端回归覆盖：多页去重、空目录、游标循环、固定 SDK 请求契约、选中模型传到实际 thread_start、失败/切换/回答保留模型、默认模型不覆写 SDK 默认值；完成后不自动查询 GitHub、主动验证不写状态/历史、不重复调用 Codex、不可见任务/运行中任务/私聊拒绝验证。

macOS WebKit 操作覆盖：点击刷新立即等待、防重入、普通轮询不清除等待、失败保留目录、空列表可重试、相同目录成功提示、选模型并发送、单一对话滚动、阅读位置保持、贴近底部跟随、用户主动验证的等待/失败/重试/成功、验证后任务状态不变、桥接发送失败恢复按钮。现有 CI 还执行 TypeScript 检查、前端构建、Python 全套测试、原生窗口截图、DMG 打包及安装包检查。

修复后远端验收已通过：[运行 40188814909](https://github.com/C0verSnow/PRBot/actions/runs/40188814909)，代码提交 56e4355。

- Python 全套 318 个测试通过，0 失败、0 跳过；依赖检查、ruff、TypeScript 和前端构建通过。
- 原生 WebKit 操作通过，标准窗口与最小窗口均有截图：[源码界面证据](https://github.com/C0verSnow/PRBot/actions/runs/40188814909/artifacts/12596537650)。
- DMG 构建、挂载后安装包验证及安装包内 WebKit 检查均通过：[安装包界面证据](https://github.com/C0verSnow/PRBot/actions/runs/40188814909/artifacts/12596687722)、[DMG 下载](https://github.com/C0verSnow/PRBot/actions/runs/40188814909/artifacts/12596682568)。
- 联调中补齐原生焦点防滚动等待与心跳状态隔离，避免测试还未开始向上阅读就被焦点拉回，或测试状态被真实轮询覆盖。主滚动区关闭浏览器滚动锚定，跟随逻辑保持由页面统一控制。
- [PR #26](https://github.com/C0verSnow/PRBot/pull/26) 已提交，issue #25 已按要求关闭，PR 保持打开等待审阅。

模拟目录和 GitHub 响应不代表真实账户每个模型都可用；本次未使用用户凭据发起付费模型推理。可选的真实 provider acceptance 作业未启用，不能把模拟操作验收当作生产账户的模型授权证明。


最终边界补充也已完成：[运行 40189453278](https://github.com/C0verSnow/PRBot/actions/runs/40189453278)，功能提交 8dfd4aa。318 个 Python 测试、原生界面与安装包检查再次全部通过，包括旧失败快照被新成功查询替代的场景，以及相同状态测量期间暂存并重放其他原生回执的测试隔离。

最新证据：[源码界面](https://github.com/C0verSnow/PRBot/actions/runs/40189453278/artifacts/12597179207)、[安装包界面](https://github.com/C0verSnow/PRBot/actions/runs/40189453278/artifacts/12596998086)、[DMG 下载](https://github.com/C0verSnow/PRBot/actions/runs/40189453278/artifacts/12596558618)。后续仅补充文档与任务清单记录。
