"""Размер корневого окна X11 в физических пикселях."""
import os
import subprocess

from support_protocol.media import CaptureGeometry, x11_display


def read_capture_geometry() -> CaptureGeometry:
    display = x11_display(os.environ.get("DISPLAY", ""))
    result = subprocess.run(
        ["xdotool", "getdisplaygeometry"],
        env=dict(os.environ, DISPLAY=display),
        capture_output=True,
        text=True,
        check=True,
        timeout=3,
    )
    width, height = map(int, result.stdout.split())
    return CaptureGeometry(width, height)
