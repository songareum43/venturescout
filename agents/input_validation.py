"""Input quality checks used before an analysis job is allowed to run."""

from __future__ import annotations

import re


class InsufficientInputError(ValueError):
    def __init__(self, missing_details: list[str]):
        details = ", ".join(dict.fromkeys(missing_details))
        self.missing_details = list(dict.fromkeys(missing_details))
        super().__init__(
            f"{{{details}}} 내용을 더 자세히 입력해주세요"
        )


def validate_input_detail(raw_input: str) -> None:
    """Reject empty, extremely short, or obviously non-descriptive input."""

    text = " ".join(raw_input.split())
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", text)
    unique_tokens = {token.lower() for token in tokens}

    if len(text) < 30 or len(tokens) < 6 or len(unique_tokens) < 5:
        raise InsufficientInputError(
            ["대상 고객", "해결하려는 문제", "제공할 제품 또는 서비스"]
        )
