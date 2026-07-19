# Django 코드 컨벤션

이 문서는 현재 코드의 경계를 유지하면서 새 코드를 어디에 두고 어떤 안전장치를
사용할지 정한다. 추상적인 Django 모범 사례보다 이 애플리케이션의 기존 계약이
우선한다.

## 언어와 도구

- Python 3.13, Django 5.2 문법과 API를 사용한다.
- Ruff의 lint/format 결과를 기준으로 한다. 수동 스타일 규칙을 추가해 formatter와
  경쟁하지 않는다.
- import는 `apps.ratings` 패키지 구조를 따른다. 앱 내부에서는 현재처럼 상대 import를
  사용할 수 있지만, patch path와 설정의 dotted path는 실제 runtime lookup 경로를
  쓴다.
- 함수와 변수는 역할을 드러내는 이름을 쓰고, boolean은 `is_`, `has_`, `can_`처럼
  상태가 분명한 이름을 선호한다.
- 공개 service와 여러 같은 타입 인자를 받는 함수는 keyword-only 인자를 선호한다.
- 현재 문제에 필요하지 않은 base class, generic repository, manager, signal을 미리
  만들지 않는다.
- 교체 가능한 collaborator의 실제 공개 계약이 있을 때만 작은 `Protocol`을 둔다.
  테스트 더블만 편하게 만들기 위한 production 추상화는 추가하지 않으며, 이미 계약이
  있다면 stub과 fake도 같은 타입을 만족하게 한다.

## 코드 배치

### 순수 도메인 규칙

- 점수 계산·정규화처럼 DB와 request가 필요 없는 규칙은 `score_rules.py` 또는 같은
  성격의 독립 모듈에 둔다.
- 순수 규칙은 ORM model instance, settings, 전역 mutable state, 네트워크를 참조하지
  않는다.
- 여러 계층에서 같은 규칙이 필요하면 view/form에 복제하지 않고 순수 함수에서
  공유한다.

### Model과 DB

- model은 영속 관계, 명확한 field semantics, DB가 보장해야 하는 constraint를
  소유한다.
- 중요한 불변식은 애플리케이션 validation만 믿지 말고 이름 있는 DB constraint로
  보호한다.
- 새 queryset/manager 메서드는 여러 호출자가 공유하는 실제 query 개념일 때만
  추가한다.
- related object를 반복 접근하는 list/detail query는 `select_related` 또는
  `prefetch_related`를 명시해 N+1을 피한다.
- 부분 update에는 의도한 field가 분명하도록 `update_fields`를 사용한다. 단,
  `auto_now` field처럼 함께 갱신해야 하는 field를 빠뜨리지 않는다.
- 공유된 migration은 수정하지 않는다. constraint와 index에는 안정적인 명시적
  이름을 주고, `RunPython`에서는 현재 model import가 아니라 historical app registry를
  사용한다.

### Service와 트랜잭션

- 여러 model 쓰기, 잠금, 이력 생성, 외부 부작용을 조정하는 재사용 가능하거나 복잡한
  workflow는 `services/`에 둔다. 한 HTTP endpoint에만 속한 제한된 기기 등록 흐름은
  현재 `views/push.py`처럼 view 가까이에 둘 수 있다.
- 동시 갱신 가능한 점수/기기 상태는 `transaction.atomic`과 필요한 row lock을
  사용하고, 읽기-계산-쓰기가 한 transaction 안에 있는지 확인한다.
- 외부 I/O는 열린 DB transaction 안에서 수행하지 않는다. commit 성공 뒤 필요한
  작업은 `transaction.on_commit`을 사용한다.
- 보조 기능의 실패가 핵심 쓰기를 취소하면 안 되는 경우 경계에서 예외를 기록하고
  격리한다. 반대로 핵심 데이터 오류를 broad catch로 숨기지 않는다.
- 핵심 workflow는 signal보다 명시적 함수 호출을 선호한다. 실행 순서와 rollback
  경계를 코드에서 읽을 수 있어야 한다.

### Form과 View

- form은 HTTP 입력의 형식 검증과 cleaned value 생성을 담당한다. 복잡하거나 여러
  caller가 공유하는 DB workflow는 service에 위임한다.
- view는 인증/권한, method, 입력 변환, service 또는 위에서 허용한 endpoint-local
  workflow 호출, response 조립에 집중한다.
- mutation에는 `POST` 등 정확한 method decorator를 쓰고 Django CSRF middleware를
  우회하지 않는다.
- 사용자 입력을 검증하는 HTML form에는 Django Form을 사용한다. CSRF token과 submit
  button만 있는 logout 같은 action form에는 빈 Form class를 만들 필요가 없다. 작은
  JSON endpoint는 content type, body 크기, JSON shape과 field 형식을 명시적으로
  제한한다.
- redirect target은 host/scheme을 검증한다. 로그인만으로 참가자 권한을 가정하지
  말고 `get_current_participant` 경계를 유지한다.
- 예측 가능한 사용자 입력 오류는 endpoint의 기존 계약에 맞는 상태 코드와 한국어
  피드백으로 반환한다. JSON/mutation endpoint의 잘못된 요청은 적절한 4xx를 사용하고,
  programming error를 사용자 오류로 바꿔 숨기지 않는다.
- URL name과 공개 path는 호환성 계약이다. 이름 변경은 모든 caller와 회귀 테스트를
  함께 다루는 명시적 변경이어야 한다.

### Template, 정적 파일과 브라우저 코드

- ratings 전용 template은 `apps/ratings/templates/ratings/`, asset은
  `apps/ratings/static/ratings/`에 둔다. 프로젝트 공용 또는 루트 scope 파일만
  `templates/`에 둔다.
- Django autoescape를 기본으로 유지한다. JSON을 script/template에 넣을 때는 문맥에
  맞는 escaping을 사용하고 사용자 문자열을 `safe` 처리하지 않는다.
- 현재 frontend는 build step 없는 HTML/CSS/vanilla JavaScript다. 작은 변경을 위해
  별도 framework나 bundler를 도입하지 않는다.
- form label, focus, keyboard 사용, 상태 메시지와 reduced-motion 등 기존 접근성을
  보존한다.
- service worker는 root scope, cache 정책, offline/PWA 계약을 함께 고려한다.
- 사용자 문구는 간결하고 자연스러운 한국어로 작성한다.

## 보안과 개인정보

- 실제 PIN, secret key, database URL, Firebase service account를 저장하거나
  출력하지 않는다. 설정 예시는 명백한 placeholder만 사용한다.
- 인증 실패, push 오류, provisioning 진단 로그에 PIN 또는 private payload를 넣지
  않는다.
- push notification은 잠금 화면에 보일 수 있다. 참가자, 점수, 변경 이유를 본문에
  노출하지 않는다.
- 신뢰 proxy에서 제공한 IP만 사용하고 임의의 forwarding header를 신뢰하지 않는다.
- 입력 길이는 DB field뿐 아니라 파싱 전 request 경계에서도 제한한다.
- `SECRET_KEY`, `DATABASE_URL` 등 운영 필수 설정은 debug가 꺼진 상태에서 fail-fast
  해야 한다. 새 설정은 `.env.example`과 운영 검사도 함께 갱신한다.

## 호환성과 운영

- app label `ratings`, 명시적 DB table 이름, 참가자 slot, score history의 불변성은
  기존 데이터와 연결된 계약이다.
- schema 변경은 무중단 배포 순서와 이전 코드/새 schema의 짧은 공존 가능성을
  검토한다. 파괴적 변경은 expand → migrate/backfill → contract 단계를 선호한다.
- `collectstatic`은 build 단계, `migrate`는 단일 pre-deploy 단계, participant
  provisioning은 명시적인 one-off 작업으로 유지한다.
- `/health/`의 응답과 HTTPS 예외는 Railway readiness 설정과 함께 변경한다.
- 환경별 분기는 암묵적인 hostname 추측보다 검증된 settings 값으로 표현한다.
- module import나 `AppConfig.ready()`에서 DB query를 실행하지 않는다. 앱 초기화는
  migration 전, test DB 생성 전, management command에서도 일어날 수 있다.

## 의존성과 문서

- 운영 의존성 추가 전 현재 Django/표준 라이브러리로 해결 가능한지 확인한다.
- 의존성을 바꾸면 `pyproject.toml`과 `uv.lock`을 같은 변경에서 갱신하고 전체 검사를
  실행한다.
- 공개 동작, 환경 변수, 실행·배포 절차가 바뀌면 `README.md`와 `.env.example`을 함께
  갱신한다.
- 코드만으로 드러나지 않는 반복 가능한 workflow가 생기면 `AGENTS.md`를 비대하게
  만들지 말고 `docs/agent/`에 설명한 뒤 링크한다.

## 리뷰 체크리스트

- 동작이 올바른 계층에 배치됐는가?
- DB constraint, transaction, lock, rollback 경계가 충분한가?
- 인증, 권한, CSRF, redirect, 비밀/개인정보 노출이 안전한가?
- query와 외부 I/O가 요청 경로에서 불필요하게 반복되지 않는가?
- migration과 운영 순서가 기존 데이터를 보존하는가?
- 테스트가 실제 결과를 검증하고 구현 세부를 고정하지 않는가?
- 변경된 계약이 테스트와 문서에 반영됐는가?
