# 우리 사이

두 참가자가 서로의 친밀도 점수를 기록하는 소규모 Django 애플리케이션입니다.
참가자 이름과 4자리 PIN은 환경 변수로 관리하며, 점수 변경 이력은 수정할 수
없는 기록으로 보존합니다. 각 점수 변경에는 두 참가자가 시간순 댓글을 남길 수
있습니다.

## 로컬 실행

Python 3.13이 필요하고 전체 품질 게이트는 Node.js 18 이상도 사용합니다. 로컬
기본값은 SQLite이며 CI와 운영 환경은 PostgreSQL을 사용합니다.

```bash
make setup
cp .env.example .env
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py provision_participants
make run
```

`.env`의 참가자 이름과 PIN은 예시값에서 바꿔 사용하세요. 실제 `.env`와 Firebase
서비스 계정은 Git에 포함하지 않습니다.

## 비공개 미디어 업로드

점수를 기록할 때는 이미지 한 장을 이유와 함께 남길 수 있고, 점수 대화의 댓글에는
이미지 최대 네 장 또는 짧은 영상 한 개를 첨부할 수 있습니다. 실제 파일은 Django나
Railway를 통과하지 않고 브라우저가 짧은 유효기간의 서명된 `PUT` URL로 Cloudflare
R2에 직접 전송합니다. PostgreSQL에는 참가자 소유권, 대화 연결, 파일 종류와 크기 같은
메타데이터만 저장합니다.

R2 버킷은 public access를 켜지 않은 비공개 버킷으로 만들고, 해당 버킷의 object
read/write만 허용한 R2 S3 API token을 발급합니다. Wrangler의 OAuth 로그인은 버킷과
CORS 같은 인프라 설정에 사용할 수 있지만, Django가 presigned URL을 생성하려면 별도의
R2 Access Key ID와 Secret Access Key가 필요합니다.

브라우저 직접 업로드를 위해 버킷 CORS는 실제 앱 origin과 로컬 개발 origin만
허용합니다. `Content-Type`, 브라우저가 계산한 정확한 `Content-Length`, 비공개
`Cache-Control`도 서명에 포함되며, 현재 적용한 규칙은 `config/r2-cors.json`에
보존합니다.

업로드를 사용하려면 Railway 또는 로컬 `.env`에 다음 값을 등록합니다. endpoint에는
Cloudflare R2 화면에 표시되는 S3 API endpoint를 사용하고 비밀값은 로그나 Git에 넣지
않습니다.

```dotenv
MEDIA_UPLOADS_ENABLED=True
R2_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=replace-with-r2-access-key-id
R2_SECRET_ACCESS_KEY=replace-with-r2-secret-access-key
R2_BUCKET_NAME=friendly-rating-private-media
R2_REGION_NAME=auto
MEDIA_UPLOAD_URL_TTL_SECONDS=900
MEDIA_DOWNLOAD_URL_TTL_SECONDS=300
```

허용 형식과 상한은 이미지 JPEG/PNG/WebP 10MiB, 영상 MP4/WebM/QuickTime 100MiB입니다.
점수 변경에는 이미지만 한 장 첨부할 수 있습니다. 서버는 요청한 크기·MIME type과
기본 파일 서명을 확인한 뒤 재사용할 수 없는 object key로 복사합니다. 이는 공개형
미디어 서비스의 전체 디코딩·영상 트랜스코딩을 대신하지 않으므로, 다중 사용자로
확장할 때는 이미지 해상도 제한과 영상 길이 검사·트랜스코딩을 추가해야 합니다.

한 참가자가 연결하지 않은 업로드는 최대 20개, 요청 크기 합계 512MiB까지만 유지할
수 있습니다. 완료됐지만 점수나 댓글에 연결되지 않은 파일은 24시간 뒤 만료됩니다.
업로드가 끝나지 않은 `pending/` object는 R2 lifecycle rule로 하루 뒤 삭제하도록
설정했고, 만료된 DB metadata와 `media/` object는 다음 명령을 정기적으로 실행해
함께 정리합니다. 이 명령을 배포 전 migration 단계에 추가하지 말고 별도 일일 작업으로
운영합니다.

로컬에서는 `.env`를 명시해 상태를 확인하거나 정리할 수 있습니다.

```bash
# 로컬 무쓰기 확인
uv run --env-file .env python manage.py cleanup_media_uploads --check

# 로컬에서 한 번에 최대 100개 정리
uv run --env-file .env python manage.py cleanup_media_uploads --limit 100
```

Railway의 별도 `media-cleanup` Cron 서비스는 `/railway.cron.toml`을 사용하며, 매일
`0 18 * * *`(UTC, 한국 시간 03:00)에 실행됩니다. 환경 변수가 런타임에 이미
주입되므로 `.env`를 참조하지 않고 다음 명령을 사용합니다.

서비스 변수는 운영 비밀을 복사하지 않고 Railway reference로 연결합니다.
`DATABASE_URL`은 `${{Postgres.DATABASE_URL}}`을, `SECRET_KEY`,
`MEDIA_UPLOADS_ENABLED`, 모든 `R2_*`와 미디어 URL TTL 변수는 각각 같은 이름의
`${{web.<변수명>}}` 값을 참조합니다.

```bash
python manage.py cleanup_media_uploads --limit 100
```

하나라도 R2 삭제에 실패하면 명령은 0이 아닌 종료 코드로 끝나므로 일일 작업의 재시도나
경고 조건으로 사용할 수 있습니다. 사용자 미디어는 PWA offline cache에 저장하지
않습니다.

## 참가자 설정

`provision_participants`의 기본 실행은 빈 데이터베이스를 최초 설정합니다. 이미
설정이 정확하면 아무 데이터도 쓰지 않고 끝나며, 차이가 있으면 자동으로 덮어쓰지
않습니다.

```bash
# 읽기 전용 검사
uv run --env-file .env python manage.py provision_participants --check

# 안전하게 처리 가능한 차이만 명시적으로 반영
uv run --env-file .env python manage.py provision_participants --reconcile
```

복구 과정에서도 참가자·관계 점수의 기존 PK, 현재 점수, 변경 이력과 댓글, 푸시
기기 연결을 보존합니다. 사용자 소유권이 모호한 충돌은 수동 확인을 요구합니다.

## 프로젝트 구조

도메인 앱은 `apps/ratings/` 아래에 모읍니다. 앱 내부 템플릿과 정적 파일도 앱이
소유하고, 프로젝트 전체에서 공유하는 설정과 템플릿만 루트에 둡니다.

```text
apps/ratings/
├── api/
├── migrations/
├── participant_provisioning/
├── services/
├── static/ratings/
├── templates/ratings/
├── tests/
└── views/
config/
templates/
```

파이썬 경로는 `apps.ratings`이지만 Django의 논리 앱 라벨은 기존 `ratings`를
유지합니다. 따라서 마이그레이션 이력, content type, admin URL은 바뀌지 않으며
도메인 테이블명은 `participant`, `relationship_score`, `score_change`,
`score_change_comment`, `push_device`, `media_attachment`입니다.

## JSON API

DRF 기반 API는 `/api/v1/` 아래에 둡니다. 현재 endpoint는 다음과 같습니다.

- `GET /api/v1/score-changes/`: 두 참가자가 공유하는 점수 변경 이력 조회
- `POST /api/v1/score-changes/`: 로그인한 참가자의 outgoing score 변경
- `POST /api/v1/media-uploads/`: 비공개 R2 직접 업로드 URL 발급
- `POST /api/v1/media-uploads/{id}/complete/`: 업로드된 파일 확인 및 첨부 준비
- `GET /api/v1/score-changes/{id}/`: 점수 변경과 시간순 댓글로 구성된 대화 조회
- `POST /api/v1/score-changes/{id}/comments/`: 로그인한 참가자의 댓글 작성
- `GET /api/v1/relationship-scores/`: 두 방향의 현재 관계 점수 조회
- `POST /api/v1/push-devices/register/`: 현재 기기를 푸시 알림 대상으로 등록
- `POST /api/v1/push-devices/unregister/`: 현재 기기의 푸시 알림 등록 해제

브라우저의 기존 Django 세션을 사용하고, 변경 요청에는 같은 출처에서 얻은
`X-CSRFToken` 헤더가 필요합니다. 참가자 ID는 요청에서 받지 않고 로그인 세션에서
결정합니다. 푸시 기기 API는 정확한 형식의 `fid`만 입력받고 성공 시
`success.registered`로 최종 등록 상태를 반환합니다. 현재 알림 프런트도 이 versioned
API만 사용합니다.

홈, 마음 기록과 점수 대화는 인증된 Django HTML 셸을 렌더링한 뒤 JSON API로
데이터를 채웁니다. 홈의 점수 변경도 API로 전송하며 `/score/change/` HTML
mutation은 제거되었습니다. JavaScript를 사용할 수 없으면 세 화면에서 관계
데이터를 조회하거나 변경할 수 없습니다. 관계 점수 응답은 `success.results`에 현재
참가자의 outgoing score를 먼저 반환하고 각 항목의 `isMine`으로 변경 가능한 방향을
명시합니다. 점수 변경 이력은 `pageNumber`로 조회하고 20개 고정 크기의
`success.results`와 `success.paging`을 반환합니다. 각 이력은 댓글 수와 점수 대화
화면 경로를 포함합니다. 점수 대화 화면은 최초 진입, 백그라운드·BFCache 복귀와
활성 화면의 푸시 수신 때 API에서 최신 댓글을 다시 조회합니다. 점수 변경·댓글
푸시는 사적인 내용 없이 해당 대화 화면으로 연결됩니다. 인증된 관계 데이터에는
`Cache-Control: private, no-store`가 적용됩니다.

점수 변경 요청은 기존처럼 `delta`에 증감값을 보내거나 `targetScore`에 0~100 사이의
최종 점수를 보낼 수 있습니다. 두 필드 중 정확히 하나만 입력하며 `reason`은 선택
사항입니다. 두 방식 모두 응답과 변경 이력에는 실제 증감값인 `delta`와 변경 후 점수인
`resultingScore`가 기록됩니다.

모든 API 응답은 `resultType`, `error`, `success`를 갖는 JSON envelope를 사용합니다.
성공과 오류 branch는 서로 배타적이며 HTTP 상태 코드를 그대로 유지합니다. 생성된
OpenAPI 3.1 문서는 `/api/schema/`에서 확인할 수 있습니다. 이 schema endpoint 자체는
표준 OpenAPI 문서를 반환하므로 API envelope를 사용하지 않습니다.

## 품질 검사

```bash
make lint
make typecheck
make test PYTEST_ARGS="apps/ratings/tests/test_score_rules.py -q"
make check
```

`Makefile`은 사람이 기억하기 쉬운 얇은 진입점입니다. 부분 검사와 전체 검사의 실제
구현은 `scripts/check` 한 곳에 있고, `make check`는 dependency lock, Ruff, Pyrefly,
Django system/deploy checks, 기존 migration의 수정·삭제와 누락 migration, Node
브라우저 코드 테스트, 전체 pytest, OpenAPI 생성·검증, production static collection을
실행합니다. 로컬과 GitHub Actions가 같은 진입점을 사용합니다. `make test`와 전체
검사는 shell의 일반 `DATABASE_URL`을 사용하지 않고 격리된 SQLite를 사용하며, 전체
검사의 static collection 출력은 임시 디렉터리에만 만듭니다.

`make typecheck`는 migration을 제외한 프로젝트 전체를 Pyrefly `default` preset으로
검사하고, `score_rules.py`, `services/`, `api/`는 `strict`로 한 번 더 검사합니다.
타입 오류를 숨기는 baseline,
대량 suppression과 전역 ignore는 사용하지 않습니다.

테스트 러너는 `pytest`와 `pytest-django`를 사용합니다. DB가 필요 없는 규칙은
plain pytest 함수로 작성하고, ORM 통합 테스트는 `django_db`로 DB 의존성을
표시합니다. 실제 커밋·행 잠금·마이그레이션 수명주기를 다루는 테스트는
`TransactionTestCase`를 유지할 수 있습니다. 애플리케이션 DB와 Django Client는
실제로 사용하고 Firebase 같은 외부 경계에는 목적에 맞는 stub, fake, mock 또는
spy를 선택합니다. transaction rollback처럼 직접 만들기 어려운 실패를 주입할 때는
내부 collaborator를 제한적으로 patch합니다. 이 용어는 클래스 이름이 아니라
테스트에서 맡는 역할을 뜻하며 세부 선택 기준은 테스트 가이드에 있습니다.

테스트는 경계마다 관찰 가능한 결과를 검증합니다. 순수 규칙은 반환값과 오류,
서비스는 최종 DB 상태, HTTP 기능은 응답과 상태 변화, 외부 연동은 전달된 메시지를
계약으로 삼습니다. Django registry, 내부 세션 키, 정확한 SQL이나 내부 호출 횟수는
제품 계약으로 고정하지 않습니다. 단, 읽기 전용 명령의 무쓰기 보장과 Railway 배포
설정처럼 운영 안전에 직접 연결되는 구성은 명시적인 계약 테스트로 유지합니다.

## 에이전트 하네스

저장소 루트의 [`AGENTS.md`](AGENTS.md)는 코딩 에이전트가 매 작업에서 따를 저장소
지도, 핵심 도메인·운영 불변식, 작업 순서와 완료 조건을 정의합니다. 세부 기준은
다음 문서로 분리합니다.

- [`docs/agent/code-conventions.md`](docs/agent/code-conventions.md): Django 계층,
  트랜잭션, 보안, migration과 frontend 코드 컨벤션
- [`docs/agent/testing.md`](docs/agent/testing.md): 변경 경계별 테스트 선택, fixture와
  테스트 더블 규칙, PostgreSQL/SQLite 차이와 검증 명령

에이전트가 같은 실수를 반복하거나 리뷰에서 같은 피드백이 나오면 실행 가능한 검사로
강제할 수 있는지 먼저 확인하고, 그렇지 않은 팀 규칙만 가장 가까운 하네스 문서에
추가합니다.

`main` 브랜치의 변경은 GitHub Actions 검사를 통과한 PR로 병합되며 Railway가
자동 배포합니다. 배포 전에는 마이그레이션만 실행하고 참가자 설정은 실행하지
않습니다. 새 Railway 환경에서는 먼저 웹 서비스의 Variables에
`PARTICIPANT_1_NAME`, `PARTICIPANT_1_PIN`, `PARTICIPANT_2_NAME`,
`PARTICIPANT_2_PIN`을 등록합니다. 그다음 해당 서비스에 연결한 CLI에서 다음
one-off를 실행해 최초 설정과 읽기 전용 검사를 완료합니다.

```bash
railway ssh -- python manage.py provision_participants
railway ssh -- python manage.py provision_participants --check
```

Railway에서 과거 배포를 다시 활성화해도 데이터베이스 migration은 자동으로
되돌아가지 않습니다. 롤백 전에는 다음 읽기 전용 명령으로 대상 코드가 현재 schema와
호환되는지 확인합니다.

```bash
railway ssh -- python manage.py showmigrations ratings --plan
```

`ratings.0006_rename_domain_tables`가 적용된 환경의 schema 롤백 하한은 해당 migration을
도입한 `6519358`입니다. 별도로 검토한 reverse migration 계획 없이 그 이전 revision을
배포하지 않고, 가능하면 현재 schema와 호환되는 forward fix를 배포합니다. 이후의
파괴적 schema 변경은 `docs/agent/code-conventions.md`의 expand → migrate/backfill →
contract 순서를 따릅니다.

## 보안 범위

4자리 PIN은 두 사람이 가볍게 쓰는 용도에 맞춘 의도적인 선택이며, 강한 사용자
인증 수단은 아닙니다. 로그인 시도 제한이 적용되지만 공개 서비스나 민감정보를
다루는 용도로 확장할 때는 더 강한 인증으로 교체해야 합니다.

이 저장소에는 별도의 오픈 소스 라이선스가 부여되어 있지 않습니다.
