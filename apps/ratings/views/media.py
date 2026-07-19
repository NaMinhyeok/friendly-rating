from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from ..models import MediaAttachment
from ..services import (
    MediaUploadNotFoundError,
    MediaUploadPermissionError,
    MediaUploadStateError,
    MediaUploadStorageError,
    generate_media_download_url,
)
from ._participants import get_current_participant


@login_required
@require_GET
def media_content(
    request: HttpRequest,
    attachment_id: UUID,
) -> HttpResponse:
    if not getattr(settings, "MEDIA_UPLOADS_AVAILABLE", False):
        raise Http404

    participant = get_current_participant(request)
    attachment = get_object_or_404(MediaAttachment, pk=attachment_id)
    try:
        download_url = generate_media_download_url(
            attachment=attachment,
            participant=participant,
        )
    except (MediaUploadNotFoundError, MediaUploadStateError) as error:
        raise Http404 from error
    except MediaUploadPermissionError as error:
        raise PermissionDenied("이 첨부 파일을 볼 수 없습니다.") from error
    except MediaUploadStorageError:
        response = HttpResponse(
            "파일을 지금 불러올 수 없습니다.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    response = HttpResponseRedirect(download_url)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
