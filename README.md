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

## 품질 검사

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py test
```

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
