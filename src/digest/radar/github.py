"""GitHub REST API helpers shared by the radar steps."""

from collections.abc import Mapping, Sequence

import httpx

API = "https://api.github.com"


def resolve_token(env_names: Sequence[str], env: Mapping[str, str]) -> str | None:
    """First non-empty token among env_names; the value itself is never logged."""
    for name in env_names:
        if env.get(name):
            return env[name]
    return None


def headers(token: str | None, accept: str = "application/vnd.github+json") -> dict[str, str]:
    result = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        result["Authorization"] = f"Bearer {token}"
    return result


def describe_status(response: httpx.Response) -> str:
    """'HTTP 403', plus a hint when GitHub says the rate limit is used up."""
    text = f"HTTP {response.status_code}"
    if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        text += " (rate limited; set GH_READ_TOKEN or GITHUB_TOKEN)"
    return text
