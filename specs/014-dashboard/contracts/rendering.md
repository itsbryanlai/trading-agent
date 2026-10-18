# Contract: rendering untrusted text

What the dashboard does with model- and broker-written text (FR-015 to FR-017, SC-003). Research: D9, D10.

## Which fields

| Rendered as Markdown (restricted) | Shown as escaped plain text |
|---|---|
| `reports.rationale_md`, `decisions.reasoning_md`, `journal.summary_md` | `reports.sources[*].title` and `.publisher`, `orders.broker_reason`, `execution_refusals.reason` and `.details`, `risk_verdicts.rejection_rule`, `orchestrator_runs.detail`, symbols, every number and enum |

A source's `url` becomes a link only if it is `http(s)`; otherwise the title shows without a link.

## The Markdown policy

- CommonMark, raw HTML off: `<b>`, `<script>`, `<iframe>`, comments and entities in raw HTML appear as literal text.
- Links kept only for `http://` and `https://` (case-insensitive, after trimming). Any other scheme (`javascript:`, `data:`, `vbscript:`, `file:`, relative, protocol-relative `//`) renders the link's text without a link.
- Kept links get `rel="nofollow noopener noreferrer"` and `target="_blank"`.
- Images never load: `![alt](url)` renders the alt text, escaped.
- Headings `#` to `######` render as `<h4>` at most.
- No autolinking of bare URLs, no typographic replacement.
- Long text is placed inside `<details>`, its first 300 characters (escaped, as plain text) as the summary.

## Hostile fixtures (each a test; none may produce live HTML, a script or an outside request)

| Input | Expected output contains |
|---|---|
| `<script>alert(1)</script>` | `&lt;script&gt;alert(1)&lt;/script&gt;` |
| `<img src=x onerror=alert(1)>` | `&lt;img src=x onerror=alert(1)&gt;`, no `<img` |
| `[click](javascript:alert(1))` | `click`, no `href` |
| `[x](JaVaScRiPt:alert(1))`, `[x]( javascript:alert(1))` | `x`, no `href` |
| `[x](data:text/html;base64,PHNjcmlwdD4=)` | `x`, no `href` |
| `[x](//evil.example/a)` | `x`, no `href` |
| `![tracker](https://evil.example/p.gif)` | `tracker`, no `<img` |
| `<a href="https://ok.example">ok</a>` | the tag as text |
| `[ok](https://news.example/a?b=1&c=2)` | `<a href="https://news.example/a?b=1&amp;c=2" rel="nofollow noopener noreferrer" target="_blank">ok</a>` |
| `"><svg onload=alert(1)>` in a source title | escaped, inside text only |
| `{{ 7*7 }}` and `{% raw %}` | literal text (never evaluated by the templates) |
| a 100,000-character rationale | rendered once, collapsed, page under 2 s |

## Page-level guarantees

- Jinja autoescape is on for every template; `|safe` appears only where `render.markdown()`'s result is inserted (a test greps the templates).
- Templates emit no `<script>` and no `style=` attribute (a test checks every template file).
- Every response carries the CSP in [routes.md](routes.md); a test asserts it on every route.
