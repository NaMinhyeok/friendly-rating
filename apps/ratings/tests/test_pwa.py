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
    body = response.content.decode()

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/javascript"
    assert response.headers["Service-Worker-Allowed"] == "/"
    assert "static-v3" in body
    assert "static-v2" not in body


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
    assertContains(
        response,
        'data-register-url="/api/v1/push-devices/register/"',
    )
    assertContains(
        response,
        'data-unregister-url="/api/v1/push-devices/unregister/"',
    )
    assertContains(response, "test-project")
    assertNotContains(response, "private_key")


def test_notification_client_sends_the_rendered_csrf_token():
    script_path = finders.find("ratings/notifications.js")
    assert script_path is not None

    source = Path(script_path).read_text()

    assert 'credentials: "same-origin"' in source
    assert '"X-CSRFToken": getCsrfToken()' in source
    assert 'document.querySelector("[name=csrfmiddlewaretoken]")?.value' in source
    assert 'payload?.resultType !== "SUCCESS"' in source
    assert "payload?.error !== null" in source
    assert "payload?.success?.registered !== expectedRegistered" in source
    assert source.count("syncFid(root.dataset.registerUrl, fid, true)") == 1
    assert source.count("syncFid(root.dataset.unregisterUrl, fid, false)") == 2
    assert "syncFid(root.dataset.registerUrl, fid, false)" not in source
    assert "syncFid(root.dataset.unregisterUrl, fid, true)" not in source


def test_dashboard_client_uses_the_versioned_same_origin_api_safely():
    script_path = finders.find("ratings/dashboard.js")
    assert script_path is not None

    source = Path(script_path).read_text()

    assert 'credentials: "same-origin"' in source
    assert '"Content-Type": "application/json"' in source
    assert '"X-CSRFToken": getCsrfToken(form)' in source
    assert 'payload?.resultType !== "SUCCESS"' in source
    assert "event.preventDefault()" in source
    assert "if (isSubmitting)" in source
    assert "innerHTML" not in source
    assert "textContent" in source
    assert "새로고침해 현재 점수를 확인해 주세요" in source
    assert "setFormFieldsDisabled(form, true)" in source
    assert "submitButton.disabled = !shouldUnlockSubmission" in source
    assert 'shouldUnlockSubmission ? "이 마음 기록하기" : "새로고침 후 확인"' in source
