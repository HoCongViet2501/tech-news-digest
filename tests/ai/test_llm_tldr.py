import json

import pytest

from digest.ai.fake import PREFIX, fake_chain
from digest.ai.llm import AIUnavailable, LLMChain, Provider
from digest.ai.tldr import TldrInput, clean_tldr, llm_tldr_batch
from digest.config import Config
from fakes import FakeClient

ITEMS = [
    TldrInput(id="a" * 40, title="Postgres 18 released", content="Async I/O and more. " * 10),
    TldrInput(id="b" * 40, title="Kafka 4.1", content="Queues for Kafka. " * 10),
]


def chain(client: FakeClient) -> LLMChain:
    return LLMChain([Provider("p", "m", client)])


def reply(*answers: tuple[str, object]) -> str:
    return json.dumps({"items": [{"id": i, "tldr": t} for i, t in answers]})


def test_answers_map_back_to_full_ids_and_prompt_has_language() -> None:
    client = FakeClient(
        reply(("a" * 8, "  Postgres 18 có async I/O.\n Nhanh hơn. "), ("b" * 8, "Kafka."))
    )

    out = llm_tldr_batch(chain(client), ITEMS, output_language="vi", max_chars=600)

    assert out == {"a" * 40: "Postgres 18 có async I/O. Nhanh hơn.", "b" * 40: "Kafka."}
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Vietnamese" in prompt
    assert '"id": "aaaaaaaa"' in prompt and "a" * 40 not in prompt  # short ids only
    assert "Queues for Kafka." in prompt


def test_unknown_ids_are_dropped_and_missing_ids_left_out() -> None:
    client = FakeClient(reply(("zzzzzzzz", "Bịa."), ("a" * 8, "Thật.")))

    assert llm_tldr_batch(chain(client), ITEMS, output_language="vi", max_chars=600) == {
        "a" * 40: "Thật."
    }


def test_empty_answers_are_kept_as_empty_and_never_replace_a_real_one() -> None:
    client = FakeClient(reply(("a" * 8, "Có nội dung."), ("a" * 8, ""), ("b" * 8, None)))

    out = llm_tldr_batch(chain(client), ITEMS, output_language="vi", max_chars=600)

    assert out == {"a" * 40: "Có nội dung.", "b" * 40: ""}


def test_invalid_json_from_every_provider_raises() -> None:
    with pytest.raises(AIUnavailable):
        llm_tldr_batch(chain(FakeClient("not json")), ITEMS, output_language="vi", max_chars=600)


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("Một. Hai.", 600, "Một. Hai."),
        ("Câu một. Câu hai dài hơn nhiều.", 15, "Câu một."),
        ("Câu? Có! Ba… bốn năm", 14, "Câu? Có! Ba…"),
        ("không có dấu chấm câu nào ở đây cả", 12, "không có…"),
        ("v1.2 ra mắt hôm nay rồi", 8, "v1.2…"),  # a dot inside a word is no sentence end
        ("khôngcókhoảngtrắng", 6, "không…"),
    ],
)
def test_clean_tldr_caps_length_at_a_sentence_end(text: str, limit: int, expected: str) -> None:
    out = clean_tldr(text, limit)
    assert out == expected
    assert len(out) <= limit


def test_offline_fake_answers_the_tldr_prompt(cfg: Config) -> None:
    out = llm_tldr_batch(fake_chain(cfg.ai), ITEMS, output_language="vi", max_chars=600)

    assert set(out) == {"a" * 40, "b" * 40}
    assert out["a" * 40].startswith(PREFIX) and "Postgres 18 released" in out["a" * 40]
    assert llm_tldr_batch(fake_chain(cfg.ai), ITEMS, output_language="vi", max_chars=600) == out
