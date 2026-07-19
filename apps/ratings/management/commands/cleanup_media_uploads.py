from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from ...services.media_cleanup import (
    cleanup_expired_media_uploads,
    expired_media_upload_count,
)
from ...services.media_uploads import MediaUploadError


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("1 이상의 정수를 입력해 주세요.") from error
    if parsed <= 0:
        raise ValueError("1 이상의 정수를 입력해 주세요.")
    return parsed


class Command(BaseCommand):
    help = "만료된 미연결 R2 업로드와 메타데이터를 안전하게 정리합니다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--limit",
            type=positive_integer,
            default=100,
            help="한 번에 정리할 최대 업로드 수입니다. 기본값은 100입니다.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="만료된 업로드 수만 확인하고 파일이나 DB를 변경하지 않습니다.",
        )

    def handle(self, *args: object, **options: object) -> None:
        if not getattr(settings, "MEDIA_UPLOADS_AVAILABLE", False):
            raise CommandError("R2 미디어 업로드 설정이 활성화되지 않았습니다.")

        limit = options["limit"]
        if not isinstance(limit, int):
            raise CommandError("--limit 값이 올바르지 않습니다.")
        if options["check"]:
            count = expired_media_upload_count()
            self.stdout.write(f"만료된 미연결 업로드: {count}개")
            return

        try:
            result = cleanup_expired_media_uploads(limit=limit)
        except MediaUploadError as error:
            raise CommandError(str(error)) from error

        message = (
            f"만료 업로드 {result.scanned}개를 확인해 {result.deleted}개를 "
            f"정리했습니다. 실패: {result.failed}개."
        )
        if result.failed:
            raise CommandError(message)
        self.stdout.write(self.style.SUCCESS(message))
