"""GitHub operations: reconcile before retrying externally visible writes."""

import asyncio

import httpx


class DeliveryError(Exception):
    pass


class GitHub:
    def __init__(self, token):
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            follow_redirects=False,
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}
        except httpx.HTTPError:
            # URLs/response bodies may include private task context; never echo raw exceptions.
            raise DeliveryError("GitHub 请求未确认，请检查网络、凭据和目标仓库权限。") from None

    async def repository(self, repo):
        result = await self.request("GET", f"/repos/{repo}")
        if (
            result.get("archived")
            or result.get("disabled")
            or not result.get("permissions", {}).get("push")
        ):
            raise DeliveryError("仓库不可写：请检查是否归档及 Contents/PR/Issues 写入权限。")
        # Read the API surfaces needed for subsequent writes before executing any code task.
        await self.request("GET", f"/repos/{repo}/pulls", params={"per_page": 1})
        await self.request("GET", f"/repos/{repo}/issues", params={"per_page": 1})
        return {k: result[k] for k in ("full_name", "default_branch", "html_url", "updated_at")}

    async def pull_request(self, task, body):
        repo, branch = task["repo"], task["branch"]
        params = {"head": repo.split("/")[0] + ":" + branch, "state": "all"}
        existing = await self.request("GET", f"/repos/{repo}/pulls", params=params)
        if existing:
            if existing[0]["state"] != "open":
                raise DeliveryError("任务对应 PR 已关闭，请人工检查，未重复创建 PR。")
            return existing[0]
        try:
            return await self.request(
                "POST",
                f"/repos/{repo}/pulls",
                json={
                    "title": f"PRBot task {task['id']}",
                    "head": branch,
                    "base": task["repository"]["default_branch"],
                    "body": body,
                    "draft": True,
                },
            )
        except DeliveryError:
            existing = await self.request("GET", f"/repos/{repo}/pulls", params=params)
            if existing:
                return existing[0]
            raise

    async def checks(self, repo, sha, required):
        checks = await self.request(
            "GET",
            f"/repos/{repo}/commits/{sha}/check-runs",
            params={"per_page": 100, "filter": "latest"},
        )
        statuses = await self.request("GET", f"/repos/{repo}/commits/{sha}/status")
        values = {s["context"]: s["state"] for s in reversed(statuses.get("statuses", []))}
        values.update(
            {
                c["name"]: c["conclusion"] if c["status"] == "completed" else "pending"
                for c in reversed(checks["check_runs"])
            }
        )
        runs = await self.request(
            "GET", f"/repos/{repo}/actions/runs", params={"head_sha": sha, "per_page": 100}
        )
        if any(run["status"] != "completed" for run in runs.get("workflow_runs", [])):
            return False
        if any(
            values.get(k)
            in {
                "failure",
                "error",
                "cancelled",
                "timed_out",
                "action_required",
                "startup_failure",
                "stale",
            }
            for k in required
        ):
            raise DeliveryError("远端必需检查失败；请打开 PR 的 Checks 排查，未报告完成。")
        return all(values.get(k) == "success" for k in required)

    async def ready(self, repo, number, expected_sha=None):
        pr = await self.request("GET", f"/repos/{repo}/pulls/{number}")
        if expected_sha and pr["head"]["sha"] != expected_sha:
            raise DeliveryError("PR 代码已被修改，需重新验证；未报告完成。")
        if not pr["draft"]:
            return
        try:
            result = await self.request(
                "POST",
                "/graphql",
                json={
                    "query": "mutation($id:ID!){markPullRequestReadyForReview("
                    "input:{pullRequestId:$id})"
                    "{pullRequest{id isDraft}}}",
                    "variables": {"id": pr["node_id"]},
                },
            )
            if result.get("errors"):
                raise DeliveryError("PR 转为待审失败，请检查 Pull requests 写入权限。")
        except DeliveryError:
            current = await self.request("GET", f"/repos/{repo}/pulls/{number}")
            if current["draft"]:
                raise

    async def close_issue(self, repo, number):
        path = f"/repos/{repo}/issues/{number}"
        current = await self.request("GET", path)
        if current["state"] == "closed":
            return "已关闭（核对远端）"
        try:
            await self.request("PATCH", path, json={"state": "closed", "state_reason": "completed"})
        except DeliveryError:
            if (await self.request("GET", path))["state"] != "closed":
                raise
        return "已关闭"

    async def wait_checks(self, task, required, timeout):
        async with asyncio.timeout(timeout):
            while not await self.checks(task["repo"], task["commit_sha"], required):
                await asyncio.sleep(15)
