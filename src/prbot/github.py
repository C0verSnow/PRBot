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
        except (httpx.HTTPError, ValueError):
            # URLs/response bodies may include private task context; never echo raw exceptions.
            raise DeliveryError(
                "GitHub request unconfirmed. Check network, sign-in and repository access."
            ) from None

    async def pages(self, path, key, **params):
        """Read every page before making a delivery decision."""
        items = []
        page = 1
        while True:
            response = await self.request(
                "GET", path, params={**params, "per_page": 100, "page": page}
            )
            if not isinstance(response, dict) or not isinstance(response.get(key), list):
                raise DeliveryError("GitHub checks could not be read. Review the PR Checks tab.")
            batch = response[key]
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    async def checks(self, repo, sha, required):
        checks = await self.pages(
            f"/repos/{repo}/commits/{sha}/check-runs",
            "check_runs", filter="latest",
        )
        statuses = await self.pages(f"/repos/{repo}/commits/{sha}/status", "statuses")
        runs = await self.pages(
            f"/repos/{repo}/actions/runs", "workflow_runs", head_sha=sha,
        )
        # A pending successor must finish before cancelled older runs are judged.
        if any(run["status"] != "completed" for run in runs):
            return False
        # Check runs and commit statuses can share a name. Every applicable
        # result must pass; one success must never overwrite another failure.
        values = {}
        for status in reversed(statuses):
            values[status["context"]] = [status["state"]]
        for check in checks:
            values.setdefault(check["name"], []).append(
                check["conclusion"] if check["status"] == "completed" else "pending"
            )
        if any(
            value
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
            for value in values.get(k, [])
        ):
            raise CheckFailure("Required remote checks failed. Open the PR Checks tab.")
        return bool(required) and all(
            values.get(k) and all(value == "success" for value in values[k]) for k in required
        )
