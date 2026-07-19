from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from drf_spectacular.extensions import OpenApiSerializerExtension
from drf_spectacular.utils import (
    Direction,
    PolymorphicProxySerializer,
    extend_schema_field,
)
from rest_framework import serializers
from rest_framework.settings import api_settings

from ..models import ScoreChange
from .contracts import ErrorCode, ErrorType, ResultType

if TYPE_CHECKING:
    from drf_spectacular.openapi import AutoSchema


@dataclass(frozen=True, slots=True)
class ScoreChangeCommand:
    delta: int
    reason: str


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
        if self.max_length is not None and len(data) > self.max_length:
            self.fail(
                "max_length",
                max_length=self.max_length,
                length=len(data),
            )
        return super().to_internal_value(data)


class ScoreChangeRequestSerializer(serializers.Serializer[object]):
    default_error_messages = {
        "invalid": "JSON 객체를 입력해 주세요.",
    }

    delta = ScoreDeltaField(min_value=-100, max_value=100)
    reason = StrictCharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=200,
        trim_whitespace=True,
    )

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


class ScoreChangeDataSerializer(serializers.Serializer[ScoreChange]):
    id = serializers.IntegerField(read_only=True)
    delta = serializers.IntegerField(
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


class NotAcceptableErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = NotAcceptableApiErrorSerializer()


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
