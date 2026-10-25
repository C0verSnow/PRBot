"""Local WebKit window with a validated native bridge and shared task service."""

import asyncio
import getpass
import json
import logging
import sys
from pathlib import Path
from threading import Thread

from prbot.codex_adapter import SDKFailure
from prbot.config import ConfigError


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


def cancel_desktop_task(request, actor):
    """Offline-capable cancellation; never load cloud credentials to stop work."""
    from prbot.conversation_service import ConversationService
    from prbot.conversation_store import LocalHistoryArchive

    return ConversationService(archive=LocalHistoryArchive(), values={}).cancel(
        request.get("task_id"), actor, "desktop"
    )


async def run_desktop_tasks(stop, runtime, factory, retry_delay=3):
    """Recover the executor after unexpected journal/archive errors."""
    while not stop.is_set():
        try:
            runtime["service"] = factory()
            runtime["executor"] = "running"
            await runtime["service"].run(stop, source="desktop")
        except Exception as error:
            runtime["executor"] = "reconnecting"
            # SQL/SDK exceptions may contain task content or credentials.
            logging.getLogger(__name__).error(
                "Desktop executor retry type=%s", type(error).__name__
            )
        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), retry_delay)
            except TimeoutError:
                pass


def show(check_output=None):
    # These imports are intentionally lazy: daemon and CLI never start AppKit/WebKit.
    import AppKit
    import objc
    import WebKit
    from Foundation import NSURL, NSObject
    from PyObjCTools import AppHelper

    from prbot import service
    from prbot.conversation_service import ConversationService
    from prbot.journal import initialize_journal
    from prbot.ui import ACTIONS, perform_action

    journal_path = initialize_journal()

    # Separate event loop owns the task runner; no model/network operation blocks AppKit.
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    runtime = {"service": None, "task": None}

    def background():
        asyncio.set_event_loop(loop)
        runtime["task"] = loop.create_task(run_desktop_tasks(stop, runtime, ConversationService))
        try:
            loop.run_until_complete(runtime["task"])
        except asyncio.CancelledError:
            pass

    worker_thread = Thread(target=background, daemon=True)
    worker_thread.start()
    service.migrate_registration()

    class WindowDelegate(NSObject):
        def windowWillClose_(self, notification):
            if loop.is_running() and not loop.is_closed():
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
        cached_status = {"state": "unknown"}

        @objc.python_method
        def deliver(self, message=None, ok=True, extra=None):
            payload = {"message": message, "ok": ok, "busy": self.busy}
            payload["status"] = self.cached_status
            payload.update(extra or {})
            self.web.evaluateJavaScript_completionHandler_(
                "window.receiveNative(" + json.dumps(payload, ensure_ascii=True) + ")", None
            )

        @objc.python_method
        def read_status(self):
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
                status = {
                    "user": getpass.getuser(),
                    "loaded": loaded,
                    "state": health,
                    "reported_at": report.get("time"),
                    "control_verified": time.time() - self.control_verified_at < 10,
                    "detail": report.get("detail", ""),
                    "workspace": state["workspace_root"],
                    "configuration": (
                        str(Path(state["env_file"]).parent) if state["env_file"] else ""
                    ),
                    "workspace_error": state.get("workspace_error", ""),
                    "executor": runtime.get("executor", "starting"),
                    "database": str(journal_path),
                }
            except Exception:
                status = {"state": "unavailable"}
            return status

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
                    "answer",
                    "cancel_task",
                    "history",
                    "retry_save",
                    "tasks",
                    "models",
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
            background_action = action in {
                "ready",
                "tasks",
                "history",
                "connections",
                "configuration",
                "cancel_task",
                "models",
            }
            if action in self.inflight:
                return
            if self.busy and not background_action:
                self.deliver(
                    "An operation is still in progress. Please wait.",
                    False,
                    {"completed_action": action, "bridge_id": body.get("bridge_id")},
                )
                return
            # JSON round trip prevents retaining PyObjC objects on a worker thread.
            try:
                request = native_request(body)
            except ConfigError as error:
                self.deliver(str(error), False, {"completed_action": action})
                return
            self.inflight.add(action)
            if not background_action:
                self.busy = True
            bridge_id = request.get("bridge_id")

            def finish(feedback, ok, extra):
                self.inflight.discard(action)
                if not background_action:
                    self.busy = False
                self.deliver(
                    feedback, ok, {**extra, "completed_action": action, "bridge_id": bridge_id}
                )

            def work():
                try:
                    extra = {}
                    feedback = None
                    if action == "open_github":
                        from prbot.conversation_service import target_link

                        link = request.get("text")
                        target_link(link, "Pull Request")
                        opened = AppKit.NSWorkspace.sharedWorkspace().openURL_(
                            NSURL.URLWithString_(link)
                        )
                        feedback = (
                            "Pull request opened." if opened else "Could not open pull request."
                        )
                        AppHelper.callAfter(finish, feedback, bool(opened), {})
                        return
                    if action in {"ready", "refresh_service"}:
                        import time

                        if action == "refresh_service":
                            self.control_verified_at = time.time()
                            feedback = "Service process status verified."
                        status = self.read_status()
                        AppHelper.callAfter(setattr, self, "cached_status", status)
                        extra["status"] = status
                    elif action in ACTIONS:
                        feedback = perform_action(action)
                    elif action in SETTINGS_ACTIONS:
                        feedback, extra = settings_request(action, request)
                    elif action == "models":
                        from prbot.codex_adapter import CodexAdapter
                        from prbot.execution_setup import codex_login

                        extra["model_catalog"] = asyncio.run(
                            CodexAdapter(codex_login()).list_models()
                        )
                    elif action == "cancel_task":
                        import time

                        task = cancel_desktop_task(request, "local:" + getpass.getuser())
                        extra["task_update"] = task
                        extra["tasks_revision"] = time.monotonic()
                        feedback = (
                            "Stop requested. Waiting for SDK process cleanup."
                            if task["status"] == "cancelling"
                            else "Task refreshed. Cancelled queued tasks will not execute."
                        )
                    else:
                        app_service = (
                            ConversationService.for_desktop_read()
                            if action in {"history", "tasks"}
                            else ConversationService()
                        )
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
                                    model=request.get("model"),
                                )
                            )
                            extra["accepted"] = task
                            feedback = "Task accepted."
                        elif action == "answer":
                            extra["accepted"] = asyncio.run(
                                app_service.answer(
                                    request.get("task_id"),
                                    request.get("request_id"),
                                    actor,
                                    "desktop",
                                    request.get("text"),
                                )
                            )
                            feedback = "Answer accepted. Continuing the original task."
                        elif action == "retry_save":
                            asyncio.run(app_service.retry_save(request.get("text"), actor))
                            feedback = "Message saved."
                        elif action == "history":
                            mode = request.get("mode")
                            if mode not in {"All", "Issue", "Pull Request", "Chat History"}:
                                raise ConfigError("Choose a task mode.")
                            extra["history"] = dict(
                                asyncio.run(
                                    app_service.desktop_history(
                                        actor,
                                        mode,
                                        request.get("cursor", "0"),
                                        session=request.get("session", ""),
                                    )
                                ),
                                mode=mode,
                                request_id=request.get("request_id"),
                            )
                        import time

                        extra["tasks_revision"] = time.monotonic()
                        extra["tasks"] = app_service.visible_tasks(actor)
                    AppHelper.callAfter(finish, feedback, True, extra)
                except (ConfigError, SDKFailure) as error:
                    extra = (
                        {"model_catalog": {"models": [], "error": str(error)}}
                        if action == "models"
                        else {
                            "history_error": {
                                "request_id": request.get("request_id"),
                                "message": str(error),
                            }
                        }
                        if action == "history"
                        else {}
                    )
                    AppHelper.callAfter(finish, str(error), False, extra)
                except Exception:
                    AppHelper.callAfter(
                        finish,
                        "Operation failed. Check connections and retry.",
                        False,
                        {
                            "history_error": {
                                "request_id": request.get("request_id"),
                                "message": "History unavailable. Retry loading messages.",
                            }
                        }
                        if action == "history"
                        else {
                            "model_catalog": {
                                "models": [],
                                "error": "Models unavailable. Check Codex sign-in and retry.",
                            }
                        }
                        if action == "models"
                        else {},
                    )

            Thread(target=work, daemon=True).start()

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
    # WKWebView relies on the responder chain's standard Edit menu for Cmd+C/V/X/A.
    menu = AppKit.NSMenu.alloc().init()
    application_item = AppKit.NSMenuItem.alloc().init()
    menu.addItem_(application_item)
    application_menu = AppKit.NSMenu.alloc().initWithTitle_("PRBot")
    application_item.setSubmenu_(application_menu)
    application_menu.addItemWithTitle_action_keyEquivalent_("Quit PRBot", "terminate:", "q")
    edit_item = AppKit.NSMenuItem.alloc().init()
    menu.addItem_(edit_item)
    edit = AppKit.NSMenu.alloc().initWithTitle_("Edit")
    edit_item.setSubmenu_(edit)
    for title, selector, key in (
        ("Undo", "undo:", "z"),
        ("Cut", "cut:", "x"),
        ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"),
        ("Select All", "selectAll:", "a"),
    ):
        edit.addItemWithTitle_action_keyEquivalent_(title, selector, key)
    app.setMainMenu_(menu)
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
    bridge.inflight = set()
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
