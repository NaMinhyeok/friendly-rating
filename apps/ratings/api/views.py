from typing import Never, override

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Participant, RelationshipScore
from ..services import (
    change_relationship_score,
    register_participant_push_device,
    unregister_participant_push_device,
)
from .exceptions import ParticipantRequired, ScoreOutOfRange
from .serializers import (
    BadRequestErrorEnvelopeSerializer,
    ForbiddenErrorEnvelopeSerializer,
    InternalServerErrorEnvelopeSerializer,
    NotAcceptableErrorEnvelopeSerializer,
    PushDeviceRegisteredSuccessEnvelopeSerializer,
    PushDeviceRequestSerializer,
    PushDeviceUnregisteredSuccessEnvelopeSerializer,
    ReadForbiddenErrorEnvelopeSerializer,
    RelationshipScoreListDataSerializer,
    RelationshipScoreListSuccessEnvelopeSerializer,
    RequestBodyTooLargeErrorEnvelopeSerializer,
    ScoreChangeDataSerializer,
    ScoreChangeRequestSerializer,
    ScoreChangeSuccessEnvelopeSerializer,
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


def _participant_for_request(request: Request) -> Participant:
    try:
        return Participant.objects.get(user_id=request.user.pk)
    except Participant.DoesNotExist as error:
        raise ParticipantRequired() from error


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


class ScoreChangeListView(APIView):
    @extend_schema(
        operation_id="createScoreChange",
        summary="현재 참가자의 관계 점수 변경",
        description=(
            "세션에 연결된 참가자의 outgoing score만 변경하고 변경 이력을 생성합니다. "
            "같은 출처 브라우저는 X-CSRFToken 헤더를 함께 전송해야 하며 JSON 요청 "
            "본문은 4KiB 이하여야 합니다."
        ),
        tags=("scoreChanges",),
        parameters=[CSRF_HEADER_PARAMETER],
        request=ScoreChangeRequestSerializer,
        responses={
            201: ScoreChangeSuccessEnvelopeSerializer,
            400: BadRequestErrorEnvelopeSerializer,
            403: ForbiddenErrorEnvelopeSerializer,
            406: NotAcceptableErrorEnvelopeSerializer,
            409: ScoreOutOfRangeErrorEnvelopeSerializer,
            413: RequestBodyTooLargeErrorEnvelopeSerializer,
            415: UnsupportedMediaTypeErrorEnvelopeSerializer,
            500: InternalServerErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        participant = _participant_for_request(request)
        serializer = ScoreChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = serializer.to_command()

        try:
            change = change_relationship_score(
                source_participant=participant,
                delta=command.delta,
                reason=command.reason,
            )
        except DjangoValidationError as error:
            reason = error.messages[0] if error.messages else None
            raise ScoreOutOfRange(reason=reason) from error

        response_serializer = ScoreChangeDataSerializer(change)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


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
