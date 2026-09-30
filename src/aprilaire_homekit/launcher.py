"""Open the local window. The bridge process is separate and keeps running."""

from __future__ import annotations

import logging
import webbrowser

try:
    import webview
except ImportError:
    webview = None

_LOG = logging.getLogger(__name__)


def open_window(url: str) -> None:
    """Show the app. If the desktop toolkit is missing, open a browser tab."""
    if webview is None:
        _open_browser(url)
        return
    try:
        webview.create_window(
            "AprilAire Home",
            url,
            width=980,
            height=820,
            min_size=(760, 640),
            background_color="#efeae2",
            text_select=True,
        )
        webview.start()
    except Exception:
        _LOG.exception("The desktop window could not open")
        _open_browser(url)


def _open_browser(url: str) -> None:
    print(f"Open AprilAire Home at {url}")
    print("Leave AprilAire Home running in the background. You can close this terminal.")
    webbrowser.open(url)
