import logging
import threading
import time

import httpx

from support_protocol.errors import ServiceUnavailable, TicketError

log = logging.getLogger(__name__)


class TicketAPI:
    def __init__(self, base: str, username: str, password: str, verify: bool = True):
        self.base = base.rstrip("/")
        self.username = username
        self.password = password
        self.verify = verify
        self.ticket_token = ""
        self._log_lock = threading.Lock()
        self._last_error = None
        self._last_error_at = 0.0

    def _url(self, path: str) -> str:
        return self.base + path

    def create_ticket(self, account_id: str, description: str, camera: bool) -> dict:
        return self._json("POST", "/api/v1/tickets", {"account_id": account_id, "description": description, "camera": camera})

    def list_queued(self) -> list:
        return self._json("GET", "/api/v1/tickets").get("tickets") or []

    def claim(self, ticket_id: str) -> dict:
        return self._json("POST", f"/api/v1/tickets/{ticket_id}/claim", {})

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

    def health(self):
        return self._json("GET", "/health")

    def _log_failure(self, error):
        # Периодический опрос не должен заполнять журнал одинаковыми traceback.
        key = (type(error), str(error))
        with self._log_lock:
            now = time.monotonic()
            if key != self._last_error or now - self._last_error_at >= 30:
                log.error("Ticket service request failed", exc_info=(type(error), error, error.__traceback__))
                self._last_error, self._last_error_at = key, now

    def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {}
        if self.ticket_token:
            headers["X-Ticket-Token"] = self.ticket_token
        try:
            response = httpx.request(
                method, self._url(path), json=body, auth=(self.username, self.password),
                # Принятие и закрытие заявки включают запрос к relay (до 5 с).
                # Сохраняем прежний бюджет POST, иначе сервер может уже изменить
                # заявку после того, как GUI перестал ждать результат.
                headers=headers, timeout=httpx.Timeout(3.0, connect=1.5, read=15.0 if method == "POST" else 3.0),
                verify=self.verify,
            )
            response.raise_for_status()
            result = response.json() if response.content else {}
            if not isinstance(result, dict):
                raise ValueError("expected JSON object from ticket service")
        except httpx.HTTPStatusError as exc:
            self._log_failure(exc)
            code = exc.response.status_code
            if code >= 500:
                raise ServiceUnavailable() from exc
            messages = {
                401: "Не удалось войти. Проверьте учётные данные в конфигурации.",
                403: "Нет разрешения на это действие.",
                404: "Заявка или ресурс не найдены.",
                409: "Состояние заявки изменилось. Обновите данные и повторите действие.",
            }
            raise TicketError(messages.get(code, "Сервис отклонил запрос. Проверьте введённые данные.")) from exc
        except (httpx.HTTPError, ValueError) as exc:
            self._log_failure(exc)
            raise ServiceUnavailable() from exc
        with self._log_lock:
            self._last_error = None
        return result
