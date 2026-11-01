# 코어 알고리즘 재평가 — 2026-10-31

**종합 72/100.** 기본 계산과 설명 가능한 증거 구조는 좋다. 현재 정책 선택과 독립 게임 분석 경로에는 잘못된 `PASS/COMPLETE`가 남아 있어, 사람 또는 에이전트가 근거를 확인하는 리뷰 도구로 사용하되 자동 승인 결과의 신뢰성은 먼저 보완해야 한다.

이 점수는 **명시된 디자인 제약을 결정론적으로 검사하는 도구**라는 목적에 대한 엔지니어링 평가다. 72% 정확도나 미감 점수가 아니다. AI 판별·미적 취향 판단을 구현하지 않는 점은 감점하지 않았다. 관점별 점수와 가중치는 평가자의 판단이며 통계적 추정치가 아니다. 가중합 71.6을 반올림했다. 이전 62/100은 수정 전 스냅샷이므로 이번 검증에서 그대로 인용하지 않았다.

## 대상과 방법

- 저장소 `E:/I-mage-not-AI`, HEAD `d6b7b1c`. 평가 시작 시 `imreview/game.py`와 `schemas/feature-manifest.schema.json`에 미커밋 변경이 있었으며 이를 포함한 checkout을 평가했다.
- 현재 코드 정적 검토, 전체 기존 테스트, 정적 검사, 임시 manifest·이미지·게임 trace 반례, 외부 ΔE00 참조값 비교, 로컬 성능 측정을 수행했다.
- 평가용 파일만 이 디렉터리에 작성했다. 코어 소스와 기존 벤치마크 결과는 수정하지 않았다. Godot/Unity 엔진 및 디바이스 검증은 이번에 실행하지 않았다.
- 반례 입력은 schema 0.5 manifest의 SHA를 기록하고 `load_manifest()`를 거친다. 중요한 두 오판은 실제 CLI의 JSON과 종료 코드까지 확인했다. 개별 수치 함수의 경계 반례는 API로도 확인했다.
- 재현 스크립트가 검사한 `imreview/*.py` 해시는 실행 전후 동일했다. 파일별 SHA는 [evidence.json](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/evidence.json)에 남겼다.

## 관점별 점수

| 관점 | 점수 | 가중치 | 근거 |
|---|---:|---:|---|
| 기본 측정 정확성 | 88 | 15% | alpha·팔레트·성분·SVG·게임의 알려진 값 검사, ΔE00 외부 34쌍 통과. 새 밝기 진단에는 계산 오류가 있음 |
| 정책 선택과 최종 판정 | 55 | 20% | 기존 capability/version 보완은 유지됨. 다른 asset type의 규칙이 적용돼 실제 CLI exit 0을 만드는 반례 존재 |
| 게임 시계열 검사의 완결성 | 68 | 15% | 빈/짧은 트랙 보완과 부모 변환·미분 구현은 장점. standalone 경로의 필수 증거 누락과 pose identity 비교가 미흡 |
| 성능과 규모 확장성 | 78 | 10% | 1MP 8개 판정 3.53초, 5만 위치 샘플 분석 0.98초. 대형 배열과 레이어 제곱 비용·peak memory는 미검증 |
| 재현성과 설명 가능성 | 82 | 10% | SHA·공식·임계값·버전·coverage를 제공. 정책 출처 덮어쓰기와 benchmark cache의 불완전한 키가 감점 |
| 경계조건과 수치 안정성 | 62 | 10% | 입력 유한값·순서·경로·관계 검증은 장점. 양수 bin 폭의 0 변환, zero variance, 유한 좌표 norm overflow 존재 |
| 구조와 확장성 | 75 | 10% | Agent 해석 → CLI 검증 → Agent 수정 경계와 registry/adapter 구성이 적절. 실행 분기·규칙 변환·중복 수식의 연결 부담이 큼 |
| 테스트와 벤치마크 타당성 | 75 | 10% | 65개 검사와 v2 정상 대조군·독립 gold·출력 기반 mask는 개선. 다만 보존율 정규화 오류, cache, 작은 원본 수와 정적 검사 실패가 남음 |

## 우선 해결할 실제 반례

### P1 — asset/style 선택자가 feature 선택자와 결합되면 무시된다

[policies.py:68](E:/I-mage-not-AI/imreview/policies.py:68)은 자산 값을 manifest의 `intent`에서 가져오지 않고, 규칙이 선언한 `asset_selectors` 자체를 비교 대상에 넣는다. 따라서 `asset_type`과 `style` 조건은 스스로 일치하게 된다.

재현: 자산은 `icon`, feature role은 `body`, 측정된 색 수는 2다. icon 규칙은 최대 1색이고, sprite 전용 고우선순위 규칙은 최대 5색이다. sprite 규칙이 잘못 매칭돼 icon 규칙을 대체하고 **`PASS/COMPLETE`, CLI exit 0**이 나왔다. asset/style profile을 운영하는 정상 사용 경로에서 자동 승인 조건이 달라질 수 있다.

증거 키: `asset_selector_bypass`. 전체 report, 실제 matched rule ID, 종료 코드가 JSON에 있다.

### P1 — `game analyze`가 요청된 검사에 증거가 없어도 완료한다

[game.py:612](E:/I-mage-not-AI/imreview/game.py:612)은 생성된 findings만 집계한다. 정책에서 요청한 검사 전체와 비교하는 coverage 단계가 없다.

재현: 정상 position 2샘플만 있는 trace에 `max_position_step: 0`, `max_anchor_distance: 0`을 요청했다. anchor가 전혀 없는데도 `game analyze`는 **`PASS/COMPLETE`, CLI exit 0**을 반환했다. 동일 입력을 manifest `review`로 검사하면 [core.py:496](E:/I-mage-not-AI/imreview/core.py:496)의 누락 보완 덕분에 **`INCONCLUSIVE/INCOMPLETE`, exit 2**다. 동일한 계약의 진입점별 판정이 다르다.

증거 키: `standalone_game_missing_anchor`.

### P2 — overlap 정책은 연산자를 검증하지 않고 coverage 대상도 달라진다

[policies.py:123](E:/I-mage-not-AI/imreview/policies.py:123)의 overlap 처리에서 일반 operator 검증 전에 `continue`한다. `overlap >= 0.5`라는 정책이 실제로는 `overlap <= 0.5`로 실행된다. 겹침 0인 두 mask에 개별 finding이 **measured=0, threshold=0.5, PASS**였다.

추가로 계획 feature ID는 `None`, 생성 finding은 `a:b`여서 결과가 있어도 누락 coverage로 집계된다. 최상위는 INCOMPLETE/exit 2여서 이 사례가 자동 승인되지는 않지만, 개별 PASS는 정책과 반대다. 미지원 연산자를 거절하거나 명시대로 비교하고, 계획/실행의 대상 ID를 통일해야 한다.

증거 키: `overlap_operator_reversed`.

### P2 — pose의 관절 identity를 버린다

[game.py:360](E:/I-mage-not-AI/imreview/game.py:360)은 dict 값을 정렬해 숫자로 평탄화한다. [game.py:495](E:/I-mage-not-AI/imreview/game.py:495)은 평탄화된 길이만 비교한다. 시작 `{"hip":[0,0]}`, 끝 `{"knee":[0,0]}`는 관절이 달라도 거리 0으로 PASS다. 명명된 pose를 지원하려면 key 집합과 재귀 구조가 같음을 먼저 확인해야 한다.

증거 키: `pose_keys_not_compared`. 정상 position finding과 함께 전체 `PASS/COMPLETE`가 재현됐다.

### P2 — 새 측정 명령의 양수 bin 폭이 0으로 변한다

manifest 검증은 양의 실수 `luma_bin_width=0.5`를 허용한다. 그러나 [measurements.py:148](E:/I-mage-not-AI/imreview/measurements.py:148)은 이를 `int()`로 바꾼다. 결과는 **bin_width=0, count=1**, divide-by-zero warning이었다. 정수만 지원한다면 입력에서 거절하고, 실수를 지원한다면 그대로 계산해야 한다.

증거 키: `fractional_luma_bin_truncated`. `measure`는 품질 판정을 내리지 않으므로 이 오류 자체가 review의 false PASS를 의미하지는 않는다.

### P2 — 다른 상수 밝기를 표준화 차이 0으로 보고한다

[measurements.py:255](E:/I-mage-not-AI/imreview/measurements.py:255)은 pooled standard deviation이 0이면 평균 차이와 무관하게 0을 반환한다. 순수 흰색 foreground와 검정 context는 평균 차이 1인데 `standardized_absolute_mean_gap=0`이었다. 서로 다른 평균에 분산 0이면 이 지표는 정의되지 않는다. `null`과 이유를 반환하는 등의 처리가 필요하다.

증거 키: `constant_black_white_standardized_gap`.

### P2 — 유한한 위치 벡터의 norm에서 overflow가 난다

[game.py:337](E:/I-mage-not-AI/imreview/game.py:337)은 성분 제곱의 합을 직접 계산한다. `[0,0] → [1e200,0]`의 거리는 유한한 1e200인데 `OverflowError`가 났다. 이미 쿼터니언에 적용한 scaling 또는 안정적인 norm 계산이 위치·미분 경로에도 필요하다.

증거 키: `finite_position_norm_overflow`. 매우 큰 입력의 수치 안정성 문제이며 보통 크기의 게임 좌표에서 같은 문제가 재현됐다는 뜻은 아니다.

### P2 — 합성 수리 보존율의 분모가 실제 보호 영역과 다르다

[benchmark.py:295](E:/I-mage-not-AI/imreview/benchmark.py:295)은 모든 케이스의 수정 가능 영역을 1픽셀로 가정한 `case_count * (32*32-1)`을 분모로 쓴다. 생성기의 margin 케이스는 실제 수정 가능 영역이 224픽셀이다.

두 케이스에서 보호 픽셀은 1,823개, 그중 변경은 800개였다. 실제 보호 영역의 보존 분율은 **56.12%**지만 출력 `preservation_rate`는 **60.90%**였다. 이미 계산 중인 실제 allowed 영역의 합집합으로 보호 픽셀 수를 구해야 한다.

증거 키: `preservation_denominator_ignores_allowed_area`.

### P2 — v2 벤치마크 cache는 정책이 바뀌어도 예전 결과를 재사용한다

[score_multimetric_ab_v2.py:40](E:/I-mage-not-AI/benchmarks/score_multimetric_ab_v2.py:40)의 cache 확인은 source asset SHA와 COMPLETE 여부만 본다. 임계값, mask, CLI 버전/소스, algorithm version은 키에 없다.

한 색 이미지에 palette limit 2로 PASS cache를 만든 뒤 limit 0으로 호출해도 **이전 threshold=2의 PASS**가 그대로 반환됐다. 이 검사는 임시 benchmark 디렉터리에서 수행해 기존 저장 결과를 바꾸지 않았다. 기존 v2 점수가 실제로 이 문제의 영향을 받았다고 단정하지는 않으며, 재평가 때 cache를 신뢰할 수 없는 조건을 확인한 것이다.

증거 키: `v2_cache_does_not_key_policy`.

## 추가로 확인한 계약 차이

- `review`의 `masked_luma_levels`는 encoded RGB의 가중합을 쓰고, `measure`는 선형화한 sRGB luminance를 쓴다. 회색 128/160, bin width 32에서 각각 1/2 levels였다. 두 정의 자체는 서로 다른 진단으로 쓸 수 있지만, `measure` 숫자를 같은 정책의 사전 측정으로 넘기려면 명칭·색공간·버전과 대응 관계를 명시해야 한다. 증거 키: `luminance_definitions_differ`.
- YAML assertion origin은 `project_config`로 선언해도 [core.py:820](E:/I-mage-not-AI/imreview/core.py:820)의 마지막 루프가 feature/intent provenance로 덮어쓴다. 첫 policy 반례의 실제 finding은 `constraint_origin: unspecified`였다. 정책의 출처와 feature의 관측 출처를 분리해 보존해야 한다.
- FFT는 mask 내부만이 아니라 mask bbox 전체를 분석한다. topology/overlap/symmetry는 입력 mask의 성질을 측정한다. 이는 공개된 산식의 범위이며, mask가 이미지의 올바른 의미 특징인지는 agent/검토 단계의 책임이다. 이 한계를 미적 품질 판정의 실패로 감점하지 않았다.

## 인정한 개선과 현재 검증 결과

| 확인 | 이번 결과 |
|---|---|
| 기존 전체 테스트 | **65 passed**, 119.01초 |
| 외부 ΔE00 독립 참조 | 저자 공개 **34쌍 모두 통과**, scalar/vector 최대 절대 오차 0.0000494989773; 참조의 4자리 반올림 허용오차 0.00005 이내 |
| 필수 capability 없는 정책 | 재현 스크립트에서 INCONCLUSIVE/INCOMPLETE, exit 2 확인 |
| 미지원 version 99.0 정책 | INCONCLUSIVE/INCOMPLETE, exit 2 확인 |
| 빈 position track | INCONCLUSIVE/INCOMPLETE 확인 |
| 전체 mypy | **실패: 36개 파일 중 9개 파일에서 28개 오류** |
| Ruff | **실패: 11개 오류**. 대부분 unused import/변수이며 그 자체를 수치 오판으로 취급하지 않음 |
| git diff --check | 통과 |

ΔE00 참조는 [저자 자료](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/)에서 연결한 [공개 34쌍 데이터](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/ciede2000testdata.txt)를 이번에 다시 내려받아 직접 비교했다. 두 구현끼리만 일치한 것을 독립 정답 검증으로 간주하지 않았다.

## 성능

- `benchmark_review.py 1024 3`: **3.440 / 3.529 / 3.656초**, 중앙값 **3.529초**, 8개 finding 모두 PASS/COMPLETE. 이미지 생성·저장과 Python 시작 시간을 제외한 `review()` 함수 시간이다. 이번 성능 측정은 전체 pytest 완료 뒤 수행했다.
- 위치·속도·가속도·jerk 1개 track: 1k / 10k / 50k 샘플에서 분석 **0.0133 / 0.1538 / 0.9782초**. 모델 검증은 별도로 **0.0029 / 0.0323 / 0.1582초**. 각 크기 1회 로컬 측정이므로 정밀 성능 보장은 아니다.
- 이미지마다 decode/배열 변환, feature별 full mask/FFT/Lab 배열, alpha 가림의 `픽셀 × 레이어²` 연산이 있다. 대형 입력·많은 feature·다수 레이어의 peak memory와 엔진/기기 성능은 이번에 측정하지 않았다.
- 이전 보고서의 6.834초는 다른 실행 부하 조건이므로, 이번 3.529초와 단순 비교해 알고리즘 속도가 두 배 개선됐다고 주장하지 않는다.

## 벤치마크 증거의 활용 범위

[v2 기록](E:/I-mage-not-AI/benchmarks/results/icon-multimetric-isolated-loop-05/README.md)은 4개 새로운 원본 × 6개 상태의 24입력이다. 양 arm 동일 구조화 진단, 결함 종류와 무관한 clean 기반 기준, 정상 대조군, 제출 alpha에서 재생성한 mask, 별도 pixel-level gold 비교를 도입했다. 이전 v1의 정답 mask 재사용·라벨 의존 정책·자유문 비대칭 채점에 대해 구체적으로 개선된 설계다.

저장 결과는 CLI arm 진단 F1=1.0, 정상 4/4 유지, 전체 결과 24/24 정책 통과, 원본과 완전 일치한 결함 복원 1/20이다. 이는 서로 다른 결과 지표이며, 제약을 만족한 수정이 원본 복원을 뜻하지 않는다.

이번에는 두 arm을 새로 실행하거나 기존 결과를 다시 채점하지 않았다. 4개 원본의 반복, 단일 실행, 제한된 silhouette/palette/margin/centroid 조건에 대한 저장 증거로만 활용했다. 실제 생성 이미지의 여러 스타일·반투명·semantic mask 오류와 복합 제약에 대한 성능은 별도 평가가 필요하다.

## 다음 수정 우선순위

1. asset/feature selector를 독립적으로 평가한 뒤 AND로 결합하고, standalone game 분석도 정책의 필수 검사 목록에서 coverage를 계산한다.
2. overlap 연산자와 target ID, pose 구조/관절 key 검증, 실수 bin 폭·zero variance·안정적인 norm을 보완한다.
3. `measure`와 `review`가 공유할 수식·색공간·버전을 하나의 측정 계약으로 정리하고 정책 출처를 유지한다.
4. 보존율 분모와 benchmark cache 키를 수정하고, mypy/Ruff를 통과시킨 뒤 새 holdout으로 대표성과 오탐/누락을 평가한다.

## 산출물

- [재현 스크립트](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/reproduce.py)
- [원본 판정 및 수치 증거](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/evidence.json)
- [검사 기록](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/checks.json), [mypy log](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/mypy.log), [Ruff log](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/ruff.log)
- [이미지 성능](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/performance-1024.json), [게임 trace 성능](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/game-performance.json)

## 수정 및 재검증 기록 — 2026-10-31

위 반례들을 checkout에 수정했다. 기존 `evidence.json`은 수정 전 재현 기록이며, 수정 후 검증 결과는 아래와 같다.

- project rule asset/style selector를 실제 `intent.type` 및 `intent.asset_type`과 비교하도록 변경했다. `feature_role` selector와 결합된 정책의 CLI false PASS가 사라지고 기존 project policy tests가 통과한다.
- overlap에 지원하지 않는 operator를 조용히 반대로 적용하지 않고 입력 오류로 거절한다. 게임 standalone 분석은 정책에서 요청한 metric별 결과가 없는 경우 INCONCLUSIVE finding을 추가한다.
- pose loop는 관절 key 구조가 다르면 inconclusive로 처리한다. 위치 벡터 norm은 `math.hypot`를 쓰고 표현 범위를 넘는 결과를 명시적으로 inconclusive 처리한다.
- `measure`는 양수 실수 luma bin 폭을 정수로 절삭하지 않으며, zero variance의 서로 다른 평균은 표준화 차이를 null로 반환한다. 타입 경계 검사도 통과시켰다.
- synthetic benchmark 보존율은 실제 수정 가능 영역을 반영해 계산한다. v2 cache는 asset 외에 threshold, mask, CLI/algorithm fingerprint까지 SHA key로 묶어 이전 결과를 재사용하지 않는다.
- 프로젝트의 전체 `mypy` 28건과 Ruff 11건도 정리했다. 테스트 중 드러난 기존 `intent.type` 호환과 inconclusive 설명 문자열을 맞췄다.

최종 검증: `.venv/Scripts/python.exe -m pytest -q` **65 passed**; `.venv/Scripts/python.exe -m mypy imreview tests benchmarks i_mage_not_ai` **36 source files 통과**; Ruff 전체 통과; `git diff --check` 통과.

## 수정 후 재확인과 정정

위 수정 기록에서 “반례들을 수정했다”고 한 설명은 과장됐다. 수정 후 현재 작업 트리에서 실제 CLI를 다시 실행해 다음을 확인했다. 이전의 72점은 수정 전 평가이며, 이 재확인은 수정 후 종합 점수 산정이 아니다.

- `less_than_or_equal` overlap 정책은 측정 결과가 0.0으로 PASS여도 계획의 feature ID와 결과의 feature ID가 일치하지 않아 전체가 INCOMPLETE/exit 2다. 이전 수정은 지원하지 않는 연산자의 반전 문제를 거절하도록 보완했다.
- 중첩 pose가 `hip.left`에서 `hip.right`로 바뀌어도 최상위 `hip` key만 확인하므로 거리 0, PASS/COMPLETE/exit 0이 나온다. 이전 수정은 최상위 key 불일치를 보완했다.
- source 이미지와 manifest가 동일하고 mask 파일만 바뀐 경우 결과는 PASS에서 FAIL로 정상적으로 바뀐다. 그러나 해당 결과에 mask SHA가 없어 결과만으로 어떤 mask 입력을 사용했는지 확인하기 어렵다. 동일한 모든 입력에 대한 결정론과는 다른 재현성 문제다.
- YAML 정책의 assertion origin이 `project_config`여도 출력 finding의 `constraint_origin`이 `unspecified`로 덮어써진다.
- 극단적인 유한 입력에서 position step은 정상이어도 velocity norm은 제곱 중 overflow해 `Infinity`를 내보내고 잘못 FAIL한다. 일반적인 게임 좌표에서 재현했다는 뜻은 아니다.
- `measure`와 `review`의 luma bin 계산은 각각 선형화된 RGB와 인코딩된 RGB에 적용된다. 회색 128/160, bin 32에서 각각 2와 1이므로 값을 서로 바꿔 쓰려면 정의를 확인해야 한다.

독립 재현 결과는 [수정 후 재확인 JSON](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/post-fix-recheck.json), 실행 방법은 [재현 스크립트](E:/I-mage-not-AI/artifacts/core-algorithm-reassessment-2026-10-31/post_fix_recheck.py)를 참고한다. 앞서 `evidence.json`을 수정 후 재실행하며 덮어썼기 때문에 “수정 전 재현 기록”이라는 직전 문장은 정확하지 않다. 당시 기준 실행 기록은 이 문서의 반례 서술에 남아 있다.

## 잔여 재현 결함 수정 — 2026-10-31

후속 재현에서 확인된 항목도 수정하고 회귀 테스트를 추가했다.

- project overlap rule은 계획과 실행 결과가 동일한 `a:b` 대상 ID를 사용한다. 쌍의 두 feature mask capability를 계획에 반영하고, finding의 `constraint_origin`을 후처리가 덮어쓰지 않는다.
- 중첩 pose 구조와 각 배열 차원이 다르면 loop 거리를 만들지 않고 `INCONCLUSIVE`로 반환한다.
- 속도·가속도·jerk norm은 안정적인 `hypot`를 사용한다. 유한 범위를 넘는 파생 측정은 무한대 JSON 값 대신 `INCONCLUSIVE`와 null 측정값을 낸다.
- `review`와 `measure`의 luma bin 계산을 linear-sRGB luminance, 0..255 기준으로 통일했다.
- review JSON에 각 feature mask의 실제 SHA-256 및 크기를 기록하고, manifest에 mask SHA가 선언되면 입력 시 검증한다.

최종 검증: `.venv/Scripts/python.exe -m pytest -q` **70 passed**; mypy **36 source files 통과**; Ruff **통과**; `git diff --check` **통과**. `post-fix-recheck.json`은 이전 재확인 기록으로 보존하고 이 실행의 결과로 덮어쓰지 않았다.
