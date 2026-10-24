"""Read GitHub delivery facts; repository writes belong to the SDK."""

import httpx


class DeliveryError(Exception):
    pass


class CheckFailure(DeliveryError):
    """Required checks finished unsuccessfully; distinct from an unreadable API response."""


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
            raise DeliveryError(
                "GitHub request unconfirmed. Check network, sign-in and repository access."
            ) from None

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
            raise CheckFailure("Required remote checks failed. Open the PR Checks tab.")
        return all(values.get(k) == "success" for k in required)
