# Agent Improvement Analysis

## 현재 에이전트 (v1: smart_sniper)

**전략**: 각 소유 행성에서 가장 가까운 비소유 행성을 향해 점령 가능한 최소 함대를 발사. 태양 회피 추가.

**로컬 테스트 결과**:
- vs random: ~73% (30게임)
- vs nearest_planet_sniper (baseline): ~40-50% — 유의미한 차이 없음

---

## 우수 사례 분석 (examples/ 6개)

| 파일 | 수준 | 핵심 특징 |
|------|------|-----------|
| `lb-958-1-...reinforce` | LB ~958 | 미션 기반 계획, 110턴 lookahead, 3-source 협공, 적 충돌 이용 |
| `...-ow-proto-passed-1-000` | ~1020–1080점 | 커스텀 점수 공식, 반복 필요 함대 계산, 60턴 궤적 예측 |
| `...-structured-baseline` | 구조화 baseline | 9가지 미션, commitment 전파, binary search 방어 최적화 |
| `agent-smith-1000lb` | ~1000점 | 게임 페이즈, 적 프로파일링, Accumulator 패턴, 시간 예산 관리 |
| `lb-1050-heuristic-simulation` | **LB ~1050** | depth-2 탐색, 가중 스냅샷 평가, multiprong 강제 딜레마 |
| `orbit-wars-heuristic-simulation` | ~1050 동일 계열 | 위와 동일 코드베이스 (Council 개선본) |

---

## 전 에이전트 공통 핵심 기술

### 1. 함대 속도 공식 ✅ (이미 구현)
```python
speed = 1.0 + 5.0 * (log(ships) / log(1000)) ** 1.5
```

### 2. 태양 회피 ✅ (이미 구현)
선분-원 교차 판별. 전원 동일 수식.

### 3. 공전 행성 위치 예측 ❌
```python
def predict_position(planet, initial_by_id, ang_vel, turns):
    r = dist(planet_init, center)
    cur_angle = atan2(planet.y - 50, planet.x - 50)
    new_angle = cur_angle + ang_vel * turns
    return (50 + r*cos(new_angle), 50 + r*sin(new_angle))
```
**내행성 판별**: `dist(init, center) + init.radius >= ROTATION_LIMIT` → 정적 행성

### 4. 반복 조준 수렴 (Iterative Aim) ❌
현재 위치만 조준하는 것이 아닌, 도착 예정 위치를 반복 계산으로 수렴:
```python
for _ in range(6):  # 최대 6회
    pred_x, pred_y = predict_position(target, ang_vel, turns)
    new_angle, new_turns = estimate_arrival(src, pred_x, pred_y, ships)
    if abs(new_turns - turns) <= 2 and dist_diff < 0.6:
        break  # 수렴
    turns = new_turns
```

### 5. 이동 중 생산량 보정 ❌
```python
# 적 행성: garrison + production × turns
# 중립 행성: garrison 그대로 (owner == -1은 생산 없음)
ships_needed = target.ships + (target.production * turns if target.owner >= 0 else 0) + 1
```

### 6. 수비 (Defense) ❌
적 함대가 내 행성을 향하는지 감지 → 도착 전 지원 발사.

---

## 에이전트별 고유 기법

### lb-958 (최상위 계획 에이전트)

**Binary Search 최소 방어 함대**: "k척을 지키면 공격을 버티는가?" 시뮬레이션 → binary search.

**Indirect Wealth**: 행성의 전략 가치 = 주변 production 가중합
```python
wealth += other.production / (distance + 12.0)
# 적 이웃: 1.25x, 중립 이웃: 0.90x, 아군 이웃: 0.35x
```

**3-source 협공**: garrison ≥ 20 목표를 3행성이 1턴 이내 동시 도착.

**Crash Exploit**: 두 적 함대가 2턴 이내 같은 행성 도착 예정이면, 충돌 직후 빈 행성 점령.

**Doomed Evacuation**: 수학적으로 잃을 행성에서 함대를 미리 빼서 다른 행성으로 이동.

---

### proto-v15 (~1000점)

**커스텀 타깃 점수**:
```python
score = (100 - dist) + (15 * production) + (10 * enemy_bonus) - (0.7 * ships_needed) - (2 * eta)
# enemy_bonus = 10 if target.owner >= 0 else 0
```

**반복 필요 함대 계산 (3회)**:
```python
for _ in range(3):
    turns = travel_time(dist, ships_needed)
    new_needed = target.ships + target.production * turns + 1
    if new_needed == ships_needed: break
    ships_needed = new_needed
```
함대 크기 → 속도 → 이동 시간 → 필요 함대가 순환하므로 수렴까지 반복.

**60턴 궤적 미리계산**: 내행성 60턴치 좌표를 배열로 저장 후 `miss_dist < planet.radius` 조건으로 도달 판단.

**플릿 추적 + 최소 함대 5척**: 이미 공격 중인 목표 중복 방지, 1~2척 소형 함대 차단.

---

### structured-baseline (구조화)

**경계 기반 발사 좌표**: 중심이 아닌 planet radius 외곽에서 발사 → 더 정확한 이동 시간.

**Commitment 전파**: 발사 결정 직후 world model 업데이트 → 다음 결정이 갱신된 상태 참조.

**Snipe (저격)**: 적 함대 도착 1턴 전 도착으로 상쇄. `anchor_turn = enemy_eta ± 1`.

**9가지 미션 타입 우선순위**:
`reinforce → rescue → recapture → capture → snipe → swarm → crash_exploit → doomed_evac → rear_funnel`

---

### agent-smith-1000lb (~1000점)

**게임 페이즈 인식**:
- Opening (< 14턴): K=3 타깃, max_travel=24
- Mid-game: K=2, max_travel=18
- Late-game (< 25턴 남음): 최소 overkill, 긴 hammer 사거리

**Accumulator 패턴**: "lead" 행성이 함대를 쌓고, "feeder" 행성들이 ships를 보내다가 임계치(230+)에서 일격.

**Effective Garrison 시뮬레이션**:
```python
def effective_garrison_at_arrival(target, travel_turns, world):
    # 이동 중 도착하는 적/아군 플릿을 순서대로 전투 해결
    # production 누적 포함
    for eta, fleet_owner, fleet_ships in sorted_arrivals:
        if fleet_owner == owner: ships += fleet_ships  # 지원
        else: ships = abs(ships - fleet_ships); ...    # 전투
```

**적 프로파일링**: 적의 production share, fleet 발사 빈도로 weakest enemy 식별.

**Personality 적응 시스템**: 적 행동 관찰 → aggression 0.10~0.30 범위 조정.

**뒤-태양 대기 (Behind-Sun Wait)**: 직접 경로가 태양에 막히면 2~10턴 후 공전으로 경로가 열릴 때까지 대기.

**Melis Sanity Check**: 최선의 행동 이득 < 1.5이면 아무것도 하지 않음 (낭비 방지).

---

### lb-1050 + heuristic-simulation (최상위, 동일 코드베이스)

**Depth-2 탐색 (Budget-Gated)**:
```python
# Depth-1: 내 행동 평가
# Depth-2: 상대 최선 응수 + 패널티 (top-3 행동에만 적용)
if time.perf_counter() < deadline * 0.70:  # 70% 예산 이내만
    act["score"] += depth2_penalty(world, act)
```

**가중 스냅샷 평가 (1/t 가중치)**:
```python
snap_turns = (4, 8, 14, 20)
for t in snap_turns:
    w = 1.0 / t  # 4턴 스냅샷 = 20턴 스냅샷의 5배 신뢰
    total += forward_score(snaps[t]) * w
```
20턴 후 상태보다 4턴 후 상태가 훨씬 신뢰도 높음 → 가중치로 보정.

**Forward Score 가중치**:
```python
score = ships_diff + 5 * planets_diff + 8 * production_diff
# production 1 ≈ ships 8개 가치
```

**Multiprong (강제 딜레마)**: 주 타깃(hammer) 공격 동시에 그 지원 행성을 부 타깃으로 공격.
→ 상대는 hammer 타깃 방어 OR 부 타깃 방어 중 하나만 선택 가능.

**플릿 타깃 캐싱**: O(T×P) → O(1). `(fleet_id, step)` 키로 매 턴 자동 무효화.

**Endgame ROI Filter**:
```python
# 남은 턴 × production > 점령 비용이어야만 공격
remaining_after = remaining_steps - travel_turns
return target.production * remaining_after > target.ships
```

**2P vs 4P 파라미터 자동 전환**: 플레이어 수 감지 → 확장 K, 공격 overkill 비율, 방어 예비 등 전체 파라미터 교체.

---

## 우선순위 개선 로드맵

### v2 (단기, 구현 쉬움 + 효과 큼)

| 기법 | 출처 | 난이도 |
|------|------|--------|
| 타깃 점수 공식 `production/dist + enemy_bonus` | proto | ★☆☆ |
| 이동 시간 × 생산량 보정 (중립 제외) | 전체 공통 | ★☆☆ |
| 최소 함대 크기 5척 | proto | ★☆☆ |
| 중복 공격 방지 (`targeted` set) | proto | ★☆☆ |
| 반복 필요 함대 계산 (3회 수렴) | proto | ★★☆ |
| 공전 행성 도착 위치 반복 조준 | 전체 공통 | ★★☆ |

### v3 (중기)

| 기법 | 출처 | 난이도 |
|------|------|--------|
| 수비 (incoming fleet 감지 + 지원 발사) | 전체 공통 | ★★☆ |
| Effective garrison 시뮬레이션 | smith, lb-1050 | ★★★ |
| 2-source 협공 (swarm) | 전체 공통 | ★★★ |
| Doomed Evacuation | lb-958, structured | ★★★ |
| Commitment 전파 | structured | ★★★ |

### v4 (장기)

| 기법 | 출처 | 난이도 |
|------|------|--------|
| Forward simulation (20턴 lookahead) | lb-1050, smith | ★★★★ |
| 가중 스냅샷 평가 (1/t) | lb-1050 | ★★★★ |
| Depth-2 탐색 + budget gating | lb-1050 | ★★★★ |
| Multiprong 강제 딜레마 | lb-1050 | ★★★★ |
| Accumulator 패턴 | smith | ★★★★ |
| 적 충돌 이용 (Crash Exploit) | lb-958 | ★★★★ |
| Binary search 최소 방어 함대 | lb-958 | ★★★★ |

---

## v2 구현 스케치

```python
targeted = set()

for mine in sorted(my_planets, key=lambda p: p.ships, reverse=True):
    best_score, best_t, best_ships, best_angle = -inf, None, 0, 0

    for t in targets:
        if t.id in targeted:
            continue

        dist_cur = hypot(mine.x - t.x, mine.y - t.y)

        # 반복 수렴: 필요 함대 → 속도 → 이동시간 → 필요 함대
        ships_needed = t.ships + 1
        for _ in range(3):
            turns = dist_cur / fleet_speed(ships_needed)
            garrison = t.ships if t.owner < 0 else t.ships + t.production * turns
            new_needed = int(garrison) + 1
            if new_needed == ships_needed:
                break
            ships_needed = new_needed

        if mine.ships < max(ships_needed, 5):
            continue

        # 공전 행성 반복 조준
        tx, ty = t.x, t.y
        for _ in range(6):
            pred_x, pred_y = predict_position(t, ang_vel, turns)
            new_dist = hypot(mine.x - pred_x, mine.y - pred_y)
            new_turns = new_dist / fleet_speed(ships_needed)
            if abs(new_turns - turns) <= 2:
                tx, ty = pred_x, pred_y
                break
            turns = new_turns

        if path_hits_sun(mine.x, mine.y, tx, ty):
            continue

        # proto 스타일 점수
        enemy_bonus = 10 if t.owner >= 0 else 0
        score = (100 - dist_cur) + 15 * t.production + enemy_bonus \
                - 0.7 * ships_needed - 2 * turns

        if score > best_score:
            best_score, best_t = score, t
            best_ships = ships_needed
            best_angle = atan2(ty - mine.y, tx - mine.x)

    if best_t:
        moves.append([mine.id, best_angle, best_ships])
        targeted.add(best_t.id)
```
