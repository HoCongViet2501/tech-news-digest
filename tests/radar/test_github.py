from digest.radar.github import github_headers, github_token


def test_pat_wins_over_actions_token() -> None:
    env = {"GH_READ_TOKEN": "pat", "GITHUB_TOKEN": "actions"}

    assert github_token(env) == "pat"


def test_empty_pat_falls_back_to_actions_token() -> None:
    # An unset Actions secret arrives as an empty string.
    assert github_token({"GH_READ_TOKEN": "", "GITHUB_TOKEN": "actions"}) == "actions"


def test_no_token_means_anonymous_requests() -> None:
    assert github_token({}) is None
    assert "Authorization" not in github_headers(None)
