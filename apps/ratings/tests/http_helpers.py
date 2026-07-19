from html.parser import HTMLParser
from typing import Protocol


class _ResponseWithContent(Protocol):
    @property
    def content(self) -> bytes: ...


class _FormCsrfTokenParser(HTMLParser):
    def __init__(self, action: str | None):
        super().__init__()
        self.action = action
        self.in_target_form = False
        self.token: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self.in_target_form = attributes.get("action") == self.action
            return
        if (
            self.in_target_form
            and tag == "input"
            and attributes.get("name") == "csrfmiddlewaretoken"
        ):
            value = attributes.get("value")
            if value is not None:
                self.token = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.in_target_form = False


def csrf_token_from_form(
    response: _ResponseWithContent,
    action: str | None,
) -> str:
    parser = _FormCsrfTokenParser(action)
    parser.feed(response.content.decode())
    assert parser.token is not None, f"CSRF token not found in form for {action!r}"
    return parser.token
