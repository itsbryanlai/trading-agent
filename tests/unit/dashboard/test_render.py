"""Restricted Markdown for untrusted text (specs/014 tasks T007; contracts/rendering.md)."""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from markupsafe import Markup

from trading_agent.dashboard import render

LINK_ATTRS = 'rel="nofollow noopener noreferrer" target="_blank"'


def _tags(html: str) -> list[tuple[str, dict[str, str | None]]]:
    found: list[tuple[str, dict[str, str | None]]] = []

    class Collect(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.append((tag, dict(attrs)))

        handle_startendtag = handle_starttag

    Collect().feed(html)
    return found


# --- every row of contracts/rendering.md "Hostile fixtures" ----------------------------------


def test_a_script_tag_is_shown_as_text():
    out = render.markdown("<script>alert(1)</script>")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "<script" not in out


def test_an_img_tag_is_shown_as_text():
    out = render.markdown("<img src=x onerror=alert(1)>")
    assert "&lt;img src=x onerror=alert(1)&gt;" in out
    assert "<img" not in out


def test_a_javascript_link_keeps_its_text_and_loses_its_link():
    out = render.markdown("[click](javascript:alert(1))")
    assert "click" in out and "href" not in out and "javascript" not in out


@pytest.mark.parametrize(
    "text",
    [
        "[x](JaVaScRiPt:alert(1))",
        "[x]( javascript:alert(1))",
        "[x](  JAVASCRIPT:alert(1)  )",
        "[x](data:text/html;base64,PHNjcmlwdD4=)",
        "[x](vbscript:msgbox(1))",
        "[x](file:///etc/passwd)",
        "[x](//evil.example/a)",
        "[x](/relative/path)",
        "[x](relative)",
        "[x](ftp://files.example/a)",
        "[x](&#106;avascript:alert(1))",
        "[x](jav&#x09;ascript:alert(1))",
        "[x](<javascript:alert(1)>)",
        "[x]\n\n[x]: javascript:alert(1)",
        "<javascript:alert(1)>",
        "<mailto:a@b.example>",
    ],
)
def test_every_non_http_link_loses_its_link(text):
    out = render.markdown(text)
    assert "href" not in out
    assert "<a" not in out


def test_a_non_http_link_leaves_only_its_text():
    assert render.markdown("[x](//evil.example/a)") == Markup("<p>x</p>\n")


def test_an_image_shows_only_its_escaped_alt_text():
    out = render.markdown("![tracker](https://evil.example/p.gif)")
    assert "tracker" in out and "<img" not in out and "evil.example" not in out
    out = render.markdown('![<b>"alt"</b>](https://evil.example/p.gif)')
    assert "<img" not in out and "<b>" not in out and "evil.example" not in out
    assert "&lt;b&gt;" in out


def test_raw_html_anchors_are_shown_as_text():
    out = render.markdown('<a href="https://ok.example">ok</a>')
    assert "&lt;a href=" in out
    assert "<a " not in out


def test_a_http_link_is_kept_with_the_safety_attributes():
    out = render.markdown("[ok](https://news.example/a?b=1&c=2)")
    assert (
        '<a href="https://news.example/a?b=1&amp;c=2" '
        'rel="nofollow noopener noreferrer" target="_blank">ok</a>'
    ) in out


def test_http_in_any_case_with_surrounding_space_is_kept():
    for url in ("HTTP://news.example/a", "HtTpS://news.example/a"):
        out = render.markdown(f"[ok]({url})")
        assert "<a href=" in out and LINK_ATTRS in out


def test_an_autolink_to_http_is_kept_and_to_another_scheme_is_not():
    assert LINK_ATTRS in render.markdown("<https://news.example/a>")
    assert "href" not in render.markdown("<javascript:alert(1)>")


def test_a_bare_url_is_not_linked():
    assert "<a" not in render.markdown("see https://news.example/a now")


def test_template_syntax_is_literal():
    out = render.markdown("{{ 7*7 }} and {% raw %}")
    assert "{{ 7*7 }}" in out and "{% raw %}" in out and "49" not in out


def test_a_source_title_with_markup_is_escaped_inside_text():
    out = render.link_or_text('"><svg onload=alert(1)>', "https://news.example/a")
    assert "<svg" not in out
    assert "&lt;svg onload=alert(1)&gt;" in out
    assert out.startswith('<a href="https://news.example/a"')


def test_a_very_long_rationale_renders_once_and_quickly():
    text = ("A rationale sentence with *emphasis* and a [link](https://news.example). " * 1500)[
        :100_000
    ]
    assert len(text) == 100_000
    started = time.monotonic()
    out = render.markdown(text)
    assert time.monotonic() - started < 2
    assert out.startswith("<p>")


# --- the rest of the policy -----------------------------------------------------------------


def test_the_result_is_marked_safe_and_plain_text_is_not():
    assert isinstance(render.markdown("hi"), Markup)
    assert isinstance(render.link_or_text("t", "https://a.example"), Markup)
    assert not isinstance(render.excerpt("hi", 300), Markup)


def test_ordinary_commonmark_still_works():
    out = render.markdown("**bold** and *em*\n\n- one\n- two\n\n> quoted\n\n`code`")
    for fragment in (
        "<strong>bold</strong>",
        "<em>em</em>",
        "<li>one</li>",
        "<blockquote>",
        "<code>",
    ):
        assert fragment in out


@pytest.mark.parametrize(
    ("hashes", "tag"),
    [("#", "h4"), ("##", "h4"), ("###", "h4"), ("####", "h4"), ("#####", "h5"), ("######", "h6")],
)
def test_headings_are_never_above_h4(hashes, tag):
    out = render.markdown(f"{hashes} Title")
    assert f"<{tag}>Title</{tag}>" in out
    assert not re.search(r"<h[1-3][ >]", out)


def test_setext_headings_are_capped_too():
    assert "<h4>Title</h4>" in render.markdown("Title\n=====")
    assert "<h4>Title</h4>" in render.markdown("Title\n-----")


def test_a_title_link_for_an_unsafe_url_is_just_the_escaped_title():
    for url in (
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html,x",
        "//evil.example/a",
        "/relative",
        "",
        None,
        "ftp://x.example",
    ):
        assert render.link_or_text("A <b>title</b>", url) == Markup("A &lt;b&gt;title&lt;/b&gt;")


def test_a_title_link_for_an_http_url():
    out = render.link_or_text("Title", ' HTTPS://news.example/a?x=1&y="2" ')
    assert out == Markup(
        f'<a href="HTTPS://news.example/a?x=1&amp;y=&#34;2&#34;" {LINK_ATTRS}>Title</a>'
    )


def test_excerpt_is_plain_text_cut_at_the_limit():
    assert render.excerpt("short  text\nwith   gaps", 300) == "short text with gaps"
    long = "word " * 200
    cut = render.excerpt(long, 300)
    assert len(cut) <= 301 and cut.endswith("…")
    assert render.excerpt("x" * 300, 300) == "x" * 300
    assert render.excerpt("<script>x</script>", 300) == "<script>x</script>"  # plain, escaped later


# --- property: nothing hostile survives -----------------------------------------------------

_ALLOWED_TAGS = {
    "p", "em", "strong", "a", "ul", "ol", "li", "code", "pre", "blockquote", "hr", "br",
    "h4", "h5", "h6",
}  # fmt: skip
_ALLOWED_ATTRS = {"a": {"href", "rel", "target"}, "ol": {"start"}, "code": {"class"}}

_hostile = st.one_of(
    st.text(max_size=200),
    st.lists(
        st.sampled_from(
            [
                "<script>", "</script>", "<img src=x onerror=a()>", "<iframe src=//x>", "[a](",
                "javascript:", ")", "![", "](", "http://x.example", "https://y.example/?a=1&b=2",
                " ", "\n", "#", "*", "_", "`", ">", "<", "&", "\"", "'", "onclick=", "<a href=",
                "[ref]: javascript:alert(1)", "[ref]", "<https://z.example>", "<javascript:x>",
                "```js\n", "~~~", "    ", "- ", "1. ", "\\", "&#106;", "%6aavascript:",
            ]
        ),
        max_size=30,
    ).map("".join),
)  # fmt: skip


@settings(max_examples=400, deadline=None)
@given(_hostile)
def test_no_input_produces_script_image_frame_handler_or_unsafe_link(text):
    out = render.markdown(text)
    lowered = out.lower()
    for forbidden in ("<script", "<img", "<iframe", "<svg", "<style", "<object", "<embed"):
        assert forbidden not in lowered
    for tag, attrs in _tags(out):
        assert tag in _ALLOWED_TAGS, tag
        assert set(attrs) <= _ALLOWED_ATTRS.get(tag, set()), (tag, attrs)
        if tag == "a":
            assert attrs["href"].lower().startswith(("http://", "https://"))
            assert attrs["rel"] == "nofollow noopener noreferrer"
            assert attrs["target"] == "_blank"
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", out, flags=re.IGNORECASE)
