import math

try:
    from kaggle_environments.envs.orbit_wars.orbit_wars import Planet
except ImportError:
    from collections import namedtuple
    Planet = namedtuple("Planet", ["id", "owner", "x", "y", "radius", "ships", "production"])

DEBUG = False

SUN_X, SUN_Y, SUN_R = 50.0, 50.0, 10.0
MAX_SPEED = 6.0


def fleet_speed(ships: int) -> float:
    if ships <= 1:
        return 1.0
    return 1.0 + (MAX_SPEED - 1.0) * (math.log(ships) / math.log(1000)) ** 1.5


def path_hits_sun(x1: float, y1: float, x2: float, y2: float) -> bool:
    """Ray-circle intersection: returns True if the segment (x1,y1)→(x2,y2) passes through the sun."""
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
    sqrt_disc = math.sqrt(disc)
    t1 = (-b - sqrt_disc) / (2 * a)
    t2 = (-b + sqrt_disc) / (2 * a)
    return (0 <= t1 <= 1) or (0 <= t2 <= 1)


def agent(obs):
    moves = []

    player = obs.get("player", 0) if isinstance(obs, dict) else obs.player
    raw_planets = obs.get("planets", []) if isinstance(obs, dict) else obs.planets

    planets = [Planet(*p) for p in raw_planets]
    my_planets = [p for p in planets if p.owner == player]
    targets = [p for p in planets if p.owner != player]

    if not targets or not my_planets:
        return moves

    for mine in my_planets:
        # Find nearest valid target (sun-safe)
        candidates = sorted(targets, key=lambda t: math.hypot(mine.x - t.x, mine.y - t.y))

        for t in candidates:
            if path_hits_sun(mine.x, mine.y, t.x, t.y):
                if DEBUG:
                    print(f"[SKIP sun] {mine.id} -> {t.id}")
                continue

            ships_needed = t.ships + 1
            if mine.ships < ships_needed:
                if DEBUG:
                    print(f"[SKIP ships] {mine.id} -> {t.id}: need {ships_needed}, have {mine.ships}")
                continue

            angle = math.atan2(t.y - mine.y, t.x - mine.x)
            moves.append([mine.id, angle, ships_needed])
            if DEBUG:
                print(f"[SEND] {mine.id} -> {t.id}: {ships_needed} ships")
            break

    return moves
