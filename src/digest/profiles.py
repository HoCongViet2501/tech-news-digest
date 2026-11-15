"""Embed the onboarding free text into profiles.user_vec (docs/PLAN.md §15 Cold start).

The web cannot run the embedding model, so the ingest run embeds profiles whose
profile_text is set and profile_embedded_at is null, with the same model as the items:

    user_vec = normalize(w·text_vec + (1 - w)·follow_centroid),  w = ingest.profiles.text_weight

Both inputs are unit vectors, so the default w = 0.5 gives them the same pull. The text
states what the user wants in their own words, including things the taxonomy has no tag
for; the centroid of the followed tags' recent items lives in the same space as the
items it is compared with and steadies a short or vague text. Without followed tags the
text vector is used alone.

Privacy: user ids and profile texts never reach a log line; only counts do.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

import numpy as np

from digest.config import Config
from digest.embedding import Embedder, normalize_rows
from digest.store import ContentStore

log = logging.getLogger(__name__)


def profile_vector(
    text_vec: Sequence[float] | np.ndarray,
    centroid: Sequence[float] | np.ndarray | None,
    text_weight: float,
) -> np.ndarray:
    """normalize(w·text + (1 - w)·centroid) over unit-normalized inputs; the text alone
    without a centroid (or when the two cancel out)."""
    text = normalize_rows(np.asarray(text_vec, dtype=np.float32)[None, :])[0]
    if centroid is None:
        return text
    cen = normalize_rows(np.asarray(centroid, dtype=np.float32)[None, :])[0]
    mixed = text_weight * text + (1 - text_weight) * cen
    if not np.linalg.norm(mixed) > 1e-6:
        return text
    return normalize_rows(mixed[None, :])[0]


def embed_profiles(store: ContentStore, embedder: Embedder, cfg: Config, now: datetime) -> int:
    """Embed up to `ingest.profiles.per_run` pending profiles; returns how many were saved.
    A profile edited while this ran is left pending for the next run."""
    pc = cfg.ingest.profiles
    if pc.per_run <= 0:
        return 0
    pending = store.pending_profiles(pc.per_run)
    if not pending:
        return 0
    vectors = embedder.embed([p.text.strip()[: cfg.ingest.embed.max_chars] for p in pending])
    saved = with_tags = 0
    for profile, text_vec in zip(pending, vectors, strict=True):
        vec = profile_vector(text_vec, profile.centroid, pc.text_weight)
        if store.save_profile_vector(profile, vec.tolist(), now):
            saved += 1
            with_tags += profile.centroid is not None
    log.info(
        "profiles: %d embedded (%d with followed tags, %d without), %d changed meanwhile",
        saved,
        with_tags,
        saved - with_tags,
        len(pending) - saved,
    )
    return saved
