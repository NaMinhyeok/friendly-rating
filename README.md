# 우리 사이

두 참가자가 서로의 친밀도 점수를 기록하는 소규모 Django 애플리케이션입니다.
참가자 이름과 4자리 PIN은 환경 변수로 관리하며, 점수 변경 이력은 수정할 수
없는 기록으로 보존합니다.

## 로컬 실행

Python 3.13이 필요합니다. 로컬 기본값은 SQLite이며 CI와 운영 환경은 PostgreSQL을
사용합니다.

```bash
uv sync --locked
cp .env.example .env
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py provision_participants
uv run --env-file .env python manage.py runserver
```

`.env`의 참가자 이름과 PIN은 예시값에서 바꿔 사용하세요. 실제 `.env`와 Firebase
서비스 계정은 Git에 포함하지 않습니다.

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

복구 과정에서도 참가자·관계 점수의 기존 PK, 현재 점수, 변경 이력과 푸시 기기
연결을 보존합니다. 사용자 소유권이 모호한 충돌은 수동 확인을 요구합니다.

## 프로젝트 구조

도메인 앱은 `apps/ratings/` 아래에 모읍니다. 앱 내부 템플릿과 정적 파일도 앱이
소유하고, 프로젝트 전체에서 공유하는 설정과 템플릿만 루트에 둡니다.

```text
apps/ratings/
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
`push_device`입니다.

## 품질 검사

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run pytest
```

테스트 러너는 `pytest`와 `pytest-django`를 사용합니다. DB가 필요 없는 규칙은
plain pytest 함수로 작성하고, ORM 통합 테스트는 `django_db`로 DB 의존성을
표시합니다. 실제 커밋·행 잠금·마이그레이션 수명주기를 다루는 테스트는
`TransactionTestCase`를 유지할 수 있습니다. 애플리케이션 DB와 Django Client는
실제로 사용하며 Firebase 같은 외부 시스템 경계만 mock합니다.

테스트는 경계마다 관찰 가능한 결과를 검증합니다. 순수 규칙은 반환값과 오류,
서비스는 최종 DB 상태, HTTP 기능은 응답과 상태 변화, 외부 연동은 전달된 메시지를
계약으로 삼습니다. Django registry, 내부 세션 키, 정확한 SQL이나 내부 호출 횟수는
제품 계약으로 고정하지 않습니다. 단, 읽기 전용 명령의 무쓰기 보장과 Railway 배포
설정처럼 운영 안전에 직접 연결되는 구성은 명시적인 계약 테스트로 유지합니다.

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

## 보안 범위

4자리 PIN은 두 사람이 가볍게 쓰는 용도에 맞춘 의도적인 선택이며, 강한 사용자
인증 수단은 아닙니다. 로그인 시도 제한이 적용되지만 공개 서비스나 민감정보를
다루는 용도로 확장할 때는 더 강한 인증으로 교체해야 합니다.

이 저장소에는 별도의 오픈 소스 라이선스가 부여되어 있지 않습니다.
