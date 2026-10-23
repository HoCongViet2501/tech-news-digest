"""GitHub REST API helpers shared by the radar; the token is sent but never logged."""

import os
from collections.abc import Mapping

API = "https://api.github.com"
TOKEN_ENV = ("GH_READ_TOKEN", "GITHUB_TOKEN")  # PAT for private repos, else the Actions token


def github_token(env: Mapping[str, str] = os.environ) -> str | None:
    return next((env[name] for name in TOKEN_ENV if env.get(name)), None)


def github_headers(token: str | None, accept: str = "application/vnd.github+json") -> dict:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
