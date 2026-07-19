import tomllib
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class RailwayConfigurationTests(SimpleTestCase):
    def test_pre_deploy_only_runs_migrations(self):
        config_path = Path(settings.BASE_DIR) / "railway.toml"
        config = tomllib.loads(config_path.read_text())
        raw_commands = config["deploy"]["preDeployCommand"]
        commands = [raw_commands] if isinstance(raw_commands, str) else raw_commands

        self.assertEqual(commands, ["python manage.py migrate --noinput"])
        deploy_commands = "\n".join(str(value) for value in config["deploy"].values())
        build_commands = "\n".join(str(value) for value in config["build"].values())
        self.assertNotIn("provision_participants", deploy_commands)
        self.assertNotIn("provision_participants", build_commands)
