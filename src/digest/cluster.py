"""Near-duplicate clustering (docs/PLAN.md §15 Dedupe): the same story under different
URLs, e.g. a launch on Product Hunt and the vendor's own announcement.

Rule: title cosine >= `cluster.title_threshold` (0.92) between items published within
`cluster.window_hours` (72 h) -> same cluster.

The stored vector embeds title + description, so it is not a title similarity: on real
items a true duplicate scored 0.83 on it (0.98 on titles) while two different posts of
one blog scored 0.89. Titles are therefore embedded transiently, but only for items with
a candidate partner: pairs whose stored vectors reach `prefilter` (0.5; every pair with
title cosine >= 0.92 in the sample was >= 0.54). That is about half the window, not all
of it, so a run stays cheap without storing a second vector.

Two guards reject what titles alone get wrong on real feeds (8 of 11 raw matches in the
sample): items from the same source (release series, "Security updates for Thursday" /
"... Friday"), and titles with different version numbers ("Node.js 26.13.1" vs "22.23.5").

Each run re-clusters the whole window; a group whose items already share a cluster is
left alone, so reruns write nothing. Clusters only grow or merge, never split.
"""

import logging
import re
from collections.abc import Sequence
from datetime import datetime, timedelta

import numpy as np

from digest.config import ClusterConfig
from digest.embedding import Embedder
from digest.store import ContentStore, StoredItem

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(title: str) -> set[str]:
    return set(_NUMBER.findall(title))


def _compatible(a: StoredItem, b: StoredItem, cfg: ClusterConfig) -> bool:
    if a.source_id == b.source_id:
        return False
    if abs(a.published_at - b.published_at) > timedelta(hours=cfg.window_hours):
        return False
    na, nb = _numbers(a.title), _numbers(b.title)
    return not (na and nb and na != nb)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def find_groups(
    items: Sequence[StoredItem],
    stored: np.ndarray,
    embedder: Embedder,
    cfg: ClusterConfig,
) -> list[list[str]]:
    """Groups (>= 2 item ids) of near-duplicates. `stored` holds the items' stored unit
    vectors in the same order."""
    n = len(items)
    if n < 2:
        return []
    sims = stored @ stored.T
    rows, cols = np.nonzero(np.triu(sims >= cfg.prefilter, k=1))
    pairs = [(int(i), int(j)) for i, j in zip(rows, cols, strict=True)]
    pairs = [(i, j) for i, j in pairs if _compatible(items[i], items[j], cfg)]
    if not pairs:
        return []
    involved = sorted({k for pair in pairs for k in pair})
    titles = embedder.embed([items[k].title for k in involved])
    index = {k: row for row, k in enumerate(involved)}
    uf = _UnionFind(n)
    for i, j in pairs:
        if float(titles[index[i]] @ titles[index[j]]) >= cfg.title_threshold:
            uf.union(i, j)
    groups: dict[int, list[str]] = {}
    for k in involved:
        groups.setdefault(uf.find(k), []).append(items[k].id)
    return [ids for ids in groups.values() if len(ids) >= 2]


def cluster_recent(
    store: ContentStore, embedder: Embedder, cfg: ClusterConfig, *, now: datetime
) -> int:
    """Cluster embedded items published in the window; returns how many groups changed."""
    items = store.recent_items(now - timedelta(hours=cfg.window_hours))
    vectors = store.get_embeddings([i.id for i in items])
    items = [i for i in items if i.id in vectors]
    if len(items) < 2:
        return 0
    stored = np.asarray([vectors[i.id] for i in items], dtype=np.float32)
    stored /= np.maximum(np.linalg.norm(stored, axis=1, keepdims=True), 1e-12)
    by_id = {i.id: i for i in items}
    changed = 0
    for group in find_groups(items, stored, embedder, cfg):
        clusters = {by_id[i].cluster_id for i in group}
        if len(clusters) == 1 and None not in clusters:
            continue  # already together: nothing to write
        store.merge_cluster(group)
        changed += 1
        log.info("cluster: %s", " | ".join(by_id[i].title[:60] for i in group))
    log.info("cluster: %d items in the window, %d groups changed", len(items), changed)
    return changed
