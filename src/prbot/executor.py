"""Durable queue, bounded workers and remote-validation delivery state machine."""

import asyncio
import time

from telegram.error import BadRequest, Forbidden, TelegramError

from prbot.github import DeliveryError
from prbot.workspace import apply_edits, git, inspect_diff, prepare


class Executor:
    def __init__(self, store, settings, github, codex, token):
        self.store, self.settings, self.github = store, settings, github
        self.codex, self.token = codex, token
        self.active = {}
        self.stopping = False

    async def recover(self):
        # Never rerun an SDK turn after an ambiguous crash. Preserve remote evidence.
        for task in self.store.list_status("running", "validating", "publishing"):
            try:
                if task.get("commit_sha"):
                    prs = await self.github.request(
                        "GET",
                        f"/repos/{task['repo']}/pulls",
                        params={
                            "head": task["repo"].split("/")[0] + ":" + task["branch"],
                            "state": "all",
                        },
                    )
                    if prs:
                        self.store.change(task["id"], task["status"], pr_url=prs[0]["html_url"])
            except DeliveryError:
                pass
            self.store.change(
                task["id"],
                "interrupted",
                error=(
                    "服务中断，已停止自动重跑。请核对任务工作区、远端分支和 PR 后重新提交需求。"
                ),
            )
            self.store.notify(self.store.get(task["id"]))

    async def execute(self, task):
        tid = task["id"]
        stage = "running"
        self.store.change(tid, stage)
        try:
            repository = await self.github.repository(task["repo"])
            self.store.change(tid, stage, repository=repository, read_at=time.time())
            task = self.store.get(tid)
            path, sha = await prepare(task, self.settings, self.token)
            self.store.change(tid, stage, workspace=str(path), base_sha=sha)
            proposal = await self.codex.execute(path, task, self.settings.timeout)
            if proposal["clarification"]:
                raise DeliveryError("需要澄清：" + proposal["clarification"][:1000])
            apply_edits(path, proposal)
            diff, files = await inspect_diff(path, task, (self.token, self.codex.auth))
            stage = "validating"
            self.store.change(tid, stage, summary=proposal["summary"][:1000], files=files)
            review = await self.codex.review(path, task, diff, self.settings.timeout)
            if not review["passed"]:
                raise DeliveryError("交付物检查失败：" + review["reason"][:1000])
            await git(
                path,
                "-c",
                "user.name=PRBot",
                "-c",
                "user.email=prbot@users.noreply.github.com",
                "commit",
                "-m",
                f"Implement PRBot task {tid}",
            )
            commit = await git(path, "rev-parse", "HEAD")
            self.store.change(
                tid,
                stage,
                commit_sha=commit,
                validation="静态差异和独立需求检查通过；等待远端必需检查",
            )
            # No force push. On uncertain push, reconcile the exact remote commit first.
            try:
                await git(
                    path, "push", "origin", f"HEAD:refs/heads/{task['branch']}", token=self.token
                )
            except DeliveryError:
                remote = await git(
                    path, "ls-remote", "origin", f"refs/heads/{task['branch']}", token=self.token
                )
                if remote.split()[0:1] != [commit]:
                    raise
            task = self.store.get(tid)
            body = self.body(task)
            pr = await self.github.pull_request(task, body)
            self.store.change(tid, stage, pr_url=pr["html_url"], pr_number=pr["number"])
            task = self.store.get(tid)
            while True:
                try:
                    await self.github.wait_checks(
                        task, self.settings.required_checks, self.settings.ci_timeout
                    )
                    break
                except TimeoutError:
                    self.store.change(
                        tid, "validating", error="远端检查尚未结束，继续等待草稿 PR。"
                    )
                    self.store.notify(self.store.get(tid))
            stage = "publishing"
            self.store.change(tid, stage, validation="静态差异、需求检查及远端必需检查全部通过")
            task = self.store.get(tid)
            await self.github.request(
                "PATCH",
                f"/repos/{task['repo']}/pulls/{pr['number']}",
                json={"body": self.body(task)},
            )
            await self.github.ready(task["repo"], pr["number"], task["commit_sha"])
            source = task["source"]
            if source["kind"] == "issue" and source.get("number"):
                result = await self.github.close_issue(task["repo"], source["number"])
                self.store.change(
                    tid,
                    stage,
                    issue_result=f"https://github.com/{task['repo']}/issues/{source['number']}：{result}",
                )
            self.store.change(tid, "succeeded", error=None)
        except asyncio.CancelledError:
            self.store.change(
                tid, "interrupted", error=f"{stage} 阶段服务停止；需人工核对后续处理。"
            )
            raise
        except TimeoutError:
            # CI timeout retains validating; it is never relabeled as successful delivery.
            if stage == "validating" and self.store.get(tid).get("pr_url"):
                self.store.change(tid, "validating", error="远端检查尚未结束，请查看草稿 PR。")
            else:
                self.store.change(tid, "failed", error=f"{stage} 阶段 Codex 执行超时，请重试。")
        except Exception as error:
            reason = str(error) if isinstance(error, DeliveryError) else type(error).__name__
            self.store.change(tid, "failed", error=f"{stage} 阶段失败：{reason}")
        finally:
            self.store.notify(self.store.get(tid))

    def body(self, task):
        body = (
            f"任务：{task['id']}\n\n变更：{task.get('summary', '')}\n\n"
            f"验证：{task.get('validation', '等待验证')}\n\n"
            f"起始提交：{task.get('base_sha')}\n交付提交：{task.get('commit_sha')}\n\n"
            "未完成：真实设备验收由管理员单独记录；此 PR 不自动合并。"
        )
        if task["source"]["kind"] == "issue":
            body += f"\n\n关联 Issue：https://github.com/{task['repo']}/issues/{task['source']['number']}"
        return body

    async def run(self, stop):
        await self.recover()
        try:
            while not stop.is_set():
                self.active = {key: t for key, t in self.active.items() if not t.done()}
                for task in self.store.list_status("queued"):
                    if len(self.active) >= self.settings.concurrency:
                        break
                    if task["id"] not in self.active:
                        self.active[task["id"]] = asyncio.create_task(self.execute(task))
                try:
                    await asyncio.wait_for(stop.wait(), 0.5)
                except TimeoutError:
                    pass
        finally:
            self.stopping = True
            for task in self.active.values():
                task.cancel()
            await asyncio.gather(*self.active.values(), return_exceptions=True)


async def send_outbox(store, bot, stop):
    while not stop.is_set():
        rows = store.db.execute(
            "SELECT id,chat_id,thread_id,text,attempts FROM task_outbox "
            "WHERE status='pending' AND attempts<5"
        ).fetchall()
        for tid, chat, thread, text, attempts in rows:
            status = "pending"
            try:
                await bot.send_message(chat_id=chat, message_thread_id=thread, text=text)
                status = "delivered"
            except (Forbidden, BadRequest):
                status = "rejected"
            except TelegramError:
                if attempts >= 4:
                    status = "exhausted"
            with store.db:
                store.db.execute(
                    "UPDATE task_outbox SET status=?,attempts=attempts+1 WHERE id=?", (status, tid)
                )
        try:
            await asyncio.wait_for(stop.wait(), 5)
        except TimeoutError:
            pass
