import uuid

import httpx


class OpenedTunnel:
    def __init__(self, tunnel_id: uuid.UUID, client_secret: str, operator_secret: str):
        self.tunnel_id = tunnel_id
        self.client_secret = client_secret
        self.operator_secret = operator_secret


class Relay:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token
        self.http = httpx.Client(timeout=5)

    def open(self, client_id: uuid.UUID, ticket_id: uuid.UUID, camera: bool) -> OpenedTunnel:
        response = self.http.post(
            self.base + "/api/v1/tunnels",
            json={"client_id": str(client_id), "ticket_id": str(ticket_id), "camera": camera},
            headers={"Authorization": "Bearer " + self.token},
        )
        if response.status_code != 201:
            raise RuntimeError(f"ретранслятор ответил {response.status_code}")
        body = response.json()
        client_secret = str(body.get("client_secret") or "")
        operator_secret = str(body.get("operator_secret") or "")
        if len(client_secret) != 64 or len(operator_secret) != 64:
            raise RuntimeError("ретранслятор не выдал секреты сессии")
        return OpenedTunnel(uuid.UUID(body["tunnel_id"]), client_secret, operator_secret)

    def close(self, ticket_id: uuid.UUID) -> None:
        response = self.http.delete(
            self.base + "/api/v1/tunnels/" + str(ticket_id),
            headers={"Authorization": "Bearer " + self.token},
        )
        if response.status_code not in (204, 404):
            raise RuntimeError(f"ретранслятор ответил {response.status_code}")
