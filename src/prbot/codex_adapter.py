"""Codex subprocess lifecycle and safe error boundary."""

import asyncio
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

from prbot.config import ConfigError
from prbot.github import DeliveryError


class SDKFailure(DeliveryError):
    def __init__(self, code, stage="controller", exception_type="Exception"):
        self.code, self.stage, self.exception_type = code, stage, exception_type
        advice = {
            "authentication": "Sign in with Codex again, then refresh connections.",
            "account_missing": "No Codex account found. Sign in and refresh connections.",
            "timeout": "Check timed out. Check network and sign-in, then retry.",
            "network": "Account connection failed. Check network and retry.",
            "dependencies": "SDK dependencies unavailable. Reinstall the remote build.",
            "runtime": "Runtime unavailable. Reinstall the application and check permissions.",
            "initialize": "Runtime initialization failed. Check installation and system "
            "permissions.",
            "auth": "Sign-in could not be read. Sign in with Codex and retry.",
            "auth_storage": "Sign-in refresh failed. Sign in with Codex again.",
            "launch": "SDK could not start. Check installation and executable permissions.",
            "protocol": "Unexpected SDK response. Reinstall the application and inspect logs.",
            "failure": "SDK check failed. Inspect the stage and error type in logs.",
        }
        super().__init__(
            f"Codex [{code}]: {advice.get(code, advice['failure'])}"
            f" (stage={stage}, type={exception_type})"
        )


def worker_failure(output):
    try:
        error = json.loads(output.splitlines()[-1])["error"]
        stage = error["stage"]
        kind = error["kind"]
        name = error["type"]
        if stage not in {
            "dependencies",
            "request",
            "runtime",
            "auth",
            "initialize",
            "account",
            "turn",
        }:
            raise ValueError
        if kind not in {"failure", "authentication", "network", "timeout"}:
            raise ValueError
        if name not in {
            "FileNotFoundError",
            "PermissionError",
            "TimeoutError",
            "RuntimeError",
            "ValueError",
            "JSONDecodeError",
            "ImportError",
            "ModuleNotFoundError",
            "CodexError",
            "JsonRpcError",
            "TransportClosedError",
            "CodexRpcError",
            "ParseError",
            "InvalidRequestError",
            "MethodNotFoundError",
            "InvalidParamsError",
            "InternalRpcError",
            "ServerBusyError",
            "RetryLimitExceededError",
            "OSError",
            "Exception",
        }:
            raise ValueError
        code = kind if kind != "failure" else stage
        if code not in {"dependencies", "runtime", "auth", "initialize"} and kind == "failure":
            code = "failure"
        return SDKFailure(code, stage, name)
    except (ValueError, TypeError, KeyError, IndexError):
        return SDKFailure("failure", "worker", "ProcessExit")


def parent_rules(path):
    rules = []
    for parent in reversed(path.resolve().parents):
        candidate = parent / "AGENTS.md"
        if candidate.is_symlink():
            raise DeliveryError("Parent AGENTS.md is a symlink. Review it before running tasks.")
        if candidate.is_file():
            if candidate.stat().st_size > 100000:
                raise DeliveryError(
                    "Parent repository rules exceed the size limit. Review them first."
                )
            rules.append({"path": str(candidate), "instructions": candidate.read_text()})
    return rules


class CodexAdapter:
    def __init__(self, auth, environment=None, on_event=None):
        self.auth = auth
        self.environment = environment or {}
        self.on_event = on_event

    async def invoke(self, path, prompt, schema, timeout):
        # The parent owns cleanup even when cancellation must SIGKILL the worker.
        with tempfile.TemporaryDirectory(prefix="prbot-sdk-controller-") as private:
            return await self._invoke(path, prompt, schema, timeout, private)

    async def _invoke(self, path, prompt, schema, timeout, private):
        original_auth = self.auth
        command = (
            [sys.executable, "sdk-worker"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "prbot.sdk_worker"]
        )
        env = {**os.environ, "LANG": "en_US.UTF-8"}
        # Operational secrets belong to the backend, never the SDK process environment.
        for key in list(env):
            if (
                key.startswith(("INFISICAL_", "UPSTASH_", "PRBOT_ACCEPTANCE_"))
                or key == "TELEGRAM_BOT_TOKEN"
            ):
                env.pop(key)
        env.update(self.environment)
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
        if not getattr(sys, "frozen", False):
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                env=env,
                cwd=path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            raise SDKFailure("launch", "controller", type(error).__name__) from None
        try:
            async with asyncio.timeout(timeout):
                request = (
                    json.dumps(
                        {
                            "auth": original_auth,
                            "private_home": private,
                            "path": str(path),
                            "prompt": prompt,
                            "schema": schema,
                        }
                    ).encode()
                    + b"\n"
                )
                if self.on_event is None:
                    out, _ = await proc.communicate(request)
                else:
                    proc.stdin.write(request)
                    await proc.stdin.drain()
                    proc.stdin.close()

                    # Drain stderr without retaining potentially sensitive diagnostics.
                    async def discard():
                        while await proc.stderr.read(8192):
                            pass

                    drain = asyncio.create_task(discard())
                    try:
                        out = b""
                        while line := await proc.stdout.readline():
                            event = json.loads(line)
                            if event.get("type") in {"progress", "text"}:
                                await self.on_event(event)
                            else:
                                out = line
                        await proc.wait()
                    finally:
                        drain.cancel()
                        await asyncio.gather(drain, return_exceptions=True)
            if proc.returncode:
                raise worker_failure(out)
            response = json.loads(out.splitlines()[-1])
            if prompt == "__prbot_runtime_check__":
                return response
            if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
                raise ValueError
            updated = response["auth"]
            if not isinstance(updated, str):
                raise ValueError
            if prompt == "__prbot_account_check__" and not response["result"].get("authenticated"):
                return response["result"]
            if updated != original_auth and self.auth == original_auth:
                from prbot.execution_setup import retain_refreshed_login

                retain_refreshed_login(original_auth, updated)
                self.auth = updated
            return response["result"]
        except (ValueError, UnicodeError, KeyError, TypeError):
            raise SDKFailure("protocol") from None
        finally:
            # Includes the app-server: cancellation/timeout must not leave paid work running.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()

    async def check_auth(self):
        from prbot.paths import data_dir

        try:
            path = data_dir() / "sdk-startup"
            path.mkdir(mode=0o700, exist_ok=True)
            result = await self.invoke(path, "__prbot_account_check__", {}, 30)
        except TimeoutError:
            raise SDKFailure("timeout", "startup", "TimeoutError") from None
        except ConfigError:
            raise SDKFailure("auth_storage") from None
        except OSError as error:
            raise SDKFailure("auth", "startup", type(error).__name__) from None
        if not result.get("authenticated"):
            raise SDKFailure("account_missing", "account")
