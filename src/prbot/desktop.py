"""Local-only macOS window. Credentials stay in native dialogs and the existing stores."""

import asyncio
import getpass
import json
import re
import sys
from pathlib import Path
from threading import Thread

from prbot.config import Config, ConfigError
from prbot.main import validate_api
from prbot.tasks import repository_url


def asset_directory():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "ui"
    return Path(__file__).resolve().parents[2] / "frontend/dist"


def prepare_draft(value):
    """Prepare Telegram text; never impersonate a Telegram update or bypass its policy."""
    if not isinstance(value, str) or len(value) > 3500:
        raise ConfigError("请输入仓库链接和需求，合计不超过 3500 字。")
    parts = value.strip().split(maxsplit=1)
    try:
        repo = repository_url(parts[0] if parts else "")
    except ValueError as error:
        raise ConfigError(str(error)) from None
    if len(parts) != 2 or len(parts[1].strip()) < 8:
        raise ConfigError("请在仓库链接后填写至少 8 个字的具体需求。")
    return f"https://github.com/{repo}\n{parts[1].strip()}"


def bot_url():
    username = asyncio.run(validate_api(Config.load().token))
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise ConfigError("无法识别 Bot 用户名，请检查 Telegram 配置。")
    return "https://t.me/" + username


def show(check_output=None):
    # These imports are intentionally lazy: daemon and CLI never start AppKit/WebKit.
    import AppKit
    import objc
    import WebKit
    from Foundation import NSURL, NSObject
    from PyObjCTools import AppHelper

    from prbot import service
    from prbot.ui import ACTIONS, perform_action

    class WindowDelegate(NSObject):
        def windowWillClose_(self, notification):
            AppKit.NSApplication.sharedApplication().terminate_(None)

    # Register the block ABI on our delegate explicitly. The packaged runtime on
    # macOS 14 otherwise receives an untyped navigation decision block.
    objc.registerMetaDataForSelector(
        b"Bridge",
        b"webView:decidePolicyForNavigationAction:decisionHandler:",
        {
            "arguments": {
                4: {
                    "type": b"@?",
                    "callable": {
                        "retval": {"type": b"v"},
                        "arguments": {0: {"type": b"^v"}, 1: {"type": b"q"}},
                    },
                }
            }
        },
    )

    class Bridge(
        NSObject,
        protocols=[
            objc.protocolNamed("WKScriptMessageHandler"),
            objc.protocolNamed("WKNavigationDelegate"),
        ],
    ):
        busy = False

        @objc.python_method
        def deliver(self, message, ok=True):
            payload = {"message": message, "ok": ok, "busy": self.busy}
            try:
                state = service.status()
                # Allow only known safe status fields across the bridge. No settings or tokens.
                report = state.get("last_service_report", {})
                payload["status"] = {
                    "user": getpass.getuser(),
                    "loaded": bool(state["agent_loaded"]),
                    "autostart": bool(state["autostart"]),
                    "state": report.get("state", "unknown"),
                    "workspace": state["workspace_root"],
                }
            except Exception:
                payload["status"] = {"state": "unavailable"}
            self.web.evaluateJavaScript_completionHandler_(
                "window.receiveNative(" + json.dumps(payload, ensure_ascii=True) + ")", None
            )

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            # Accept only the bundled top-level document, not subframes or remote content.
            if not message.frameInfo().isMainFrame():
                return
            if self.web.URL() is None or self.web.URL().path() != str(self.page):
                return
            body = message.body()
            if not hasattr(body, "get"):
                self.deliver("不支持的请求。", False)
                return
            action = body.get("action")
            if not isinstance(action, str) or action not in ACTIONS | {"ready", "draft"}:
                self.deliver("不支持的操作。", False)
                return
            if action == "ready":
                self.deliver(None)
                return
            if self.busy:
                self.deliver("上一个操作仍在进行，请稍候。", False)
                return
            draft = None
            if action == "draft":
                try:
                    draft = prepare_draft(body.get("text"))
                except ConfigError as error:
                    self.deliver(str(error), False)
                    return
            self.busy = True

            def work():
                try:
                    if action == "draft":
                        url = bot_url()
                        AppHelper.callAfter(self.handoff, draft, url)
                    else:
                        perform_action(action)
                        AppHelper.callAfter(self.finish, "操作已结束；服务状态已刷新。", True)
                except ConfigError as error:
                    AppHelper.callAfter(self.finish, str(error), False)
                except Exception:
                    # Never expose raw exceptions from OS, SDK or credential providers.
                    AppHelper.callAfter(
                        self.finish, "操作未完成，请检查配置、网络或系统权限后重试。", False
                    )

            Thread(target=work, daemon=True).start()

        @objc.python_method
        def handoff(self, draft, url):
            clipboard = AppKit.NSPasteboard.generalPasteboard()
            clipboard.clearContents()
            copied = clipboard.setString_forType_(draft, AppKit.NSPasteboardTypeString)
            opened = AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
            if not copied:
                self.finish("无法复制需求，请手动复制并在 Telegram 提交。", False)
            elif not opened:
                self.finish("需求已复制；无法打开 Telegram，请手动打开 Bot。", False)
            else:
                self.finish(
                    "需求已复制并打开 Bot。先发送 /FinishIssue，收到提示后粘贴需求。"
                    "当前尚未提交任务，Telegram 会校验身份及仓库权限。",
                    True,
                )

        @objc.python_method
        def finish(self, message, ok):
            self.busy = False
            self.deliver(message, ok)

        def webView_decidePolicyForNavigationAction_decisionHandler_(
            self, web, navigation, handler
        ):
            url = navigation.request().URL()
            allowed = url.isFileURL() and url.path() == str(self.page)
            handler(
                WebKit.WKNavigationActionPolicyAllow
                if allowed
                else WebKit.WKNavigationActionPolicyCancel
            )

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    style = (
        AppKit.NSWindowStyleMaskTitled
        | AppKit.NSWindowStyleMaskClosable
        | AppKit.NSWindowStyleMaskMiniaturizable
        | AppKit.NSWindowStyleMaskResizable
    )
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (1120, 760)), style, AppKit.NSBackingStoreBuffered, False
    )
    window.setTitle_("PRBot")
    window.setMinSize_((760, 580))
    window.center()
    delegate = WindowDelegate.alloc().init()
    window.setDelegate_(delegate)
    bridge = Bridge.alloc().init()
    bridge.page = (asset_directory() / "index.html").resolve()
    config = WebKit.WKWebViewConfiguration.alloc().init()
    config.setWebsiteDataStore_(WebKit.WKWebsiteDataStore.nonPersistentDataStore())
    config.userContentController().addScriptMessageHandler_name_(bridge, "prbot")
    web = WebKit.WKWebView.alloc().initWithFrame_configuration_(
        window.contentView().bounds(), config
    )
    bridge.web = web
    web.setNavigationDelegate_(bridge)
    web.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    window.contentView().addSubview_(web)
    web.loadFileURL_allowingReadAccessToURL_(
        NSURL.fileURLWithPath_(str(bridge.page)),
        NSURL.fileURLWithPath_(str(asset_directory().resolve())),
    )
    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    if check_output:
        from prbot.ui_check import schedule_check

        schedule_check(web, check_output)
    AppHelper.runEventLoop()
