"""Local WebKit window with a validated native bridge and shared task service."""

import asyncio
import getpass
import json
import sys
from pathlib import Path
from threading import Thread

from prbot.config import ConfigError, env_path


def asset_directory():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "ui"
    return Path(__file__).resolve().parents[2] / "frontend/dist"


SETTINGS_ACTIONS = {
    "connections",
    "use_id_token",
    "fetch_codex_id_token",
    "configuration",
    "save_configuration",
}


def native_request(body):
    """Copy nested Foundation dictionaries before passing a request to a thread."""

    def mapping(value):
        if hasattr(value, "items"):
            return dict(value.items())
        raise TypeError

    try:
        return json.loads(json.dumps(dict(body), default=mapping))
    except (TypeError, ValueError, OverflowError):
        raise ConfigError("Invalid request data. Reload the window and retry.") from None


def settings_request(action, request):
    """Settings operations shared by the validated native bridge and remote tests."""
    from prbot.config import bootstrap_form, save_bootstrap_form
    from prbot.execution_setup import codex_login, fetch_codex_id_token
    from prbot.secrets import bootstrap, clear_cache, load_secrets

    if action == "fetch_codex_id_token":
        return "ID token fetched. Choose Use token to apply it in this window.", {
            "id_token_result": {"token": fetch_codex_id_token(), "applied": False}
        }
    if action == "use_id_token":
        token = request.get("text")
        if not isinstance(token, str) or not token.strip():
            raise ConfigError("Enter a Codex ID token before choosing Use token.")
        auth = json.loads(codex_login(token))
        return "ID token applied in this window only.", {
            "id_token_result": {"token": auth["tokens"]["id_token"], "applied": True}
        }
    if action == "configuration":
        return "Configuration loaded. Changes are saved only when you choose Save configuration.", {
            "configuration": bootstrap_form()
        }
    if action == "save_configuration":
        form = save_bootstrap_form(request.get("configuration"), request.get("configuration_path"))
        clear_cache()
        return (
            "Configuration saved. Restart PRBot to apply it to all services. "
            "Process environment variables keep priority.",
            {"configuration": form},
        )
    if action == "connections":
        auth = codex_login()
        load_secrets(refresh=True)
        cfg = bootstrap()
        return "Connections refreshed. ID token changes apply in this window only.", {
            "connections": {
                "id_token": json.loads(auth)["tokens"]["id_token"],
                "infisical": "Connected",
                "project": cfg["INFISICAL_PROJECT_ID"],
            }
        }
    raise ConfigError("Unsupported settings request.")


def show(check_output=None):
    # These imports are intentionally lazy: daemon and CLI never start AppKit/WebKit.
    import AppKit
    import objc
    import WebKit
    from Foundation import NSURL, NSObject
    from PyObjCTools import AppHelper

    from prbot import service
    from prbot.conversation_service import ConversationService
    from prbot.ui import ACTIONS, perform_action

    # Separate event loop owns the task runner; no model/network operation blocks AppKit.
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    runtime = {"service": None, "task": None}

    async def run_tasks():
        while not stop.is_set():
            try:
                runtime["service"] = ConversationService()
                await runtime["service"].run(stop, source="desktop")
            except ConfigError:
                await asyncio.sleep(3)

    def background():
        asyncio.set_event_loop(loop)
        runtime["task"] = loop.create_task(run_tasks())
        try:
            loop.run_until_complete(runtime["task"])
        except asyncio.CancelledError:
            pass

    worker_thread = Thread(target=background, daemon=True)
    worker_thread.start()
    service.migrate_registration()

    class WindowDelegate(NSObject):
        def windowWillClose_(self, notification):
            loop.call_soon_threadsafe(stop.set)
            if runtime["task"]:
                loop.call_soon_threadsafe(runtime["task"].cancel)
            worker_thread.join(timeout=5)
            AppKit.NSApplication.sharedApplication().terminate_(None)

    # Register the block ABI on our delegate explicitly. The packaged runtime on
    # macOS 14 otherwise receives an untyped navigation decision block.
    objc.registerMetaDataForSelector(
        b"NSObject",
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

    class Bridge(NSObject):
        busy = False
        control_verified_at = 0

        @objc.python_method
        def deliver(self, message=None, ok=True, extra=None):
            payload = {"message": message, "ok": ok, "busy": self.busy}
            try:
                state = service.status()
                report = state.get("last_service_report", {})
                import time

                loaded = bool(state["agent_loaded"])
                health = report.get("state", "unknown")
                if not loaded and health not in {"halted", "crashed"}:
                    health = "stopped"
                if loaded and time.time() - report.get("time", 0) > 15:
                    health = "stale"
                payload["status"] = {
                    "user": getpass.getuser(),
                    "loaded": loaded,
                    "state": health,
                    "reported_at": report.get("time"),
                    "control_verified": time.time() - self.control_verified_at < 10,
                    "detail": report.get("detail", ""),
                    "workspace": state["workspace_root"],
                    "configuration": str(env_path().parent),
                }
            except Exception:
                payload["status"] = {"state": "unavailable"}
            payload.update(extra or {})
            self.web.evaluateJavaScript_completionHandler_(
                "window.receiveNative(" + json.dumps(payload, ensure_ascii=True) + ")", None
            )

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            if not message.frameInfo().isMainFrame():
                return
            if self.web.URL() is None or self.web.URL().path() != str(self.page):
                return
            body = message.body()
            allowed = (
                ACTIONS
                | SETTINGS_ACTIONS
                | {
                    "ready",
                    "open_github",
                    "refresh_service",
                    "submit_message",
                    "history",
                    "retry_save",
                    "tasks",
                }
            )
            if (
                not hasattr(body, "get")
                or not isinstance(body.get("action"), str)
                or body.get("action") not in allowed
            ):
                self.deliver("Unsupported request.", False)
                return
            action = body.get("action")
            if action == "refresh_service":
                import time

                self.control_verified_at = time.time()
                self.deliver("Service process status verified.", extra={"completed_action": action})
                return
            if action == "ready":
                self.deliver()
                return
            if self.busy:
                self.deliver(
                    "An operation is still in progress. Please wait.",
                    False,
                    {"completed_action": action},
                )
                return
            # JSON round trip prevents retaining PyObjC objects on a worker thread.
            try:
                request = native_request(body)
            except ConfigError as error:
                self.deliver(str(error), False, {"completed_action": action})
                return
            self.active_action = action
            self.busy = True

            def work():
                try:
                    extra = {}
                    feedback = None
                    if action == "open_github":
                        from prbot.conversation_service import target_link

                        link = request.get("text")
                        target_link(link, "Pull Request")
                        AppHelper.callAfter(self.open_github, link)
                        return
                    if action in ACTIONS:
                        feedback = perform_action(action)
                    elif action in SETTINGS_ACTIONS:
                        feedback, extra = settings_request(action, request)
                    else:
                        app_service = ConversationService()
                        actor = "local:" + getpass.getuser()
                        if action == "submit_message":
                            task = asyncio.run(
                                app_service.submit(
                                    request.get("request_id"),
                                    actor,
                                    "desktop",
                                    request.get("mode"),
                                    request.get("link"),
                                    request.get("text"),
                                )
                            )
                            extra["accepted"] = task
                            feedback = "Task accepted."
                        elif action == "retry_save":
                            asyncio.run(app_service.retry_save(request.get("text"), actor))
                            feedback = "Message saved."
                        elif action == "history":
                            mode = request.get("mode")
                            if mode not in {"Issue", "Pull Request"}:
                                raise ConfigError("Choose a task mode.")
                            cid = app_service.conversation(actor, "desktop", mode)
                            extra["history"] = dict(
                                asyncio.run(
                                    app_service.archive.history(cid, request.get("cursor", "0"))
                                ),
                                mode=mode,
                            )
                        extra["tasks"] = app_service.tasks(actor)
                    AppHelper.callAfter(self.finish, feedback, True, extra)
                except ConfigError as error:
                    AppHelper.callAfter(self.finish, str(error), False, {})
                except Exception:
                    AppHelper.callAfter(
                        self.finish, "Operation failed. Check connections and retry.", False, {}
                    )

            Thread(target=work, daemon=True).start()

        @objc.python_method
        def open_github(self, link):
            opened = AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(link))
            self.finish(
                "Pull request opened." if opened else "Could not open the pull request.",
                bool(opened),
                {},
            )

        @objc.python_method
        def finish(self, message, ok, extra):
            self.busy = False
            self.deliver(message, ok, {**extra, "completed_action": self.active_action})
            self.active_action = None

        @objc.typedSelector(b"v@:@@@?")
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
