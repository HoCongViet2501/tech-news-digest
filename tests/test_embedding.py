import numpy as np

from digest.config import EmbedConfig
from digest.embedding import DIM, FastEmbedder, HashEmbedder, item_text, make_embedder


def test_hash_embedder_is_deterministic_unit_and_word_based() -> None:
    embedder = HashEmbedder()

    a, b, c, empty = embedder.embed(
        ["Kafka 4.0 released", "kafka 4.0 RELEASED", "Bread recipes for winter", ""]
    )

    assert a.shape == (DIM,) and a.dtype == np.float32
    assert np.allclose([np.linalg.norm(v) for v in (a, b, c, empty)], 1.0)
    assert float(a @ b) > 0.999  # same words
    assert float(a @ c) < 0.5
    assert np.array_equal(embedder.embed(["Kafka 4.0 released"])[0], a)


def test_item_text_is_title_plus_cut_description() -> None:
    assert item_text(" Title ", None, 100) == "Title"
    assert item_text("Title", "  ", 100) == "Title"
    assert item_text("Title", "Description", 9) == "Title\nDes"


def test_offline_never_builds_the_real_model() -> None:
    assert isinstance(make_embedder(EmbedConfig(), offline=True), HashEmbedder)
    real = make_embedder(EmbedConfig(), offline=False)
    assert isinstance(real, FastEmbedder)
    # Constructing it is free: the model is only loaded (downloaded) on first embed.
    assert real._model is None
    assert real.embed([]).shape == (0, DIM)
