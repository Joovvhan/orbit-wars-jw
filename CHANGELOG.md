# Agent 개발 변경 이력

---

## v10 (확정)

### 변경 내용: Melis Forward Evaluation (v9 하이브리드)

**변경 내용**:
- `forward_project`: 20턴 미래 시뮬레이션 (생산 + 전투 + 상대 이동 간이 모델)
- `melis_score`: 스냅샷(4,8,14,20턴) 가중평균 → `ships_diff + 5×planets_diff + 8×prod_diff`
- v9 타깃 스코어에 Melis gain 보너스 추가: `score = score_v9 + 1.5 × melis_gain`
- v9의 while 루프 아키텍처 유지 (공격 빈도 보존)
- 전반 게임 협공 (neutral_ratio 제약 제거)

**개발 과정 핵심 교훈**:
1. **v9 아키텍처 교체 실패**: 처음에 Melis로 v9 while 루프를 완전히 대체 → v10 vs v9 0%로 완전 실패
2. **이중 방어 예약 버그**: `available = mine.ships - reserve`를 공격 섹션에도 적용 → 공격 자원이 0이 되어 확장 멈춤
3. **타깃 수 제한 버그**: `SEARCH_MAX_PER_SOURCE = 4`로 후보 제한 → 대부분 공격 불가
4. **Melis 보너스 방식 채택**: v9 while 루프 + Melis gain 보너스로 전환 → 정상 동작

**최종 성능 (60게임 기준)**:
| 상대 | v10 | v9 | v8 |
|------|-----|-----|-----|
| v9 | 50% | — | 53.3% |
| v8 | 45% | 53.3% | — |
| ex_proto | 1.7% | 0% | 1.7% |
| ex_lb958 | 11.7% | 11.7% | 11.7% |

**결론**: Melis forward evaluation이 타깃 스코어 보너스로는 중립적. ex_lb1050a와의 격차를 좁히지 못함.

**ex_lb1050a 분석 (100% vs 모든 에이전트)**:
- Hammer/Mega-hammer: 대규모 함대 적립 후 일격
- Multiprong: 2-3개 타깃 동시 공격으로 딜레마 유발
- Personality 시스템: patient/opportunistic/pressure 3단계 모드 전환
- Depth-2 search: 상대 반응까지 시뮬레이션

---

## v10 개발 방향 (examples 패배 원인 분석)

### v9 vs examples 패배 원인

**1. ex_proto (0% 패배) 주요 차이점**:
- ex_proto의 coop는 **전반 게임에서** 활성화 (v9는 late game만)
  - `MIN_SHIPS_TARGET_COOP_ATTACK = 20`: 타깃 garrison >= 20이면 coop 시도
  - 최대 8개 행성이 순차 도착으로 협공 (동시 도착 불필요!)
- **`fleet_trajectories` 전역 상태**: 이전 턴 발사 정보를 다음 턴에도 기억
  - 우리는 매 턴 `my_inbound`로 재계산하지만, ex_proto는 발사 각도+from_planet_id로 더 정확히 추적
- **강화(reinforcement) 전용 시스템**: `reinforcement_trajectories`로 증원 발사 중복 방지
- **3 베스트 타깃 시도**: `candidate_targets[:3]`으로 1위 타깃 실패 시 2위 시도

**2. ex_lb958 (12-15% 패배) 주요 차이점**:
- Binary search 방어 예비 함대 계산
- WorldModel 타임라인 기반 미래 상태 시뮬레이션 (110턴 ahead)
- Multi-source swarm with ETA tolerance = 2 (동시 도착 불필요)

### v10 우선 개발 항목

| 항목 | 출처 | 기대 효과 | 난이도 |
|------|------|-----------|--------|
| **전반 게임 coop** (neutral_ratio 제약 제거) | ex_proto | 초반 강한 타깃 공략 | 중 |
| **순차 coop 단순화** (동시 도착 불필요, 대신 양 충분히) | ex_proto | 현재 coop보다 효율적 | 중 |
| **fleet tracking 강화** (발사 기억) | ex_proto | 중복 발사 방지 | 중 |
| **3 타깃 후보 시도** | ex_proto | 더 유연한 공격 | 하 |
| **수비 후 즉시 역공** | ex_smith | 방어 후 기회 활용 | 중 |

### 핵심 교훈: 동시 도착 vs 순차 도착

v9 개발 중 동시 도착(simultaneous arrival)이 반드시 필요하다고 가정했으나, ex_proto 코드 분석 결과:
- **순차 도착 coop도 충분히 효과적**: 첫 함대가 garrison을 깎고 두 번째 함대가 마무리
- 핵심은 **총 투입 함대 > garrison** 이지 **도착 시간의 동기화**가 아님
- v9의 ships_for_eta 역산 기법은 과-엔지니어링이었음 (적용은 유효하나 필수 아님)

---

## 핵심 교훈 — 협공(Coop Attack)에 대하여

### 왜 단순 협공이 실패하는가

orbit_wars 전투 규칙:
```
같은 턴에 도착한 함대 → 합산 후 일괄 전투 (동시 도착이면 협공 성립)
다른 턴에 도착한 함대 → 순차 전투 (각자 싸움, 협공 불성립)
```

순차 전투 예시 (garrison=60, Fleet A=50, Fleet B=30):
```
턴 10: A 50척 vs 60 → A 패배, garrison=10
턴 13: B 30척 vs 10+3*production → B 간신히 승리 or 패배
```
총 80척 투입, 결과가 불확실. 동시 도착이었다면 80 vs 60 → 확실한 승리.

### 동시 도착 보장 방법 (v9 구현 목표)

각 행성에서 출발 시각을 조율해서 같은 턴에 도착하게 설계:

```python
# 원하는 도착 턴: target_arrival_turn
# 행성 A: dist_A / speed(ships_A) = target_arrival_turn → ships_A = f(dist_A / target_arrival)
# 행성 B: dist_B / speed(ships_B) = target_arrival_turn → ships_B = f(dist_B / target_arrival)
#
# 단, ships_A + ships_B >= garrison + production * target_arrival + 1 (점령 조건)
```

구체적으로: `fleet_speed(ships) = 1 + 5*(log(ships)/log(1000))^1.5`를 역산하여  
목표 도착 턴에 맞는 함대 크기를 계산한다.

### v9 동시 협공 알고리즘 설계

```
1. 대상 타깃 선택 (단독으로 점령 불가능한 적 행성)
2. 후보 행성들의 거리 계산
3. 공통 도착 턴 T 결정:
   - T = max(각 행성의 최소 도착 턴)  ← 모두 도달 가능한 가장 이른 턴
4. 각 행성이 T턴에 도착하기 위한 함대 크기 계산:
   - dist / T = required_speed → ships = inverse_speed(required_speed)
5. garrison = target.ships + production * T
6. 총 ships >= garrison + 1 이면 협공 실행
7. 각 행성에서 계산된 ships만큼 발사
```

### 주의사항
- 공전 행성(orbiting)은 T턴의 위치를 predict_position으로 예측해야 함
- path_hits_sun 검증 필수
- 단독 공격이 가능한 타깃에는 협공 불필요 (낭비)
- 협공 대상: 총 가용 병력 합산 >= garrison 이지만 단독으로는 불가능한 경우만

---

## v9 (확정)

### 변경 내용: 동시 도착 협공(Simultaneous Coop Attack) 구현

**변경 내용**:
1. `ships_for_eta(dist, T)`: 이진탐색으로 정확히 T턴에 도착하는 최소 함대 크기 계산
2. 최소 T 탐색: 후보 T값 순서대로 협공 가능 여부 확인 → 가장 이른 T 사용
3. `len(executed) >= 2` 조건: 진짜 2-소스 이상 협공만 실행
4. 적 행성 전용, 후반 게임(neutral_ratio < 0.35)에서만 활성화
5. 안전 마진(+5): 적 증원 가능성 고려

**개발 과정 핵심 교훈**:

1. **T 선택 실수**: 처음에는 `T = max(모든 소스의 min_eta)` 사용 → 가장 먼 소스가 T를 결정해 가까운 소스들이 불필요하게 오래 기다림. 수정: 후보 T값들을 순서대로 시도하여 최소 T 탐색.

2. **초반 게임 비활성화**: 협공을 전반에 실행하면 중립 확장을 방해하여 v7보다 성능 저하. 수정: neutral_ratio < 0.35 (후반)에서만 활성화.

3. **my_inbound 미반영 버그**: 이미 날아가는 아군 함대를 need에서 빼지 않아 매 턴 같은 타깃에 과다 발사. 수정: `need = garrison_T - already` 적용.

4. **MIN_FLEET cap 버그**: `ships_for_eta` 반환값에 `max(MIN_FLEET, ships)` 적용 시 ETA 불일치. 수정: MIN_FLEET 미달 시 해당 소스 제외.

5. **단일-소스 위장 협공**: `len(contributors) >= 2` 조건이 있지만 실행 시 1개 소스만 필요한 경우 발생. 수정: `len(executed) >= 2` 조건 추가.

**최종 성능 (30게임 기준)**:
| 상대 | v9 승률 | v8 승률 |
|------|---------|---------|
| v8 | **53.3%** ✅ | — |
| v7 | 58.3% | 58.3% |
| ex_proto | 0% | 1.7% |
| ex_lb958 | 11.7% | 15% |

**결론**: v9는 v8보다 약간 강함(53.3%). examples 대비 유의미한 격차는 좁히지 못함.

---

---

## v5 베이스라인 (기준)

**핵심 로직**: 아군 함대 중복 공격 제거 + iterative aiming + 점수 기반 타깃 선택

| 상대 | 승률 |
|------|------|
| ex_proto | 23.3% |
| ex_lb958 | 6.7% |
| ex_lb1200 | 3.3% |
| ex_smith | 0.0% |
| ex_lb1050a | 0.0% |

---

## v6 개발

### 변경 #1: 수비(Defense) + 도주(Evacuation) 추가 — 초기 버전

**변경 내용**:
- `simulate_planet_timeline()`: 내 행성별 적 도착 시뮬레이션 (60턴 horizon)
- Evacuation: 10턴 이내 함락 예정 행성에서 가장 가까운 안전 아군 행성으로 대피
- Defense: 위협받는 행성에 가장 가까운 아군 행성에서 증원 발사
- 함대 타깃 감지: 각도 기반 (기존 v5 방식, threshold=0.25rad)

**예상 결과**: 수비가 있으므로 행성 함락이 줄어 v5 대비 50%+ 승률 기대

**실제 현상**: v5 대비 22.5% — 오히려 저하

**원인 분석**: `_find_fleet_target`이 각도 0.25rad(~14도) 임계값으로 적 함대 타깃을 잘못 식별. 중립 행성을 향하는 적 함대가 내 행성을 향하는 것으로 오탐(false positive) → 불필요한 수비/도주 발동 → 공격 자원 낭비

**후속 절차**: 함대 타깃 감지를 경로 추적(path tracing) 방식으로 교체

---

### 변경 #2: 함대 타깃 감지 → 경로 추적 방식으로 교체

**변경 내용**:
- `_find_fleet_target`: 각도 매칭 → tick별 경로 추적 (120틱 × 행성 반경 충돌 체크)
- 행성 예측 위치 `predict_position` 적용으로 공전 행성도 정확히 감지
- `REINFORCE_MAX_ETA_MARGIN`: 3 → 1 (너무 엄격한 조건 완화)

**예상 결과**: false positive 제거로 수비 정확도 향상 → v5 대비 55%+ 기대

**실제 현상**: v5 대비 47.5%, ex_proto 대비 30.0%, ex_lb958 대비 15.0%

**원인 분석**: 경로 추적으로 false positive 대폭 감소. v5 대비 유사 수준 회복 + 상위 예시 에이전트 대비 향상 확인. 아직 ex_proto(23.3%→30.0%) 대비 개선됐지만 ex_lb958(6.7%→15.0%)은 아직 부족.

**후속 절차**: 
- 예시 에이전트에서 추가 기술 도출
- 협공(Multiprong), 간접 가치(Indirect Wealth) 등 적용

---

### 변경 #3: benchmark.py 병렬 처리 (ProcessPoolExecutor)

**변경 내용**:
- `ProcessPoolExecutor`로 게임을 워커 수만큼 병렬 실행
- 기본 workers=4, `-j N` 옵션으로 조정
- `_run_match_worker`: 각 워커가 독립적으로 에이전트 로드 후 게임 실행

**예상 결과**: N코어 사용 시 ~N배 속도 향상

**실제 현상**: 40게임 기준 2.52s/game → 0.82s/game (8 workers), **약 3배 빠름**
(Python GIL + 프로세스 오버헤드로 이론적 8배보다 낮음)

**원인 분석**: 프로세스 시작 오버헤드와 메모리 복사가 있어 완전한 8x는 아님.
그래도 게임이 많을수록 효율 향상.

**후속 절차**: 현재 기본값 j=4 유지, 필요시 조정

---

### 변경 #4: 협동 공격(Coop Attack) 시도 — 실패

**변경 내용**: 여러 행성에서 강한 타깃을 합공하는 coop attack 구현 시도 (3가지 버전)

**예상 결과**: 단일 행성으로 공략 불가한 강한 타깃도 점령 가능

**실제 현상**: v5 대비 0-2.5% — 완전 실패

**원인 분석**:
1. 1차 버전: `aim_iterative(mine, t, avail, ...)` 각도로 실제 `send` 발사 → 속도 불일치로 공전 타깃 빗나감
2. 2차 버전: `aim_iterative`를 `send`로 재계산했지만, `send2` (aim_iterative 반환값)는 garrison 기준 필요 함수라서 합공과 맞지 않음
3. 3차 버전: 정적 타깃 한정, 직선 각도, 비율 분배. 코드 실행 시 coop 발동은 0회 (모든 조건 불충족)인데도 성능 저하
4. 파일 I/O 디버깅으로 환경 교란 확인 → 제거 후에도 동일 결과
5. **근본 원인 미확인**: 논리적으로 동일해야 하지만 실제로 성능이 다름. 환경 격리 문제 추정

**후속 절차**: coop 완전 제거. ex_lb958의 simultaneous arrival 방식으로 재설계 필요

---

### v6 최종 성능 기준선 (병렬 벤치마크 기준)

| 상대 | v6 승률 | v5 승률 |
|------|---------|---------|
| v5 | 43.3% | — |
| ex_proto | 0.0% | 3.3% |
| ex_lb958 | 13.3% | 15.0% |

> **비고**: 병렬 벤치마크가 정확한 결과. 이전 순차 벤치마크는 ex_proto 전역 상태 오염으로 왜곡됨.
> ex_proto이 ex_lb958보다 훨씬 강함 (83.3%). v6/v5 모두 ex_proto에 거의 이기지 못함.

---

### 변경 #5: 도주 → 공격 전환 (Offensive Evacuation)

**변경 내용**:
- 함락 예정 행성에서 도주 시, 아군 행성으로 후퇴하는 대신 **공격 가능한 적/중립 행성을 먼저 시도**
- 공격 가능 타깃 없으면 기존 후퇴 방식 fallback

**예상 결과**: 함대 낭비 감소, 도주 함대가 새 행성 점령에 기여

**실제 현상**: 측정 중 (v6 vs v5, ex_proto, ex_lb958 30게임 벤치마크)

**원인 분석**: TBD

**후속 절차**: 벤치마크 결과 확인 후 판단

---

---

## v7 (확정)

### 변경 내용: 중립 행성 쟁탈 여유(race_margin) 기반 스코어링

**변경 내용**:
- `race_margin(t) = 적_최소도착턴 - 내_최소도착턴`
- 적이 3턴 이상 빠른 중립은 건너뜀 (`RACE_SKIP_THRESHOLD = -3`)
- 내가 빨리 도착 가능한 중립에 `margin * 3.0` 보너스
- v6 기본 스코어 유지, 중립에만 race_margin 반영

**개발 과정에서 실패한 시도**:
1. `production × time_to_hold` 완전 교체 → 먼 고생산 행성만 노려 v3/v5에 패배 (0-40%)
2. `neutral_bonus = production × remaining / 100` 추가 → 효과 없이 오히려 v6보다 나빠짐
3. 1회 테스트에서 regression 즉시 발견 후 수정 (사용자 피드백 반영)

**최종 성능 (20게임 기준)**:
- v3 대비: 70% ✅
- v6 대비: 57.5% ✅
- 모든 이전 버전(v1~v6) 압도

**examples 결과 (5게임 기준)**:
| 상대 | v7 승률 |
|------|---------|
| ex_proto | 10% |
| ex_lb958 | 0% |
| ex_smith | 0% |
| ex_lb1050a | 0% |

---

## v8 개발 방향 (examples 패배 원인 분석)

### 패배 원인 분석

**1. 협동 공격 부재** (모든 examples)
단일 행성으로 점령 불가한 강한 적 행성을 공략하지 못함.
ex_proto는 여러 행성이 합산하여 강한 타깃 점령.

**2. 미래 상태 예측 없음** (ex_lb1050a, ex_lb958)
v7은 현재 턴 greedy 결정. examples는 수십 턴 후 상태를 시뮬레이션.

**3. 게임 페이즈 전환 없음** (ex_smith)
v7은 중립 소진 후에도 같은 로직 사용. 중립 쟁탈→적 압박 전환 없음.

**4. 수비 반응 속도** (전체)
현재 수비는 타임라인 시뮬 기반이나, 증원 우선순위/타이밍이 조잡함.

### v8 우선 개발 항목
1. **페이즈 전환**: 중립 소진 시점 감지 → 적 압박 모드로 전환
2. **Forward simulation**: 20턴 후 상태 기반 타깃 평가
3. **개선된 수비**: Binary search 최소 방어 함대 계산

---

## v8 (확정)

### 변경 내용: Endgame ROI 필터 + 페이즈 인식 스코어

**변경 내용**:
1. **Endgame ROI**: `production × remaining_after < ships_needed`인 공격에 패널티 (`roi * 0.5`)
2. **페이즈 보너스**: `neutral_ratio` 감소할수록 (중립 소진) 적 행성 스코어 +30 증가
3. `neutral_ratio = n_neutral / len(targets)` 매 턴 계산

**개발 과정 실패 시도**:
- Reserve + Hammer: 성능 저하 및 타임아웃 (O(N²) 계산)
- 자동 협공(Implicit Coop): 순차 도착 전투에서 오히려 손해
  → 협공은 동시 도착 보장 없으면 각 함대가 순차적으로 패배

**최종 성능 (20게임 기준)**:
| 상대 | v8 승률 |
|------|---------|
| v7 | 60% ✅ |
| v6 | 70% ✅ |
| v5 | 55% ✅ |
| v2/v3 | 70% ✅ |

**examples (5게임 기준)**:
| 상대 | v8 | v7 |
|------|-----|-----|
| ex_proto | 10% | 10% |
| ex_lb958 | 0% | 0% |
| ex_smith | 0% | 0% |

**결론**: v8은 v7보다 명확히 강하지만, examples와의 격차는 비슷함. 
examples를 이기려면 더 근본적인 전략 개선(Forward Sim, 동시 협공 등) 필요.

---

---

## v8 과거 실패 기록 (반복 방지)

### 변경 내용: Reserve + Hammer

**Reserve**: 행성별 수비 예약 병력 계산 (`compute_reserve`)  
**Hammer**: 중립 소진 후 적 고생산 행성 집중 타격

**실제 현상**: v7 대비 35% (40게임) — **회귀, 미확정**

**원인 분석**:
1. Reserve를 공격 자원 제한에 사용 → 초반 확장 방해 (수정: 공격엔 적용 안 함)
2. Hammer 계산이 O(N_targets × N_planets) → 타임아웃 위험 (수정: 단순화)
3. Hammer가 최고생산 적 타깃만 노리는 단순 전략 → greedy보다 나쁜 경우 있음
4. Hammer 타깃 제외 로직이 regular attack 방해 (수정: hammer_committed로 대체)
5. **근본 문제**: Hammer가 "언제 발사해야 하는가" 판단이 아직 부정확

**후속 절차**:
- Hammer 타이밍 개선: 단순 neutral_ratio 대신 "적 생산 위협이 내 생산의 X% 초과 시"
- 또는: Reserve + Hammer 전략 자체를 재검토, 더 단순한 접근 시도

---

## 다음 적용 기술 후보 (예시 에이전트 분석 기반)

| 기술 | 출처 | 예상 효과 | 난이도 |
|------|------|-----------|--------|
| Multiprong 협공 | ex_lb1050a | 강한 적 행성 공략 | 중 |
| Indirect Wealth 점수 | ex_lb958/lb1200 | 전략적 타깃 선택 개선 | 하 |
| Doomed Evacuation → 공격 | ex_lb958 | 도주 함대를 빈 행성 공략으로 | 중 |
| 적 함대 충돌 이용 (Crash Exploit) | ex_lb958 | 적끼리 싸운 후 점령 | 상 |
| 수비 후 반격 (Counter-attack) | ex_smith | 방어 성공 후 즉시 역공 | 중 |
