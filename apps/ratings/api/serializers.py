from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override
from uuid import UUID

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
    DiaryEntry,
    MediaAttachment,
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
class DeltaScoreChangeCommand:
    delta: int
    reason: str
    media_upload_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class DiaryEntryCommand:
    content: str


@dataclass(frozen=True, slots=True)
class ScoreChangeCommentCommand:
    content: str
    media_upload_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class TargetScoreChangeCommand:
    target_score: int
    reason: str
    media_upload_ids: tuple[UUID, ...]


type ScoreChangeCommand = DeltaScoreChangeCommand | TargetScoreChangeCommand


@dataclass(frozen=True, slots=True)
class PushDeviceCommand:
    fid: str


@dataclass(frozen=True, slots=True)
class InitiateMediaUploadCommand:
    purpose: str
    kind: str
    original_name: str
    content_type: str
    expected_size: int
    score_change_id: int | None


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


@extend_schema_field(
    {
        "type": "integer",
        "minimum": 0,
        "maximum": 100,
    }
)
class ScoreTargetField(serializers.IntegerField):
    @override
    def to_internal_value(self, data: object) -> int:
        if isinstance(data, float) and data.is_integer():
            data = int(data)
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictPositiveIntegerField(serializers.IntegerField):
    @override
    def to_internal_value(self, data: object) -> int:
        if isinstance(data, float) and data.is_integer():
            data = int(data)
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictUUIDField(serializers.UUIDField):
    @override
    def to_internal_value(self, data: object) -> UUID:
        if not isinstance(data, str):
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


class DiaryEntryCreateRequestSerializer(StrictRequestSerializer):
    content = StrictCharField(
        max_length=1000,
        trim_whitespace=True,
    )

    def to_command(self) -> DiaryEntryCommand:
        content = self.validated_data.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Validated diary content is not a string.")
        return DiaryEntryCommand(content=content)


class DiaryEntryUpdateRequestSerializer(StrictRequestSerializer):
    content = StrictCharField(
        max_length=1000,
        trim_whitespace=True,
    )

    def to_command(self) -> DiaryEntryCommand:
        content = self.validated_data.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Validated diary content is not a string.")
        return DiaryEntryCommand(content=content)


def _diary_entry_request_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1000,
            },
        },
        "required": ["content"],
    }


class DiaryEntryCreateRequestSerializerExtension(OpenApiSerializerExtension):
    target_class = DiaryEntryCreateRequestSerializer

    @override
    def map_serializer(
        self,
        auto_schema: "AutoSchema",
        direction: Direction,
    ) -> dict[str, Any]:
        return _diary_entry_request_schema()


class DiaryEntryUpdateRequestSerializerExtension(OpenApiSerializerExtension):
    target_class = DiaryEntryUpdateRequestSerializer

    @override
    def map_serializer(
        self,
        auto_schema: "AutoSchema",
        direction: Direction,
    ) -> dict[str, Any]:
        return _diary_entry_request_schema()


class ScoreChangeRequestSerializer(StrictRequestSerializer):
    delta = ScoreDeltaField(required=False, min_value=-100, max_value=100)
    targetScore = ScoreTargetField(required=False, min_value=0, max_value=100)
    reason = StrictCharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=200,
        trim_whitespace=True,
    )
    mediaUploadIds = serializers.ListField(
        child=StrictUUIDField(),
        required=False,
        default=list,
        max_length=1,
        allow_empty=True,
    )

    def validate_delta(self, value: int) -> int:
        if value == 0:
            raise serializers.ValidationError(
                "변경 점수는 0이 아니어야 합니다.",
                code="non_zero",
            )
        return value

    @override
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if ("delta" in attrs) == ("targetScore" in attrs):
            raise serializers.ValidationError(
                "delta와 targetScore 중 하나만 입력해 주세요.",
                code="exactly_one",
            )
        media_upload_ids = attrs.get("mediaUploadIds", [])
        if len(media_upload_ids) != len(set(media_upload_ids)):
            raise serializers.ValidationError(
                {"mediaUploadIds": "같은 업로드를 중복해서 연결할 수 없습니다."},
                code="duplicate",
            )
        return attrs

    def to_command(self) -> ScoreChangeCommand:
        data = self.validated_data
        reason = data.get("reason")
        if not isinstance(reason, str):
            raise RuntimeError("Validated score reason is not a string.")
        raw_media_upload_ids = data.get("mediaUploadIds", [])
        if not isinstance(raw_media_upload_ids, list) or not all(
            isinstance(upload_id, UUID) for upload_id in raw_media_upload_ids
        ):
            raise RuntimeError("Validated media upload IDs are not UUIDs.")
        media_upload_ids = tuple(raw_media_upload_ids)

        if "delta" in data:
            delta = data.get("delta")
            if not isinstance(delta, int) or isinstance(delta, bool):
                raise RuntimeError("Validated score delta is not an integer.")
            return DeltaScoreChangeCommand(
                delta=delta,
                reason=reason,
                media_upload_ids=media_upload_ids,
            )

        target_score = data.get("targetScore")
        if not isinstance(target_score, int) or isinstance(target_score, bool):
            raise RuntimeError("Validated target score is not an integer.")
        return TargetScoreChangeCommand(
            target_score=target_score,
            reason=reason,
            media_upload_ids=media_upload_ids,
        )


class ScoreChangePageQuerySerializer(StrictRequestSerializer):
    pageNumber = serializers.IntegerField(default=1, min_value=1)


class DiaryEntryPageQuerySerializer(StrictRequestSerializer):
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
                "targetScore": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
                "reason": {
                    "type": "string",
                    "maxLength": 200,
                    "default": "",
                },
                "mediaUploadIds": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "maxItems": 1,
                    "uniqueItems": True,
                    "default": [],
                },
            },
            "oneOf": [
                {"required": ["delta"]},
                {"required": ["targetScore"]},
            ],
        }


class ScoreChangeCommentRequestSerializer(StrictRequestSerializer):
    content = StrictCharField(
        required=False,
        allow_blank=True,
        max_length=500,
        trim_whitespace=True,
    )
    mediaUploadIds = serializers.ListField(
        child=StrictUUIDField(),
        required=False,
        default=list,
        max_length=4,
        allow_empty=True,
    )

    @override
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        content = attrs.get("content", "")
        media_upload_ids = attrs.get("mediaUploadIds", [])
        if not content and not media_upload_ids:
            code = "blank" if "content" in attrs else "required"
            raise serializers.ValidationError(
                {"content": "댓글 내용이나 첨부 파일을 입력해 주세요."},
                code=code,
            )
        if len(media_upload_ids) != len(set(media_upload_ids)):
            raise serializers.ValidationError(
                {"mediaUploadIds": "같은 업로드를 중복해서 연결할 수 없습니다."},
                code="duplicate",
            )
        return attrs

    def to_command(self) -> ScoreChangeCommentCommand:
        content = self.validated_data.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise RuntimeError("Validated score comment is not a string.")
        raw_media_upload_ids = self.validated_data.get("mediaUploadIds", [])
        if not isinstance(raw_media_upload_ids, list) or not all(
            isinstance(upload_id, UUID) for upload_id in raw_media_upload_ids
        ):
            raise RuntimeError("Validated media upload IDs are not UUIDs.")
        return ScoreChangeCommentCommand(
            content=content,
            media_upload_ids=tuple(raw_media_upload_ids),
        )


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
                    "maxLength": 500,
                },
                "mediaUploadIds": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "maxItems": 4,
                    "uniqueItems": True,
                    "default": [],
                },
            },
            "anyOf": [
                {
                    "required": ["content"],
                    "properties": {"content": {"minLength": 1}},
                },
                {
                    "required": ["mediaUploadIds"],
                    "properties": {"mediaUploadIds": {"minItems": 1}},
                },
            ],
        }


class MediaUploadInitiateRequestSerializer(StrictRequestSerializer):
    purpose = serializers.ChoiceField(choices=("scoreChange", "comment"))
    kind = serializers.ChoiceField(choices=("image", "video"))
    fileName = StrictCharField(
        min_length=1,
        max_length=255,
        trim_whitespace=True,
    )
    contentType = StrictCharField(
        min_length=1,
        max_length=100,
        trim_whitespace=True,
    )
    byteSize = StrictPositiveIntegerField(min_value=1)
    scoreChangeId = StrictPositiveIntegerField(
        required=False,
        min_value=1,
    )

    @override
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        purpose = attrs.get("purpose")
        kind = attrs.get("kind")
        has_score_change_id = "scoreChangeId" in attrs
        if purpose == "comment" and not has_score_change_id:
            raise serializers.ValidationError(
                {"scoreChangeId": "댓글 첨부에는 점수 변경 ID가 필요합니다."},
                code="required",
            )
        if purpose == "scoreChange" and has_score_change_id:
            raise serializers.ValidationError(
                {"scoreChangeId": "점수 변경 첨부에는 사용할 수 없는 필드입니다."},
                code="forbidden",
            )
        if purpose == "scoreChange" and kind == "video":
            raise serializers.ValidationError(
                {"kind": "점수 변경에는 이미지만 첨부할 수 있습니다."},
                code="unsupported_kind",
            )
        return attrs

    def to_command(self) -> InitiateMediaUploadCommand:
        data = self.validated_data
        purpose = data.get("purpose")
        kind = data.get("kind")
        original_name = data.get("fileName")
        content_type = data.get("contentType")
        expected_size = data.get("byteSize")
        score_change_id = data.get("scoreChangeId")
        if purpose not in {"scoreChange", "comment"}:
            raise RuntimeError("Validated media purpose is invalid.")
        if kind not in {"image", "video"}:
            raise RuntimeError("Validated media kind is invalid.")
        if not isinstance(original_name, str):
            raise RuntimeError("Validated media filename is not a string.")
        if not isinstance(content_type, str):
            raise RuntimeError("Validated media content type is not a string.")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool):
            raise RuntimeError("Validated media byte size is not an integer.")
        if score_change_id is not None and (
            not isinstance(score_change_id, int) or isinstance(score_change_id, bool)
        ):
            raise RuntimeError("Validated score change ID is not an integer.")
        return InitiateMediaUploadCommand(
            purpose=purpose,
            kind=kind,
            original_name=original_name,
            content_type=content_type,
            expected_size=expected_size,
            score_change_id=score_change_id,
        )


class MediaUploadInitiateRequestSerializerExtension(OpenApiSerializerExtension):
    target_class = MediaUploadInitiateRequestSerializer

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
                "purpose": {
                    "type": "string",
                    "enum": ["scoreChange", "comment"],
                },
                "kind": {
                    "type": "string",
                    "enum": ["image", "video"],
                },
                "fileName": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255,
                },
                "contentType": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                },
                "byteSize": {
                    "type": "integer",
                    "minimum": 1,
                },
                "scoreChangeId": {
                    "type": "integer",
                    "minimum": 1,
                },
            },
            "required": [
                "purpose",
                "kind",
                "fileName",
                "contentType",
                "byteSize",
            ],
            "allOf": [
                {
                    "if": {"properties": {"purpose": {"const": "comment"}}},
                    "then": {"required": ["scoreChangeId"]},
                },
                {
                    "if": {"properties": {"purpose": {"const": "scoreChange"}}},
                    "then": {
                        "properties": {"kind": {"const": "image"}},
                        "not": {"required": ["scoreChangeId"]},
                    },
                },
            ],
        }


class MediaUploadCompleteRequestSerializer(StrictRequestSerializer):
    pass


class MediaUploadCompleteRequestSerializerExtension(OpenApiSerializerExtension):
    target_class = MediaUploadCompleteRequestSerializer

    @override
    def map_serializer(
        self,
        auto_schema: "AutoSchema",
        direction: Direction,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
        }


class InitiatedMediaUploadDataSerializer(serializers.Serializer[object]):
    uploadId = serializers.UUIDField(source="upload_id", read_only=True)
    uploadUrl = serializers.URLField(source="upload_url", read_only=True)
    requiredHeaders = serializers.DictField(
        source="required_headers",
        child=serializers.CharField(),
        read_only=True,
    )
    expiresAt = serializers.DateTimeField(source="expires_at", read_only=True)


class CompletedMediaUploadDataSerializer(serializers.Serializer[MediaAttachment]):
    id = serializers.UUIDField(read_only=True)
    kind = serializers.ChoiceField(
        choices=("image", "video"),
        read_only=True,
    )
    fileName = serializers.CharField(
        source="original_name",
        read_only=True,
        max_length=255,
    )
    contentType = serializers.CharField(
        source="content_type",
        read_only=True,
        max_length=100,
    )
    byteSize = serializers.IntegerField(
        source="actual_size",
        read_only=True,
        min_value=1,
    )


class MediaAttachmentDataSerializer(CompletedMediaUploadDataSerializer):
    contentUrl = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_contentUrl(self, attachment: MediaAttachment) -> str:
        return reverse(
            "media-content",
            kwargs={"attachment_id": attachment.pk},
        )


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
    attachments = serializers.SerializerMethodField()

    @extend_schema_field(MediaAttachmentDataSerializer(many=True))
    def get_attachments(self, change: ScoreChange) -> list[dict[str, Any]]:
        prefetched = getattr(change, "_score_media_attachments", None)
        if isinstance(prefetched, list) and all(
            isinstance(attachment, MediaAttachment) for attachment in prefetched
        ):
            attachments = prefetched
        else:
            attachments = list(
                MediaAttachment.objects.filter(
                    score_change=change,
                    purpose=MediaAttachment.Purpose.SCORE_CHANGE,
                    status=MediaAttachment.Status.ATTACHED,
                ).order_by("position", "created_at", "id")
            )
        return [dict(MediaAttachmentDataSerializer(item).data) for item in attachments]


class ParticipantSummarySerializer(serializers.Serializer[Participant]):
    slot = serializers.IntegerField(read_only=True, min_value=1, max_value=2)
    displayName = serializers.CharField(
        source="display_name",
        read_only=True,
        max_length=30,
    )


class DiaryEntryDataSerializer(serializers.Serializer[DiaryEntry]):
    id = serializers.IntegerField(read_only=True, min_value=1)
    author = ParticipantSummarySerializer(read_only=True)
    content = serializers.CharField(read_only=True, max_length=1000)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(
        source="updated_at",
        read_only=True,
        allow_null=True,
    )
    isMine = serializers.SerializerMethodField()

    def get_isMine(self, entry: DiaryEntry) -> bool:
        participant_id = self.context.get("participant_id")
        return entry.author_id == participant_id


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
    attachments = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_threadUrl(self, change: ScoreChange) -> str:
        return reverse(
            "score-change-thread",
            kwargs={"score_change_id": change.pk},
        )

    @extend_schema_field(MediaAttachmentDataSerializer(many=True))
    def get_attachments(self, change: ScoreChange) -> list[dict[str, Any]]:
        prefetched = getattr(change, "_score_media_attachments", None)
        if isinstance(prefetched, list) and all(
            isinstance(attachment, MediaAttachment) for attachment in prefetched
        ):
            attachments = prefetched
        else:
            attachments = list(
                MediaAttachment.objects.filter(
                    score_change=change,
                    purpose=MediaAttachment.Purpose.SCORE_CHANGE,
                    status=MediaAttachment.Status.ATTACHED,
                ).order_by("position", "created_at", "id")
            )
        return [dict(MediaAttachmentDataSerializer(item).data) for item in attachments]


class ScoreChangeCommentDataSerializer(serializers.Serializer[ScoreChangeComment]):
    id = serializers.IntegerField(read_only=True, min_value=1)
    author = ParticipantSummarySerializer(read_only=True)
    content = serializers.CharField(read_only=True, max_length=500)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    isMine = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()

    def get_isMine(self, comment: ScoreChangeComment) -> bool:
        participant_id = self.context.get("participant_id")
        return comment.author_id == participant_id

    @extend_schema_field(MediaAttachmentDataSerializer(many=True))
    def get_attachments(self, comment: ScoreChangeComment) -> list[dict[str, Any]]:
        prefetched = getattr(comment, "_comment_media_attachments", None)
        if isinstance(prefetched, list) and all(
            isinstance(attachment, MediaAttachment) for attachment in prefetched
        ):
            attachments = prefetched
        else:
            attachments = list(
                MediaAttachment.objects.filter(
                    comment=comment,
                    status=MediaAttachment.Status.ATTACHED,
                ).order_by("position", "created_at", "id")
            )
        return [dict(MediaAttachmentDataSerializer(item).data) for item in attachments]


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


class DiaryEntryPageDataSerializer(serializers.Serializer[object]):
    results = serializers.ListField(
        child=DiaryEntryDataSerializer(),
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


@extend_schema_field({"type": "string", "const": ErrorType.AUTHORIZATION.value})
class AuthorizationErrorTypeField(serializers.CharField):
    pass


class PermissionDeniedApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = AuthorizationErrorTypeField()
    errorCode = serializers.ChoiceField(choices=(ErrorCode.PERMISSION_DENIED.value,))


class NotAcceptableApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.REQUEST.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.NOT_ACCEPTABLE.value,))


class NotFoundApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = serializers.ChoiceField(choices=(ErrorType.NOT_FOUND.value,))
    errorCode = serializers.ChoiceField(choices=(ErrorCode.NOT_FOUND.value,))


@extend_schema_field({"type": "string", "const": ErrorType.CONFLICT.value})
class ConflictErrorTypeField(serializers.CharField):
    pass


class ScoreOutOfRangeApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = ConflictErrorTypeField()
    errorCode = serializers.ChoiceField(choices=(ErrorCode.SCORE_OUT_OF_RANGE.value,))


class ScoreUnchangedApiErrorSerializer(ScoreOutOfRangeApiErrorSerializer):
    errorCode = serializers.ChoiceField(choices=(ErrorCode.SCORE_UNCHANGED.value,))


class MediaUploadConflictApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = ConflictErrorTypeField()
    errorCode = serializers.ChoiceField(
        choices=(ErrorCode.MEDIA_UPLOAD_CONFLICT.value,)
    )


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


@extend_schema_field({"type": "string", "const": ErrorType.SERVER.value})
class ServerErrorTypeField(serializers.CharField):
    pass


class InternalServerApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = ServerErrorTypeField()
    errorCode = serializers.ChoiceField(
        choices=(ErrorCode.INTERNAL_SERVER_ERROR.value,)
    )


class MediaUploadsUnavailableApiErrorSerializer(EmptyDetailsApiErrorSerializer):
    errorType = ServerErrorTypeField()
    errorCode = serializers.ChoiceField(
        choices=(ErrorCode.MEDIA_UPLOADS_UNAVAILABLE.value,)
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


class MutationForbiddenErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = PolymorphicProxySerializer(
        component_name="MutationForbiddenApiError",
        serializers={
            ErrorCode.AUTHENTICATION_REQUIRED.value: (
                AuthenticationRequiredApiErrorSerializer
            ),
            ErrorCode.CSRF_FAILED.value: CsrfFailedApiErrorSerializer,
            ErrorCode.PARTICIPANT_REQUIRED.value: ParticipantRequiredApiErrorSerializer,
            ErrorCode.PERMISSION_DENIED.value: PermissionDeniedApiErrorSerializer,
        },
        resource_type_field_name="errorCode",
        many=False,
    )


class MediaForbiddenErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = PolymorphicProxySerializer(
        component_name="MediaForbiddenApiError",
        serializers={
            ErrorCode.AUTHENTICATION_REQUIRED.value: (
                AuthenticationRequiredApiErrorSerializer
            ),
            ErrorCode.CSRF_FAILED.value: CsrfFailedApiErrorSerializer,
            ErrorCode.PARTICIPANT_REQUIRED.value: ParticipantRequiredApiErrorSerializer,
            ErrorCode.PERMISSION_DENIED.value: PermissionDeniedApiErrorSerializer,
        },
        resource_type_field_name="errorCode",
        many=False,
    )


class NotAcceptableErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = NotAcceptableApiErrorSerializer()


class NotFoundErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = NotFoundApiErrorSerializer()


class ScoreOutOfRangeErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = PolymorphicProxySerializer(
        component_name="ScoreConflictApiError",
        serializers={
            ErrorCode.SCORE_OUT_OF_RANGE.value: ScoreOutOfRangeApiErrorSerializer,
            ErrorCode.SCORE_UNCHANGED.value: ScoreUnchangedApiErrorSerializer,
            ErrorCode.MEDIA_UPLOAD_CONFLICT.value: MediaUploadConflictApiErrorSerializer,
        },
        resource_type_field_name="errorCode",
        many=False,
    )


class MediaUploadConflictErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = MediaUploadConflictApiErrorSerializer()


class RequestBodyTooLargeErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = RequestBodyTooLargeApiErrorSerializer()


class UnsupportedMediaTypeErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = UnsupportedMediaTypeApiErrorSerializer()


class InternalServerErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = InternalServerApiErrorSerializer()


class MediaUploadsUnavailableErrorEnvelopeSerializer(ErrorEnvelopeBaseSerializer):
    error = MediaUploadsUnavailableApiErrorSerializer()


class DiaryEntrySuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = DiaryEntryDataSerializer()


class DiaryEntryPageSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = DiaryEntryPageDataSerializer()


class DiaryEntryDeletedSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = NullOnlyField()


class ScoreChangeSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = ScoreChangeDataSerializer()


class MediaUploadInitiatedSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = InitiatedMediaUploadDataSerializer()


class CompletedMediaUploadSuccessEnvelopeSerializer(serializers.Serializer[object]):
    resultType = serializers.ChoiceField(choices=(ResultType.SUCCESS.value,))
    error = NullOnlyField()
    success = CompletedMediaUploadDataSerializer()


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
