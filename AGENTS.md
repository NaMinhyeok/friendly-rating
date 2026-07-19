# 에이전트 작업 가이드

이 문서는 이 저장소에서 작업하는 코딩 에이전트의 기본 계약이다. 목표는 작은
변경을 많이 만드는 것이 아니라, 기존 데이터와 운영 계약을 보존하면서 검증 가능한
변경을 만드는 것이다.

## 먼저 읽을 것

- 모든 작업: `README.md`, 현재 `git status`, 변경 대상과 인접 테스트
- Python/Django 코드: `docs/agent/code-conventions.md`
- 테스트 작성 또는 선택: `docs/agent/testing.md`
- 배포·설정 변경: `config/settings.py`, `railway.toml`,
  `apps/ratings/tests/test_operational_contracts.py`
- 스키마·데이터 변경: 전체 `apps/ratings/migrations/`, 모델의 DB 제약,
  프로비저닝 코드와 관련 테스트

추측으로 규칙을 만들지 말고 코드, 테스트, 설정을 함께 읽어 현재 계약을 확인한다.
문서와 실행 결과가 다르면 실행 가능한 코드와 테스트를 우선 확인하고 드리프트를
함께 고친다.

## 저장소 지도

- `config/`: Django 설정, URL, WSGI/ASGI 엔트리
- `apps/ratings/models.py`: 영속 모델과 DB 불변식
- `apps/ratings/score_rules.py`: ORM과 외부 I/O가 없는 점수 도메인 규칙
- `apps/ratings/services/`: 트랜잭션을 포함한 애플리케이션 작업
- `apps/ratings/views/`: HTTP 변환, 인증, 응답 조립
- `apps/ratings/participant_provisioning/`: 두 참가자 구성을 검사·초기화·복구
- `apps/ratings/templates/ratings/`, `apps/ratings/static/ratings/`: 앱 소유 UI 자산
- `apps/ratings/tests/`: pytest/pytest-django 테스트
- `templates/`: 프로젝트 공용 또는 루트 경로용 템플릿
- `Makefile`: 사람이 쓰는 짧은 개발 명령
- `scripts/check`: 로컬과 CI가 공유하는 전체 품질 게이트

## 작업 계약

1. 수정 전에 요청을 관찰 가능한 동작과 보존해야 할 불변식으로 바꾼다.
2. URL → form/view → service/rule → model → template/static 흐름 중 실제 변경 경계를
   추적한다. 관련 없는 계층은 건드리지 않는다.
3. 기존 사용자 변경을 보존하고, 요청 밖의 리팩터링·의존성 추가·호환성 변경을
   섞지 않는다.
4. 버그 수정은 가능하면 먼저 실패를 재현하는 회귀 테스트를 추가한다. 기능 변경은
   가장 낮은 안정된 공개 경계에서 새 동작을 검증한다.
5. 구현 중에는 관련 테스트를 좁게 실행한다. 코드·설정·스크립트를 바꾼 작업은 완료
   전에 `make check`를 실행한다. 문서만 바꾼 작업은 링크·명령·diff 등 영향을
   받는 정적 계약을 검증한다.
6. 마지막에 전체 diff를 읽어 보안, 데이터 손실, 누락된 마이그레이션, 불필요한
   변경과 생성 파일을 점검한다.
7. 결과에는 변경 요약과 실제 실행한 검증, 남은 실패·skip·검증 공백을 정확히
   보고한다. 실행하지 않은 검사를 통과했다고 말하지 않는다.

복잡도에 비례해 계획을 세우되, 단순하고 국소적인 변경에 의식적인 장문 계획을
강제하지 않는다. 막히지 않은 질문은 코드와 테스트에서 먼저 답을 찾는다.

## 표준 명령

```bash
# 최초 설정 또는 잠금 파일 변경 후
make setup

# 반복 중 관련 테스트
make test PYTEST_ARGS="apps/ratings/tests/test_score_rules.py -q"

# 반복 중 정적 타입 검사
make typecheck

# 코드·설정·스크립트 변경 완료 전 전체 검사
make check
```

패키지는 `uv`와 `pyproject.toml`로만 관리한다. `pip install`로 환경만 바꾸거나
`uv.lock`을 손으로 편집하지 않는다. 새 운영 의존성은 표준 라이브러리나 현재
의존성으로 해결할 수 없는지 먼저 확인한다.

`Makefile`은 편의 진입점이고 전체 게이트의 구현 정본은 `scripts/check`다.

## 정적 타입 계약

- Pyrefly는 migration을 제외한 프로젝트 전체를 `default` preset으로 검사한다.
  `score_rules.py`와 `services/`는 별도 `strict` 검사 대상이며, 향후 API 패키지도
  생성되는 시점부터 `strict` 범위에 넣는다.
- 오류 수를 맞추기 위한 baseline, 파일 단위 대량 suppression, 규칙의 전역 비활성화는
  추가하지 않는다. 진단은 타입을 명확히 하거나 실제 결함을 고쳐 해결한다.
- 런타임에서 의도적으로 잘못된 타입을 전달하는 음성 테스트에만 정확한 진단 코드를
  지정한 한 줄 suppression을 허용한다. 이유를 인접한 테스트에서 드러내고 production
  코드나 정상 경로로 suppression을 퍼뜨리지 않는다.
- `make typecheck`는 반복 작업용 진입점이다. 검사 대상과 실행 순서의 정본은 다른
  품질 검사와 마찬가지로 `scripts/check`다.

## 반드시 보존할 도메인·운영 계약

- Python import 경로는 `apps.ratings`지만 Django app label은 `ratings`다. 기존
  migration label, content type, admin URL과 명시적 테이블명 `participant`,
  `relationship_score`, `score_change`, `push_device`를 우연히 바꾸지 않는다.
- 참가자는 정확히 두 slot으로 구성된다. 관계 점수는 방향성이 있고 0~100이며,
  한 참가자는 자신의 outgoing score만 바꾼다.
- 점수 변경은 잠금이 포함된 원자적 트랜잭션으로 현재 점수와 이력을 함께 기록한다.
  `ScoreChange` 이력은 수정·삭제 불가다.
- 푸시 알림은 DB commit 뒤에 전송하고, 외부 전송 실패가 점수 변경을 되돌리면 안
  된다. 알림 본문에 점수·이유·참가자 같은 사적 정보를 추가하지 않는다.
- `provision_participants`의 기본 동작은 빈 DB 초기화 또는 정확히 일치하는 상태의
  no-op이다. drift를 묵시적으로 덮어쓰지 않는다. `--check`는 무쓰기,
  `--reconcile`은 명시적 복구이며 PK, 점수, 이력, 기기 연결을 보존한다.
- 자동 배포 전 단계에서는 `migrate`만 실행한다. 참가자 프로비저닝을 build,
  startup 또는 매 배포 명령에 추가하지 않는다.
- 로컬 기본 DB는 SQLite지만 CI와 운영의 기준 DB는 PostgreSQL이다. 행 잠금,
  동시성, PostgreSQL 동작은 로컬 SQLite 결과만으로 결론 내리지 않는다.
- `/health/`는 Railway readiness 계약이며 DB 장애 시 503을 반환하고 HTTPS
  redirect에서 제외된다.
- PIN, Django secret, Firebase service account, 실제 `.env`를 열거나 출력하지 않는다.
  설정 구조는 `.env.example`에서 확인하고 비밀을 코드·fixture·로그·문서·Git에 넣지
  않는다. 사용자 입력과 인증 실패 로그에도 비밀을 노출하지 않는다.
- 사용자에게 보이는 문구는 자연스러운 한국어를 유지한다. 템플릿 autoescape,
  CSRF 보호, 안전한 redirect와 HTTP method 제한을 우회하지 않는다.

변경 경계별 최소 테스트와 테스트 더블 기준은 `docs/agent/testing.md`를 따른다. 테스트를
통과시키기 위해 실제 계약을 약화하거나 경고를 숨기지 않는다. 계약이 의도적으로
바뀌었다면 코드, 테스트, 문서를 같은 변경에서 갱신한다.

## 변경 권한과 완료 조건

- 요청받지 않은 commit, push, PR, deploy, 운영 데이터 변경은 하지 않는다.
- `migrate`, `flush`, `provision_participants --reconcile` 같은 쓰기 명령은 대상 DB가
  확인된 로컬·테스트 환경에서만 실행한다. 운영/공유 DB와 `migrate --fake`는 명시적인
  복구 요청과 검토된 계획 없이는 사용하지 않는다.
- 기존 migration을 공유된 이력에서 수정하거나 삭제하지 않는다. 새 migration을
  추가한다.
- `.env`, `db.sqlite3`, `staticfiles/`, 캐시 같은 로컬 생성물을 커밋하지 않는다.
- 완료는 요청한 동작, 회귀 방지 테스트, 관련 문서, 작업 범위에 맞는 검증 결과와
  diff 검토가 서로 일치하는 상태다.
