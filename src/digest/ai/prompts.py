"""Load prompt templates from ai/prompts/*.md and fill {placeholders}.

Only the named placeholders are replaced, so JSON examples in prompts keep their braces.
"""

from importlib.resources import files

LANGUAGE_NAMES = {"vi": "Vietnamese", "en": "English"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def load_prompt(name: str, **values: str) -> str:
    text = files("digest.ai").joinpath("prompts", f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text
