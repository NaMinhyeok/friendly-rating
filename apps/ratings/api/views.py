from typing import Never, override

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Participant
from ..services import change_relationship_score
from .exceptions import ParticipantRequired, ScoreOutOfRange
from .serializers import (
    BadRequestErrorEnvelopeSerializer,
    ForbiddenErrorEnvelopeSerializer,
    InternalServerErrorEnvelopeSerializer,
    NotAcceptableErrorEnvelopeSerializer,
    RequestBodyTooLargeErrorEnvelopeSerializer,
    ScoreChangeDataSerializer,
    ScoreChangeRequestSerializer,
    ScoreChangeSuccessEnvelopeSerializer,
    ScoreOutOfRangeErrorEnvelopeSerializer,
    UnsupportedMediaTypeErrorEnvelopeSerializer,
)


def _participant_for_request(request: Request) -> Participant:
    try:
        return Participant.objects.get(user_id=request.user.pk)
    except Participant.DoesNotExist as error:
        raise ParticipantRequired() from error


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
        parameters=[
            OpenApiParameter(
                name="X-CSRFToken",
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
                description="렌더링된 페이지 또는 CSRF 쿠키에서 얻은 토큰",
            ),
        ],
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
