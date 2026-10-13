import uuid

import httpx


class Relay:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token
        self.http = httpx.Client(timeout=5)

    def open(self, client_id: uuid.UUID, ticket_id: uuid.UUID, camera: bool) -> uuid.UUID:
        response = self.http.post(
            self.base + "/api/v1/tunnels",
            json={"client_id": str(client_id), "ticket_id": str(ticket_id), "camera": camera},
            headers={"Authorization": "Bearer " + self.token},
        )
        if response.status_code != 201:
            raise RuntimeError(f"ретранслятор ответил {response.status_code}")
        return uuid.UUID(response.json()["tunnel_id"])

    def close(self, ticket_id: uuid.UUID) -> None:
        response = self.http.delete(
            self.base + "/api/v1/tunnels/" + str(ticket_id),
            headers={"Authorization": "Bearer " + self.token},
        )
        if response.status_code not in (204, 404):
            raise RuntimeError(f"ретранслятор ответил {response.status_code}")
