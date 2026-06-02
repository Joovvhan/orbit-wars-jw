"""
v2: 점수 공식 + 이동시간 보정 + 반복 조준 수렴 + 최소 함대 5척 + 중복 방지
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


def fleet_speed(ships: int) -> float:
    if ships <= 1:
        return 1.0
    return 1.0 + (MAX_SPEED - 1.0) * (math.log(ships) / math.log(1000)) ** 1.5


def travel_turns(dist: float, ships: int) -> float:
    return dist / fleet_speed(max(1, ships))


def path_hits_sun(x1: float, y1: float, x2: float, y2: float) -> bool:
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
    s = math.sqrt(disc)
    t1 = (-b - s) / (2 * a)
    t2 = (-b + s) / (2 * a)
    return (0 <= t1 <= 1) or (0 <= t2 <= 1)


def predict_position(p: Planet, angular_velocity: float, turns: float):
    """공전 행성의 turns 후 위치를 반환. 외행성은 현재 위치 그대로."""
    dx, dy = p.x - SUN_X, p.y - SUN_Y
    r = math.hypot(dx, dy)
    if r + p.radius < ROTATION_RADIUS_LIMIT and angular_velocity != 0:
        cur_angle = math.atan2(dy, dx)
        new_angle = cur_angle + angular_velocity * turns
        return SUN_X + r * math.cos(new_angle), SUN_Y + r * math.sin(new_angle)
    return p.x, p.y


def aim_iterative(mine: Planet, target: Planet, ships: int, angular_velocity: float):
    """
    반복 수렴으로 도착 위치와 필요 함대를 동시에 결정.
    - 도착 위치: 공전 행성이면 turns 후 예측 위치
    - 필요 함대: 적 행성이면 garrison + production * turns (중립은 garrison)
    최대 6회 반복, 2턴 이내 수렴하면 종료.
    태양 충돌이면 None 반환.
    """
    dist_cur = math.hypot(mine.x - target.x, mine.y - target.y)
    turns = travel_turns(dist_cur, ships)

    tx, ty = target.x, target.y

    for _ in range(6):
        # 예측 위치
        px, py = predict_position(target, angular_velocity, turns)

        # 필요 함대 갱신 (중립은 생산 없음)
        if target.owner < 0:
            garrison = target.ships
        else:
            garrison = target.ships + target.production * turns
        new_ships = max(int(garrison) + 1, MIN_FLEET)

        # 새 이동 시간
        new_dist = math.hypot(mine.x - px, mine.y - py)
        new_turns = travel_turns(new_dist, new_ships)

        if abs(new_turns - turns) <= 2.0:
            tx, ty = px, py
            ships = new_ships
            turns = new_turns
            break
        turns = new_turns
        ships = new_ships
        tx, ty = px, py

    if path_hits_sun(mine.x, mine.y, tx, ty):
        return None

    angle = math.atan2(ty - mine.y, tx - mine.x)
    return angle, ships, turns


def target_score(dist: float, production: int, ships_needed: int,
                 turns: float, is_enemy: bool) -> float:
    """
    proto-v15 스타일 점수. 높을수록 우선.
    (100-dist) + 15*production + 10*enemy_bonus - 0.7*ships_needed - 2*turns
    """
    enemy_bonus = 10 if is_enemy else 0
    return (100 - dist) + 15 * production + enemy_bonus - 0.7 * ships_needed - 2 * turns


def agent(obs):
    moves = []

    player   = obs.get("player", 0)            if isinstance(obs, dict) else obs.player
    raw_pl   = obs.get("planets", [])           if isinstance(obs, dict) else obs.planets
    ang_vel  = obs.get("angular_velocity", 0.0) if isinstance(obs, dict) else obs.angular_velocity

    planets    = [Planet(*p) for p in raw_pl]
    my_planets = sorted([p for p in planets if p.owner == player],
                        key=lambda p: p.ships, reverse=True)
    targets    = [p for p in planets if p.owner != player]

    if not targets or not my_planets:
        return moves

    targeted = set()  # 이번 턴 이미 목표로 지정된 행성

    for mine in my_planets:
        best_score  = -math.inf
        best_result = None  # (target, angle, ships_needed)

        for t in targets:
            if t.id in targeted:
                continue

            dist = math.hypot(mine.x - t.x, mine.y - t.y)
            result = aim_iterative(mine, t, mine.ships, ang_vel)
            if result is None:
                continue  # 태양 충돌

            angle, ships_needed, turns = result

            if mine.ships < ships_needed:
                if DEBUG:
                    print(f"[SKIP ships] {mine.id}->{t.id}: need {ships_needed}, have {mine.ships}")
                continue

            score = target_score(dist, t.production, ships_needed,
                                 turns, t.owner >= 0)

            if score > best_score:
                best_score  = score
                best_result = (t, angle, ships_needed)

        if best_result:
            t, angle, ships_needed = best_result
            moves.append([mine.id, angle, ships_needed])
            targeted.add(t.id)
            if DEBUG:
                print(f"[SEND] {mine.id}->{t.id}: {ships_needed} ships score={best_score:.1f}")

    return moves
