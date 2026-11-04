"""Локальный веб-сервер на стандартной библиотеке (без внешних зависимостей).

Сервер stateless: браузер держит загруженные файлы у себя и присылает их
вместе с датой проверки в каждом запросе. Для прототипа на десятки документов
это проще и надёжнее, чем хранилище; при росте объёма меняется (см. README).
"""
from __future__ import annotations

import base64
import json
import mimetypes
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import serialize
from .service import DEFAULT_CHECK_DATE, analyze_files, ask

STATIC = Path(__file__).parent / "static"
SAMPLES = Path(__file__).resolve().parent.parent / "documents"
MAX_BODY = 20 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "TermsNavigator/0.1"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._file(STATIC / "index.html")
        if path.startswith("/static/"):
            target = (STATIC / path[len("/static/"):]).resolve()
            if STATIC.resolve() in target.parents and target.is_file():
                return self._file(target)
        if path == "/api/samples":
            files = [{"name": p.name, "content_b64": base64.b64encode(p.read_bytes()).decode()}
                     for p in sorted(SAMPLES.glob("*.txt"))]
            return self._json({"files": files, "default_check_date": DEFAULT_CHECK_DATE.isoformat()})
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                return self._json({"error": "Слишком большой запрос"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            body = json.loads(self.rfile.read(length) or b"{}")
            files = [(f["name"], base64.b64decode(f["content_b64"])) for f in body.get("files", [])]
            check = date.fromisoformat(body.get("check_date") or DEFAULT_CHECK_DATE.isoformat())
            analysis = analyze_files(files, check)
            if self.path == "/api/analyze":
                return self._json(serialize.analysis(analysis))
            if self.path == "/api/ask":
                return self._json(serialize.answer(ask(analysis, body["question"])))
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError) as e:
            self._json({"error": f"Некорректный запрос: {e}"}, HTTPStatus.BAD_REQUEST)

    def _file(self, path: Path) -> None:
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # тише в консоли
        if not self.path.startswith("/static/"):
            super().log_message(fmt, *args)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Сверка условий поставки: http://{host}:{port}  (Ctrl+C — остановить)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
