import pytest
from django.urls import reverse

from ..models import DiaryEntry

pytestmark = pytest.mark.django_db


def test_diary_is_a_private_api_backed_shell(client, participant_pair):
    client.force_login(participant_pair.second.user)

    response = client.get(reverse("diary"), {"pageNumber": 2})

    content = response.content.decode()
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert 'data-diary-entries-url="/api/v1/diary-entries/"' in content
    assert "ratings/diary.js" in content
    assert 'name="csrfmiddlewaretoken"' in content
    assert 'name="content"' in content
    assert 'name="entryDate"' not in content
    assert 'type="date"' not in content
    assert 'maxlength="1000"' not in content
    assert "data-diary-list" in content
    assert "data-diary-pagination" in content
    assert "data-diary-focus-compose" in content
    assert "<noscript>" in content
    assert "JavaScript를 켜 주세요" in content
    assert "HTML 셸에 미리 넣지 않을 일기 내용" not in content


def test_home_links_to_the_shared_diary(client, participant_pair):
    client.force_login(participant_pair.first.user)

    response = client.get(reverse("home"))

    content = response.content.decode()
    assert response.status_code == 200
    assert f'href="{reverse("diary")}"' in content
    assert "우리 일기" in content
    assert "점수와 상관없이 오늘의 이야기를 함께 남겨요" in content
    assert 'name="reason"' in content
    assert 'maxlength="200"' not in content


def test_diary_media_controls_follow_private_storage_availability(
    client,
    participant_pair,
    settings,
):
    client.force_login(participant_pair.first.user)

    settings.MEDIA_UPLOADS_AVAILABLE = False
    without_media = client.get(reverse("diary")).content.decode()

    assert "data-media-uploads-url" not in without_media
    assert "data-diary-media-input" not in without_media

    settings.MEDIA_UPLOADS_AVAILABLE = True
    with_media = client.get(reverse("diary")).content.decode()

    assert 'data-media-uploads-url="/api/v1/media-uploads/"' in with_media
    assert "data-diary-media-input" in with_media
    assert (
        'accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime"'
        in with_media
    )
    assert "사진은 최대 4장(장당 10MB), 영상은 1개(100MB)" in with_media


def test_diary_initializes_notification_runtime_without_losing_media_context(
    client,
    participant_pair,
    settings,
):
    client.force_login(participant_pair.first.user)
    settings.PUSH_NOTIFICATIONS_AVAILABLE = True
    settings.FIREBASE_WEB_CONFIG = {
        "apiKey": "test-api-key",
        "appId": "test-app-id",
        "projectId": "test-project",
    }
    settings.FIREBASE_VAPID_PUBLIC_KEY = "B" + "A" * 86
    settings.MEDIA_UPLOADS_AVAILABLE = True

    content = client.get(reverse("diary")).content.decode()

    assert "data-notification-settings" in content
    assert "ratings/notifications.js" in content
    assert "test-project" in content
    assert 'data-media-uploads-url="/api/v1/media-uploads/"' in content


def test_diary_requires_login_and_preserves_destination(client):
    diary_url = reverse("diary")

    response = client.get(diary_url)

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next={diary_url}"


def test_diary_shell_only_accepts_get(client, participant_pair):
    client.force_login(participant_pair.first.user)

    response = client.post(reverse("diary"))

    assert response.status_code == 405


def test_diary_entry_thread_is_a_private_api_backed_shell(
    client,
    participant_pair,
):
    entry = DiaryEntry.objects.create(
        author=participant_pair.first,
        content="HTML 셸에 미리 넣지 않을 일기 본문",
    )
    client.force_login(participant_pair.second.user)

    response = client.get(
        reverse(
            "diary-entry-thread",
            kwargs={"diary_entry_id": entry.pk},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert f'data-thread-url="/api/v1/diary-entries/{entry.pk}/"' in content
    assert f'data-comments-url="/api/v1/diary-entries/{entry.pk}/comments/"' in content
    assert "ratings/diary_entry_thread.js" in content
    assert 'name="csrfmiddlewaretoken"' in content
    assert 'maxlength="500"' not in content
    assert "data-comment-list" in content
    assert "HTML 셸에 미리 넣지 않을 일기 본문" not in content
    assert "<noscript>" in content
    assert "JavaScript를 켜 주세요" in content


def test_diary_entry_thread_requires_login_and_preserves_destination(
    client,
    participant_pair,
):
    entry = DiaryEntry.objects.create(
        author=participant_pair.first,
        content="오늘 이야기",
    )
    thread_url = reverse(
        "diary-entry-thread",
        kwargs={"diary_entry_id": entry.pk},
    )

    response = client.get(thread_url)

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next={thread_url}"


def test_diary_entry_thread_is_get_only_and_hides_missing_entries(
    client,
    participant_pair,
):
    client.force_login(participant_pair.first.user)
    missing_url = reverse(
        "diary-entry-thread",
        kwargs={"diary_entry_id": 999999},
    )

    assert client.get(missing_url).status_code == 404
    assert client.post(missing_url).status_code == 405
