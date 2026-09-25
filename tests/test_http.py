import json

import pytest
import requests

from scripts.http import ApiClient


def fake_response(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def client_returning(response, monkeypatch):
    client = ApiClient("https://api.example.com", {}, max_per_second=1000)
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: response)
    return client


def test_error_message_keeps_the_api_code_but_not_the_body(monkeypatch):
    body = {"code": "validation_error", "message": "Title 'My secret task' is invalid"}
    client = client_returning(fake_response(400, body), monkeypatch)

    with pytest.raises(requests.HTTPError) as error:
        client.request("PATCH", "/pages/1")

    assert str(error.value) == "PATCH /pages/1 → HTTP 400 (validation_error)"


def test_todoist_error_tag_is_used_as_code(monkeypatch):
    client = client_returning(fake_response(404, {"error_tag": "NOT_FOUND"}), monkeypatch)

    with pytest.raises(requests.HTTPError, match=r"HTTP 404 \(NOT_FOUND\)$"):
        client.request("GET", "/tasks/1")


def test_error_without_json_body(monkeypatch):
    response = requests.Response()
    response.status_code = 403
    response._content = b"Forbidden"
    client = client_returning(response, monkeypatch)

    with pytest.raises(requests.HTTPError, match=r"HTTP 403$"):
        client.request("GET", "/user")
