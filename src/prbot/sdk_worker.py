"""Private subprocess entry; clean environment, isolated auth, no executable tools."""

import json
import os
import sys
import tempfile
from pathlib import Path


def runtime_profile(workspace, auth_home, runtime):
    return "\n".join(
        [
            "(version 1)",
            "(deny default)",
            "(allow process-fork)",
            "(allow process-info* (target same-sandbox))",
            '(allow file-map-executable (subpath "/System") (subpath "/usr/lib") '
            f"(literal {json.dumps(runtime)}))",
            '(allow file-read* (literal "/") (subpath "/private/etc") '
            '(subpath "/private/var/db/timezone") (literal "/dev/random"))',
            '(allow file-read-data file-write-data (subpath "/dev/fd"))',
            '(allow system-mac-syscall (mac-policy-name "vnguard"))',
            '(allow system-mac-syscall (require-all (mac-policy-name "Sandbox") '
            "(mac-syscall-number 67)))",
            f"(allow process-exec (literal {json.dumps(runtime)}))",
            "(allow sysctl-read)",
            "(allow signal (target self))",
            "(allow mach-lookup)",
            "(allow network*)",
            "(allow file-read-metadata)",
            '(allow file-read* (subpath "/System") (subpath "/usr/lib") '
            '(subpath "/Library/Apple") (literal "/dev/null") (literal "/dev/urandom"))',
            f"(allow file-read* (subpath {json.dumps(str(workspace))}) "
            f"(subpath {json.dumps(str(auth_home))}) (literal {json.dumps(runtime)}))",
            f'(allow file-write* (subpath {json.dumps(str(auth_home))}) (literal "/dev/null"))',
        ]
    )


def run():
    from codex_cli_bin import bundled_codex_path
    from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

    request = json.load(sys.stdin)
    workspace = Path(request["path"]).resolve()
    runtime = str(bundled_codex_path().resolve())
    with tempfile.TemporaryDirectory(prefix="prbot-sdk-") as private:
        auth_home = Path(private).resolve()
        os.chmod(private, 0o700)
        auth = auth_home / "auth.json"
        auth.write_text(request["auth"])
        auth.chmod(0o600)
        # No shell/rg/Python/tool subprocess may execute. Only app-server itself can start.
        profile = runtime_profile(workspace, auth_home, runtime)
        config = CodexConfig(
            launch_args_override=(
                "/usr/bin/sandbox-exec",
                "-p",
                profile,
                runtime,
                "-c",
                "features.shell_tool=false",
                "-c",
                "features.apps=false",
                "-c",
                "features.apply_patch_freeform=false",
                "-c",
                'web_search="disabled"',
                "app-server",
                "--listen",
                "stdio://",
            ),
            cwd=str(workspace),
            env={
                "HOME": private,
                "CODEX_HOME": private,
                "TMPDIR": private,
                "PATH": "/usr/bin:/bin",
            },
        )
        with Codex(config) as codex:
            if request["prompt"] == "__prbot_runtime_check__":
                sys.stdout.write(json.dumps({"initialized": codex.metadata is not None}))
                return
            if request["prompt"] == "__prbot_account_check__":
                authenticated = codex.account(refresh_token=True).account is not None
                sys.stdout.write(
                    json.dumps(
                        {"result": {"authenticated": authenticated}, "auth": auth.read_text()}
                    )
                )
                return
            if codex.account().account is None:
                raise RuntimeError("Codex login required")
            thread = codex.thread_start(
                cwd=str(workspace),
                ephemeral=True,
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions="No tools. Use only supplied repository data. "
                "Return JSON matching the supplied schema.",
            )
            result = thread.run(request["prompt"], output_schema=request["schema"])
            if str(result.status.value) != "completed" or not result.final_response:
                raise RuntimeError("Codex turn did not complete")
            json.loads(result.final_response)
            sys.stdout.write(
                json.dumps({"result": json.loads(result.final_response), "auth": auth.read_text()})
            )


if __name__ == "__main__":
    run()
