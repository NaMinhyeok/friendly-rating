import json
import logging
import threading
from urllib.parse import urlsplit

import firebase_admin
from django.conf import settings
from django.utils import timezone
from firebase_admin import credentials, messaging

from .models import PushDevice

logger = logging.getLogger(__name__)

_FIREBASE_APP_NAME = "woorisai-push"
_firebase_app_lock = threading.Lock()


def _get_firebase_app():
    if not settings.PUSH_NOTIFICATIONS_ENABLED:
        return None

    try:
        return firebase_admin.get_app(_FIREBASE_APP_NAME)
    except ValueError:
        pass

    service_account_json = settings.FIREBASE_SERVICE_ACCOUNT_JSON
    if not service_account_json:
        logger.warning("Firebase service account is not configured; skipping push.")
        return None

    with _firebase_app_lock:
        try:
            return firebase_admin.get_app(_FIREBASE_APP_NAME)
        except ValueError:
            pass

        try:
            service_account = json.loads(service_account_json)
            if not isinstance(service_account, dict):
                raise ValueError("service account must be a JSON object")

            web_project_id = settings.FIREBASE_WEB_CONFIG.get("projectId")
            if service_account.get("project_id") != web_project_id:
                raise ValueError("Firebase project IDs do not match")

            credential = credentials.Certificate(service_account)
            project_id = web_project_id or service_account.get("project_id")
            options = {"httpTimeout": 5}
            if project_id:
                options["projectId"] = project_id
            return firebase_admin.initialize_app(
                credential,
                options=options,
                name=_FIREBASE_APP_NAME,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.exception("Firebase service account configuration is invalid.")
            return None


def _notification_webpush_config():
    public_base_url = settings.PUBLIC_BASE_URL
    parsed_url = urlsplit(public_base_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        return None

    return messaging.WebpushConfig(
        fcm_options=messaging.WebpushFCMOptions(link=public_base_url),
    )


def _deactivate_invalid_devices(devices, responses):
    invalid_device_ids = [
        device_id
        for (device_id, _), response in zip(devices, responses, strict=True)
        if not response.success
        and isinstance(
            response.exception,
            (messaging.UnregisteredError, messaging.SenderIdMismatchError),
        )
    ]
    if invalid_device_ids:
        PushDevice.objects.filter(pk__in=invalid_device_ids).update(
            is_active=False,
            updated_at=timezone.now(),
        )


def _send_score_change_notification(*, recipient_id: int) -> int:
    if not settings.PUSH_NOTIFICATIONS_ENABLED:
        return 0

    devices = list(
        PushDevice.objects.filter(
            participant_id=recipient_id,
            is_active=True,
        ).values_list("pk", "firebase_installation_id")
    )
    if not devices:
        return 0

    firebase_app = _get_firebase_app()
    if firebase_app is None:
        return 0

    sent_count = 0
    for start in range(0, len(devices), 500):
        device_batch = devices[start : start + 500]
        message = messaging.MulticastMessage(
            fids=[fid for _, fid in device_batch],
            notification=messaging.Notification(
                title="우리 사이",
                body="새로운 마음 기록이 도착했어요",
            ),
            webpush=_notification_webpush_config(),
        )
        response = messaging.send_each_for_multicast(message, app=firebase_app)
        sent_count += response.success_count
        _deactivate_invalid_devices(device_batch, response.responses)

    return sent_count


def send_score_change_notification(*, recipient_id: int) -> int:
    """Send a private score-change notice without affecting the score workflow."""
    try:
        return _send_score_change_notification(recipient_id=recipient_id)
    except Exception:
        logger.exception(
            "Failed to send score-change push notification.",
            extra={"recipient_id": recipient_id},
        )
        return 0
