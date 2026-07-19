import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.db import OperationalError, connections


def _railway_config():
    config_path = Path(django_settings.BASE_DIR) / "railway.toml"
    return tomllib.loads(config_path.read_text())


def test_railway_deployment_runs_migrations_without_provisioning_participants():
    config = _railway_config()
    pre_deploy_command = config["deploy"]["preDeployCommand"]

    assert "manage.py migrate" in pre_deploy_command
    assert "--noinput" in pre_deploy_command

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
