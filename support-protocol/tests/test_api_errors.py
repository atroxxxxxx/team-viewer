import logging
import httpx
import pytest
from support_protocol.api import TicketAPI
from support_protocol.errors import CONNECTION_MESSAGE, ServiceUnavailable, TicketError


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout])
def test_short_timeout_safe_error_logging_and_recovery(monkeypatch, caplog, error_type):
    api = TicketAPI("http://localhost:8080", "test", "secret")
    original = error_type("[Errno 61] socket 127.0.0.1:8080")
    def unavailable(*args, **kwargs):
        assert kwargs["timeout"].connect == 1.5
        assert kwargs["timeout"].read == 3
        raise original
    monkeypatch.setattr(httpx, "request", unavailable)
    with caplog.at_level(logging.ERROR):
        for _ in range(2):
            with pytest.raises(ServiceUnavailable) as caught:
                api.list_queued()
            assert str(caught.value) == CONNECTION_MESSAGE
            assert caught.value.__cause__ is original
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info[1] is original
    monkeypatch.setattr(httpx, "request", lambda *_a, **_k: httpx.Response(200, json={"tickets": []}, request=httpx.Request("GET", api.base)))
    assert api.list_queued() == []
    assert api._last_error is None


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 500, 502, 503])
def test_server_body_not_exposed(monkeypatch, status):
    response = httpx.Response(status, text="Traceback: [Errno 61] internal socket", request=httpx.Request("GET", "http://localhost"))
    monkeypatch.setattr(httpx, "request", lambda *_a, **_k: response)
    with pytest.raises(TicketError) as caught:
        TicketAPI("http://localhost", "u", "p").health()
    assert "Errno" not in str(caught.value)
    assert "Traceback" not in str(caught.value)


def test_bad_json_is_logged_and_safe(monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda *_a, **_k: httpx.Response(200, text="<debug>", request=httpx.Request("GET", "http://localhost")))
    with pytest.raises(ServiceUnavailable):
        TicketAPI("http://localhost", "u", "p").health()


@pytest.mark.parametrize("operation", ["claim", "create", "resolve", "input"])
def test_mutations_can_wait_for_backend_relay_without_extending_connect_timeout(monkeypatch, operation):
    api = TicketAPI("http://localhost", "user", "password")
    def request(method, url, **kwargs):
        assert method == "POST"
        assert kwargs["timeout"].connect == 1.5
        assert kwargs["timeout"].read == 15
        assert kwargs["timeout"].write == 3
        return httpx.Response(200, json={}, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, "request", request)
    if operation == "claim":
        api.claim("ticket")
    elif operation == "create":
        api.create_ticket("account", "description", False)
    elif operation == "resolve":
        api.resolve("ticket", "operator", True)
    else:
        api.post_input("ticket", {"type": "text"})
