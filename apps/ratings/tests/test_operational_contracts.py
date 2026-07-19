import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.db import OperationalError, connections

_RAILWAY_ENVIRONMENT_MARKERS = (
    "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_ENVIRONMENT",
)


def _railway_config():
    config_path = Path(django_settings.BASE_DIR) / "railway.toml"
    return tomllib.loads(config_path.read_text())


def _load_settings_with_railway_marker(marker_name: str) -> dict[str, object]:
    environment = os.environ.copy()
    for name in (
        *_RAILWAY_ENVIRONMENT_MARKERS,
        "DEBUG",
        "RAILWAY_PUBLIC_DOMAIN",
        "SECURE_HSTS_SECONDS",
        "SECURE_SSL_REDIRECT",
    ):
        environment.pop(name, None)

    environment.update(
        {
            marker_name: "settings-test-railway-environment",
            "DATABASE_URL": "sqlite:///:memory:",
            "FIREBASE_SERVICE_ACCOUNT_JSON": "",
            "FIREBASE_VAPID_PUBLIC_KEY": "",
            "FIREBASE_WEB_CONFIG_JSON": "{}",
            "PUBLIC_BASE_URL": "",
            "PUSH_NOTIFICATIONS_ENABLED": "0",
            "SECRET_KEY": "settings-test-only",
        }
    )
    script = """
import json
from config import settings

print(json.dumps({
    "csrfCookieSecure": settings.CSRF_COOKIE_SECURE,
    "debug": settings.DEBUG,
    "healthcheckHostAllowed": "healthcheck.railway.app" in settings.ALLOWED_HOSTS,
    "isRailway": settings.IS_RAILWAY,
    "sessionCookieSecure": settings.SESSION_COOKIE_SECURE,
    "sslRedirect": settings.SECURE_SSL_REDIRECT,
    "staticfilesBackend": settings.STORAGES["staticfiles"]["BACKEND"],
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        cwd=django_settings.BASE_DIR,
        env=environment,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("marker_name", _RAILWAY_ENVIRONMENT_MARKERS)
def test_railway_environment_markers_enable_production_defaults(marker_name):
    settings_snapshot = _load_settings_with_railway_marker(marker_name)

    assert settings_snapshot == {
        "csrfCookieSecure": True,
        "debug": False,
        "healthcheckHostAllowed": True,
        "isRailway": True,
        "sessionCookieSecure": True,
        "sslRedirect": True,
        "staticfilesBackend": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    }


def test_railway_deployment_runs_migrations_without_provisioning_participants():
    config = _railway_config()
    pre_deploy_command = config["deploy"]["preDeployCommand"]

    assert pre_deploy_command == "python manage.py migrate --noinput"

    automatic_commands = [
        config["build"]["buildCommand"],
        pre_deploy_command,
        config["deploy"]["startCommand"],
    ]
    assert all(
        "provision_participants" not in command for command in automatic_commands
    )


@pytest.mark.django_db
def test_railway_healthcheck_reaches_a_ready_database_without_https_redirect(
    client,
    settings,
):
    settings.SECURE_SSL_REDIRECT = True
    healthcheck_path = _railway_config()["deploy"]["healthcheckPath"]

    response = client.get(healthcheck_path)

    assert response.status_code == 200


@pytest.mark.django_db
def test_railway_healthcheck_reports_database_unavailability(client, settings):
    settings.SECURE_SSL_REDIRECT = True
    healthcheck_path = _railway_config()["deploy"]["healthcheckPath"]

    with patch.object(
        connections["default"],
        "cursor",
        side_effect=OperationalError("database unavailable"),
    ):
        response = client.get(healthcheck_path)

    assert response.status_code == 503
