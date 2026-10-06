import json
from urllib.parse import quote

import httpx


def media_url(base: str, path: str, token: str) -> str:
    return base.rstrip("/") + path + "?token=" + quote(token, safe="")


class TicketAPI:
    def __init__(self, base: str, username: str, password: str, verify: bool = True):
        self.base = base.rstrip("/")
        self.username = username
        self.password = password
        self.verify = verify
        self.ticket_token = ""

    def _url(self, path: str) -> str:
        return self.base + path

    def create_ticket(self, account_id: str, description: str, camera: bool) -> dict:
        return self._json("POST", "/api/v1/tickets", {"account_id": account_id, "description": description, "camera": camera})

    def media_up(self, ticket_id: str, camera: bool) -> dict:
        body = {"screen": "up", "camera": "up" if camera else "off"}
        return self._json("POST", f"/api/v1/tickets/{ticket_id}/media", body)

    def list_queued(self) -> list:
        return self._json("GET", "/api/v1/tickets").get("tickets") or []

    def claim(self, ticket_id: str, operator_id: str) -> dict:
        return self._json("POST", f"/api/v1/tickets/{ticket_id}/claim", {"operator_id": operator_id})

    def session(self, ticket_id: str) -> dict:
        return self._json("GET", f"/api/v1/tickets/{ticket_id}/session")

    def message(self, ticket_id: str, author: str, body: str) -> dict:
        return self._json("POST", f"/api/v1/tickets/{ticket_id}/messages", {"author": author, "body": body})

    def request_control(self, ticket_id: str, intent: str = "request_control") -> dict:
        return self._json("POST", f"/api/v1/tickets/{ticket_id}/control-requests", {"intent": intent})

    def decide_control(self, ticket_id: str, grant_id: str, allow: bool, scope: str) -> dict:
        return self._json(
            "POST",
            f"/api/v1/tickets/{ticket_id}/control-requests/{grant_id}/decision",
            {"allow": allow, "scope": scope},
        )

    def post_input(self, ticket_id: str, event: dict) -> dict:
        return self._json("POST", f"/api/v1/tickets/{ticket_id}/input", event)

    def finish_input(self, ticket_id: str, action_id: str, phase: str, observation: str) -> dict:
        return self._json(
            "POST",
            f"/api/v1/tickets/{ticket_id}/input/{action_id}/result",
            {"phase": phase, "observation": observation},
        )

    def log_blocked(self, ticket_id: str, intent: str, detail: str, actor: str = "operator") -> dict:
        return self._json(
            "POST",
            f"/api/v1/tickets/{ticket_id}/actions",
            {"actor": actor, "intent": intent, "detail": detail},
        )

    def create_plan(self, ticket_id: str, steps: list[str]) -> dict:
        return self._json("POST", f"/api/v1/tickets/{ticket_id}/plans", {"steps": steps})

    def observe(self, ticket_id: str, step_id: str, body: str) -> dict:
        return self._json("POST", f"/api/v1/tickets/{ticket_id}/steps/{step_id}/observations", {"body": body})

    def complete_step(self, ticket_id: str, step_id: str, body: str) -> dict:
        return self._json(
            "POST",
            f"/api/v1/tickets/{ticket_id}/steps/{step_id}/complete",
            {"seen_on_screen": True, "body": body},
        )

    def add_problem(self, ticket_id: str, parent_step_id: str, description: str, steps: list[str]) -> dict:
        return self._json(
            "POST",
            f"/api/v1/tickets/{ticket_id}/problems",
            {"parent_step_id": parent_step_id, "description": description, "steps": steps},
        )

    def resolve(self, ticket_id: str, role: str, accept: bool, comment: str = "") -> dict:
        return self._json(
            "POST",
            f"/api/v1/tickets/{ticket_id}/resolution",
            {"role": role, "accept": accept, "comment": comment},
        )

    def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {}
        if self.ticket_token:
            headers["X-Ticket-Token"] = self.ticket_token
        try:
            response = httpx.request(
                method,
                self._url(path),
                json=body,
                auth=(self.username, self.password),
                headers=headers,
                timeout=15,
                verify=self.verify,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"сервис заявок недоступен: {exc}") from exc
        if response.status_code >= 400:
            message = response.text
            try:
                message = response.json().get("error", message)
            except json.JSONDecodeError:
                pass
            raise RuntimeError(message)
        if not response.content:
            return {}
        return response.json()
