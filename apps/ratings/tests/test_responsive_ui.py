import re
from pathlib import Path

from django.contrib.staticfiles import finders


def _static_text(asset_path: str) -> str:
    resolved_path = finders.find(asset_path)
    assert isinstance(resolved_path, str)
    return Path(resolved_path).read_text()


def _css_rule(source: str, selector_pattern: str) -> str:
    match = re.search(rf"{selector_pattern}\s*\{{(?P<body>[^}}]+)\}}", source)
    assert match is not None
    return match.group("body")


def test_uploaded_media_uses_mobile_safe_responsive_layout():
    css = _static_text("ratings/app.css")

    gallery = _css_rule(css, r"\.attachment-gallery")
    assert "grid-template-columns: minmax(0, 1fr)" in gallery

    media = _css_rule(css, r"\.attachment img,\s*\.attachment video")
    assert "height: auto" in media
    assert "max-width: 100%" in media
    assert "object-fit: contain" in media
    assert "aspect-ratio: 4 / 3" not in css
    assert "aspect-ratio: 16 / 9" not in css

    comment_gallery = _css_rule(css, r"\.comment-bubble \.attachment-gallery")
    assert "width: 100%" in comment_gallery
    assert "max-width: 25rem" in comment_gallery

    preview = _css_rule(css, r"\.media-preview-card__visual")
    assert "object-fit: contain" in preview
    assert ".media-preview-card--video" in css


def test_mobile_chrome_and_unbroken_content_respect_available_width():
    css = _static_text("ratings/app.css")
    offline = _static_text("ratings/offline.html")

    body = _css_rule(css, r"body")
    assert "env(safe-area-inset-right)" in body
    assert "env(safe-area-inset-left)" in body

    history_reason = _css_rule(css, r"\.history-reason")
    assert "overflow-wrap: anywhere" in history_reason
    assert "white-space: pre-wrap" in history_reason

    section_heading = _css_rule(css, r"\.section-heading h2")
    assert "overflow-wrap: anywhere" in section_heading

    history_footer_items = _css_rule(css, r"\.history-card footer > \*")
    assert "min-width: 0" in history_footer_items
    assert "overflow-wrap: anywhere" in history_footer_items

    thread_layout = _css_rule(css, r"\.thread-layout")
    assert "grid-template-columns: minmax(0, 1fr)" in thread_layout

    thread_items = _css_rule(css, r"\.thread-layout > \*")
    assert "min-width: 0" in thread_items

    assert re.search(r"\.field-label-row\s*\{[^}]*flex-wrap: wrap", css) is not None
    assert ".auth-footnote {\n    position: absolute" not in css
    assert (
        re.search(
            r"@media \(min-width: 42rem\).*?\.auth-footnote\s*\{[^}]*grid-column: 2",
            css,
            re.DOTALL,
        )
        is not None
    )
    assert "env(safe-area-inset-right)" in offline
    assert "env(safe-area-inset-left)" in offline
