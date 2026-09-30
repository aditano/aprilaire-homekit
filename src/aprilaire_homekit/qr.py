"""HomeKit setup QR as SVG, drawn on this computer."""

from __future__ import annotations

import io

import pyqrcode


def setup_qr_svg(uri: str) -> str:
    buffer = io.BytesIO()
    pyqrcode.create(uri, error="M").svg(
        buffer,
        scale=6,
        quiet_zone=2,
        module_color="#1d1a16",
        background="#fffdf8",
        xmldecl=False,
        svgns=True,
        omithw=False,
    )
    svg = buffer.getvalue().decode("utf-8")
    if "<svg" not in svg:
        raise ValueError("QR encoder did not return an SVG.")
    return svg
