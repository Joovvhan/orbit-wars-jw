"""
v6: v5 + 수비(Defense) + 도주(Evacuation)
- 위협받는 내 행성에 가장 가까운 아군 행성에서 증원 발사
- 수학적으로 잃을 행성(doomed)에서 함대를 미리 대피
- 수비 처리 후 남은 함대로 공격 (v5 로직 유지)
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

        # arrivals_by_planet[pid] = [(eta, owner, ships), ...]
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
    """내 행성이 미래에 살아남는지 시뮬레이션."""
    pid: int
    holds: bool = True           # 현재 arrivals로 버티는가
    fall_turn: Optional[int] = None  # 함락되는 턴 (없으면 None)
    deficit: int = 0             # 함락 직전 부족 함대 수


def simulate_planet_timeline(planet: Planet, arrivals: list, player: int,
                              horizon: int = 60) -> PlanetTimeline:
    """
    arrivals: [(eta, owner, ships), ...] sorted by eta
    planet.owner == player 가정.
    """
    tl = PlanetTimeline(pid=planet.id)
    ships = float(planet.ships)
    owner = player
    arr_idx = 0
    n = len(arrivals)

    for turn in range(1, horizon + 1):
        # 이 턴에 도착하는 함대 처리
        friendly_arrive = 0
        enemy_arrive = 0
        while arr_idx < n and arrivals[arr_idx][0] == turn:
            _, arr_owner, arr_ships = arrivals[arr_idx]
            if arr_owner == player:
                friendly_arrive += arr_ships
            else:
                enemy_arrive += arr_ships
            arr_idx += 1

        # 생산 (소유 중일 때만)
        if owner == player:
            ships += planet.production

        # 아군 증원
        ships += friendly_arrive

        # 적 공격
        if enemy_arrive > 0:
            if owner == player:
                ships -= enemy_arrive
                if ships < 0:
                    owner = -999  # 함락
                    tl.holds = False
                    tl.fall_turn = turn
                    tl.deficit = int(-ships) + 1
                    break
            else:
                # 이미 적 소유 — 이 함수는 내 행성만 처리하므로 도달 안 함
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
    """타깃 행성의 전략적 위치 가치 = 주변 행성 생산량 가중합."""
    wealth = 0.0
    for p in planets:
        if p.id == target.id:
            continue
        d = math.hypot(target.x - p.x, target.y - p.y)
        if d < 1:
            continue
        factor = p.production / (d + 12.0)
        if p.owner == player:
            wealth += factor * 0.35    # 아군 이웃: 낮은 가중치
        elif p.owner == -1:
            wealth += factor * 0.90    # 중립 이웃
        else:
            wealth += factor * 1.25    # 적 이웃: 높은 가중치
    return wealth


def target_score(dist, production, ships_needed, turns, is_enemy, iwealth=0.0) -> float:
    enemy_bonus = 10 if is_enemy else 0
    return (100 - dist) + 15 * production + enemy_bonus - 0.7 * ships_needed - 2 * turns + iwealth * 0.15


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

    # pending_ships[pid]: 이번 턴 이미 발사 예약된 함대
    pending_ships = {p.id: 0 for p in my_planets}

    # ── 1. 내 행성 타임라인 시뮬레이션 ────────────────────────────────────────
    timelines: dict[int, PlanetTimeline] = {}
    for p in my_planets:
        arrivals = world.arrivals_by_planet.get(p.id, [])
        timelines[p.id] = simulate_planet_timeline(p, arrivals, player)

    # ── 2. 도주(Evacuation): 잃을 행성 함대 대피 ──────────────────────────────
    # 도주 대상: 함락이 확실한 행성, 10턴 이내 함락 예정
    EVAC_HORIZON = 10
    EVAC_MIN_SHIPS = MIN_FLEET

    for p in sorted(my_planets, key=lambda x: timelines[x.id].fall_turn or 999):
        tl = timelines[p.id]
        if not tl.holds and tl.fall_turn is not None and tl.fall_turn <= EVAC_HORIZON:
            available = p.ships - pending_ships[p.id]
            if available < EVAC_MIN_SHIPS:
                continue

            evac_ships = available

            # 가장 가까운 안전 아군 행성으로 후퇴
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

    # ── 3. 수비(Defense): 위협받는 행성 증원 ──────────────────────────────────
    REINFORCE_MAX_ETA_MARGIN = 1  # 적 도착 1턴 전까지 증원 가능

    # 위협 행성 목록: 함락되지만 증원으로 구할 수 있는 것
    threatened = []
    for p in my_planets:
        tl = timelines[p.id]
        if not tl.holds and tl.fall_turn is not None and tl.deficit > 0:
            threatened.append((p, tl))

    threatened.sort(key=lambda x: x[1].fall_turn)

    for threatened_p, tl in threatened:
        needed = tl.deficit
        fall_turn = tl.fall_turn

        # 가장 가까운 아군 행성에서 증원 찾기
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

            # 도착이 함락 전이어야 함 (1턴 여유)
            if eta >= fall_turn - REINFORCE_MAX_ETA_MARGIN:
                continue

            reinforce = min(available, needed + 1)
            if reinforce < MIN_FLEET:
                continue

            # 위협 행성 위치 예측
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

    # ── 4. 공격: indirect wealth + multiprong 포함 ────────────────────────────
    if not targets:
        return moves

    my_planets_sorted = sorted(my_planets, key=lambda p: p.ships, reverse=True)

    # 비행 중 아군 함대 집계
    my_inbound: dict = {}
    for pid, arrivals in world.arrivals_by_planet.items():
        total = sum(s for (_, owner, s) in arrivals if owner == player)
        if total > 0:
            my_inbound[pid] = total

    # indirect wealth 미리 계산 (타깃별 1회)
    iwealth_cache = {t.id: indirect_wealth(t, planets, player) for t in targets}

    committed: dict = {}
    attack_targeted = set()

    # ── 4a. 단일 행성 공격 ─────────────────────────────────────────────────────
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

                iw = iwealth_cache.get(t.id, 0.0)
                score = target_score(dist, t.production, ships_needed, turns,
                                     t.owner >= 0, iw)

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

    return moves
