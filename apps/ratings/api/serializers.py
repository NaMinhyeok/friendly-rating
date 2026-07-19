from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from django.core.validators import RegexValidator
from django.urls import reverse
from drf_spectacular.extensions import OpenApiSerializerExtension
from drf_spectacular.utils import (
    Direction,
    PolymorphicProxySerializer,
    extend_schema_field,
)
from rest_framework import serializers
from rest_framework.settings import api_settings

from ..models import (
    Participant,
    RelationshipScore,
    ScoreChange,
    ScoreChangeComment,
)
from ..services.push_devices import FIREBASE_INSTALLATION_ID_PATTERN
from .contracts import ErrorCode, ErrorType, ResultType

if TYPE_CHECKING:
    from drf_spectacular.openapi import AutoSchema


@dataclass(frozen=True, slots=True)
class ScoreChangeCommand:
    delta: int
    reason: str


@dataclass(frozen=True, slots=True)
class ScoreChangeCommentCommand:
    content: str


@dataclass(frozen=True, slots=True)
class PushDeviceCommand:
    fid: str


@extend_schema_field(
    {
        "type": "integer",
        "minimum": -100,
        "maximum": 100,
        "not": {"const": 0},
    }
)
class ScoreDeltaField(serializers.IntegerField):
    @override
    def to_internal_value(self, data: object) -> int:
        if isinstance(data, float) and data.is_integer():
            data = int(data)
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictCharField(serializers.CharField):
    @override
    def to_internal_value(self, data: object) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        if self.min_length is not None and len(data) < self.min_length:
            self.fail(
                "min_length",
                min_length=self.min_length,
                length=len(data),
            )
        if self.max_length is not None and len(data) > self.max_length:
            self.fail(
                "max_length",
                max_length=self.max_length,
                length=len(data),
            )
        return super().to_internal_value(data)


class StrictRequestSerializer(serializers.Serializer[object]):
    default_error_messages = {
        "invalid": "JSON 객체를 입력해 주세요.",
    }

    @override
    def run_validation(self, data: Any = serializers.empty) -> Any:
        if data is None:
            raise serializers.ValidationError(
                {api_settings.NON_FIELD_ERRORS_KEY: ["JSON 객체를 입력해 주세요."]},
                code="invalid",
            )
        return super().run_validation(data)

    @override
    def to_internal_value(self, data: object) -> dict[str, Any]:
        if isinstance(data, Mapping):
            unknown_fields = sorted(
                str(field_name) for field_name in data if field_name not in self.fields
            )
            if unknown_fields:
                raise serializers.ValidationError(
                    {
                        field_name: "알 수 없는 필드입니다."
                        for field_name in unknown_fields
                    },
                    code="unknown_field",
                )
        return super().to_internal_value(data)


class ScoreChangeRequestSerializer(StrictRequestSerializer):
    delta = ScoreDeltaField(min_value=-100, max_value=100)
    reason = StrictCharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=200,
        trim_whitespace=True,
    )

    def validate_delta(self, value: int) -> int:
        if value == 0:
            raise serializers.ValidationError(
                "변경 점수는 0이 아니어야 합니다.",
                code="non_zero",
            )
        return value

    def to_command(self) -> ScoreChangeCommand:
        data = self.validated_data
        delta = data.get("delta")
        reason = data.get("reason")
        if not isinstance(delta, int) or isinstance(delta, bool):
            raise RuntimeError("Validated score delta is not an integer.")
        if not isinstance(reason, str):
            raise RuntimeError("Validated score reason is not a string.")
        return ScoreChangeCommand(delta=delta, reason=reason)


class ScoreChangePageQuerySerializer(StrictRequestSerializer):
    pageNumber = serializers.IntegerField(default=1, min_value=1)


class ScoreChangeRequestSerializerExtension(OpenApiSerializerExtension):
    target_class = ScoreChangeRequestSerializer

    @override
    def map_serializer(
        self,
        auto_schema: "AutoSchema",
        direction: Direction,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "delta": {
                    "type": "integer",
                    "minimum": -100,
                    "maximum": 100,
                    "not": {"const": 0},
                },
                "reason": {
                    "type": "string",
                    "maxLength": 200,
                    "default": "",
                },
            },
            "required": ["delta"],
        }


class ScoreChangeCommentRequestSerializer(StrictRequestSerializer):
    content = StrictCharField(
        min_length=1,
        max_length=500,
        trim_whitespace=True,
    )

    def to_command(self) -> ScoreChangeCommentCommand:
        content = self.validated_data.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Validated score comment is not a string.")
        return ScoreChangeCommentCommand(content=content)


class ScoreChangeCommentRequestSerializerExtension(OpenApiSerializerExtension):
    target_class = ScoreChangeCommentRequestSerializer

    @override
    def map_serializer(
        self,
        auto_schema: "AutoSchema",
        direction: Direction,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "content": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                },
            },
            "required": ["content"],
        }


class ScoreChangeDataSerializer(serializers.Serializer[ScoreChange]):
    id = serializers.IntegerField(read_only=True)
    delta = ScoreDeltaField(
        read_only=True,
        min_value=-100,
        max_value=100,
    )
    reason = serializers.CharField(read_only=True, max_length=200)
    resultingScore = serializers.IntegerField(
        source="resulting_score",
        read_only=True,
        min_value=0,
        max_value=100,
    )
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)


class ParticipantSummarySerializer(serializers.Serializer[Participant]):
    slot = serializers.IntegerField(read_only=True, min_value=1, max_value=2)
    displayName = serializers.CharField(
        source="display_name",
        read_only=True,
        max_length=30,
    )


class ScoreChangeHistoryDataSerializer(serializers.Serializer[ScoreChange]):
    id = serializers.IntegerField(read_only=True, min_value=1)
    sourceParticipant = ParticipantSummarySerializer(
        source="relationship_score.source_participant",
        read_only=True,
    )
    targetParticipant = ParticipantSummarySerializer(
        source="relationship_score.target_participant",
        read_only=True,
    )
    changedBy = ParticipantSummarySerializer(source="changed_by", read_only=True)
    delta = ScoreDeltaField(
        read_only=True,
        min_value=-100,
        max_value=100,
    )
    reason = serializers.CharField(read_only=True, max_length=200)
    resultingScore = serializers.IntegerField(
        source="resulting_score",
        read_only=True,
        min_value=0,
        max_value=100,
    )
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    commentCount = serializers.IntegerField(
        source="comment_count",
        read_only=True,
        min_value=0,
    )
    threadUrl = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_threadUrl(self, change: ScoreChange) -> str:
        return reverse(
            "score-change-thread",
            kwargs={"score_change_id": change.pk},
        )


class ScoreChangeCommentDataSerializer(serializers.Serializer[ScoreChangeComment]):
    id = serializers.IntegerField(read_only=True, min_value=1)
    author = ParticipantSummarySerializer(read_only=True)
    content = serializers.CharField(read_only=True, max_length=500)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    isMine = serializers.SerializerMethodField()

    def get_isMine(self, comment: ScoreChangeComment) -> bool:
        participant_id = self.context.get("participant_id")
        return comment.author_id == participant_id


class ScoreChangeThreadDataSerializer(ScoreChangeHistoryDataSerializer):
    comments = ScoreChangeCommentDataSerializer(many=True, read_only=True)

    @override
    def to_representation(self, instance: ScoreChange) -> dict[str, Any]:
        data = super().to_representation(instance)
        comments = data.get("comments")
        if not isinstance(comments, list):
            raise RuntimeError("Serialized score comments are not a list.")
        data["commentCount"] = len(comments)
        return data


@extend_schema_field({"type": "integer", "const": 20})
class ScoreChangePageSizeField(serializers.IntegerField):
    pass


class PageNumberPagingSerializer(serializers.Serializer[object]):
    pageNumber = serializers.IntegerField(min_value=1)
    pageSize = ScoreChangePageSizeField()
    hasNext = serializers.BooleanField()
    totalCount = serializers.IntegerField(min_value=0)


class ScoreChangePageDataSerializer(serializers.Serializer[object]):
    results = serializers.ListField(
        child=ScoreChangeHistoryDataSerializer(),
        max_length=20,
    )
    paging = PageNumberPagingSerializer()


class RelationshipScoreDataSerializer(serializers.Serializer[RelationshipScore]):
    sourceParticipant = ParticipantSummarySerializer(
        source="source_participant",
        read_only=True,
    )
    targetParticipant = ParticipantSummarySerializer(
        source="target_participant",
        read_only=True,
    )
    currentScore = serializers.IntegerField(
        source="current_score",
        read_only=True,
        min_value=0,
        max_value=100,
    )
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    isMine = serializers.SerializerMethodField()

    def get_isMine(self, score: RelationshipScore) -> bool:
        participant_id = self.context.get("participant_id")
        return score.source_participant_id == participant_id


class RelationshipScoreListDataSerializer(serializers.Serializer[object]):
    results = RelationshipScoreDataSerializer(many=True)


class PushDeviceRequestSerializer(StrictRequestSerializer):
    fid = StrictCharField(
        min_length=22,
        max_length=22,
        trim_whitespace=False,
        validators=(
            RegexValidator(
                regex=FIREBASE_INSTALLATION_ID_PATTERN,
                message="올바른 Firebase 기기 ID가 필요합니다.",
                code="invalid_format",
            ),
        ),
    )

    def to_command(self) -> PushDeviceCommand:
        fid = self.validated_data.get("fid")
        if not isinstance(fid, str):
            raise RuntimeError("Validated Firebase installation ID is not a string.")
        return PushDeviceCommand(fid=fid)


class PushDeviceRequestSerializerExtension(OpenApiSerializerExtension):
    target_class = PushDeviceRequestSerializer

    @override
    def map_serializer(
        self,
        auto_schema: "AutoSchema",
        direction: Direction,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "fid": {
                    "type": "string",
                    "minLength": 22,
                    "maxLength": 22,
                    "pattern": FIREBASE_INSTALLATION_ID_PATTERN.pattern,
                },
            },
            "required": ["fid"],
        }


@extend_schema_field({"type": "boolean", "const": True})
class RegisteredTrueField(serializers.BooleanField):
    pass


@extend_schema_field({"type": "boolean", "const": False})
class RegisteredFalseField(serializers.BooleanField):
    pass


class PushDeviceRegisteredDataSerializer(serializers.Serializer[object]):
    registered = RegisteredTrueField(read_only=True)


class PushDeviceUnregisteredDataSerializer(serializers.Serializer[object]):
    registered = RegisteredFalseField(read_only=True)


class ErrorDetailSerializer(serializers.Serializer[object]):
    field = serializers.CharField(allow_null=True)
    code = serializers.CharField()
    message = serializers.CharField()


class EmptyDetailsApiErrorSerializer(serializers.Serializer[object]):
    reason = serializers.CharField()
    details = serializers.ListField(
        child=ErrorDetailSerializer(),
        max_length=0,
    )


class InvalidInputApiErrorSerializer(serializers.Serializer[object]):
    errorType = serializers.ChoiceField(choices=(ErrorType.VALIDATION.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.INVALID_INPUT.value,))
    reason = serializers.CharField()
    details = serializers.ListField(
        child=ErrorDetailSerializer(),
        min_length=1,
    )


class InvalidJsonApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.REQUEST.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.INVALID_JSON.value,))


class AuthenticationRequiredApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.AUTHENTICATION.value,))
    errorCode = serializers.ChoiceField(
        choices=(ErrorCode.AUTHENTICATION_REQUIRED.value,)
    )


class CsrfFailedApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.AUTHENTICATION.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.CSRF_FAILED.value,))


class ParticipantRequiredApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.AUTHORIZATION.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.PARTICIPANT_REQUIRED.value,))


class NotAcceptableApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.REQUEST.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.NOT_ACCEPTABLE.value,))


class NotFoundApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.NOT_FOUND.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.NOT_FOUND.value,))


class ScoreOutOfRangeApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.CONFLICT.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.SCORE_OUT_OF_RANGE.value,))


class RequestBodyTooLargeApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.REQUEST.value,))
    errorCode = serializers.ChoiceField(
        choices=(ErrorCode.REQUEST_BODY_TOO_LARGE.value,)
    )


class UnsupportedMediaTypeApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.REQUEST.value,))
    errorCode = serializers.ChoiceField(
        choices=(ErrorCode.UNSUPPORTED_MEDIA_TYPE.value,)
    )


class InternalServerApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.SERVER.value,))
    errorCode = serializers.ChoiceField(
        choices=(ErrorCode.INTERNAL_SERVER_ERROR.value,)
    )


@extend_schema_field({"type": "null"})
class NullOnlyField(serializers.Field[None, object, None, object]):
    default_error_messages = {"invalid": "null 값이어야 합니다."}

    def __init__(self) -> None:
        super().__init__(read_only=True)

    @override
    def to_internal_value(self, data: object) -> None:
        if data is not None:
            self.fail("invalid")
        return None

    @override
    def to_representation(self, value: None) -> None:
        return None


class ErrorEnvelopeBaseSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.ERROR.value,))
    success = NullOnlyField()


class BadRequestErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = PolymorphicProxySerializer(
        component_name="BadRequestApiError",
        serializers={
            ErrorCode.INVALID_JSON.value: InvalidJsonApiErrorSerializer,
            ErrorCode.INVALID_INPUT.value: InvalidInputApiErrorSerializer,
        },
        resource_type_field_name="errorCode",
        many=False,
    )


class InvalidInputErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = InvalidInputApiErrorSerializer()


class ForbiddenErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = PolymorphicProxySerializer(
        component_name="ForbiddenApiError",
        serializers={
            ErrorCode.AUTHENTICATION_REQUIRED.value: (
                AuthenticationRequiredApiErrorSerializer
            ),
            ErrorCode.CSRF_FAILED.value: CsrfFailedApiErrorSerializer,
            ErrorCode.PARTICIPANT_REQUIRED.value: ParticipantRequiredApiErrorSerializer,
        },
        resource_type_field_name="errorCode",
        many=False,
    )


class ReadForbiddenErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = PolymorphicProxySerializer(
        component_name="ReadForbiddenApiError",
        serializers={
            ErrorCode.AUTHENTICATION_REQUIRED.value: (
                AuthenticationRequiredApiErrorSerializer
            ),
            ErrorCode.PARTICIPANT_REQUIRED.value: ParticipantRequiredApiErrorSerializer,
        },
        resource_type_field_name="errorCode",
        many=False,
    )


class NotAcceptableErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = NotAcceptableApiErrorSerializer()


class NotFoundErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = NotFoundApiErrorSerializer()


class ScoreOutOfRangeErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = ScoreOutOfRangeApiErrorSerializer()


class RequestBodyTooLargeErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = RequestBodyTooLargeApiErrorSerializer()


class UnsupportedMediaTypeErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = UnsupportedMediaTypeApiErrorSerializer()


class InternalServerErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = InternalServerApiErrorSerializer()


class ScoreChangeSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = ScoreChangeDataSerializer()


class ScoreChangeCommentSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = ScoreChangeCommentDataSerializer()


class ScoreChangeThreadSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = ScoreChangeThreadDataSerializer()


class ScoreChangePageSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = ScoreChangePageDataSerializer()


class RelationshipScoreListSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = RelationshipScoreListDataSerializer()


class PushDeviceRegisteredSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = PushDeviceRegisteredDataSerializer()


class PushDeviceUnregisteredSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = PushDeviceUnregisteredDataSerializer()
