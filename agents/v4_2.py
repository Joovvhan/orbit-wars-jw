"""
v4_2: v3 + 검증된 두 가지 추가만
1. 엔드게임 ROI 필터 (lb-1050 방식): 남은 턴 * production < ships_needed → 공격 안 함
2. 적 행성 점령 우선: enemy_bonus를 거리에 따라 추가 보정
v3의 while-loop + greedy 점수 구조는 그대로 유지
"""
import math

try:
    from kaggle_environments.envs.orbit_wars.orbit_wars import Planet, ROTATION_RADIUS_LIMIT
except ImportError:
    from collections import namedtuple
    Planet = namedtuple("Planet", ["id", "owner", "x", "y", "radius", "ships", "production"])
    ROTATION_RADIUS_LIMIT = 40.0

DEBUG = False

SUN_X, SUN_Y, SUN_R = 50.0, 50.0, 10.0
MAX_SPEED = 6.0
MIN_FLEET = 5
MAX_TURNS = 500


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


def predict_position(p: Planet, ang_vel: float, turns: float):
    dx, dy = p.x - SUN_X, p.y - SUN_Y
    r = math.hypot(dx, dy)
    if r + p.radius < ROTATION_RADIUS_LIMIT and ang_vel != 0:
        angle = math.atan2(dy, dx) + ang_vel * turns
        return SUN_X + r * math.cos(angle), SUN_Y + r * math.sin(angle)
    return p.x, p.y


def aim_iterative(mine: Planet, target: Planet, ships: int, ang_vel: float):
    """v2/v3 방식 그대로."""
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


def target_score(dist: float, production: int, ships_needed: int,
                 turns: float, is_enemy: bool) -> float:
    """v3와 동일한 proto-v15 점수."""
    enemy_bonus = 10 if is_enemy else 0
    return (100 - dist) + 15 * production + enemy_bonus - 0.7 * ships_needed - 2 * turns


def agent(obs):
    moves = []

    player  = obs.get("player", 0)            if isinstance(obs, dict) else obs.player
    raw_pl  = obs.get("planets", [])           if isinstance(obs, dict) else obs.planets
    ang_vel = obs.get("angular_velocity", 0.0) if isinstance(obs, dict) else obs.angular_velocity
    step    = obs.get("step", 0)               if isinstance(obs, dict) else getattr(obs, "step", 0)

    remaining_turns = MAX_TURNS - (step or 0)

    planets    = [Planet(*p) for p in raw_pl]
    my_planets = sorted([p for p in planets if p.owner == player],
                        key=lambda p: p.ships, reverse=True)
    targets    = [p for p in planets if p.owner != player]

    if not targets or not my_planets:
        return moves

    targeted      = set()
    pending_ships = {p.id: 0 for p in my_planets}

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
                dist   = math.hypot(mine.x - t.x, mine.y - t.y)
                # available(pending 차감)으로 aim하면 속도가 느려져 garrison이 커지는 악순환
                # → mine.ships 기준으로 속도 계산
                result = aim_iterative(mine, t, mine.ships, ang_vel)
                if result is None:
                    continue
                angle, ships_needed, turns = result
                if available < ships_needed:
                    continue

                # 엔드게임 ROI 필터 (lb-1050): 점령 후 생산이 비용을 못 회수하면 스킵
                # 단, 적 행성은 production 탈취 효과도 있으므로 절반 기준 적용
                remaining_after = remaining_turns - turns
                if remaining_after > 0:
                    roi_threshold = ships_needed if not (t.owner >= 0) else ships_needed / 2
                    if t.production * remaining_after < roi_threshold and remaining_turns < 100:
                        continue  # 엔드게임에서만 필터 (100턴 미만)

                score = target_score(dist, t.production, ships_needed,
                                     turns, t.owner >= 0)

                if score > best_score:
                    best_score  = score
                    best_result = (t, angle, ships_needed)

            if not best_result:
                break

            t, angle, ships_needed = best_result
            moves.append([mine.id, angle, ships_needed])
            pending_ships[mine.id] += ships_needed
            targeted.add(t.id)

            if DEBUG:
                print(f"[SEND] {mine.id}->{t.id}: {ships_needed} ships score={best_score:.1f}")

    return moves
