# 돌모리 v002 2D 자연스러움 검사

검사일: 2026-10-31<br>
대상: PAC Unity의 돌모리 v002 스프라이트 애니메이션 및 8방향 캐릭터 표시<br>
Unity 실행: 전체 모션 `runtime-v2-cli-run20-full-naturalness`; 이벤트·효과 경로 `runtime-v2-cli-run28-event-binding`<br>
판정: **측정 완료, 자연스러움 종합 판정은 INCONCLUSIVE**

## 결과 요약

Unity 6000.3.25f1 CLI에서 격리된 PAC 화면 경로를 실행했습니다. 692개 런타임 확인이 모두 통과했고 PAC 런타임 오류는 0건입니다. 아틀라스 원본 대조도 196/196 영역 및 10,437,580/10,437,580 가시 픽셀이 일치했습니다. Idle, Walk, Attack, Ability, Hurt, Hop, Sleep 등의 실제 모션 클립을 재생했습니다. Attack은 방향별 9프레임(72/72 관측), Ability는 12프레임(96/96), Sleep은 2프레임(16/16) 관측이 통과했습니다. 런타임 메타데이터에는 총 57개 클립이 있습니다. Attack은 `Rotate`, Ability는 `RearUp` 클립에 연결됩니다. Sleep은 8개 방향에서 별도 클립 8개가 있는 구조가 아니라 `Sleep/Anim/0` 하나를 재사용합니다.

자연스러움 측정은 i-mage-not-ai `game analyze` CLI가 수행했습니다. 266개 측정 결과가 만들어졌고 모두 `INCONCLUSIVE`입니다. 자연스러움 허용 기준이 프로젝트에 정의되지 않아 알고리즘이 수치를 PASS/FAIL로 바꾸지 않았습니다. 따라서 “자연스럽다”는 승인 결과는 아닙니다.

## 수치 결과와 검토 후보

| 항목 | 측정 결과 | 해석 |
|---|---:|---|
| 월드 위치 이동 | 8방향 각각 181개 위치 샘플, 이동 경로 23.2 UI px, 프레임 간 최대 이동 0.921 px | 테스트 픽스처가 행 2에서 행 3으로 이동한 PAC 표시 보간 결과입니다. 운영 전투나 서버 동기화 검증은 아닙니다. |
| 속도 | p95 87.17 px/s, 최대 211.34 px/s | 위치 표본 간 차분으로 계산했습니다. |
| 가속도·jerk | p95 가속도 7,713.76 px/s², p95 jerk 3,523,096.92 px/s³; 최대는 각각 30,977.26 px/s², 8,670,848.91 px/s³ | 샘플 시간 간격이 약 2.9–13.3 ms로 달라 큰 값은 프레임 스케줄링에 민감합니다. 숫자만으로 끊김 판정은 하지 않았습니다. |
| 지면 기준점 대리값 | 8방향 스프라이트 하단 위치 범위의 최댓값 5.625 UI px | 실제 발 접촉점 라벨이 아니라 스프라이트 알파 하단 기준입니다. 접지 판정으로 간주할 수 없습니다. |
| 프레임 체류시간 | 64개 방향/클립 그룹 중 비교 표본이 있는 그룹 48개. 관측/원본 시간 비율 중앙값 1.0015, 그룹별 p95 절대 오차의 중앙값 3.12 ms, 최댓값 6.07 ms | Attack/Rotate에는 PAC의 `delay × (0.4 + speed × 0.007) / 36` 보정을 반영했습니다. 관측 시작 전에 이미 진행 중이던 첫 구간은 제외했습니다. |
| 루프 실루엣 경계 | 반복 클립 17개. 첫/마지막 실루엣 IoU 최솟값 0.728 (`dir4_Walk`), 중앙값 0.880 | `dir4_Walk`는 루프 경계에서 모양 차이가 커 우선 눈으로 검토할 후보입니다. 이 수치는 결함 확정이 아닙니다. |
| 방향별 하단 실루엣 변동 | 소스 `dir7_Walk` support-line y 범위 45 source px. Unity 이동 표본에서 하단 기준점 범위 최댓값 5.625 UI px | 스프라이트 소스 좌표와 UI 좌표는 다른 스케일입니다. 예상 접지 기준이 없어 자동 합격/불합격은 내지 않았습니다. |
| 캐릭터 간 가림 | 보드 31개 표본 프레임, 알파 가림 결과 217개. 비율 중앙값 0.114, p95 0.199, 최대 0.206 | 런타임 좌표·Unity sibling 순서와 원본 알파로 계산한 캐릭터 간 겹침입니다. Unity 화면 픽셀 캡처는 아닙니다. |

### 우선 검토 후보: `dir4_Walk` 루프 경계

첫/마지막 소스 프레임을 비교하면 자세와 외곽이 이동합니다. IoU 0.728은 17개 반복 클립 중 가장 낮은 값입니다. 다만 걷기 주기의 위상 차이가 의도된 것인지, 루프 순간에 튀는지 최종 판단하려면 실제 렌더링에서 마지막→첫 프레임 전환을 봐야 합니다.

![dir4_Walk 루프 첫/마지막 프레임 및 실루엣 차이](/E:/I-mage-not-AI/artifacts/dolmori-v002-naturalness-2026-10-31/dir4-walk-loop-seam-review.png)

Overlay 색상: 공통 영역 노랑, 첫 프레임에만 있는 영역 청록, 마지막 프레임에만 있는 영역 자홍.

## 아직 측정할 근거가 없는 항목

- **실제 발 접지 미끄러짐:** 프레임별 발 접촉 라벨과 접촉 구간이 없습니다. 스프라이트 하단 변동은 대리 측정일 뿐입니다.
- **캐릭터 내부 부위 가림·그림자:** v002에는 별도 그림자 클립 0개, 독립 부위 레이어/마스크가 없습니다.
- **모션과 의미 이벤트는 별도입니다.** Attack/Ability/Sleep 등의 모션 클립 및 재생 프레임 시간은 검사했습니다. 다만 타격 접촉 프레임(`attackContactLogicalFrame`)과 능력 명중 프레임(`abilityHitLogicalFrame`)은 모두 null이고 `runtimeEventVerified=false`입니다. 그래서 “몇 번째 프레임에서 실제 공격 판정/명중이 일어나야 하는가”는 검사할 기준이 없습니다.
- **synthetic 이벤트에서 PAC 효과 렌더러 경로는 실행 검증했습니다.** 격리 Unity QA가 `RearUp/Anim/{direction}` 논리 프레임 5(10/36초)를 8방향 모두 관측한 뒤, 합성 ABILITY/ERUPTION 이벤트 37건(30회 일정 + 나머지 7방향 probe)을 PAC `PacCombatEffects.Render`에 전달했습니다. 37/37 시각 효과가 생성·종료됐고, 미지원 스킬 0건, 누락 에셋 0건, 종료 후 활성 효과 0건, 선언된 보드 원점 대비 위치 오차 최대 0.137 UI px였습니다. 첫 8방향 probe는 모두 프레임 5에서 발행됐습니다. 30회 연속 발사 구간에서 관측된 몸체 logical frame은 `5:4, 6:1, 7:1, 8:2, 10:2, 11:17, 0:1, 2:1, 3:1`입니다. 따라서 30발 모두 frame 5에서 나온 것은 아닙니다. “첫 릴리스만 frame 5에 연결하고 뒤 29발은 지속 발사”가 의도라면 맞을 수 있지만, 매 발사를 frame 5에 묶어야 하는 명세라면 이 결과는 불일치입니다. 이 검사는 테스트 하네스가 프레임을 기다린 후 이벤트를 직접 발행한 동기화 probe입니다. 실제 전투 이벤트가 프레임 5에서 자동 발행되는지 또는 CAMERUPT0323가 ERUPTION으로 매핑되는지는 아직 증명하지 않았습니다.
- **효과 종류와 일정 소유권:** PAC `ERUPTION`은 Unity `ParticleSystem`이 아닌 스프라이트 효과 렌더러입니다. 이벤트 시각 편차는 중앙값 2.786ms, 최댓값 5.555ms였고 측정 수명은 497.2–500.0ms였습니다. i-mage-not-ai CLI가 이벤트 37건과 위치 track 37개, 종료된 수명 37개를 분석했으며 결과 336개는 모두 `INCONCLUSIVE`입니다. 임계값을 지정하지 않아 PASS/FAIL을 만들지 않은 결과입니다. CAMERUPT0323의 `bodyKeyframeBinding`, `physicalEmitter`는 원본 자료에서 여전히 null이며, 이 프로브로 해당 출처 연결이 확정되지는 않습니다.
- **실제 화면 픽셀과 색상 처리:** Unity는 `-nographics`/`Null` 그래픽 장치로 실행되어 화면 캡처가 없습니다. GUI 렌더링, 색보정, 해상도별 외관은 검증하지 못했습니다.
- **실전 전투·서버·기기:** 로컬 상태 픽스처로 PAC의 캐릭터 표시/보간을 실행했습니다. 실전 전투, 서버 이벤트, 모바일 기기 검증은 아닙니다.

## 실제 게임 연결을 확정하기 위한 남은 검증

1. 실제 전투 이벤트의 skill id가 `ERUPTION`인지, CAMERUPT0323 30회 일정의 소유 코드가 무엇인지 확인합니다.
2. Ability 릴리스의 의미 프레임을 애니메이션 제작 자료 또는 승인된 이벤트 명세로 확정합니다. 현재 프레임 5는 시각 검토에서 정한 후보입니다. 필요하면 프레임별 2D emitter 기준점 `(u,v)` 또는 sprite-local 픽셀 좌표를 선언합니다.
3. synthetic 주입 대신 실제 게임의 전투 이벤트 생성 경로를 타는 Unity QA 시나리오를 만들어, 자동 이벤트 시각과 캐릭터 프레임/방향을 같은 trace로 기록합니다. 네트워크나 서버 결과를 흉내 내지 말고 격리된 전투 fixture에서 이벤트 생성 코드를 호출해야 합니다.
4. 실제 이벤트가 승인된 릴리스 프레임에서 나오는지, 방향별 발사 좌표가 body anchor/보드 기준과 일치하는지, 30회 생성·수명 종료·보드 경계를 확인합니다. 그 다음 프로젝트 담당자가 시각 편차·수명·위치 허용치를 정하면 CLI에서 PASS/FAIL 검사를 실행합니다.
5. 그래픽을 켠 Unity 실행과 화면 캡처로 외관을 확인합니다. 현재 `-nographics`는 스프라이트의 실제 합성 픽셀, 색, 가림을 검증하지 않습니다.

## 재현 및 세부 자료

전체 애니메이션 CLI 영수증/표본은 `unity-receipt.json`, `unity-runtime-samples.json`입니다. 이번 이벤트 경로의 영수증/표본은 `unity-event-binding-receipt.json`, `unity-event-binding-samples.json`; 상세 효과 관측과 요약은 `unity-event-binding-runtime.json`, `event-binding-summary.json`입니다. CLI 입력은 `unity-event-binding-trace.json`, 정책은 `event-binding-measurement-policy.json`, 저장 결과는 `event-binding-game-analysis.json`입니다. `build_event_binding_trace.py`가 기존 canonical 트레이스에 이벤트 시각, body logical frame, 효과 위치 표본, 수명을 결합합니다.

재실행 명령:

```powershell
.venv\Scripts\python.exe -m i_mage_not_ai game analyze `
  --trace artifacts/dolmori-v002-naturalness-2026-10-31/unity-game-trace.json `
  --policy artifacts/dolmori-v002-naturalness-2026-10-31/measurement-policy.json `
  --output artifacts/dolmori-v002-naturalness-2026-10-31/game-analysis.json
```

CLI 알고리즘이 측정값을 임계값 없이 보고하도록 `imreview/game.py`와 `schemas/feature-manifest.schema.json`에서 수치 기준 `null`을 측정 전용 모드로 허용했습니다.

이벤트 경로 측정 재실행:

```powershell
.venv\Scripts\python.exe artifacts/dolmori-v002-naturalness-2026-10-31/build_event_binding_trace.py
.venv\Scripts\python.exe -m i_mage_not_ai game analyze `
  --trace artifacts/dolmori-v002-naturalness-2026-10-31/unity-event-binding-trace.json `
  --policy artifacts/dolmori-v002-naturalness-2026-10-31/event-binding-measurement-policy.json `
  --output artifacts/dolmori-v002-naturalness-2026-10-31/event-binding-game-analysis.json
```
