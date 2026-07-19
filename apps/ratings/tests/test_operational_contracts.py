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


def _railway_cron_config():
    config_path = Path(django_settings.BASE_DIR) / "railway.cron.toml"
    return tomllib.loads(config_path.read_text())


def _load_settings_with_railway_marker(marker_name: str) -> dict[str, object]:
    environment = os.environ.copy()
    for name in (
        *_RAILWAY_ENVIRONMENT_MARKERS,
        "DEBUG",
        "MEDIA_UPLOADS_ENABLED",
        "R2_ACCESS_KEY_ID",
        "R2_BUCKET_NAME",
        "R2_ENDPOINT_URL",
        "R2_REGION_NAME",
        "R2_SECRET_ACCESS_KEY",
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


def _load_media_settings(overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in (
        "MEDIA_DOWNLOAD_URL_TTL_SECONDS",
        "MEDIA_UPLOADS_ENABLED",
        "MEDIA_UPLOAD_URL_TTL_SECONDS",
        "R2_ACCESS_KEY_ID",
        "R2_BUCKET_NAME",
        "R2_ENDPOINT_URL",
        "R2_REGION_NAME",
        "R2_SECRET_ACCESS_KEY",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "DEBUG": "True",
            "FIREBASE_SERVICE_ACCOUNT_JSON": "",
            "FIREBASE_VAPID_PUBLIC_KEY": "",
            "FIREBASE_WEB_CONFIG_JSON": "{}",
            "PUSH_NOTIFICATIONS_ENABLED": "0",
            **overrides,
        }
    )
    script = """
import json
from config import settings

print(json.dumps({
    "available": settings.MEDIA_UPLOADS_AVAILABLE,
    "downloadTtl": settings.MEDIA_DOWNLOAD_URL_TTL_SECONDS,
    "enabled": settings.MEDIA_UPLOADS_ENABLED,
    "region": settings.R2_REGION_NAME,
    "uploadTtl": settings.MEDIA_UPLOAD_URL_TTL_SECONDS,
}))
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        cwd=django_settings.BASE_DIR,
        env=environment,
        text=True,
    )


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


def test_railway_media_cleanup_cron_is_a_daily_one_shot_without_migrations():
    config = _railway_cron_config()

    assert config == {
        "build": {"builder": "RAILPACK"},
        "deploy": {
            "cronSchedule": "0 18 * * *",
            "restartPolicyType": "NEVER",
            "startCommand": "python manage.py cleanup_media_uploads --limit 100",
        },
    }


def test_media_uploads_are_disabled_without_explicit_r2_configuration():
    completed = _load_media_settings({})

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "available": False,
        "downloadTtl": 300,
        "enabled": False,
        "region": "auto",
        "uploadTtl": 900,
    }


def test_media_uploads_fail_fast_when_enabled_without_r2_credentials():
    completed = _load_media_settings({"MEDIA_UPLOADS_ENABLED": "True"})

    assert completed.returncode != 0
    assert "Media uploads are enabled but the R2 endpoint" in completed.stderr


def test_valid_private_r2_configuration_enables_media_uploads():
    completed = _load_media_settings(
        {
            "MEDIA_UPLOADS_ENABLED": "True",
            "R2_ACCESS_KEY_ID": "test-access-key-only",
            "R2_BUCKET_NAME": "test-private-media",
            "R2_ENDPOINT_URL": ("https://test-account-id.r2.cloudflarestorage.com"),
            "R2_SECRET_ACCESS_KEY": "test-secret-key-only",
        }
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "available": True,
        "downloadTtl": 300,
        "enabled": True,
        "region": "auto",
        "uploadTtl": 900,
    }


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
