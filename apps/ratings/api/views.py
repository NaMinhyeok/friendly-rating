from __future__ import annotations

from typing import Never, cast, override
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count, Prefetch, Q, QuerySet
from django.shortcuts import get_object_or_404
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import (
    DiaryEntry,
    MediaAttachment,
    Participant,
    RelationshipScore,
    ScoreChange,
    ScoreChangeComment,
)
from ..services import (
    MediaUploadError,
    MediaUploadNotFoundError,
    MediaUploadPermissionError,
    MediaUploadStateError,
    MediaUploadStorageError,
    MediaUploadValidationError,
    ScoreUnchangedError,
    add_score_change_comment,
    change_relationship_score,
    complete_media_upload,
    create_diary_entry,
    delete_diary_entry,
    initiate_media_upload,
    register_participant_push_device,
    set_relationship_score,
    unregister_participant_push_device,
    update_diary_entry,
)
from .contracts import ErrorCode, ErrorType
from .exceptions import (
    ApiProblem,
    ParticipantRequired,
    ScoreOutOfRange,
    ScoreUnchanged,
)
from .serializers import (
    BadRequestErrorEnvelopeSerializer,
    CompletedMediaUploadDataSerializer,
    CompletedMediaUploadSuccessEnvelopeSerializer,
    DeltaScoreChangeCommand,
    DiaryEntryCreateRequestSerializer,
    DiaryEntryDataSerializer,
    DiaryEntryDeletedSuccessEnvelopeSerializer,
    DiaryEntryPageDataSerializer,
    DiaryEntryPageQuerySerializer,
    DiaryEntryPageSuccessEnvelopeSerializer,
    DiaryEntrySuccessEnvelopeSerializer,
    DiaryEntryUpdateRequestSerializer,
    ForbiddenErrorEnvelopeSerializer,
    InitiatedMediaUploadDataSerializer,
    InternalServerErrorEnvelopeSerializer,
    InvalidInputErrorEnvelopeSerializer,
    MediaForbiddenErrorEnvelopeSerializer,
    MediaUploadCompleteRequestSerializer,
    MediaUploadConflictErrorEnvelopeSerializer,
    MediaUploadInitiatedSuccessEnvelopeSerializer,
    MediaUploadInitiateRequestSerializer,
    MediaUploadsUnavailableErrorEnvelopeSerializer,
    MutationForbiddenErrorEnvelopeSerializer,
    NotAcceptableErrorEnvelopeSerializer,
    NotFoundErrorEnvelopeSerializer,
    PushDeviceRegisteredSuccessEnvelopeSerializer,
    PushDeviceRequestSerializer,
    PushDeviceUnregisteredSuccessEnvelopeSerializer,
    ReadForbiddenErrorEnvelopeSerializer,
    RelationshipScoreListDataSerializer,
    RelationshipScoreListSuccessEnvelopeSerializer,
    RequestBodyTooLargeErrorEnvelopeSerializer,
    ScoreChangeCommentDataSerializer,
    ScoreChangeCommentRequestSerializer,
    ScoreChangeCommentSuccessEnvelopeSerializer,
    ScoreChangeDataSerializer,
    ScoreChangePageDataSerializer,
    ScoreChangePageQuerySerializer,
    ScoreChangePageSuccessEnvelopeSerializer,
    ScoreChangeRequestSerializer,
    ScoreChangeSuccessEnvelopeSerializer,
    ScoreChangeThreadDataSerializer,
    ScoreChangeThreadSuccessEnvelopeSerializer,
    ScoreOutOfRangeErrorEnvelopeSerializer,
    UnsupportedMediaTypeErrorEnvelopeSerializer,
)

CSRF_HEADER_PARAMETER = OpenApiParameter(
    name="X-CSRFToken",
    type=str,
    location=OpenApiParameter.HEADER,
    required=True,
    description="렌더링된 페이지 또는 CSRF 쿠키에서 얻은 토큰",
)
SCORE_CHANGE_PAGE_SIZE = 20
DIARY_ENTRY_PAGE_SIZE = 20


class MediaUploadConflict(ApiProblem):
    status_code = status.HTTP_409_CONFLICT
    default_code = "media_upload_conflict"
    error_type = ErrorType.CONFLICT
    error_code = ErrorCode.MEDIA_UPLOAD_CONFLICT
    reason = "업로드 상태가 현재 작업과 맞지 않습니다."


class MediaUploadServiceUnavailable(ApiProblem):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "media_upload_service_unavailable"
    error_type = ErrorType.SERVER
    error_code = ErrorCode.MEDIA_UPLOADS_UNAVAILABLE
    reason = "파일 업로드 서비스를 지금 사용할 수 없습니다."


class ScoreChangeAutoSchema(AutoSchema):
    @override
    def _get_request_body(
        self,
        direction: str = "request",
    ):
        request_body = super()._get_request_body(direction)
        if self.method == "POST" and request_body is not None:
            cast(dict[str, object], request_body)["required"] = True
        return request_body


class DiaryEntryAutoSchema(AutoSchema):
    @override
    def _get_request_body(
        self,
        direction: str = "request",
    ):
        request_body = super()._get_request_body(direction)
        if self.method in {"POST", "PATCH"} and request_body is not None:
            cast(dict[str, object], request_body)["required"] = True
        return request_body


def _participant_for_request(request: Request) -> Participant:
    try:
        return Participant.objects.get(user_id=request.user.pk)
    except Participant.DoesNotExist as error:
        raise ParticipantRequired() from error


def _private_no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _raise_diary_validation_error(error: DjangoValidationError) -> Never:
    raise ValidationError(error.messages) from error


def _ensure_media_uploads_available() -> None:
    if not getattr(settings, "MEDIA_UPLOADS_AVAILABLE", False):
        raise MediaUploadServiceUnavailable()


def _raise_media_upload_api_error(error: MediaUploadError) -> Never:
    reason = str(error).strip() or None
    if isinstance(error, MediaUploadValidationError):
        raise ValidationError(reason or "업로드 정보를 확인해 주세요.") from error
    if isinstance(error, MediaUploadNotFoundError):
        raise NotFound() from error
    if isinstance(error, MediaUploadPermissionError):
        raise PermissionDenied() from error
    if isinstance(error, MediaUploadStateError):
        raise MediaUploadConflict(reason=reason) from error
    if isinstance(error, MediaUploadStorageError):
        raise MediaUploadServiceUnavailable() from error
    raise APIException() from error


def _score_attachment_prefetch() -> Prefetch[str]:
    return Prefetch(
        "media_attachments",
        queryset=MediaAttachment.objects.filter(
            purpose=MediaAttachment.Purpose.SCORE_CHANGE,
            status=MediaAttachment.Status.ATTACHED,
        ).order_by("position", "created_at", "id"),
        to_attr="_score_media_attachments",
    )


def _comment_attachment_prefetch() -> Prefetch[str]:
    return Prefetch(
        "media_attachments",
        queryset=MediaAttachment.objects.filter(
            status=MediaAttachment.Status.ATTACHED,
        ).order_by("position", "created_at", "id"),
        to_attr="_comment_media_attachments",
    )


def _score_changes_for_participant(
    participant: Participant,
) -> QuerySet[ScoreChange]:
    return (
        ScoreChange.objects.select_related(
            "changed_by",
            "relationship_score__source_participant",
            "relationship_score__target_participant",
        )
        .filter(
            Q(relationship_score__source_participant=participant)
            | Q(relationship_score__target_participant=participant),
        )
        .annotate(comment_count=Count("comments"))
    )


class DiaryEntryListView(APIView):
    schema = DiaryEntryAutoSchema()

    @extend_schema(
        operation_id="listDiaryEntries",
        summary="공유 일기 목록 조회",
        description=(
            "두 참가자가 공유하는 일기를 작성 시각의 최신순으로 조회합니다. "
            "페이지 크기는 20개로 고정됩니다."
        ),
        tags=("diaryEntries",),
        parameters=[DiaryEntryPageQuerySerializer],
        responses={
            200: DiaryEntryPageSuccessEnvelopeSerializer,
            400: InvalidInputErrorEnvelopeSerializer,
            403: ReadForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        query_serializer = DiaryEntryPageQuerySerializer(
            data=dict(request.query_params.items())
        )
        query_serializer.is_valid(raise_exception=True)
        page_number = query_serializer.validated_data.get("pageNumber")
        if not isinstance(page_number, int) or isinstance(page_number, bool):
            raise RuntimeError("Validated page number is not an integer.")

        entries = DiaryEntry.objects.select_related("author").order_by(
            "-created_at",
            "-pk",
        )
        paginator = Paginator(entries, DIARY_ENTRY_PAGE_SIZE)
        try:
            page = paginator.page(page_number)
        except EmptyPage as error:
            raise NotFound() from error

        serializer = DiaryEntryPageDataSerializer(
            {
                "results": list(page.object_list),
                "paging": {
                    "pageNumber": page.number,
                    "pageSize": DIARY_ENTRY_PAGE_SIZE,
                    "hasNext": page.has_next(),
                    "totalCount": paginator.count,
                },
            },
            context={"participant_id": participant.pk},
        )
        return _private_no_store(Response(serializer.data))

    @extend_schema(
        operation_id="createDiaryEntry",
        summary="공유 일기 작성",
        description=(
            "현재 참가자를 작성자로 하여 공유 일기를 작성합니다. 작성자는 요청 "
            "본문이 아니라 로그인 세션에서 결정됩니다."
        ),
        tags=("diaryEntries",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=DiaryEntryCreateRequestSerializer,
        responses={
            201: DiaryEntrySuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: ForbiddenErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        request_serializer = DiaryEntryCreateRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        command = request_serializer.to_command()
        try:
            entry = create_diary_entry(
                author=participant,
                content=command.content,
            )
        except DjangoValidationError as error:
            _raise_diary_validation_error(error)

        response_serializer = DiaryEntryDataSerializer(
            entry,
            context={"participant_id": participant.pk},
        )
        return _private_no_store(
            Response(response_serializer.data, status=status.HTTP_201_CREATED)
        )


class DiaryEntryDetailView(APIView):
    schema = DiaryEntryAutoSchema()

    @staticmethod
    def _entry(diary_entry_id: int) -> DiaryEntry:
        return get_object_or_404(
            DiaryEntry.objects.select_related("author"),
            pk=diary_entry_id,
        )

    @extend_schema(
        operation_id="retrieveDiaryEntry",
        summary="공유 일기 조회",
        description="두 참가자가 공유하는 일기 한 편을 조회합니다.",
        tags=("diaryEntries",),
        responses={
            200: DiaryEntrySuccessEnvelopeSerializer,
            403: ReadForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, diary_entry_id: int) -> Response:
        participant = _participant_for_request(request)
        entry = self._entry(diary_entry_id)
        serializer = DiaryEntryDataSerializer(
            entry,
            context={"participant_id": participant.pk},
        )
        return _private_no_store(Response(serializer.data))

    @extend_schema(
        operation_id="updateDiaryEntry",
        summary="공유 일기 수정",
        description="작성자만 일기 내용을 수정할 수 있습니다.",
        tags=("diaryEntries",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=DiaryEntryUpdateRequestSerializer,
        responses={
            200: DiaryEntrySuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: MutationForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, diary_entry_id: int) -> Response:
        participant = _participant_for_request(request)
        entry = self._entry(diary_entry_id)
        request_serializer = DiaryEntryUpdateRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        command = request_serializer.to_command()
        try:
            updated_entry = update_diary_entry(
                entry=entry,
                author=participant,
                content=command.content,
            )
        except DjangoValidationError as error:
            _raise_diary_validation_error(error)

        response_serializer = DiaryEntryDataSerializer(
            updated_entry,
            context={"participant_id": participant.pk},
        )
        return _private_no_store(Response(response_serializer.data))

    @extend_schema(
        operation_id="deleteDiaryEntry",
        summary="공유 일기 삭제",
        description="작성자만 자신이 작성한 공유 일기를 삭제할 수 있습니다.",
        tags=("diaryEntries",),
        parameters=[CSRF_HEADER_PARAMETER],
        responses={
            200: DiaryEntryDeletedSuccessEnvelopeSerializer,
            403: MutationForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def delete(self, request: Request, diary_entry_id: int) -> Response:
        participant = _participant_for_request(request)
        entry = self._entry(diary_entry_id)
        delete_diary_entry(entry=entry, author=participant)
        return _private_no_store(Response(None))


class RelationshipScoreListView(APIView):
    @extend_schema(
        operation_id="listRelationshipScores",
        summary="두 참가자의 관계 점수 조회",
        description=(
            "두 방향의 현재 관계 점수를 조회합니다. 현재 참가자의 outgoing score가 "
            "먼저 오며 isMine으로 변경 가능한 점수를 식별할 수 있습니다."
        ),
        tags=("relationshipScores",),
        responses={
            200: RelationshipScoreListSuccessEnvelopeSerializer,
            403: ReadForbiddenErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        scores = list(
            RelationshipScore.objects.select_related(
                "source_participant",
                "target_participant",
            )
            .filter(
                Q(source_participant=participant) | Q(target_participant=participant),
            )
            .order_by("source_participant__slot")
        )
        scores.sort(
            key=lambda score: score.source_participant_id != participant.pk,
        )
        serializer = RelationshipScoreListDataSerializer(
            {"results": scores},
            context={"participant_id": participant.pk},
        )
        response = Response(serializer.data)
        response.headers["Cache-Control"] = "private, no-store"
        return response


class MediaUploadInitiateView(APIView):
    schema = ScoreChangeAutoSchema()

    @extend_schema(
        operation_id="initiateMediaUpload",
        summary="파일 직접 업로드 준비",
        description=(
            "현재 참가자가 비공개 저장소에 이미지 또는 짧은 영상을 직접 업로드할 "
            "수 있도록 만료 시간이 짧은 URL을 발급합니다. 댓글 첨부는 현재 참가자가 "
            "접근할 수 있는 점수 변경 ID가 필요하고, 점수 변경 이유에는 이미지만 "
            "첨부할 수 있습니다."
        ),
        tags=("mediaUploads",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=MediaUploadInitiateRequestSerializer,
        responses={
            201: MediaUploadInitiatedSuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: MediaForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            409: MediaUploadConflictErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
            503: MediaUploadsUnavailableErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        _ensure_media_uploads_available()
        serializer = MediaUploadInitiateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = serializer.to_command()

        score_change = None
        if command.score_change_id is not None:
            score_change = get_object_or_404(
                _score_changes_for_participant(participant),
                pk=command.score_change_id,
            )

        purpose = (
            MediaAttachment.Purpose.SCORE_CHANGE
            if command.purpose == "scoreChange"
            else MediaAttachment.Purpose.COMMENT
        )
        kind = (
            MediaAttachment.Kind.IMAGE
            if command.kind == "image"
            else MediaAttachment.Kind.VIDEO
        )
        try:
            initiated = initiate_media_upload(
                uploader=participant,
                purpose=purpose,
                kind=kind,
                original_name=command.original_name,
                content_type=command.content_type,
                expected_size=command.expected_size,
                score_change=score_change,
            )
        except MediaUploadError as error:
            _raise_media_upload_api_error(error)

        response = Response(
            InitiatedMediaUploadDataSerializer(initiated).data,
            status=status.HTTP_201_CREATED,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response


class MediaUploadCompleteView(APIView):
    schema = ScoreChangeAutoSchema()

    @extend_schema(
        operation_id="completeMediaUpload",
        summary="파일 직접 업로드 완료 확인",
        description=(
            "비공개 저장소에 직접 전송한 파일의 형식과 크기를 확인하고 첨부 가능한 "
            "상태로 전환합니다. 빈 JSON 객체와 CSRF 토큰을 전송해야 합니다."
        ),
        tags=("mediaUploads",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=MediaUploadCompleteRequestSerializer,
        responses={
            200: CompletedMediaUploadSuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: MediaForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            409: MediaUploadConflictErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
            503: MediaUploadsUnavailableErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, upload_id: UUID) -> Response:
        participant = _participant_for_request(request)
        _ensure_media_uploads_available()
        serializer = MediaUploadCompleteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            completed = complete_media_upload(
                upload_id=upload_id,
                uploader=participant,
            )
        except MediaUploadError as error:
            _raise_media_upload_api_error(error)

        response = Response(
            CompletedMediaUploadDataSerializer(completed.attachment).data
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response


class ScoreChangeListView(APIView):
    schema = ScoreChangeAutoSchema()

    @extend_schema(
        operation_id="listScoreChanges",
        summary="관계 점수 변경 이력 조회",
        description=(
            "두 참가자가 공유하는 양방향 점수 변경 이력을 최신순으로 조회합니다. "
            "페이지 크기는 20개로 고정됩니다."
        ),
        tags=("scoreChanges",),
        parameters=[ScoreChangePageQuerySerializer],
        responses={
            200: ScoreChangePageSuccessEnvelopeSerializer,
            400: InvalidInputErrorEnvelopeSerializer,
            403: ReadForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        query_serializer = ScoreChangePageQuerySerializer(
            data=dict(request.query_params.items())
        )
        query_serializer.is_valid(raise_exception=True)
        page_number = query_serializer.validated_data.get("pageNumber")
        if not isinstance(page_number, int) or isinstance(page_number, bool):
            raise RuntimeError("Validated page number is not an integer.")

        changes = (
            _score_changes_for_participant(participant)
            .prefetch_related(_score_attachment_prefetch())
            .order_by(
                "-created_at",
                "-pk",
            )
        )
        paginator = Paginator(changes, SCORE_CHANGE_PAGE_SIZE)
        try:
            page = paginator.page(page_number)
        except EmptyPage as error:
            raise NotFound() from error

        response_serializer = ScoreChangePageDataSerializer(
            {
                "results": list(page.object_list),
                "paging": {
                    "pageNumber": page.number,
                    "pageSize": SCORE_CHANGE_PAGE_SIZE,
                    "hasNext": page.has_next(),
                    "totalCount": paginator.count,
                },
            }
        )
        response = Response(response_serializer.data)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @extend_schema(
        operation_id="createScoreChange",
        summary="현재 참가자의 관계 점수 변경",
        description=(
            "세션에 연결된 참가자의 outgoing score만 변경하고 변경 이력을 생성합니다. "
            "delta와 targetScore 중 하나만 입력하며 targetScore는 최종 점수를 뜻합니다. "
            "같은 출처 브라우저는 X-CSRFToken 헤더를 함께 전송해야 하며 JSON 요청 "
            "본문은 4KiB 이하여야 합니다."
        ),
        tags=("scoreChanges",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=ScoreChangeRequestSerializer,
        responses={
            201: ScoreChangeSuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: MediaForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            409: ScoreOutOfRangeErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
            503: MediaUploadsUnavailableErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        serializer = ScoreChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = serializer.to_command()
        if command.media_upload_ids:
            _ensure_media_uploads_available()

        try:
            if isinstance(command, DeltaScoreChangeCommand):
                change = change_relationship_score(
                    source_participant=participant,
                    delta=command.delta,
                    reason=command.reason,
                    media_upload_ids=command.media_upload_ids,
                )
            else:
                try:
                    change = set_relationship_score(
                        source_participant=participant,
                        target_score=command.target_score,
                        reason=command.reason,
                        media_upload_ids=command.media_upload_ids,
                    )
                except ScoreUnchangedError as error:
                    raise ScoreUnchanged(
                        reason=f"이미 {command.target_score}점이에요."
                    ) from error
        except DjangoValidationError as error:
            reason = error.messages[0] if error.messages else None
            raise ScoreOutOfRange(reason=reason) from error
        except MediaUploadError as error:
            _raise_media_upload_api_error(error)

        response_serializer = ScoreChangeDataSerializer(change)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class ScoreChangeDetailView(APIView):
    @extend_schema(
        operation_id="retrieveScoreChangeThread",
        summary="점수 변경 대화 조회",
        description=(
            "특정 점수 변경과 그 아래의 댓글을 시간순으로 조회합니다. 현재 참가자가 "
            "속한 관계의 점수 변경만 조회할 수 있습니다."
        ),
        tags=("scoreChanges",),
        responses={
            200: ScoreChangeThreadSuccessEnvelopeSerializer,
            403: ReadForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, score_change_id: int) -> Response:
        participant = _participant_for_request(request)
        change = get_object_or_404(
            _score_changes_for_participant(participant).prefetch_related(
                _score_attachment_prefetch(),
                Prefetch(
                    "comments",
                    queryset=ScoreChangeComment.objects.select_related("author")
                    .prefetch_related(_comment_attachment_prefetch())
                    .order_by("created_at", "pk"),
                ),
            ),
            pk=score_change_id,
        )
        serializer = ScoreChangeThreadDataSerializer(
            change,
            context={"participant_id": participant.pk},
        )
        response = Response(serializer.data)
        response.headers["Cache-Control"] = "private, no-store"
        return response


class ScoreChangeCommentCreateView(APIView):
    schema = ScoreChangeAutoSchema()

    @extend_schema(
        operation_id="createScoreChangeComment",
        summary="점수 변경에 댓글 작성",
        description=(
            "현재 참가자가 특정 점수 변경에 댓글을 작성합니다. 작성자는 세션에서 "
            "결정되며 JSON 요청 본문은 4KiB 이하여야 합니다."
        ),
        tags=("scoreChanges",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=ScoreChangeCommentRequestSerializer,
        responses={
            201: ScoreChangeCommentSuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: MediaForbiddenErrorEnvelopeSerializer,
            404: NotFoundErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            409: MediaUploadConflictErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
            503: MediaUploadsUnavailableErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, score_change_id: int) -> Response:
        participant = _participant_for_request(request)
        change = get_object_or_404(
            _score_changes_for_participant(participant),
            pk=score_change_id,
        )
        serializer = ScoreChangeCommentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = serializer.to_command()
        if command.media_upload_ids:
            _ensure_media_uploads_available()

        try:
            comment = add_score_change_comment(
                score_change=change,
                author=participant,
                content=command.content,
                media_upload_ids=command.media_upload_ids,
            )
        except DjangoValidationError as error:
            raise ValidationError({"content": error.messages}) from error
        except MediaUploadError as error:
            _raise_media_upload_api_error(error)

        response_serializer = ScoreChangeCommentDataSerializer(
            comment,
            context={"participant_id": participant.pk},
        )
        response = Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response


class PushDeviceRegisterView(APIView):
    @extend_schema(
        operation_id="registerPushDevice",
        summary="현재 참가자의 푸시 기기 등록",
        description=(
            "Firebase 기기 ID를 현재 참가자에게 연결하고 활성화합니다. 같은 출처 "
            "브라우저는 X-CSRFToken 헤더를 함께 전송해야 하며 JSON 요청 본문은 "
            "4KiB 이하여야 합니다."
        ),
        tags=("pushDevices",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=PushDeviceRequestSerializer,
        responses={
            200: PushDeviceRegisteredSuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: ForbiddenErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        serializer = PushDeviceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = serializer.to_command()

        user_agent = request.META.get("HTTP_USER_AGENT", "")
        if not isinstance(user_agent, str):
            user_agent = ""
        register_participant_push_device(
            participant=participant,
            firebase_installation_id=command.fid,
            user_agent=user_agent,
        )
        return Response({"registered": True})


class PushDeviceUnregisterView(APIView):
    @extend_schema(
        operation_id="unregisterPushDevice",
        summary="현재 참가자의 푸시 기기 해제",
        description=(
            "현재 참가자에게 연결된 Firebase 기기를 비활성화합니다. 기기가 없거나 "
            "다른 참가자 소유여도 같은 성공 응답을 반환합니다."
        ),
        tags=("pushDevices",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=PushDeviceRequestSerializer,
        responses={
            200: PushDeviceUnregisteredSuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: ForbiddenErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        serializer = PushDeviceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = serializer.to_command()

        unregister_participant_push_device(
            participant=participant,
            firebase_installation_id=command.fid,
        )
        return Response({"registered": False})


@extend_schema(exclude=True)
class ApiNotFoundView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @override
    def http_method_not_allowed(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> Never:
        raise NotFound()

    @override
    def options(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> Never:
        raise NotFound()
