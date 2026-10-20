"""Restricted Markdown for model-written text, and escaped links for stored titles.

Contracts: specs/014-dashboard/contracts/rendering.md, research D9. This is the only module
that makes `Markup`, so it is the only source of text a template may insert unescaped.

- CommonMark with raw HTML off, no autolinking of bare URLs, no typographic replacement.
- A link survives only if its URL (trimmed, lower-cased) starts with `http://` or `https://`;
  any other link renders as its text. Kept links open in a new tab and send no referrer.
- Images never load: they render as their escaped alt text.
- Headings are never larger than `<h4>`, so a rationale can't break the page's outline.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.renderer import RendererHTML
from markdown_it.rules_core import StateCore
from markdown_it.token import Token
from markupsafe import Markup, escape

_LINK_REL = "nofollow noopener noreferrer"
_SCHEMES = ("http://", "https://")
_SMALLEST_HEADING = 4
_WHITESPACE = re.compile(r"\s+")


def _is_web_url(url: str | None) -> bool:
    return bool(url) and url.strip().lower().startswith(_SCHEMES)


def _image(self: RendererHTML, tokens: Sequence[Token], idx: int, options, env) -> str:
    alt = self.renderInlineAsText(tokens[idx].children or [], options, env)
    return escapeHtml(alt)


def _restrict(state: StateCore) -> None:
    """Drop links that aren't http(s), mark the rest, and cap heading sizes."""
    for block in state.tokens:
        if block.type in ("heading_open", "heading_close"):
            level = int(block.tag[1:])
            block.tag = f"h{max(level, _SMALLEST_HEADING)}"
        if block.type != "inline" or not block.children:
            continue
        kept: list[Token] = []
        keep_stack: list[bool] = []
        for token in block.children:
            if token.type == "link_open":
                keep = _is_web_url(str(token.attrGet("href") or ""))
                keep_stack.append(keep)
                if keep:
                    token.attrs = {"href": token.attrs["href"]}
                    token.attrSet("rel", _LINK_REL)
                    token.attrSet("target", "_blank")
                    kept.append(token)
            elif token.type == "link_close":
                if keep_stack and keep_stack.pop():
                    kept.append(token)
            else:
                kept.append(token)
        block.children = kept


def _build() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    # Let every link parse; `_restrict` is the one place that decides which survive, so a
    # rejected link renders as its text rather than as the literal Markdown.
    md.validateLink = lambda url: True
    md.add_render_rule("image", _image)
    md.core.ruler.push("dashboard_restrict", _restrict)
    return md


_MD = _build()


def markdown(text: str) -> Markup:
    """Render model-written Markdown; the result is safe to insert unescaped."""
    return Markup(_MD.render(text))


def link_or_text(title: str, url: str | None) -> Markup:
    """A source title as a link when its URL is http(s), else as escaped text."""
    if not _is_web_url(url):
        return Markup(escape(title))
    return Markup(
        f'<a href="{escape(url.strip())}" rel="{_LINK_REL}" target="_blank">{escape(title)}</a>'
    )


def excerpt(text: str, limit: int) -> str:
    """The start of a text as plain, single-spaced text (escaped later by the template)."""
    flat = _WHITESPACE.sub(" ", text).strip()
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "…"
