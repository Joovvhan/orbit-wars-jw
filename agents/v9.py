"""
v9: v8 + 동시 도착 협공(Simultaneous Coop Attack)

핵심 전략:
- fleet_speed 역산으로 각 소스 행성이 목표 턴 T에 정확히 도착하는 함대 크기 계산
- T = max(각 행성의 최소 도착 턴): 모두 도달 가능한 가장 이른 공통 턴
- 단독 점령 불가 타깃에만 적용 (attack_targeted에 없는 타깃)
- v8의 모든 기능 유지 (evac, defense, single attack, endgame ROI, phase score)

동시 도착 원리:
  fleet_speed(ships) = 1 + 5*(log(ships)/log(1000))^1.5
  ships = 1000^(((speed-1)/5)^(2/3))  ← 역산
  required_speed = dist / T → ships_i = inverse(required_speed)
  모든 소스가 동일 T에 도착 → 합산 전투 (협공 성립)
"""
import math
from collections import defaultdict
from typing import Optional
from dataclasses import dataclass, field

try:
    from kaggle_environments.envs.orbit_wars.orbit_wars import ROTATION_RADIUS_LIMIT
except ImportError:
    ROTATION_RADIUS_LIMIT = 40.0

DEBUG = False

SUN_X, SUN_Y, SUN_R = 50.0, 50.0, 10.0
MAX_SPEED = 6.0
MIN_FLEET = 5

from collections import namedtuple
Planet = namedtuple("Planet", ["id", "owner", "x", "y", "radius", "ships", "production"])
Fleet  = namedtuple("Fleet",  ["id", "owner", "x", "y", "angle", "from_planet_id", "ships"])


# ─── 물리/기하 헬퍼 ────────────────────────────────────────────────────────────

def fleet_speed(ships: int) -> float:
    if ships <= 1:
        return 1.0
    return 1.0 + (MAX_SPEED - 1.0) * (math.log(ships) / math.log(1000)) ** 1.5


def travel_turns(dist: float, ships: int) -> float:
    return dist / fleet_speed(max(1, ships))


def path_hits_sun(x1, y1, x2, y2) -> bool:
    dx, dy = x2 - x1, y2 - y1
    fx, fy = x1 - SUN_X, y1 - SUN_Y
    a = dx * dx + dy * dy
    if a == 0:
        return (fx * fx + fy * fy) < SUN_R * SUN_R
    b = 2 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - SUN_R * SUN_R
    disc = b * b - 4 * a * c
    if disc < 0:
        return False
    sq = math.sqrt(disc)
    t1 = (-b - sq) / (2 * a)
    t2 = (-b + sq) / (2 * a)
    return (0 <= t1 <= 1) or (0 <= t2 <= 1)


def predict_position(p, ang_vel: float, turns: float):
    dx, dy = p.x - SUN_X, p.y - SUN_Y
    r = math.hypot(dx, dy)
    if r + p.radius < ROTATION_RADIUS_LIMIT and ang_vel != 0:
        angle = math.atan2(dy, dx) + ang_vel * turns
        return SUN_X + r * math.cos(angle), SUN_Y + r * math.sin(angle)
    return p.x, p.y


# ─── 동시 도착 역산 함수 ────────────────────────────────────────────────────────

def ships_for_eta(dist: float, target_eta: int) -> Optional[int]:
    """
    dist를 정확히 target_eta 턴에 도착하는 최소 함대 크기를 반환.
    불가능하면 None.

    원리: ETA = ceil(dist/speed), speed는 ships에 단조증가.
    이진탐색으로 ETA <= target_eta가 되는 최소 ships 탐색.
    """
    if target_eta <= 0 or dist <= 0:
        return None

    # 최대 속도로도 늦으면 불가
    if math.ceil(dist / MAX_SPEED) > target_eta:
        return None

    # 1척(최소 속도)으로도 너무 빠르면 불가 (T턴에 도착시킬 방법 없음)
    if math.ceil(dist / fleet_speed(1)) < target_eta:
        return None

    # 이진탐색: ETA <= target_eta인 최소 ships
    lo, hi = 1, 1000
    while lo < hi:
        mid = (lo + hi) // 2
        if math.ceil(dist / fleet_speed(mid)) <= target_eta:
            hi = mid
        else:
            lo = mid + 1

    if math.ceil(dist / fleet_speed(lo)) == target_eta:
        return lo
    return None  # ETA = target_eta인 ships가 없음 (jump over T)


# ─── World: obs 파싱 + 도착 스케줄 ────────────────────────────────────────────

class World:
    def __init__(self, obs):
        _r = lambda key, default: (obs.get(key, default) if isinstance(obs, dict)
                                   else getattr(obs, key, default))

        self.player  = _r("player", 0)
        self.step    = _r("step", 0) or 0
        self.ang_vel = _r("angular_velocity", 0.0) or 0.0

        raw_planets = _r("planets", []) or []
        raw_fleets  = _r("fleets",  []) or []

        self.planets    = [Planet(*p) for p in raw_planets]
        self.fleets     = [Fleet(*f)  for f in raw_fleets]
        self.planet_map = {p.id: p for p in self.planets}

        self.arrivals_by_planet: dict = defaultdict(list)
        self._build_arrivals()

    def _build_arrivals(self):
        for f in self.fleets:
            target_id = self._find_fleet_target(f)
            if target_id is None:
                continue
            tgt  = self.planet_map[target_id]
            dist = math.hypot(f.x - tgt.x, f.y - tgt.y)
            eta  = math.ceil(dist / fleet_speed(f.ships))
            self.arrivals_by_planet[target_id].append((int(eta), int(f.owner), int(f.ships)))

        for pid in self.arrivals_by_planet:
            self.arrivals_by_planet[pid].sort()

    def _find_fleet_target(self, fleet) -> Optional[int]:
        """경로 추적으로 첫 번째로 닿는 행성을 반환 (false positive 방지)."""
        spd = fleet_speed(fleet.ships)
        cos_a = math.cos(fleet.angle)
        sin_a = math.sin(fleet.angle)
        for tick in range(1, 120):
            fx = fleet.x + cos_a * spd * tick
            fy = fleet.y + sin_a * spd * tick
            for p in self.planets:
                if p.id == fleet.from_planet_id:
                    continue
                px, py = predict_position(p, self.ang_vel, tick)
                if math.hypot(fx - px, fy - py) <= p.radius + 0.5:
                    return p.id
        return None


# ─── 행성 타임라인 시뮬레이션 ──────────────────────────────────────────────────

@dataclass
class PlanetTimeline:
    pid: int
    holds: bool = True
    fall_turn: Optional[int] = None
    deficit: int = 0


def simulate_planet_timeline(planet: Planet, arrivals: list, player: int,
                              horizon: int = 60) -> PlanetTimeline:
    tl = PlanetTimeline(pid=planet.id)
    ships = float(planet.ships)
    owner = player
    arr_idx = 0
    n = len(arrivals)

    for turn in range(1, horizon + 1):
        friendly_arrive = 0
        enemy_arrive = 0
        while arr_idx < n and arrivals[arr_idx][0] == turn:
            _, arr_owner, arr_ships = arrivals[arr_idx]
            if arr_owner == player:
                friendly_arrive += arr_ships
            else:
                enemy_arrive += arr_ships
            arr_idx += 1

        if owner == player:
            ships += planet.production

        ships += friendly_arrive

        if enemy_arrive > 0:
            if owner == player:
                ships -= enemy_arrive
                if ships < 0:
                    owner = -999
                    tl.holds = False
                    tl.fall_turn = turn
                    tl.deficit = int(-ships) + 1
                    break
            else:
                break

    return tl


# ─── 조준 / 점수 ───────────────────────────────────────────────────────────────

def aim_iterative(mine, target, ships: int, ang_vel: float):
    dist_cur = math.hypot(mine.x - target.x, mine.y - target.y)
    turns = travel_turns(dist_cur, ships)
    tx, ty = target.x, target.y
    for _ in range(6):
        px, py = predict_position(target, ang_vel, turns)
        garrison = target.ships if target.owner < 0 else target.ships + target.production * turns
        new_ships = max(int(garrison) + 1, MIN_FLEET)
        new_dist  = math.hypot(mine.x - px, mine.y - py)
        new_turns = travel_turns(new_dist, new_ships)
        if abs(new_turns - turns) <= 2.0:
            tx, ty = px, py
            ships, turns = new_ships, new_turns
            break
        turns, ships, tx, ty = new_turns, new_ships, px, py
    if path_hits_sun(mine.x, mine.y, tx, ty):
        return None
    return math.atan2(ty - mine.y, tx - mine.x), ships, turns


def indirect_wealth(target, planets, player) -> float:
    wealth = 0.0
    for p in planets:
        if p.id == target.id:
            continue
        d = math.hypot(target.x - p.x, target.y - p.y)
        if d < 1:
            continue
        factor = p.production / (d + 12.0)
        if p.owner == player:
            wealth += factor * 0.35
        elif p.owner == -1:
            wealth += factor * 0.90
        else:
            wealth += factor * 1.25
    return wealth


TOTAL_STEPS = 500

def race_margin(target, my_planets, enemy_planets) -> float:
    my_min = min(
        travel_turns(math.hypot(m.x - target.x, m.y - target.y), max(1, int(m.ships)))
        for m in my_planets
    )
    if not enemy_planets:
        return my_min
    enemy_min = min(
        travel_turns(math.hypot(e.x - target.x, e.y - target.y), max(1, int(e.ships)))
        for e in enemy_planets
    )
    return enemy_min - my_min


def target_score(t, dist, ships_needed, turns, step,
                 margin=0.0, iwealth=0.0, neutral_ratio=1.0) -> float:
    remaining = max(1, TOTAL_STEPS - step)
    enemy_bonus = 10 if t.owner >= 0 else 0
    base = (100 - dist) + 15 * t.production + enemy_bonus \
           - 0.7 * ships_needed - 2 * turns + iwealth * 0.15

    if t.owner < 0:
        base += margin * 3.0
    else:
        phase_bonus = (1.0 - neutral_ratio) * 30.0
        base += phase_bonus

    remaining_after = max(0, remaining - turns)
    roi = t.production * remaining_after - ships_needed
    if roi < 0:
        base += roi * 0.5

    return base


# ─── 에이전트 ──────────────────────────────────────────────────────────────────

def agent(obs):
    moves = []
    world  = World(obs)
    player = world.player

    planets    = world.planets
    my_planets = [p for p in planets if p.owner == player]
    targets    = [p for p in planets if p.owner != player]

    if not my_planets:
        return moves

    pending_ships = {p.id: 0 for p in my_planets}

    # ── 1. 내 행성 타임라인 시뮬레이션 ────────────────────────────────────────
    timelines: dict[int, PlanetTimeline] = {}
    for p in my_planets:
        arrivals = world.arrivals_by_planet.get(p.id, [])
        timelines[p.id] = simulate_planet_timeline(p, arrivals, player)

    # ── 2. 도주(Evacuation) ────────────────────────────────────────────────────
    EVAC_HORIZON = 10
    EVAC_MIN_SHIPS = MIN_FLEET

    for p in sorted(my_planets, key=lambda x: timelines[x.id].fall_turn or 999):
        tl = timelines[p.id]
        if not tl.holds and tl.fall_turn is not None and tl.fall_turn <= EVAC_HORIZON:
            available = p.ships - pending_ships[p.id]
            if available < EVAC_MIN_SHIPS:
                continue

            evac_ships = available

            best_dest = None
            best_dist = math.inf
            for dest in my_planets:
                if dest.id == p.id:
                    continue
                if timelines[dest.id].holds:
                    d = math.hypot(p.x - dest.x, p.y - dest.y)
                    if d < best_dist:
                        best_dist = d
                        best_dest = dest

            if best_dest is None:
                continue

            angle = math.atan2(best_dest.y - p.y, best_dest.x - p.x)
            if not path_hits_sun(p.x, p.y, best_dest.x, best_dest.y):
                moves.append([p.id, angle, evac_ships])
                pending_ships[p.id] += evac_ships
                if DEBUG:
                    print(f"[EVAC] {p.id}->{best_dest.id}: {evac_ships} (fall@{tl.fall_turn})")

    # ── 3. 수비(Defense) ───────────────────────────────────────────────────────
    REINFORCE_MAX_ETA_MARGIN = 1

    threatened = []
    for p in my_planets:
        tl = timelines[p.id]
        if not tl.holds and tl.fall_turn is not None and tl.deficit > 0:
            threatened.append((p, tl))

    threatened.sort(key=lambda x: x[1].fall_turn)

    for threatened_p, tl in threatened:
        needed = tl.deficit
        fall_turn = tl.fall_turn

        donors = sorted(
            [p for p in my_planets if p.id != threatened_p.id],
            key=lambda p: math.hypot(p.x - threatened_p.x, p.y - threatened_p.y)
        )

        for donor in donors:
            available = donor.ships - pending_ships[donor.id]
            if available < MIN_FLEET:
                continue

            dist = math.hypot(donor.x - threatened_p.x, donor.y - threatened_p.y)
            eta = math.ceil(dist / fleet_speed(max(MIN_FLEET, needed)))

            if eta >= fall_turn - REINFORCE_MAX_ETA_MARGIN:
                continue

            reinforce = min(available, needed + 1)
            if reinforce < MIN_FLEET:
                continue

            tx, ty = predict_position(threatened_p, world.ang_vel, eta)
            angle = math.atan2(ty - donor.y, tx - donor.x)
            if path_hits_sun(donor.x, donor.y, tx, ty):
                continue

            moves.append([donor.id, angle, reinforce])
            pending_ships[donor.id] += reinforce
            needed = max(0, needed - reinforce)
            if DEBUG:
                print(f"[DEF] {donor.id}->{threatened_p.id}: {reinforce} (fall@{fall_turn})")
            if needed <= 0:
                break

    # ── 4. 공격 ────────────────────────────────────────────────────────────────
    if not targets:
        return moves

    my_planets_sorted = sorted(my_planets, key=lambda p: p.ships, reverse=True)

    my_inbound: dict = {}
    for pid, arrivals in world.arrivals_by_planet.items():
        total = sum(s for (_, owner, s) in arrivals if owner == player)
        if total > 0:
            my_inbound[pid] = total

    iwealth_cache = {t.id: indirect_wealth(t, planets, player) for t in targets}

    enemy_planets = [p for p in planets if p.owner not in (-1, player)]
    race_margins = {}
    for t in targets:
        if t.owner < 0:
            race_margins[t.id] = race_margin(t, my_planets, enemy_planets)

    n_neutral = sum(1 for t in targets if t.owner < 0)
    neutral_ratio = n_neutral / max(1, len(targets))

    committed: dict = {}
    attack_targeted = set()

    # ── 4a. 단일 행성 공격 ─────────────────────────────────────────────────────
    RACE_SKIP_THRESHOLD = -3
    for mine in my_planets_sorted:
        while True:
            available = mine.ships - pending_ships[mine.id]
            if available < MIN_FLEET:
                break

            best_score  = -math.inf
            best_result = None

            for t in targets:
                if t.id in attack_targeted:
                    continue

                dist = math.hypot(mine.x - t.x, mine.y - t.y)
                already = my_inbound.get(t.id, 0) + committed.get(t.id, 0)

                result = aim_iterative(mine, t, mine.ships, world.ang_vel)
                if result is None:
                    continue

                angle, ships_needed, turns = result

                if already >= ships_needed:
                    continue

                if available < ships_needed:
                    continue

                margin = race_margins.get(t.id, 0.0)
                if t.owner < 0 and margin < RACE_SKIP_THRESHOLD:
                    continue

                iw = iwealth_cache.get(t.id, 0.0)
                score = target_score(t, dist, ships_needed, turns, world.step,
                                     margin, iw, neutral_ratio)

                if score > best_score:
                    best_score  = score
                    best_result = (t, angle, ships_needed)

            if not best_result:
                break

            t, angle, ships_needed = best_result
            moves.append([mine.id, angle, ships_needed])
            pending_ships[mine.id] += ships_needed
            committed[t.id] = committed.get(t.id, 0) + ships_needed
            attack_targeted.add(t.id)

            if DEBUG:
                print(f"[ATK] {mine.id}->{t.id}: {ships_needed} ships")

    # ── 4b. 동시 도착 협공(Simultaneous Coop Attack) ───────────────────────────
    # 핵심: 모든 소스가 동일 턴 T에 도착 → 합산 전투 → 협공 성립
    # T 선택 전략: T를 증가시키면서 최초로 협공 가능한 최소 T를 찾음
    # 적용 조건: 중립 소진(후반 게임)에서만 활성화 - 초반 확장 방해 방지
    COOP_MIN_GARRISON  = 15   # 협공 대상 최소 garrison(T)
    COOP_MAX_T         = 20   # 너무 먼 미래까지 기다리지 않음
    COOP_NEUTRAL_RATIO = 0.35 # 중립 비율이 이 이하일 때만 협공 (후반 게임 조건)

    remaining_turns = max(1, TOTAL_STEPS - world.step)

    # 중립 행성이 많이 남아 있으면 협공 비활성화 (확장 우선)
    if neutral_ratio > COOP_NEUTRAL_RATIO:
        return moves

    for t in targets:
        if t.id in attack_targeted:
            continue

        # 협공은 적 행성만 대상 (중립은 단독 공격 or race_margin으로 처리)
        if t.owner < 0:
            continue

        # 소스 후보: 가용 함대가 있는 모든 아군 행성, min_eta 기준 정렬
        source_candidates = []
        for mine in my_planets:
            available = mine.ships - pending_ships[mine.id]
            if available < MIN_FLEET:
                continue
            dist_now = math.hypot(mine.x - t.x, mine.y - t.y)
            if dist_now == 0:
                continue
            min_eta = math.ceil(dist_now / MAX_SPEED)
            if min_eta > COOP_MAX_T:
                continue
            source_candidates.append((mine, available, min_eta))

        if len(source_candidates) < 2:
            continue

        # 최소 T 탐색: T를 min_eta 값들 중 가장 작은 것부터 시도
        # T = source_candidates[i].min_eta → 해당 소스 이후 모두 참여 가능
        source_candidates.sort(key=lambda c: c[2])  # min_eta 오름차순
        unique_T_values = sorted(set(c[2] for c in source_candidates))

        best_coop = None

        for T in unique_T_values:
            if T > COOP_MAX_T:
                break

            tx, ty = predict_position(t, world.ang_vel, T)
            # 안전 마진 +5: 적 증원 가능성 고려
            garrison_T = int(t.ships) + t.production * T + 5
            if garrison_T < COOP_MIN_GARRISON:
                continue

            # ROI: 점령 후 남은 생산 가치 > 투입 비용
            remaining_after = max(0, remaining_turns - T)
            if t.production * remaining_after < (garrison_T - 5) * 0.8:
                continue

            # 이미 날아가는 아군 함대 고려 (이전 턴 coop 포함)
            already = my_inbound.get(t.id, 0) + committed.get(t.id, 0)
            need = garrison_T - already
            if need <= 0:
                continue

            # T턴 예측 위치 기준으로 각 소스의 함대 크기 계산
            contributors = []
            for mine, available, _ in source_candidates:
                actual_dist = math.hypot(mine.x - tx, mine.y - ty)
                if actual_dist == 0:
                    continue

                ships_needed = ships_for_eta(actual_dist, T)
                if ships_needed is None or ships_needed < MIN_FLEET:
                    continue
                if ships_needed > available:
                    continue
                if path_hits_sun(mine.x, mine.y, tx, ty):
                    continue

                angle = math.atan2(ty - mine.y, tx - mine.x)
                contributors.append((mine, angle, ships_needed, available))

            if len(contributors) < 2:
                continue

            total_ships = sum(c[2] for c in contributors)
            if total_ships < need:
                if DEBUG:
                    print(f"[COOP-SKIP] target={t.id} T={T} garrison={garrison_T} "
                          f"need={need} avail={total_ships}")
                continue

            # 최소 T에서 협공 가능 → 저장 후 탐색 중단
            best_coop = (T, tx, ty, garrison_T, need, contributors)
            break

        if best_coop is None:
            continue

        T, tx, ty, garrison_T, need, contributors = best_coop

        # 최소 소스로 need 충족 (병력 많은 소스 우선, 비용 최소화)
        contributors_sorted = sorted(contributors, key=lambda c: c[2], reverse=True)
        remaining_need = need
        executed = []
        for mine, angle, ships_send, available in contributors_sorted:
            if remaining_need <= 0:
                break
            executed.append((mine, angle, ships_send))
            remaining_need -= ships_send

        if remaining_need > 0:
            continue

        # 진짜 협공: 2개 이상의 소스가 실제로 참여해야
        if len(executed) < 2:
            continue

        for mine, angle, ships_send in executed:
            moves.append([mine.id, angle, ships_send])
            pending_ships[mine.id] += ships_send
            committed[t.id] = committed.get(t.id, 0) + ships_send

        attack_targeted.add(t.id)

        if DEBUG:
            src_str = "+".join(f"{m.id}({s})" for m, _, s in executed)
            print(f"[COOP] target={t.id} T={T} garrison_T={garrison_T} "
                  f"need={need} sources={src_str}")

    return moves
