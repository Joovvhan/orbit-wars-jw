"""
v5: v3 + 비행 중 아군 함대 추적으로 중복 공격 제거
- arrivals_by_planet으로 이미 충분한 함대가 향하는 타깃 스킵
- v3의 32% 낭비 중복 공격 → 새 타깃 공격으로 전환
- aim_iterative / score 공식은 v3와 동일 (검증된 코드)
- simulator.py 의존성 제거: Kaggle 환경 호환을 위해 필요 코드 인라인 포함
"""
import math
from collections import defaultdict, namedtuple
from typing import Optional

try:
    from kaggle_environments.envs.orbit_wars.orbit_wars import ROTATION_RADIUS_LIMIT
except ImportError:
    ROTATION_RADIUS_LIMIT = 40.0

DEBUG = False

SUN_X, SUN_Y, SUN_R = 50.0, 50.0, 10.0
MAX_SPEED = 6.0
MIN_FLEET = 5

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
    """obs 파싱 + arrivals_by_planet 자동 계산 (simulator.py 인라인 버전)."""

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
        best_diff = 0.25
        best_id   = None
        for p in self.planets:
            if p.id == fleet.from_planet_id:
                continue
            angle_to = math.atan2(p.y - fleet.y, p.x - fleet.x)
            diff = abs(math.atan2(math.sin(fleet.angle - angle_to),
                                  math.cos(fleet.angle - angle_to)))
            if diff < best_diff:
                best_diff = diff
                best_id   = p.id
        return best_id


# ─── 에이전트 로직 ─────────────────────────────────────────────────────────────

def aim_iterative(mine, target, ships: int, ang_vel: float):
    """v3 방식 그대로 — 검증된 코드."""
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


def target_score(dist, production, ships_needed, turns, is_enemy) -> float:
    """v3 점수 공식 그대로."""
    enemy_bonus = 10 if is_enemy else 0
    return (100 - dist) + 15 * production + enemy_bonus - 0.7 * ships_needed - 2 * turns


def agent(obs):
    moves = []

    world  = World(obs)
    player = world.player

    planets    = world.planets
    my_planets = sorted([p for p in planets if p.owner == player],
                        key=lambda p: p.ships, reverse=True)
    targets    = [p for p in planets if p.owner != player]

    if not targets or not my_planets:
        return moves

    # 이미 비행 중인 아군 함대 집계: my_inbound[target_id] = 합산 ships
    # v3의 32% 중복 공격을 막기 위한 핵심 데이터
    my_inbound: dict = {}
    for pid, arrivals in world.arrivals_by_planet.items():
        total = sum(s for (_, owner, s) in arrivals if owner == player)
        if total > 0:
            my_inbound[pid] = total

    committed: dict = {}      # target_id → ships committed this turn
    pending_ships = {p.id: 0 for p in my_planets}
    targeted = set()

    for mine in my_planets:
        while True:
            available = mine.ships - pending_ships[mine.id]
            if available < MIN_FLEET:
                break

            best_score  = -math.inf
            best_result = None

            for t in targets:
                if t.id in targeted:
                    continue

                dist = math.hypot(mine.x - t.x, mine.y - t.y)

                # 이미 비행 중인 아군 + 이번 턴 커밋 합산
                already = my_inbound.get(t.id, 0) + committed.get(t.id, 0)

                # available(pending 차감)으로 aim하면 속도가 느려져
                # garrison이 커지는 악순환 → mine.ships 기준으로 속도 계산
                result = aim_iterative(mine, t, mine.ships, world.ang_vel)
                if result is None:
                    continue

                angle, ships_needed, turns = result

                # 이미 충분한 함대가 향하면 스킵 (v3 대비 핵심 개선)
                # 부족분만 보내면 소수 함대가 느리게 도착해 garrison이 더 커지는 문제 발생
                # → skip만 하고, 보낼 때는 항상 full ships_needed 발사
                if already >= ships_needed:
                    continue

                if available < ships_needed:
                    continue

                score = target_score(dist, t.production, ships_needed, turns, t.owner >= 0)

                if score > best_score:
                    best_score  = score
                    best_result = (t, angle, ships_needed)

            if not best_result:
                break

            t, angle, ships_needed = best_result
            moves.append([mine.id, angle, ships_needed])
            pending_ships[mine.id] += ships_needed
            committed[t.id] = committed.get(t.id, 0) + ships_needed
            targeted.add(t.id)

            if DEBUG:
                print(f"[SEND] {mine.id}->{t.id}: {ships_needed} ships score={best_score:.1f}")

    return moves
