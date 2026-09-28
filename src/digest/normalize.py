"""Canonical URLs, item ids and cross-source duplicate merging."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from digest.models import Item, item_id

TRACKING_PARAMS = {"ref", "fbclid"}


def _is_tracking(key: str) -> bool:
    return key.lower().startswith("utm_") or key in TRACKING_PARAMS


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = f"{host}:{parts.port}" if parts.port else host
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    ]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def normalize(items: list[Item]) -> list[Item]:
    """Canonicalize every item and merge duplicates, keeping the highest-scoring copy.

    Output order follows the first occurrence of each URL.
    """
    kept: dict[str, Item] = {}
    sources: dict[str, list[str]] = {}
    for raw in items:
        url = canonical_url(raw.url)
        current = raw.model_copy(update={"url": url, "id": item_id(url)})
        seen = sources.setdefault(url, [])
        if current.source not in seen:
            seen.append(current.source)
        if url not in kept or current.score > kept[url].score:
            # Assigning to an existing key keeps its original insertion position.
            kept[url] = current
    return [
        item.model_copy(update={"also_on": [s for s in sources[url] if s != item.source]})
        for url, item in kept.items()
    ]
