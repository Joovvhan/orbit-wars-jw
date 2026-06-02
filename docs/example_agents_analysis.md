# Example Agents 전략 분석

강도 순서: `ex_lb1050a ≈ ex_smith >> ex_lb1200 >> ex_lb958 > ex_proto >> v5`

---

## ex_proto

**파일**: `agents/ex_proto.py`  
**리더보드**: 미상 (가장 낮은 examples 에이전트)

### 핵심 전략

**점수 기반 greedy + 협동 공격(coop attack)**

1. **목표 점수 공식** (우리 v3/v5와 거의 동일):
   ```
   (100 - dist) + 15*production + 10*enemy_bonus - 0.7*total_ships - 2*eta
   ```

2. **방어 감지**: 비행 중인 적 함대가 내 행성을 향하는지 시뮬레이션으로 추적
   - `get_planets_under_attack()`: 적 함대 궤적을 tick 단위로 추적해 내 행성 충돌 여부 확인
   - 위협받는 행성에 **강화(reinforcement)** 우선 발송

3. **협동 공격(coop attack)**: 단일 행성의 함대가 부족할 때, 여러 행성이 합쳐서 공격
   - 최대 8개 행성이 동시에 같은 목표를 향해 발사

4. **비행 중 함대 추적**: `fleet_trajectories` 리스트로 이미 보낸 함대 관리 → 중복 공격 방지

5. **moving/static 행성 구분**: 공전 행성은 별도 함수(`find_angle_to_moving_planet`)로 조준

### 우리 v5 대비 차이점
- 방어 감지 + 강화 발송 있음
- 협동 공격 있음 (여러 행성 → 하나의 목표)
- 전반적인 구조는 비슷하지만 코드량이 훨씬 많음

---

## ex_lb958

**파일**: `agents/ex_lb958.py`  
**리더보드**: ~958점

### 핵심 전략

**WorldModel + 미래 타임라인 시뮬레이션**

1. **행성별 미래 타임라인 계산** (`simulate_planet_timeline`):
   - 각 행성의 향후 110턴 owner/ships를 미리 시뮬레이션
   - 비행 중인 함대 도착을 반영한 미래 상태 예측

2. **방어 예산 자동 계산** (`_compute_defense_buffers`):
   - 이진 탐색으로 "행성을 지키기 위한 최소 유지 함대"를 계산
   - `reserve[planet_id]` = 내보낼 수 없는 최소 보유량
   - `available[planet_id]` = 실제 공격에 쓸 수 있는 함대

3. **타깃 가치 계산** (`target_value`):
   - `production * 남은 턴` 기반
   - 내행성/외행성 구분, 안전/경쟁 중립 구분, 게임 단계(early/late) 구분
   - 다양한 multiplier로 세밀하게 조정

4. **미션 시스템**: 단순 capture 외에 snipe, swarm(다중 출처), reinforce, crash_exploit 미션

5. **`planned_commitments`**: 이번 턴 결정한 행동을 다음 결정에 반영 (중복 공격 방지)

6. **후방-전방 물류(rear-to-front logistics)**: 전선과 먼 후방 행성의 잉여 함대를 전방으로 이동

### 우리 v5 대비 핵심 차이
- **미래 타임라인 기반 방어 예산** — 단순한 reserve 없이 공격하는 v5와 달리 방어를 고려함
- **타깃 가치가 "남은 턴 × 생산량"** — v5의 단순 거리/생산 점수보다 게임 흐름을 반영
- **스웜(여러 행성 동시 공격) 지원**

---

## ex_lb1200

**파일**: `agents/ex_lb1200.py`  
**리더보드**: ~1200점

### 핵심 전략

ex_lb958의 확장판. 구조는 동일하지만 전반적으로 더 정교함.

**ex_lb958과의 주요 차이**:

1. **`settle_plan` 반복 수렴**: 발사할 ships를 여러 번 재계산해 최적 send 값으로 수렴
   - aim(ships) → 도착 시간 → 필요 함대 재계산 → aim 재계산 ... (최대 4회 반복)

2. **방어 미션 확장**:
   - `build_rescue_missions`: 빼앗길 것 같은 행성에 구조대 발송
   - `build_recapture_missions`: 빼앗긴 직후 탈환 계획

3. **`probe_ship_candidates`**: 탐색할 함대 수를 여러 후보로 나눠 가장 좋은 조합 선택

4. **`reinforcement_needed_to_hold_until`**: 이진 탐색으로 유지에 필요한 정확한 함대 수 계산

5. **더 정교한 타깃 가치**: `indirect_features` (주변 행성에서의 간접 가치) 포함

---

## ex_smith (Agent Smith, ~1000 LB)

**파일**: `agents/ex_smith.py`  
**리더보드**: ~1000점

### 핵심 전략

ex_lb958/1200과 거의 동일한 WorldModel 구조, 다른 이름의 에이전트.

**특징**:
- ex_lb1200과 매우 유사한 코드 구조
- 세부 파라미터와 미션 처리 방식이 약간 다름
- `HOSTILE_TARGET_VALUE_MULT = 2.0` (lb1200은 1.85) — 적 행성 공격을 더 선호

---

## ex_lb1050a (가장 강함)

**파일**: `agents/ex_lb1050a.py`  
**리더보드**: ~1050점 (실제 대전 성능은 가장 강함)

### 핵심 전략

**Melis 전방 시뮬레이션 + 다중 전략 레이어**

가장 복잡한 에이전트. 구조가 완전히 다름.

1. **Melis 평가 시스템** (`melis_evaluate`):
   - 각 행동 후보에 대해 N턴 forward simulation (상대 움직임 포함)
   - 여러 스냅샷(4, 8, 14, 20턴) 평균으로 행동 점수 계산
   - 단순 greedy 점수가 아닌 **미래 상태 기반 평가**

2. **forward_project**: 상대방도 매 4턴마다 가장 가까운 적에게 함대를 발사한다고 가정하고 시뮬레이션

3. **Hammer 전략**: 큰 함대를 축적했다가 한 번에 목표 행성을 압도
   - `HAMMER_STOCKPILE_MIN = 38~50` ships 이상 → 한꺼번에 쏨

4. **Coalition(연합 공격)**: 여러 행성이 협력해 강한 행성 공략

5. **게임 단계(personality) 시스템**:
   - `patient` / `opportunistic` / `pressure` 3가지 모드
   - 상대방의 공격 성향을 감지해 모드 전환

6. **depth-2 탐색**: 내 행동 → 상대 반응 → 내 점수 를 2단계로 평가

7. **다양한 보조 전략**:
   - anti-snipe: 적이 뺏으려는 행성 먼저 방어
   - counter-snipe: 적 함대가 비어있는 사이 적 행성 공격
   - race: 적보다 먼저 중립 행성 도착
   - late-flush: 게임 종료 전 잉여 함대 모두 투입
   - doomed-evac: 잃을 것 같은 행성에서 철수

8. **BEAST 모드** (ex_smith.py에서): 더 공격적인 파라미터 설정
   - `HAMMER_STOCKPILE_MIN = 38` (일반 50보다 낮음 → 더 자주 hammer)
   - `HOSTILE_TARGET_VALUE_MULT = 2.0` → 적 공격 선호도 높음

---

## 요약 비교

| 특징 | v5 (우리) | ex_proto | ex_lb958 | ex_lb1200 | ex_lb1050a |
|------|-----------|----------|----------|-----------|------------|
| 점수 공식 | greedy (거리/생산) | greedy | 남은턴×생산 | 남은턴×생산 | Melis 시뮬레이션 |
| 방어 예산 | 없음 | 적 감지만 | 타임라인 기반 | 이진탐색 정밀 | 동적 reserve |
| 중복 공격 방지 | arrivals 추적 | fleet_trajectories | planned_commitments | planned_commitments | planned_commitments |
| 협동 공격 | 없음 | 있음 (coop) | swarm | swarm+rescue | coalition+hammer |
| 상대 모델링 | 없음 | 없음 | 없음 | 없음 | 있음 (forward sim) |
| 게임 단계 적응 | 없음 | 없음 | early/late | early/late | 3단계 personality |
| 코드 크기 | ~160줄 | ~800줄 | ~1900줄 | ~3200줄 | ~4800줄 |

## v6 개선 방향 제안

1. **타깃 가치를 `남은턴 × 생산량`으로 교체** (ex_lb958 방식) — 가장 임팩트가 클 것
2. **방어 예산(reserve) 추가** — 공격 중 무방비 상태 방지
3. **planned_commitments 도입** — 이미 보내기로 한 함대 고려
4. **Melis 평가 도입** (ex_lb1050a 방식) — 가장 어렵지만 가장 강력
