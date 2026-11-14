"""Content store (Part 2, W1): Postgres tables for sources and items, plus a test double.

state.py keeps the Part 1 Telegram state (seen.json, digests) and is unrelated.
"""

from digest.store.base import DEFAULT_DUE_SLACK, ContentStore, StoreError
from digest.store.memory import MemoryStore
from digest.store.models import (
    FetchOutcome,
    NewItem,
    Source,
    SourceSpec,
    Stage,
    StoredItem,
    SyncResult,
    UpsertResult,
)
from digest.store.postgres import PostgresStore
from digest.store.sources_yaml import SourcesFile, load_sources

__all__ = [
    "DEFAULT_DUE_SLACK",
    "ContentStore",
    "FetchOutcome",
    "MemoryStore",
    "NewItem",
    "PostgresStore",
    "Source",
    "SourceSpec",
    "SourcesFile",
    "Stage",
    "StoreError",
    "StoredItem",
    "SyncResult",
    "UpsertResult",
    "load_sources",
]
