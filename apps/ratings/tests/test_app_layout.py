from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.contrib.staticfiles import finders
from django.core.management import get_commands
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.template.loader import get_template
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils.module_loading import import_string

from apps.ratings import security, views
from apps.ratings.models import (
    Participant,
    PushDevice,
    RelationshipScore,
    ScoreChange,
)


class RatingsAppLayoutTests(TestCase):
    def test_app_registry_and_model_identity_remain_stable(self):
        self.assertIn(
            "apps.ratings.apps.RatingsConfig",
            settings.INSTALLED_APPS,
        )
        self.assertNotIn("ratings", settings.INSTALLED_APPS)

        app_config = django_apps.get_app_config("ratings")
        self.assertEqual(app_config.name, "apps.ratings")
        self.assertEqual(app_config.label, "ratings")
        self.assertEqual(
            Path(app_config.path).resolve(),
            (Path(settings.BASE_DIR) / "apps" / "ratings").resolve(),
        )

        expected_models = {
            Participant: ("ratings.participant", "participant"),
            RelationshipScore: (
                "ratings.relationshipscore",
                "relationship_score",
            ),
            ScoreChange: ("ratings.scorechange", "score_change"),
            PushDevice: ("ratings.pushdevice", "push_device"),
        }
        for model, (label, table_name) in expected_models.items():
            with self.subTest(model=model.__name__):
                self.assertIs(model._meta.app_config, app_config)
                self.assertEqual(model._meta.app_label, "ratings")
                self.assertEqual(model._meta.label_lower, label)
                self.assertEqual(model._meta.db_table, table_name)

    def test_content_types_and_admin_urls_keep_the_logical_app_label(self):
        models = (Participant, RelationshipScore, ScoreChange, PushDevice)
        expected_model_names = {model._meta.model_name for model in models}

        content_types = ContentType.objects.filter(
            app_label="ratings",
            model__in=expected_model_names,
        )
        self.assertEqual(
            {
                (app_label, model_name)
                for app_label, model_name in content_types.values_list(
                    "app_label",
                    "model",
                )
            },
            {("ratings", model_name) for model_name in expected_model_names},
        )
        self.assertFalse(
            ContentType.objects.filter(
                app_label__in=("apps", "apps_ratings"),
                model__in=expected_model_names,
            ).exists()
        )

        for model in models:
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site.is_registered(model))
                self.assertEqual(
                    reverse(f"admin:ratings_{model._meta.model_name}_changelist"),
                    f"/admin/ratings/{model._meta.model_name}/",
                )

    def test_migration_and_command_discovery_use_the_existing_app_label(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)

        self.assertIn(
            ("ratings", "0006_rename_domain_tables"),
            loader.disk_migrations,
        )
        self.assertTrue(loader.graph.leaf_nodes("ratings"))
        self.assertFalse(loader.graph.leaf_nodes("apps.ratings"))
        self.assertEqual(
            {
                app_label
                for app_label, migration_name in loader.disk_migrations
                if migration_name == "0006_rename_domain_tables"
            },
            {"ratings"},
        )
        self.assertEqual(
            get_commands().get("provision_participants"),
            "apps.ratings",
        )

    def test_configured_dotted_callables_resolve_from_the_moved_package(self):
        self.assertIs(
            import_string(settings.AXES_CLIENT_IP_CALLABLE),
            security.get_client_ip_address,
        )
        self.assertIs(
            import_string(settings.AXES_LOCKOUT_CALLABLE),
            views.login_lockout,
        )

    def test_rating_templates_have_the_expected_physical_origins(self):
        app_template_root = (
            Path(settings.BASE_DIR) / "apps" / "ratings" / "templates" / "ratings"
        )
        for filename in ("_messages.html", "history.html", "home.html", "login.html"):
            with self.subTest(filename=filename):
                template = get_template(f"ratings/{filename}")
                self.assertEqual(
                    Path(template.origin.name).resolve(),
                    (app_template_root / filename).resolve(),
                )

        project_template_root = Path(settings.BASE_DIR) / "templates"
        self.assertFalse((project_template_root / "ratings").exists())
        for filename in ("base.html", "service-worker.js"):
            with self.subTest(filename=filename):
                template = get_template(filename)
                self.assertEqual(
                    Path(template.origin.name).resolve(),
                    (project_template_root / filename).resolve(),
                )

    def test_rating_static_assets_have_one_app_owned_origin(self):
        asset_names = (
            "ratings/app.css",
            "ratings/app.js",
            "ratings/notifications.js",
            "ratings/offline.html",
            "ratings/manifest.webmanifest",
            "ratings/icons/apple-touch-icon.png",
            "ratings/icons/icon-192.png",
            "ratings/icons/icon-512.png",
            "ratings/icons/icon-source.svg",
            "ratings/icons/maskable-icon-512.png",
            "ratings/icons/maskable-icon-source.svg",
        )
        app_static_root = Path(settings.BASE_DIR) / "apps" / "ratings" / "static"

        for asset_name in asset_names:
            with self.subTest(asset_name=asset_name):
                matches = finders.find(asset_name, all=True)
                self.assertEqual(
                    [Path(match).resolve() for match in matches],
                    [(app_static_root / asset_name).resolve()],
                )

    def test_public_url_names_paths_and_views_remain_stable(self):
        routes = (
            ("home", "/", views.home),
            ("login", "/login/", views.login_view),
            ("logout", "/logout/", views.logout_view),
            ("change-score", "/score/change/", views.change_score_view),
            ("history", "/history/", views.history_view),
            (
                "register-push-device",
                "/notifications/devices/register/",
                views.register_push_device,
            ),
            (
                "unregister-push-device",
                "/notifications/devices/unregister/",
                views.unregister_push_device,
            ),
            ("service-worker", "/service-worker.js", views.service_worker),
            ("health", "/health/", views.health),
        )

        for url_name, path, expected_view in routes:
            with self.subTest(url_name=url_name):
                self.assertEqual(reverse(url_name), path)
                match = resolve(path)
                self.assertEqual(match.url_name, url_name)
                self.assertIs(match.func, expected_view)
