from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .state_contract import ContractError, load_state


class WorkbenchHandler(SimpleHTTPRequestHandler):
    state_path: Path
    journal_path: Path | None
    static_dir: Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.static_dir), **kwargs)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/state":
            try:
                validated = load_state(self.state_path)
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            self._json(
                HTTPStatus.OK,
                {"state": validated.payload, "snapshot_sha256": validated.sha256},
            )
            return
        if path == "/api/health":
            self._json(HTTPStatus.OK, {"status": "ok", "mode": "read_only"})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/journal":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if self.journal_path is None:
            self._json(HTTPStatus.FORBIDDEN, {"error": "journal disabled"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64_000:
                raise ValueError("invalid body length")
            incoming = json.loads(self.rfile.read(length).decode("utf-8"))
            validated = load_state(self.state_path)
            stance = str(incoming.get("stance", "WATCH")).upper()
            if stance not in {"WATCH", "AVOID", "INTERESTED", "NO_ACTION"}:
                raise ValueError("invalid stance")
            note = str(incoming.get("note", ""))[:4000]
            record = {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "ticker": validated.payload["asset"]["ticker"],
                "stance": stance,
                "note": note,
                "snapshot_sha256": validated.sha256,
                "contract_version": validated.payload["contract_version"],
            }
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except (ValueError, json.JSONDecodeError, OSError, ContractError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._json(HTTPStatus.CREATED, {"saved": True, "record": record})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Investment Workbench V0 local server")
    parser.add_argument("--state", required=True, help="InvestmentState JSON file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--journal", help="Optional JSONL journal output path")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    state_path = Path(args.state).resolve()
    validated = load_state(state_path)
    static_dir = Path(__file__).resolve().parent / "static"
    handler = type(
        "ConfiguredWorkbenchHandler",
        (WorkbenchHandler,),
        {
            "state_path": state_path,
            "journal_path": Path(args.journal).resolve() if args.journal else None,
            "static_dir": static_dir,
        },
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Investment Workbench V0: http://{args.host}:{args.port}")
    print(f"state={state_path}")
    print(f"snapshot_sha256={validated.sha256}")
    print(f"journal={'disabled' if not args.journal else Path(args.journal).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
