from __future__ import annotations

import logging
from typing import Any, Dict

import requests
from flask import Flask, jsonify, render_template, request
from requests.auth import HTTPBasicAuth

app = Flask(__name__)

logger = logging.getLogger(__name__)


class JiraConnectionError(Exception):
    """Custom exception raised when connecting to Jira fails."""


class JiraClient:
    """Minimal Jira client able to verify authentication credentials."""

    def __init__(self, base_url: str, email: str, api_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"

    def test_connection(self) -> Dict[str, Any]:
        """Call Jira's `/myself` endpoint to verify the credentials."""

        url = self._build_url("/rest/api/3/myself")
        try:
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.email, self.api_token),
                timeout=10,
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as error:  # pragma: no cover - logging aid
            logger.warning("Jira responded with an error", exc_info=error)
            raise JiraConnectionError(str(error)) from error
        except requests.exceptions.RequestException as error:  # pragma: no cover - logging aid
            logger.error("Unable to reach Jira", exc_info=error)
            raise JiraConnectionError(str(error)) from error

        return response.json()


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


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/test-connection", methods=["POST"])
def test_connection() -> Any:
    try:
        payload = validate_payload(request.get_json(silent=True) or {})
    except ValueError as error:
        return jsonify({"ok": False, "message": str(error)}), 400

    jira_client = JiraClient(
        base_url=payload["baseUrl"],
        email=payload["email"],
        api_token=payload["apiToken"],
    )

    try:
        user_data = jira_client.test_connection()
    except JiraConnectionError as error:
        return (
            jsonify({"ok": False, "message": f"No se pudo conectar: {error}"}),
            502,
        )

    display_name = user_data.get("displayName") or user_data.get("name") or payload["email"]
    return jsonify(
        {
            "ok": True,
            "message": f"Conexión exitosa. Usuario autenticado: {display_name}.",
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
