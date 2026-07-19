from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from drf_spectacular.extensions import OpenApiSerializerExtension
from drf_spectacular.utils import Direction, extend_schema_field
from rest_framework import serializers

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
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictCharField(serializers.CharField):
    @override
    def to_internal_value(self, data: object) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class ScoreChangeRequestSerializer(serializers.Serializer[object]):
    delta = ScoreDeltaField(min_value=-100, max_value=100)
    reason = StrictCharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=200,
        trim_whitespace=True,
    )

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


class ApiErrorSerializer(serializers.Serializer[object]):
    errorType = serializers.ChoiceField(choices=tuple(ErrorType))
    errorCode = serializers.ChoiceField(choices=tuple(ErrorCode))
    reason = serializers.CharField()
    details = ErrorDetailSerializer(many=True)


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


class ErrorEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.ERROR.value,))
    error = ApiErrorSerializer()
    success = NullOnlyField()


class ScoreChangeSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = ScoreChangeDataSerializer()
