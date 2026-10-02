"""An HTTP opener that refuses redirects (specs/007-research-agent, adversarial review L4).

urllib follows 301/302/303/307/308 by default and copies the request's headers to the
new address, `Authorization` and `X-Finnhub-Token` included, even to plain http://.
No provider this system calls needs a redirect, so any 3xx is refused: urllib then
raises HTTPError with the 3xx status, which each adapter maps to "unavailable".
"""

from __future__ import annotations

from urllib.request import HTTPRedirectHandler, build_opener


class _RefuseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_without_redirects():
    """A drop-in for urllib.request.urlopen: `open(request, timeout=...)`."""
    return build_opener(_RefuseRedirects()).open
