"""
orbit_wars forward simulator — 재사용 가능한 모듈

사용법:
    from simulator import World, simulate, score_state, fleet_speed, aim

    world = World(obs)
    # 행동 후보 평가
    action = {"src_id": 3, "target_id": 7, "ships": 20}
    final_state = simulate(world, [action], horizon=8)
    s = score_state(final_state, world.player)

    # 스냅샷 평가 (lb-1050 방식)
    score = snapshot_score(world, [action], snap_turns=(4, 8, 14, 20))
"""

import math
from collections import defaultdict, namedtuple
from typing import Optional

try:
    from kaggle_environments.envs.orbit_wars.orbit_wars import ROTATION_RADIUS_LIMIT
except ImportError:
    ROTATION_RADIUS_LIMIT = 40.0

# ─── 기본 타입 ─────────────────────────────────────────────────────────────────

Planet = namedtuple("Planet", ["id", "owner", "x", "y", "radius", "ships", "production"])
Fleet  = namedtuple("Fleet",  ["id", "owner", "x", "y", "angle", "from_planet_id", "ships"])

SUN_X, SUN_Y, SUN_R = 50.0, 50.0, 10.0
MAX_SPEED = 6.0
MIN_FLEET = 5


# ─── 물리/기하 헬퍼 ────────────────────────────────────────────────────────────

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
    sq = math.sqrt(disc)
    t1 = (-b - sq) / (2 * a)
    t2 = (-b + sq) / (2 * a)
    return (0 <= t1 <= 1) or (0 <= t2 <= 1)


def predict_planet_pos(p: Planet, angular_velocity: float, turns: float) -> tuple[float, float]:
    """공전 행성의 turns 후 위치. 외행성은 현재 위치 그대로."""
    dx, dy = p.x - SUN_X, p.y - SUN_Y
    r = math.hypot(dx, dy)
    if r + p.radius < ROTATION_RADIUS_LIMIT and angular_velocity != 0:
        angle = math.atan2(dy, dx) + angular_velocity * turns
        return SUN_X + r * math.cos(angle), SUN_Y + r * math.sin(angle)
    return p.x, p.y


def aim(src_planet: Planet, tgt_planet: Planet, src_ships: int,
        angular_velocity: float) -> Optional[tuple[float, int, float]]:
    """
    반복 수렴으로 (발사 각도, 필요 함대, 이동 턴) 계산.
    태양 충돌 경로면 None 반환.
    """
    dist = math.hypot(src_planet.x - tgt_planet.x, src_planet.y - tgt_planet.y)
    turns = travel_turns(dist, src_ships)
    ships = src_ships
    tx, ty = tgt_planet.x, tgt_planet.y

    for _ in range(6):
        px, py = predict_planet_pos(tgt_planet, angular_velocity, turns)
        garrison = (tgt_planet.ships if tgt_planet.owner < 0
                    else tgt_planet.ships + tgt_planet.production * turns)
        new_ships = max(int(garrison) + 1, MIN_FLEET)
        new_dist  = math.hypot(src_planet.x - px, src_planet.y - py)
        new_turns = travel_turns(new_dist, new_ships)
        if abs(new_turns - turns) <= 2.0:
            tx, ty = px, py
            ships, turns = new_ships, new_turns
            break
        turns, ships, tx, ty = new_turns, new_ships, px, py

    if path_hits_sun(src_planet.x, src_planet.y, tx, ty):
        return None
    return math.atan2(ty - src_planet.y, tx - src_planet.x), ships, turns


# ─── World: obs 파싱 + 도착 스케줄 ────────────────────────────────────────────

class World:
    """
    obs를 파싱하고, forward_simulate에 필요한 도착 스케줄을 미리 계산.

    arrivals_by_planet[pid] = sorted list of (eta_turns, owner, ships)
    """

    def __init__(self, obs):
        _r = lambda key, default: (obs.get(key, default) if isinstance(obs, dict)
                                   else getattr(obs, key, default))

        self.player   = _r("player", 0)
        self.step     = _r("step", 0) or 0
        self.ang_vel  = _r("angular_velocity", 0.0) or 0.0

        raw_planets = _r("planets", []) or []
        raw_fleets  = _r("fleets",  []) or []

        self.planets    = [Planet(*p) for p in raw_planets]
        self.fleets     = [Fleet(*f)  for f in raw_fleets]
        self.planet_map = {p.id: p for p in self.planets}

        # 행성별 도착 예정 함대 스케줄 계산
        self.arrivals_by_planet: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        self._build_arrivals()

    def _build_arrivals(self):
        for f in self.fleets:
            # 함대가 향하는 행성 찾기: 각도 기준 가장 근접한 행성
            target_id = self._find_fleet_target(f)
            if target_id is None:
                continue
            tgt = self.planet_map[target_id]
            dist = math.hypot(f.x - tgt.x, f.y - tgt.y)
            eta  = math.ceil(dist / fleet_speed(f.ships))
            self.arrivals_by_planet[target_id].append((int(eta), int(f.owner), int(f.ships)))

        for pid in self.arrivals_by_planet:
            self.arrivals_by_planet[pid].sort()

    def _find_fleet_target(self, fleet: Fleet) -> Optional[int]:
        """함대 진행 방향과 각도가 가장 일치하는 행성 id 반환."""
        best_diff = 0.25  # 약 14도 이내
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


# ─── 시뮬레이터 핵심 ───────────────────────────────────────────────────────────

def _resolve_battle(state: dict, pid: int, arrivals: list[tuple[int, int]]):
    """
    state[pid] = [owner, ships, production]
    arrivals: list of (owner, ships) — 이번 턴 동시 도착
    게임 전투 규칙: 가장 큰 두 세력이 먼저 싸우고 승자가 garrison과 싸움
    """
    st = state[pid]
    defender_owner, garrison = st[0], st[1]

    # 세력별 합산
    from_owner: dict[int, int] = defaultdict(int)
    for o, s in arrivals:
        from_owner[o] += s

    # 방어자 추가
    if defender_owner != -1:
        from_owner[defender_owner] += garrison
    else:
        # 중립: garrison은 별도 처리 (소유권 없음)
        pass

    if not from_owner and defender_owner == -1:
        return  # 변화 없음

    # 가장 큰 두 세력 결정
    sorted_forces = sorted(from_owner.items(), key=lambda x: -x[1])

    if defender_owner == -1:
        # 중립 행성: 공격 함대만 싸우고, 승자가 garrison과 싸움
        if len(sorted_forces) >= 2:
            top_o, top_s = sorted_forces[0]
            sec_o, sec_s = sorted_forces[1]
            if top_s == sec_s:
                survivor_o, survivor_s = -1, 0
            else:
                survivor_o, survivor_s = top_o, top_s - sec_s
        else:
            survivor_o, survivor_s = sorted_forces[0]

        if survivor_s > 0:
            net = survivor_s - garrison
            if net > 0:
                st[0] = survivor_o
                st[1] = net
            else:
                st[0] = -1
                st[1] = -net
    else:
        # 소유 행성: 방어자 포함해서 싸움
        if len(sorted_forces) >= 2:
            top_o, top_s = sorted_forces[0]
            sec_o, sec_s = sorted_forces[1]
            if top_s == sec_s:
                st[0] = -1
                st[1] = 0
            else:
                st[0] = top_o
                st[1] = top_s - sec_s
        else:
            st[0] = sorted_forces[0][0]
            st[1] = sorted_forces[0][1]


def simulate(
    world: World,
    actions: list[dict],
    horizon: int = 20,
    project_opponent: bool = False,
    opponent_emit_fraction: float = 0.4,
    snapshot_turns: Optional[tuple] = None,
) -> dict | tuple[dict, dict]:
    """
    world 상태에서 actions를 적용하고 horizon 턴 후 상태를 반환.

    actions: list of {"src_id": int, "target_id": int, "ships": int}
      - 각 action은 이번 턴 우리가 발사하는 함대
      - ships: 실제 발사 수 (aim()으로 계산된 값)

    반환값:
      state: {planet_id: (owner, ships)} — horizon 턴 후 행성 상태
      snapshots (snapshot_turns 지정 시): {turn: state}

    state dict 포맷: {pid: [owner, ships, production]}  (내부 mutable)
    반환 시 (owner, ships) tuple로 변환
    """
    # ── 도착 스케줄 초기화 ──────────────────────────────────────────────────
    by_pid: dict[int, list[tuple[int, int, int]]] = defaultdict(list)

    # 기존 비행 중인 함대 (world에서 계산된 arrivals)
    for pid, arrs in world.arrivals_by_planet.items():
        for eta, owner, ships in arrs:
            if 0 < eta <= horizon:
                by_pid[pid].append((eta, owner, ships))

    # 이번 턴 발사 행동 추가
    for act in actions:
        src = world.planet_map.get(act["src_id"])
        tgt = world.planet_map.get(act["target_id"])
        if src is None or tgt is None:
            continue
        tx, ty = predict_planet_pos(tgt, world.ang_vel, 0)
        dist = math.hypot(src.x - tx, src.y - ty)
        # 정확한 ETA: aim()으로 계산하거나 간단히 dist/speed
        ships = act["ships"]
        eta = max(1, int(math.ceil(travel_turns(dist, ships))))
        if eta <= horizon:
            by_pid[act["target_id"]].append((eta, world.player, ships))

    # ── 상태 초기화 ────────────────────────────────────────────────────────
    state: dict[int, list] = {
        p.id: [int(p.owner), int(p.ships), int(p.production)]
        for p in world.planets
    }
    planet_pos = {p.id: (p.x, p.y) for p in world.planets}

    snap_set = set(snapshot_turns) if snapshot_turns else None
    snapshots: dict[int, dict] = {} if snap_set else None

    # ── 턴별 시뮬레이션 ───────────────────────────────────────────────────
    for t in range(1, horizon + 1):
        # 1. 생산
        for st in state.values():
            if st[0] != -1:
                st[1] += st[2]

        # 2. 상대 이동 예측 (간단히: 각 상대 행성이 nearest non-ally에 emit)
        if project_opponent and t % 4 == 0:
            for pid, st in state.items():
                if st[0] == -1 or st[0] == world.player or st[1] < 10:
                    continue
                sx, sy = planet_pos[pid]
                best_d, best_op = math.inf, None
                for opid, ost in state.items():
                    if opid == pid or ost[0] == st[0]:
                        continue
                    ox, oy = planet_pos[opid]
                    d = math.hypot(sx - ox, sy - oy)
                    if d < best_d:
                        best_d, best_op = d, opid
                if best_op is None:
                    continue
                emit = int(st[1] * opponent_emit_fraction)
                if emit < MIN_FLEET:
                    continue
                speed = fleet_speed(emit)
                eta_arr = t + max(1, int(math.ceil(best_d / speed)))
                if eta_arr <= horizon:
                    by_pid[best_op].append((eta_arr, st[0], emit))
                    st[1] -= emit

        # 3. 도착 처리
        for pid, arrs in by_pid.items():
            this_turn = [(o, s) for et, o, s in arrs if et == t]
            if not this_turn:
                continue
            _resolve_battle(state, pid, this_turn)

        # 4. 스냅샷
        if snap_set and t in snap_set:
            snapshots[t] = {pid: (st[0], st[1]) for pid, st in state.items()}

    final = {pid: (st[0], st[1]) for pid, st in state.items()}

    if snapshot_turns is not None:
        return final, snapshots
    return final


# ─── 평가 함수 ─────────────────────────────────────────────────────────────────

def score_state(state: dict, player: int, n_players: int = 2,
                planet_map: Optional[dict] = None) -> float:
    """
    state = {pid: (owner, ships)} 에서 player의 우위 점수 계산.
    lb-1050 방식: ships_diff + 5*planets_diff + 8*production_diff
    """
    my_ships = my_planets = my_prod = 0
    best_enemy_ships = best_enemy_planets = best_enemy_prod = 0

    enemy_ships:   dict[int, int] = defaultdict(int)
    enemy_planets: dict[int, int] = defaultdict(int)
    enemy_prod:    dict[int, int] = defaultdict(int)

    for pid, (owner, ships) in state.items():
        prod = planet_map[pid].production if planet_map else 0
        if owner == player:
            my_ships   += ships
            my_planets += 1
            my_prod    += prod
        elif owner >= 0:
            enemy_ships[owner]   += ships
            enemy_planets[owner] += 1
            enemy_prod[owner]    += prod

    if enemy_ships:
        best_enemy_ships   = max(enemy_ships.values())
        best_enemy_planets = max(enemy_planets.values())
        best_enemy_prod    = max(enemy_prod.values())

    return ((my_ships   - best_enemy_ships)
            + 5  * (my_planets - best_enemy_planets)
            + 8  * (my_prod    - best_enemy_prod))


def snapshot_score(
    world: World,
    actions: list[dict],
    snap_turns: tuple = (4, 8, 14, 20),
    project_opponent: bool = False,
) -> float:
    """
    lb-1050 방식: 여러 스냅샷 평가값의 1/t 가중 합산.
    4턴 후 상태가 20턴 후보다 5배 신뢰.
    """
    horizon = max(snap_turns)
    planet_map = world.planet_map

    final, snaps = simulate(
        world, actions,
        horizon=horizon,
        project_opponent=project_opponent,
        snapshot_turns=snap_turns,
    )

    total = w_sum = 0.0
    for t in snap_turns:
        w = 1.0 / t
        state = snaps.get(t, final)
        total += score_state(state, world.player, planet_map=planet_map) * w
        w_sum += w

    return total / w_sum if w_sum > 0 else 0.0


def greedy_score(
    world: World,
    action: dict,
    dist: float,
    ships_needed: int,
    turns: float,
) -> float:
    """v2/v3 방식 점수 — 하위 호환용."""
    t = world.planet_map.get(action["target_id"])
    if t is None:
        return -math.inf
    enemy_bonus = 10 if t.owner >= 0 else 0
    return (100 - dist) + 15 * t.production + enemy_bonus - 0.7 * ships_needed - 2 * turns
