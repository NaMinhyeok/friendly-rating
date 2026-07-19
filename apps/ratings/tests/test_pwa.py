import json
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse
from pytest_django.asserts import assertContains, assertNotContains
from whitenoise.middleware import WhiteNoiseMiddleware


@pytest.fixture
def authenticated_client(client, participant_pair):
    client.force_login(participant_pair.first.user)
    return client


def test_manifest_uses_the_web_app_manifest_content_type():
    middleware = WhiteNoiseMiddleware(lambda request: None)

    assert (
        middleware.media_types.get_type("manifest.webmanifest")
        == "application/manifest+json"
    )


def test_manifest_declares_standalone_app_and_required_icons():
    manifest_path = finders.find("ratings/manifest.webmanifest")
    assert manifest_path is not None

    manifest = json.loads(Path(manifest_path).read_text())

    assert manifest["id"] == "/"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert "192x192" in {icon["sizes"] for icon in manifest["icons"]}
    assert "512x512" in {icon["sizes"] for icon in manifest["icons"]}
    assert "maskable" in {icon["purpose"] for icon in manifest["icons"]}


def test_service_worker_is_available_at_its_public_root_path(client):
    response = client.get("/service-worker.js")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/javascript"
    assert response.headers["Service-Worker-Allowed"] == "/"


@pytest.mark.django_db
def test_home_includes_installable_pwa_metadata(authenticated_client):
    response = authenticated_client.get(reverse("home"))

    assertContains(response, 'rel="manifest"')
    assertContains(response, 'rel="apple-touch-icon"')
    assertContains(response, "apple-mobile-web-app-capable")


@pytest.mark.django_db
def test_notification_client_is_hidden_when_push_is_unavailable(
    authenticated_client,
):
    response = authenticated_client.get(reverse("home"))

    assertNotContains(response, "data-notification-settings")
    assertNotContains(response, "ratings/notifications.js")


@pytest.mark.django_db
def test_notification_client_receives_only_public_configuration(
    authenticated_client,
    settings,
):
    settings.PUSH_NOTIFICATIONS_AVAILABLE = True
    settings.FIREBASE_WEB_CONFIG = {
        "apiKey": "test-api-key",
        "appId": "test-app-id",
        "authDomain": "test.firebaseapp.com",
        "messagingSenderId": "123456",
        "projectId": "test-project",
    }
    settings.FIREBASE_VAPID_PUBLIC_KEY = "B" + "A" * 86

    response = authenticated_client.get(reverse("home"))

    assertContains(response, "data-notification-settings")
    assertContains(response, "ratings/notifications.js")
    assertContains(response, "test-project")
    assertNotContains(response, "private_key")


def test_notification_client_sends_the_rendered_csrf_token():
    script_path = finders.find("ratings/notifications.js")
    assert script_path is not None

    source = Path(script_path).read_text()

    assert '"X-CSRFToken": getCsrfToken()' in source
    assert 'document.querySelector("[name=csrfmiddlewaretoken]")?.value' in source
