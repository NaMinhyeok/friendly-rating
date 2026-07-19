# Django 테스트 전략

이 프로젝트의 테스트는 구현 세부가 아니라 사용자가 관찰하는 동작, 영속 데이터의
불변식, 운영 안전 계약을 빠르게 피드백해야 한다. 테스트 수나 mock 호출 수보다
실패했을 때 원인을 좁혀 주는 경계와 실제성에 우선순위를 둔다.

## 기본 원칙

1. 변경된 동작을 검증하는 가장 낮은 안정된 경계를 선택한다.
2. DB가 필요 없는 규칙은 plain pytest로 유지한다. ORM을 쓰는 테스트만
   `django_db`를 요청한다.
3. 애플리케이션 DB, Django request/response, form, template은 가능한 실제 구현을
   사용한다. 프로세스 밖 경계를 목적에 맞는 테스트 더블로 대체하며, rollback처럼
   만들기 어려운 실패를 주입하거나 독립된 collaborator를 격리할 때만 내부 seam을
   제한적으로 patch한다.
4. 입력보다 결과를 검증한다. 서비스는 최종 DB 상태와 rollback, HTTP 기능은 응답과
   상태 변화, 외부 연동은 전달되는 공개 계약을 단언한다.
5. 하나의 테스트는 한 가지 실패 이유를 갖되, 같은 행동의 경계값은
   `pytest.mark.parametrize`로 묶는다.
6. 버그 테스트는 수정 전 실패하고 수정 후 통과해야 한다. 기존 테스트가 이미
   재현한다면 중복 테스트를 만들지 않는다.

## 경계 선택표

| 변경 대상 | 기본 테스트 형태 | 검증할 것 | 피할 것 |
| --- | --- | --- | --- |
| `score_rules.py` 같은 순수 규칙 | plain pytest 함수 | 반환값, 정규화, 경계값, 예외 | DB marker, Django Client |
| model 제약과 manager | `pytest.mark.django_db` | DB가 거부/보존하는 실제 상태 | model method만 mock |
| service/트랜잭션 | `django_db` | 관련 row의 최종 상태, 이력, rollback | 내부 ORM 호출 순서 고정 |
| form/view/auth | pytest-django `client`, 필요 시 `rf` | status, redirect, content, 인증·DB 변화 | 내부 session key, view 호출 횟수 |
| CSRF 또는 method 보안 | `Client(enforce_csrf_checks=True)`와 공개 URL | 403/405와 무쓰기 | middleware를 직접 호출 |
| commit, row lock, 실제 transaction | `TransactionTestCase` | commit 경계, 직렬화, DB 기능 | SQLite 결과를 운영 근거로 사용 |
| Firebase 등 외부 경계 | adapter가 사용하는 심볼을 patch | 공개 메시지, 실패 격리, 기기 상태 | 네트워크 호출, SDK 내부 구현 단언 |
| template/PWA/static | HTTP 응답과 공개 파일 | 사용자 문구, metadata, cache/content type | template registry/물리 경로 고정 |
| 배포·관리 명령 | 설정 파일 또는 `call_command` 계약 테스트 | migrate/provision 분리, check 무쓰기, health | 단순 문자열 snapshot만 검증 |
| data migration | `MigrationExecutor`가 필요한 통합 테스트 | 이전 state에서 forward 적용 후 데이터/제약 | 현재 model을 historical migration에서 사용 |

`TestCase` class는 django-axes 상태, `captureOnCommitCallbacks`, 복잡한 setup/cleanup처럼
수명주기를 명확하게 만드는 경우 유지한다. 단순한 새 테스트는 함수형 pytest와
명시적인 fixture를 우선한다. 실제 commit이나 여러 connection이 필요할 때만
`TransactionTestCase`를 사용한다.

## 프로젝트 fixture와 데이터

- 공통 참가자 aggregate는 `participant_pair` fixture 또는
  `create_participant_pair()`를 사용한다.
- fixture는 테스트가 사용하는 의존성을 인자에서 드러낸다. 모든 테스트에 영향을
  주는 `autouse` fixture는 전역 격리 계약이 아니면 만들지 않는다.
- 실제 PIN, Firebase credential, 운영 URL을 fixture에 넣지 않는다. 테스트용 값은
  명백한 가짜 값이어야 한다.
- 시간, PK, queryset 기본 순서처럼 계약이 아닌 우연한 값은 단언하지 않는다.
- factory가 도메인 불변식을 우회해야 하는 migration/손상 복구 테스트는 이유와
  복원 절차를 테스트 안에 명시한다.

## Django와 DB 세부 규칙

- URL은 하드코딩보다 `reverse()`로 호출한다. 경로 자체가 공개 계약인 테스트만
  문자열 경로를 단언한다.
- mutation endpoint는 성공뿐 아니라 비로그인, 잘못된 method/입력, 경계값과
  실패 시 무쓰기를 검증한다.
- DB constraint는 `full_clean()`만으로 대신하지 말고 실제 저장 시 DB가 보장하는지
  검증한다.
- 트랜잭션 테스트는 성공한 row뿐 아니라 중간 쓰기 실패 시 모든 관련 row가 원래
  상태로 돌아오는지 확인한다.
- query SQL 문자열이나 query 수는 DB 동작/성능이 명시적 회귀 계약일 때만
  단언한다. 행 잠금 테스트는 SQL 모양보다 동시 결과를 우선하고, 필요한 경우 잠금
  존재를 보조 증거로 확인한다.
- SQLite는 `select_for_update`를 지원하지 않는다. 로컬 skip은 허용되지만 PostgreSQL
  CI에서 해당 테스트가 실행되는 것이 완료 조건이다.
- schema 변경은 새 migration을 만든다. rename, 데이터 변환, nullability 강화,
  삭제처럼 데이터 손실 가능성이 있으면 이전 상태의 representative row를 만든 뒤
  forward migration 후 PK, 관계, 값, 제약을 확인한다.

## 테스트 더블 선택

dummy, stub, fake, mock, spy는 Python 클래스 이름이 아니라 테스트에서 맡는 역할이다.
`patch`와 `monkeypatch`는 collaborator를 바꾸는 도구일 뿐 그 자체가 mock은 아니다.
같은 `Mock` 객체도 고정값만 주면 stub이고 호출 계약을 검증하면 mock이 된다.

| 종류 | 사용할 때 | 이 저장소의 예 또는 기준 |
| --- | --- | --- |
| dummy | 자리는 필요하지만 테스트 대상이 값을 읽지 않을 때 | Firebase app 자리에 전달하는 `object()` |
| stub | 정해진 반환값·예외로 특정 경로를 만들 때 | password-status lambda, `SimpleNamespace` 전송 결과, `OperationalError`/FCM 실패 주입 |
| fake | 작은 동작과 상태가 있어 여러 시나리오에서 재사용할 때 | 필요 시 외부 gateway의 in-memory 구현; 실제 adapter와 같은 좁은 계약과 공통 contract test를 둔다 |
| mock | 호출 인자·횟수·시점 자체가 제품 계약일 때 | commit 전 미호출과 commit 후 1회 알림 전송 검증 |
| spy | 실제 구현을 실행하면서 제한된 상호작용만 관찰해야 할 때 | `wraps`를 사용하되 현재 필요한 사례는 없다 |

- 가장 먼저 실제 in-process Django DB, Client, form, template을 쓸 수 있는지 본다.
  SQLite를 PostgreSQL 행 잠금의 fake로 취급하지 않는다.
- 외부 응답 하나나 실패 한 번이면 stub을 쓴다. 상태가 누적되는 여러 시나리오가
  필요할 때만 fake를 만들고, production Protocol 또는 공개 adapter 계약을 만족하게
  한다. `SimpleNamespace`라고 자동으로 fake가 되는 것은 아니다.
- patch는 정의된 곳이 아니라 시스템이 lookup하는 곳에 적용한다. 가능한 안정된
  callable에는 `spec`/`autospec`을 쓰되, 동적 SDK에는 실제로 소비하는 속성만 가진
  contract-shaped stub을 허용한다.
- 내부 collaborator patch는 transaction 중간 실패를 주입하거나 별도 규칙을 격리하는
  경우에만 사용하고, 내부 호출 자체보다 시스템의 최종 결과를 함께 단언한다.
- Django ORM, form validation, template rendering, transaction 자체는 기본적으로
  대체하지 않는다. call count는 외부 부작용의 중복 방지처럼 제품 계약일 때만
  검증하고 내부 ORM/view 호출 순서를 고정하지 않는다.
- spy는 실제 부작용까지 실행한다. 네트워크나 쓰기 작업에 무심코 적용하지 않고,
  stub이나 결과 검증으로 충분하면 사용하지 않는다.
- 여기서 말하는 fake는 테스트 더블이다. Django migration의 `--fake`와 무관하며,
  `migrate --fake`는 별도 복구 승인 없이는 사용하지 않는다.

## 실행 전략

반복 중에는 가장 가까운 테스트부터 실행한다.

```bash
make test PYTEST_ARGS="apps/ratings/tests/test_score_rules.py -q"
make test PYTEST_ARGS="apps/ratings/tests/test_services.py -q"
make test PYTEST_ARGS="apps/ratings/tests/test_score_workflow.py::test_out_of_range_change_returns_bad_request_without_writing -q"
```

코드·설정·스크립트를 바꾼 구현 작업은 완료 전에 저장소 루트에서 다음 하나를
실행한다.

```bash
make check
```

`make check`는 `scripts/check`의 전체 모드를 호출한다. 실제 검사 구현의 정본은
스크립트다. 로컬 SQLite에서 skip된 PostgreSQL 전용 테스트는 최종 보고에 적고 CI
결과를 확인한다.

로컬에서 PostgreSQL 전용 동작까지 확인하려면 운영 DB가 아닌 전용 테스트 DB를
`HARNESS_DATABASE_URL`로 명시한다.

```bash
HARNESS_DATABASE_URL=postgresql://user:password@127.0.0.1:5432/friendly_rating_test \
  make check
```

검증 스크립트는 일반 `DATABASE_URL`을 의도적으로 이어받지 않는다. GitHub Actions도
PostgreSQL service URL을 `HARNESS_DATABASE_URL`로 명시한다.

## 실패와 skip 처리

- 실패를 해결하지 않고 테스트 삭제, assertion 완화, broad exception catch, warning
  suppression을 추가하지 않는다.
- 의도적 계약 변경이면 새 요구사항을 설명하고 production code, test, 문서를 함께
  바꾼다.
- skip에는 실행 불가능한 이유가 구체적으로 보여야 한다. 단순히 flaky하거나 느리다는
  이유로 skip하지 않는다.
- Django test DB와 달리 cache는 자동으로 별도 저장소가 되거나 초기화된다고 가정하지
  않는다. cache를 사용하는 테스트는 backend를 override하고 상태를 정리한다.
- 환경 문제로 전체 검증을 못 했다면 성공처럼 포장하지 말고 실행 명령, 오류, 영향을
  받는 검증 범위를 보고한다.
