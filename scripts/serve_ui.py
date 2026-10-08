#!/usr/bin/env python3
"""Lightweight static HTTP server for the ATLAS Enterprise Demonstration UI.

Usage:
    python scripts/serve_ui.py [PORT]
Default port: 8080
"""
from __future__ import annotations

import functools
import http.server
import socketserver
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
UI_DIR = WORKSPACE / "ui"


class AtlasUIHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the UI directory with CORS enabled to allow local API interactions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Request-ID")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
    print("=" * 65)
    print("  ATLAS — Evidence-Grounded Enterprise Search Platform")
    print(f"  Demonstration UI Server: http://127.0.0.1:{port}/")
    print(f"  Root Directory: {UI_DIR}")
    print("=" * 65)
    print("Press Ctrl+C to terminate the UI server.")

    with socketserver.TCPServer(("127.0.0.1", port), AtlasUIHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down demonstration UI server.")


if __name__ == "__main__":
    main()
