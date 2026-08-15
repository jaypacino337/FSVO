#!/usr/bin/env python3
"""Webhook receiver for your licensed FSVZO's alerts.

Point TradingView / terminal alerts at  POST http://host:8422/signal  with:

    {"timeframe": "1h", "direction": 0.75}      # -1 .. +1
or  {"timeframe": "4h", "state": "strong_bull"} # states map to directions

Each alert updates signals.json, which ExternalSignal blends into the
confluence score the market maker consumes. Stale timeframes decay to 0.
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

STATE_DIRECTIONS = {
    "strong_bull": 1.0, "bull": 0.5, "neutral": 0.0,
    "bear": -0.5, "strong_bear": -1.0,
    "overbought": -0.75, "oversold": 0.75,
}


def make_handler(state_file: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/signal":
                self.send_error(404)
                return
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                payload = json.loads(body)
                tf = str(payload["timeframe"])
                if "direction" in payload:
                    direction = float(payload["direction"])
                else:
                    direction = STATE_DIRECTIONS[str(payload["state"])]
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                self.send_error(400, f"bad payload: {exc}")
                return

            state = {}
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text())
                except json.JSONDecodeError:
                    state = {}
            state[tf] = {"direction": max(-1.0, min(1.0, direction)), "ts": time.time()}
            state_file.write_text(json.dumps(state, indent=2))

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, fmt, *args):
            print(f"[signal] {fmt % args}")

    return Handler


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8422)
    p.add_argument("--state-file", default="signals.json")
    args = p.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), make_handler(Path(args.state_file)))
    print(f"Listening on :{args.port} — POST /signal, writing {args.state_file}")
    server.serve_forever()


if __name__ == "__main__":
    main()
