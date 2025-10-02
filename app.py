"""Minimal Jira connection tester using only the Python standard library."""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

APP_ROOT = Path(__file__).parent
TEMPLATE_DIR = APP_ROOT / "templates"
STATIC_DIR = APP_ROOT / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
LOGGER = logging.getLogger(__name__)


class JiraConnectionError(Exception):
    """Raised when the Jira API cannot be reached or returns an error."""


class JiraClient:
    """Minimal Jira client able to verify authentication credentials."""

    def __init__(self, base_url: str, email: str, api_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token

    def _build_url(self, path: str) -> str:
        if path.startswith("/"):
            return f"{self.base_url}{path}"
        return f"{self.base_url}/{path}"

    def test_connection(self) -> Dict[str, Any]:
        """Call Jira's `/myself` endpoint to verify the credentials."""

        url = self._build_url("/rest/api/3/myself")
        credentials = f"{self.email}:{self.api_token}".encode("utf-8")
        encoded_credentials = base64.b64encode(credentials).decode("ascii")

        request = Request(url, method="GET")
        request.add_header("Authorization", f"Basic {encoded_credentials}")
        request.add_header("Accept", "application/json")

        try:
            with urlopen(request, timeout=10) as response:  # noqa: S310 - Jira URL provided by the user
                charset = response.headers.get_content_charset() or "utf-8"
                raw_body = response.read().decode(charset)
        except HTTPError as error:
            LOGGER.warning("Jira responded with an error", exc_info=error)
            raise JiraConnectionError(f"{error.code} {error.reason}") from error
        except URLError as error:
            LOGGER.error("Unable to reach Jira", exc_info=error)
            raise JiraConnectionError(str(error.reason)) from error

        if not raw_body:
            return {}

        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as error:  # pragma: no cover - defensive programming
            raise JiraConnectionError("Respuesta de Jira inválida") from error


def validate_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    """Ensure that the payload contains the required Jira configuration fields."""

    base_url = payload.get("baseUrl", "").strip()
    email = payload.get("email", "").strip()
    api_token = payload.get("apiToken", "").strip()

    missing_fields = [
        field_name
        for field_name, value in (
            ("baseUrl", base_url),
            ("email", email),
            ("apiToken", api_token),
        )
        if not value
    ]

    if missing_fields:
        raise ValueError(
            "Los siguientes campos son obligatorios: " + ", ".join(missing_fields)
        )

    return {"baseUrl": base_url, "email": email, "apiToken": api_token}


class JiraRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves the static assets and exposes the Jira test endpoint."""

    server_version = "JiraProxy/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - signature defined by BaseHTTPRequestHandler
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path in {"/", "/index.html"}:
            self._serve_file(TEMPLATE_DIR / "index.html", "text/html; charset=utf-8")
            return

        if self.path.startswith("/static/"):
            relative_path = self.path.removeprefix("/static/")
            self._serve_file(STATIC_DIR / relative_path)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Recurso no encontrado")

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path != "/test-connection":
            self.send_error(HTTPStatus.NOT_FOUND, "Recurso no encontrado")
            return

        content_length_header = self.headers.get("Content-Length")
        try:
            content_length = int(content_length_header or 0)
        except ValueError:
            self._write_json({"ok": False, "message": "Solicitud inválida."}, HTTPStatus.BAD_REQUEST)
            return

        raw_body = self.rfile.read(content_length) if content_length else b""

        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            self._write_json(
                {"ok": False, "message": "El cuerpo debe ser un JSON válido."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            cleaned_payload = validate_payload(payload)
        except ValueError as error:
            self._write_json({"ok": False, "message": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        jira_client = JiraClient(
            base_url=cleaned_payload["baseUrl"],
            email=cleaned_payload["email"],
            api_token=cleaned_payload["apiToken"],
        )

        try:
            user_data = jira_client.test_connection()
        except JiraConnectionError as error:
            self._write_json(
                {"ok": False, "message": f"No se pudo conectar: {error}"},
                HTTPStatus.BAD_GATEWAY,
            )
            return

        display_name = (
            user_data.get("displayName")
            or user_data.get("name")
            or cleaned_payload["email"]
        )

        self._write_json(
            {
                "ok": True,
                "message": f"Conexión exitosa. Usuario autenticado: {display_name}.",
            }
        )

    def _serve_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Recurso no encontrado")
            return

        if content_type is None:
            guessed_type, _ = mimetypes.guess_type(str(path))
            content_type = guessed_type or "application/octet-stream"
            if content_type.startswith("text/") and "charset" not in content_type:
                content_type += "; charset=utf-8"

        data = path.read_bytes()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    server_address = (host, port)

    httpd = ThreadingHTTPServer(server_address, JiraRequestHandler)
    LOGGER.info("Servidor iniciado en http://%s:%s", host, port)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Deteniendo el servidor...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_server()
