from digest.models import item_id


def test_item_id_is_sha1_of_url() -> None:
    # sha1("https://example.com/a"), computed independently with hashlib
    assert item_id("https://example.com/a") == "c4ed1c218d14a0f15bba7044693ec4b0d68e0a63"
