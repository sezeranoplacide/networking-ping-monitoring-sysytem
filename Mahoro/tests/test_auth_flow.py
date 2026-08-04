from pathlib import Path

import pytest
from werkzeug.security import check_password_hash

import app as flask_app
from ping_monitor.device_manager import DeviceManager


@pytest.fixture()
def client(tmp_path: Path):
    db_path = tmp_path / "ping_monitor.sqlite3"
    manager = DeviceManager(db_path=str(db_path))
    flask_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    flask_app.dm = manager

    with flask_app.app.test_client() as test_client:
        yield test_client, manager


def test_login_without_mfa_code_allows_registered_user(client) -> None:
    test_client, manager = client
    manager.create_user(username="operator1", password="Password123", role="operator")

    response = test_client.post(
        "/login",
        data={"username": "operator1", "password": "Password123"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_operator_cannot_create_devices(client) -> None:
    test_client, manager = client
    manager.create_user(username="operator2", password="Password123", role="operator")

    test_client.post(
        "/login",
        data={"username": "operator2", "password": "Password123"},
        follow_redirects=False,
    )

    response = test_client.post(
        "/api/devices",
        json={"name": "Router-1", "ip_address": "192.168.1.1"},
    )

    assert response.status_code == 403


def test_admin_can_manage_network_gateway(client) -> None:
    test_client, manager = client
    manager.create_user(username="admin", password="Password123", role="admin")

    test_client.post(
        "/login",
        data={"username": "admin", "password": "Password123"},
        follow_redirects=False,
    )

    response = test_client.post(
        "/api/settings/network-gateway",
        json={"gateway_ip": "192.168.1.254"},
    )

    assert response.status_code == 200
    assert response.get_json()["gateway_ip"] == "192.168.1.254"

    response = test_client.get("/api/settings/network-gateway")
    assert response.status_code == 200
    assert response.get_json()["gateway_ip"] == "192.168.1.254"


def test_existing_admin_account_is_reset_to_default_credentials(client) -> None:
    _, manager = client
    manager.create_user(username="admin", password="OldPassword123", role="operator")

    manager.ensure_default_admin()

    admin_user = manager.get_user_by_username("admin")
    assert admin_user is not None
    assert admin_user["role"] == "admin"
    assert check_password_hash(admin_user["password_hash"], "Admin12345!")
