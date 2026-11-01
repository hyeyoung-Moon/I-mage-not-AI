# 코어 알고리즘 독립 재평가 — 2026-10-31

**종합 69/100**. 이는 명시된 제약을 feature mask와 메타데이터에 적용해 결정론적인 검토 의견을 반환하는 도구에 대한 평가자의 가중 점수다. 정답률이나 이미지 미감 점수가 아니다. 코드의 정해진 범위 밖인 미적 취향, AI 생성 여부, 의미적 분할 자체를 구현하지 않았다는 이유로 감점하지 않았다.

평가 대상은 `E:/I-mage-not-AI`의 HEAD `d6b7b1c`와 당시 미커밋 변경을 포함한 작업 트리다. 평가 중 제품 코드는 변경하지 않았다. 실행한 코드의 SHA-256은 [evidence.json](./evidence.json)의 `snapshot`에 기록했다.

## 가중 점수

이전 평가와 같은 8개 범주 및 가중치를 사용했다.

| 관점 | 점수 | 가중치 | 근거 |
|---|---:|---:|---|
| 기본 측정 정확성 | 82 | 15% | 기존 정답값 검증은 강점이나 투명 픽셀에 대한 `measure`와 `review`의 표본이 다르다. |
| 정책 선택과 최종 판정 | 50 | 20% | 빈 mask가 전체 PASS/COMPLETE이며, 같은 쌍에 두 overlap 기준이 있으면 개별 finding의 기준과 상태가 뒤섞인다. |
| 게임 시계열 검사의 완결성 | 68 | 15% | anchor 증거 누락과 짧은 트랙은 inconclusive로 표시하지만 극단적 유한 시간에서 내부 오류가 재현된다. |
| 성능과 규모 확장성 | 78 | 10% | 1024×1024 단일 feature fixture의 3회 중앙값 4.06초. peak memory와 대규모 실사용은 미측정이다. |
| 재현성과 설명 가능성 | 79 | 10% | mask SHA, 측정값과 기준, 출처를 출력한다. brightness 산식 변경 전후가 같은 알고리즘 버전을 사용한다. |
| 경계조건과 수치 안정성 | 58 | 10% | 유한한 두 위치 step의 합이 넘치면 Infinity가 출력될 수 있으며 timestamp midpoint가 겹치면 내부 오류가 난다. |
| 구조와 확장성 | 75 | 10% | feature 검토, registry, CLI 측정의 역할이 구분되어 있다. 규칙의 계획·변환·결과 연결은 아직 오류에 취약하다. |
| 테스트와 벤치마크 타당성 | 72 | 10% | 직전 전체 테스트 70개와 이번 선택 회귀 9개는 통과했으나 이번 반례는 포함되지 않았다. 저장된 blind pilot은 원본 4종이다. |

가중합은 68.7점이며 반올림해 69점이다. 이전 72점보다 낮은 이유는 이번 수정이 코드를 악화시켰다는 실험 결과가 아니라, 이전 평가에서 놓쳤던 결함을 새 반례로 확인해 그 근거를 반영했기 때문이다.

## 실제 CLI 재현 결과

모든 입력, CLI stdout/stderr와 재현 스크립트는 이 디렉터리에 있다. 입력은 인위적으로 만든 작은 사례이며 빈도나 실제 배포 피해를 통계적으로 추정하지 않는다.

### P1. 빈 feature mask가 전체 PASS로 처리된다

4×4 불투명 이미지에 모든 픽셀이 0인 `body` mask를 주고 팔레트 최대 1, 밝기 최대 1, 대칭 IoU 최소 0.9, 연결 성분 최대 1을 선언했다. 결과는 팔레트 0, 밝기 0, 성분 0, 대칭 1.0으로 모두 PASS이며, 최종 결과도 **PASS/COMPLETE, exit 0**이다. 특히 눈에 보이는 픽셀 없는 색상·밝기와 빈 집합의 대칭을 feature가 확인된 결과처럼 전달한다. 검사가 필요로 하는 픽셀이나 feature evidence가 비어 있는 경우의 계약이 필요하다.

재현: [입력 manifest](./empty-mask/manifest.json), [전체 결과](./empty-mask/review.stdout.txt), [구현](../../imreview/core.py#L695).

### P1. 두 overlap 제약의 개별 finding이 서로 뒤바뀐다

같은 `a`, `b` mask가 완전히 겹쳐 측정값이 1.0이다. manifest에서는 최대 1, 프로젝트 정책에서는 최대 0을 요구했다. 프로젝트 finding이 **측정 1.0, 기준 0, PASS**이며, 다른 행은 기준 0, FAIL이다. 최종 결과는 다른 행 때문에 **FAIL/COMPLETE, exit 1**이어서 전체가 PASS인 사례는 아니다. 그러나 행 단위 의견과 기준은 잘못 전달된다. 두 기준을 각각 유지하거나 충돌/우선순위를 명확히 처리해야 한다.

재현: [manifest](./overlap-strict-project/manifest.json), [정책](./overlap-strict-project/rules.yaml), [전체 결과](./overlap-strict-project/review.stdout.txt), [결과 연결 구현](../../imreview/core.py#L804). 기준을 반대로 한 경우도 [별도로 기록](./overlap-two-constraints/review.stdout.txt)했다.

### P2. 투명 픽셀에서 `measure`와 `review`의 밝기 표본이 다르다

128 회색 불투명 픽셀과 255 흰색 완전 투명 픽셀을 mask에 넣고 폭 64를 사용했다. 두 경로가 linear-sRGB 수식을 사용하지만 `review`는 보이는 픽셀만 세어 **1**, `measure`는 투명 RGB도 포함해 **2**를 반환했다. 둘 중 어떤 표본을 요구할지는 계약에서 정할 수 있으나, 지금은 같은 luma 값으로 교환해 쓰면 결과가 다르다.

재현: [review](./transparent-pixels/review.stdout.txt), [measure](./transparent-pixels/measure.stdout.txt), [측정 구현](../../imreview/measurements.py#L147).

### P2. 극단적 유한 게임 trace에서 Infinity와 내부 오류가 남는다

위치 0 → `1e308` → 0은 각 step이 유한하지만 step 합이 넘쳐 평균이 `Infinity`다. CLI는 **PASS/COMPLETE, exit 0**과 표준 JSON에서 허용하지 않는 토큰을 출력했다. 또 유효하고 서로 다른 시간 `10000000000000002`, `10000000000000004`, `10000000000000006`은 부동소수점 midpoint가 같아져 **INTERNAL_ERROR, exit 4, float division by zero**가 났다. 이 수치는 극단적인 경계값이며 일반 게임 좌표에서 같은 문제가 재현됐다는 뜻은 아니다.

재현: [Infinity 출력](./mean-step-overflow/game.stdout.txt), [시간 입력](./timestamp-midpoint/trace.json), [내부 오류](./timestamp-midpoint/game.stderr.txt), [구현](../../imreview/game.py#L447).

### P2. `:`가 들어간 유효 feature ID의 overlap 계획이 누락으로 처리된다

`part:left`와 `right`를 가진 두 정상 mask가 완전히 겹치고 한도가 1인 경우, 측정은 1이지만 계획이 합성 ID를 `:`로 잘못 분리해 **INCONCLUSIVE/INCOMPLETE, exit 2**가 된다. 이 사례는 오승인이 아니라 불필요한 미완료다.

재현: [manifest](./colon-feature-id/manifest.json), [결과](./colon-feature-id/review.stdout.txt), [계획 구현](../../imreview/core.py#L308).

### 재현성 한계

이번 작업 트리에서 `masked_luma_levels`의 수식은 HEAD의 encoded RGB 가중합에서 linear-sRGB luminance로 바뀌었으나 [registry](../../imreview/rules/algorithms.yaml#L17)는 계속 `1.0`이다. 출력 formula도 bin 폭만 설명한다. 같은 algorithm version만으로 이전 산식과 현재 산식을 구별하기 어렵다. mask 파일 SHA 기록과 선언 SHA의 불일치 거절은 별도로 확인했다.

## 통과한 확인과 평가 범위

- 직전 수정 후 전체 pytest **70 passed**. 이번에는 수정된 overlap, pose, 작은 시간 간격, 누락 capability/version, 빈 게임 트랙과 골든 값 관련 **9개 회귀를 재실행해 9 passed**다.
- 이번 mypy는 `imreview tests benchmarks i_mage_not_ai`의 **36개 source files 통과**, Ruff는 같은 범위에서 통과했다.
- anchor 정책에 anchor가 없을 때 **INCONCLUSIVE/INCOMPLETE, exit 2**였다. 잘못된 mask SHA는 **INVALID_INPUT, exit 3**이었다. 각각 [anchor 기록](./missing-anchor/game.stdout.txt), [hash 기록](./mask-hash/validate.stderr.txt)에 있다.
- 이전 평가의 ΔE00 참조 34쌍 대조는 모두 허용 오차 이내였다. 이는 기존 [측정 기록](../core-algorithm-reassessment-2026-10-31/evidence.json)의 결과이며 이번에 외부 참조를 다시 내려받지는 않았다.
- 이번 성능은 `benchmarks/benchmark_review.py 1024 3`을 단독 실행한 `review()` 함수 시간으로 4.058 / 4.098 / 3.598초다. 입력 생성, CLI 시작, manifest 검증과 peak memory는 포함하지 않는다.
- 저장된 v2 blind pilot은 원본 4종의 24변형에 대한 기존 기록이다. 해당 고정 pilot에서 CLI 진단 규칙은 독립 gold와 24/24 일치했지만, 새로운 다양한 원본이나 의미 feature mask에 대한 일반 성능을 증명하지 않는다.

## 수정 우선순위

1. 비어 있거나 보이는 픽셀이 없는 feature에 필요한 검사를 INCONCLUSIVE/INCOMPLETE 또는 명시적 입력 오류로 처리한다.
2. 동일 쌍의 각 overlap 제약을 규칙 ID와 기준에 1:1로 연결하고, 충돌과 우선순위를 명시한다.
3. 투명 픽셀 표본 계약을 정하고 `measure`와 `review`에 반영하며 결과에 산식을 기록한다.
4. 시간 midpoint와 평균 등 모든 파생 수치의 finite 여부를 검사하고 표준 JSON만 내보낸다.
5. feature 쌍을 구분자가 포함될 수 있는 문자열 하나로 분해하지 않고 구조화해 계획과 결과를 연결한다. 산식 변경에는 추적 가능한 버전도 부여한다.

## 후속 결함 수정 및 최종 검사 — 2026-10-31

독립 재평가에서 재현한 오류를 수정했다. 빈 feature mask는 해당 마스크 측정들을 INCONCLUSIVE로 돌려 COMPLETE/PASS가 되지 않게 했다. `measure`의 mask 표본도 보이는 픽셀로 제한해 `review`와 밝기 측정 모집단을 맞췄다. 프로젝트 overlap은 각 finding을 별도 기준과 rule ID로 평가하며, feature 쌍은 구조화된 ID 목록으로 계획해 콜론이 든 feature ID도 처리한다. linear-sRGB 밝기 산식 변경은 registry 알고리즘 버전을 2.0으로 올렸다. 게임 trace step 평균을 overflow에 안전하게 계산하고, 겹치는 floating-point midpoint에서는 가속도를 inconclusive로 처리한다.

최종 검사: `.venv/Scripts/python.exe -m pytest -q` **75 passed**; mypy **36 source files 통과**; Ruff **통과**; `git diff --check` **통과**.

최종 전체 검사 재실행: 투명 픽셀 회귀 테스트를 추가한 뒤 pytest **76 passed**; mypy **36 source files 통과**; Ruff **통과**.
