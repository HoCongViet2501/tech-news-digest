"""robots.txt cache: one fetch per host per run, RFC 9309 matching.

urllib.robotparser does not support `*` / `$` wildcards and uses first-match instead of
longest-match, so a small parser lives here instead.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from digest.http import USER_AGENT, get_with_retry

log = logging.getLogger(__name__)

# The product token robots.txt groups are matched against ("tech-news-digest").
AGENT_TOKEN = USER_AGENT.split("/", 1)[0].lower()
# RFC 9309 asks crawlers to parse at least 500 KiB.
MAX_ROBOTS_BYTES = 512 * 1024
# A missing robots.txt allows everything (RFC 9309 treats all 4xx this way, but only
# "not found" is unambiguous; 401/403 are treated as errors, the conservative choice).
MISSING_STATUSES = frozenset({404, 410})


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


@dataclass
class RobotsRules:
    rules: list[Rule] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str, agent: str = AGENT_TOKEN) -> "RobotsRules":
        groups: list[tuple[list[str], list[Rule]]] = []
        in_agents = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not in_agents:
                    groups.append(([], []))
                    in_agents = True
                groups[-1][0].append(value.lower())
            elif key in ("allow", "disallow"):
                in_agents = False
                if groups and value:
                    rule = Rule(allow=key == "allow", pattern=value, regex=_compile(value))
                    groups[-1][1].append(rule)
            # Other keys (sitemap, crawl-delay, ...) neither start nor end a group.

        specific = [rules for agents, rules in groups if agent in agents]
        chosen = specific or [rules for agents, rules in groups if "*" in agents]
        return cls(rules=[rule for rules in chosen for rule in rules])

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: Rule | None = None
        for rule in self.rules:
            if not rule.regex.match(path):
                continue
            longer = best is None or len(rule.pattern) > len(best.pattern)
            tie_allow = best is not None and len(rule.pattern) == len(best.pattern) and rule.allow
            if longer or tie_allow:
                best = rule
        return best is None or best.allow


ALLOW_ALL = RobotsRules()


class RobotsCache:
    """Fetches each host's robots.txt at most once; create one per pipeline run."""

    def __init__(self) -> None:
        # host origin -> rules, or an error string when the host is skipped for this run
        self._cache: dict[str, RobotsRules | str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def check(self, client: httpx.AsyncClient, url: str, *, timeout_s: float) -> str | None:
        """None if `url` may be fetched, otherwise the reason it may not."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return "invalid url"
        origin = f"{parts.scheme}://{parts.netloc}"
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._cache:
                self._cache[origin] = await self._load(client, origin, timeout_s)
        entry = self._cache[origin]
        if isinstance(entry, str):
            return entry
        return None if entry.allowed(url) else "robots: disallowed"

    async def _load(
        self, client: httpx.AsyncClient, origin: str, timeout_s: float
    ) -> RobotsRules | str:
        robots_url = f"{origin}/robots.txt"
        try:
            async with asyncio.timeout(timeout_s):
                response = await get_with_retry(client, robots_url, timeout=timeout_s)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in MISSING_STATUSES:
                return ALLOW_ALL
            log.info("robots.txt for %s returned %d; skipping host this run", origin, status)
            return f"robots: http {status}"
        except Exception as exc:  # timeouts, transport errors, anything else
            log.info("robots.txt for %s failed (%s); skipping host this run", origin, exc)
            return f"robots: {type(exc).__name__}"
        text = response.content[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace")
        return RobotsRules.parse(text)
