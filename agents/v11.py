"""
v11: v10 + Accumulator/Hammer/Multiprong

의도한 전략:
1. Accumulator: 후반 게임(neutral_ratio < 0.35)에서 lead 행성에 함대 집중
   - 나머지(feeder) 행성들의 여유 ships를 lead로 전송
   - Lead를 일반 공격에서 제외 (hammer를 위해 예약)

2. Hammer: Lead ships ≥ HAMMER_STOCKPILE_MIN 달성 시 적 요새 일격
   - 타깃: Hammer 거리 내 최고 production 적 행성
   - 발사량: garrison × HAMMER_OVERKILL_RATIO (안전 마진 포함)

3. Multiprong: Hammer 발사 시 인접 적 행성도 동시 공격
   - 상대가 hammer 타깃 방어 OR secondary 방어 중 하나만 선택 가능
   - 강제 딜레마 효과

4. (v10 유지) Melis 보너스 타깃 스코어 + 전반 게임 협공
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
TOTAL_STEPS = 500

# ══════════════════════════════════════════════════════════════════════
# 조절 파라미터 — 값과 의미 설명
# ══════════════════════════════════════════════════════════════════════

# ── Hammer 파라미터 (잉여 함대로만 발사 — 일반 공격·협공 후 섹션 9) ──────
HAMMER_ENABLED         = True  # False면 순수 v10 동작 (중립성 검증용)
HAMMER_STOCKPILE_MIN   = 70    # hammer 발사 최소 잉여 ships [높이면 더 강한 punch]
HAMMER_TARGET_PROD_MIN = 1     # hammer 대상 최소 production [낮추면 약한 타깃도 공격]
HAMMER_OVERKILL_RATIO  = 1.35  # garrison 대비 발사 배수 [높이면 더 확실, 더 비쌈]
HAMMER_MAX_TRAVEL      = 12    # hammer 최대 이동 거리(턴) [짧을수록 함대가 빨리 복귀, 장거리 hammer는 회귀]

# ── Accumulator: 비활성화 (현 설계는 feeder→lead 전송 없이 잉여로만 hammer)
ACCUM_ENABLED = False  # True로 바꾸면 feeder→lead 전송 활성화 [실험적]

# ── Multiprong 파라미터 ──────────────────────────────────────────────
MULTIPRONG_ENABLED     = True  # multiprong 기능 활성화 여부
MULTIPRONG_SHIPS_RATIO = 0.45  # hammer 타깃 garrison 대비 secondary 발사 비율 [조절]
MULTIPRONG_MAX_TRAVEL  = 28    # secondary 최대 이동 거리(턴) [조절]

# ── Melis 파라미터 ───────────────────────────────────────────────────
MELIS_HORIZON    = 20
MELIS_SNAP_TURNS = (4, 8, 14, 20)
MELIS_OPP_FRAC   = 0.15
MELIS_WEIGHT     = 1.5    # Melis gain의 v9 스코어 대비 가중치 [조절]

# ══════════════════════════════════════════════════════════════════════

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


# ─── Melis Forward Evaluation ─────────────────────────────────────────────────

def forward_project(world, our_capture_target=None, our_capture_turn=None,
                    our_capture_ships=None, horizon=MELIS_HORIZON,
                    snapshot_turns=None):
    player = world.player
    by_pid: dict = defaultdict(list)
    for pid, arrs in world.arrivals_by_planet.items():
        for eta, owner, ships in arrs:
            if 0 < eta <= horizon:
                by_pid[pid].append((int(eta), int(owner), int(ships)))

    if our_capture_target is not None and our_capture_turn is not None:
        by_pid[our_capture_target].append(
            (int(our_capture_turn), int(player), int(our_capture_ships or 1))
        )

    state = {}
    for p in world.planets:
        state[p.id] = [int(p.owner), int(p.ships), int(p.production)]

    planet_pos = {p.id: (float(p.x), float(p.y)) for p in world.planets}
    snap_set = set(snapshot_turns) if snapshot_turns else set()
    snapshots = {}

    for t in range(1, horizon + 1):
        for pid, st in state.items():
            if st[0] != -1:
                st[1] += st[2]

        if t % 4 == 0:
            for pid, st in state.items():
                if st[0] == -1 or st[0] == player or st[1] < 10:
                    continue
                src_x, src_y = planet_pos[pid]
                src_owner = st[0]
                best_d, best_op = float("inf"), None
                for opid, ost in state.items():
                    if opid == pid or ost[0] == src_owner:
                        continue
                    d = math.hypot(src_x - planet_pos[opid][0],
                                   src_y - planet_pos[opid][1])
                    if d < best_d:
                        best_d, best_op = d, opid
                if best_op is None:
                    continue
                emit = int(st[1] * MELIS_OPP_FRAC)
                if emit < MIN_FLEET:
                    continue
                spd = fleet_speed(emit)
                eta_arrive = max(1, math.ceil(best_d / spd))
                if t + eta_arrive > horizon:
                    continue
                by_pid[best_op].append((t + eta_arrive, src_owner, emit))
                st[1] -= emit

        for pid, arrs in by_pid.items():
            this_turn = [(o, s) for et, o, s in arrs if et == t]
            if not this_turn:
                continue
            st = state[pid]
            defender_owner, garrison = st[0], st[1]
            from_owner: dict = defaultdict(int)
            for o, s in this_turn:
                from_owner[o] += s
            sorted_owners = sorted(from_owner.items(), key=lambda x: -x[1])
            top_owner, top_ships = sorted_owners[0]
            if len(sorted_owners) >= 2:
                second_ships = sorted_owners[1][1]
                survivor_ships = max(0, top_ships - second_ships) if top_ships > second_ships else 0
                survivor_owner = top_owner if top_ships > second_ships else -1
            else:
                survivor_ships, survivor_owner = top_ships, top_owner
            if survivor_ships > 0:
                if defender_owner == survivor_owner:
                    st[1] = garrison + survivor_ships
                else:
                    new_garrison = garrison - survivor_ships
                    if new_garrison < 0:
                        st[0], st[1] = survivor_owner, -new_garrison
                    else:
                        st[1] = new_garrison

        if t in snap_set:
            snapshots[t] = {pid: (st[0], st[1]) for pid, st in state.items()}

    final = {pid: (st[0], st[1]) for pid, st in state.items()}
    return final, snapshots


def _forward_score(snap: dict, player: int, planet_prod: dict) -> float:
    by_owner: dict = defaultdict(lambda: [0, 0, 0])
    for pid, (owner, ships) in snap.items():
        if owner >= 0:
            by_owner[owner][0] += max(0, ships)
            by_owner[owner][1] += 1
            by_owner[owner][2] += planet_prod.get(pid, 0)
    if player not in by_owner:
        return -1e6
    m = by_owner[player]
    my_ships, my_planets, my_prod = m[0], m[1], m[2]
    opp_ships = opp_planets = opp_prod = 0
    for owner, stats in by_owner.items():
        if owner != player:
            opp_ships  = max(opp_ships,  stats[0])
            opp_planets = max(opp_planets, stats[1])
            opp_prod   = max(opp_prod,   stats[2])
    return (my_ships - opp_ships) + 5.0*(my_planets - opp_planets) + 8.0*(my_prod - opp_prod)


def melis_score(world, capture_target=None, capture_turn=None, capture_ships=None) -> float:
    _, snaps = forward_project(
        world, our_capture_target=capture_target,
        our_capture_turn=capture_turn, our_capture_ships=capture_ships,
        horizon=MELIS_HORIZON, snapshot_turns=MELIS_SNAP_TURNS,
    )
    total = weight_sum = 0.0
    for t in MELIS_SNAP_TURNS:
        snap = snaps.get(t)
        if snap is None:
            continue
        w = 1.0 / t
        total += _forward_score(snap, world.player, world.planet_prod) * w
        weight_sum += w
    return total / max(1e-9, weight_sum)


# ─── World 파싱 ────────────────────────────────────────────────────────────────

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
        self.planet_prod = {p.id: int(p.production) for p in self.planets}
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


# ─── 타임라인 시뮬레이션 ──────────────────────────────────────────────────────

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
        friendly_arrive = enemy_arrive = 0
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
        if enemy_arrive > 0 and owner == player:
            ships -= enemy_arrive
            if ships < 0:
                tl.holds = False
                tl.fall_turn = turn
                tl.deficit = int(-ships) + 1
                break
    return tl


# ─── 조준 / 스코어 헬퍼 ───────────────────────────────────────────────────────

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


def target_score(t, dist, ships_needed, turns, step, melis_gain=0.0,
                 margin=0.0, iwealth=0.0, neutral_ratio=1.0) -> float:
    remaining = max(1, TOTAL_STEPS - step)
    enemy_bonus = 10 if t.owner >= 0 else 0
    base = (100 - dist) + 15 * t.production + enemy_bonus \
           - 0.7 * ships_needed - 2 * turns + iwealth * 0.15
    if t.owner < 0:
        base += margin * 3.0
    else:
        base += (1.0 - neutral_ratio) * 30.0
    remaining_after = max(0, remaining - turns)
    roi = t.production * remaining_after - ships_needed
    if roi < 0:
        base += roi * 0.5
    base += MELIS_WEIGHT * melis_gain
    return base


# ─── Hammer 헬퍼 ─────────────────────────────────────────────────────────────
# 주 hammer 타깃 선택은 agent() 섹션 9에 인라인(잉여 함대 기준). 여기는 보조 공격만.

def pick_multiprong_secondary(world, hammer_target, my_planets, enemy_planets,
                               pending_ships, lead_id, committed_targets,
                               remaining_turns):
    """
    multiprong secondary 타깃 선택:
    - Hammer 타깃 인근의 다른 적 행성
    - Lead가 아닌 행성에서 발사 가능
    - MULTIPRONG_SHIPS_RATIO × hammer_garrison 정도의 함대로 공격
    """
    if hammer_target is None:
        return None

    ht = hammer_target
    best = None
    best_dist = math.inf

    for t in enemy_planets:
        if t.id == ht.id or t.id in committed_targets:
            continue
        # hammer 타깃 인근 (가까울수록 딜레마 효과 큼)
        d_to_hammer = math.hypot(t.x - ht.x, t.y - ht.y)
        if d_to_hammer > 60:
            continue

        # 발사 가능한 소스 행성 찾기 (lead 제외)
        for mine in my_planets:
            if mine.id == lead_id:
                continue
            avail = mine.ships - pending_ships[mine.id]
            if avail < MIN_FLEET:
                continue

            result = aim_iterative(mine, t, avail, world.ang_vel)
            if result is None:
                continue

            angle, ships_needed, turns = result
            if turns > MULTIPRONG_MAX_TRAVEL:
                continue
            if ships_needed > avail:
                continue

            remaining_after = max(0, remaining_turns - turns)
            roi = t.production * remaining_after - ships_needed
            if roi < 0:
                continue

            dist_from_mine = math.hypot(mine.x - t.x, mine.y - t.y)
            if dist_from_mine < best_dist:
                best_dist = dist_from_mine
                best = (mine, t, angle, ships_needed)

    return best


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

    # ── 1. 타임라인 시뮬레이션 ────────────────────────────────────────────────
    timelines: dict = {}
    for p in my_planets:
        arrivals = world.arrivals_by_planet.get(p.id, [])
        timelines[p.id] = simulate_planet_timeline(p, arrivals, player)

    # ── 2. 도주(Evacuation) ────────────────────────────────────────────────────
    for p in sorted(my_planets, key=lambda x: timelines[x.id].fall_turn or 999):
        tl = timelines[p.id]
        if not tl.holds and tl.fall_turn is not None and tl.fall_turn <= 10:
            avail = p.ships - pending_ships[p.id]
            if avail < MIN_FLEET:
                continue
            best_dest = min(
                (d for d in my_planets if d.id != p.id and timelines[d.id].holds),
                key=lambda d: math.hypot(p.x - d.x, p.y - d.y),
                default=None
            )
            if best_dest is None:
                continue
            angle = math.atan2(best_dest.y - p.y, best_dest.x - p.x)
            if not path_hits_sun(p.x, p.y, best_dest.x, best_dest.y):
                moves.append([p.id, angle, avail])
                pending_ships[p.id] += avail
                if DEBUG:
                    print(f"[EVAC] {p.id}->{best_dest.id}: {avail}")

    # ── 3. 수비(Defense) ───────────────────────────────────────────────────────
    threatened = [(p, tl) for p in my_planets
                  for tl in [timelines[p.id]]
                  if not tl.holds and tl.fall_turn and tl.deficit > 0]
    threatened.sort(key=lambda x: x[1].fall_turn)

    for threatened_p, tl in threatened:
        needed = tl.deficit
        donors = sorted(
            [p for p in my_planets if p.id != threatened_p.id],
            key=lambda p: math.hypot(p.x - threatened_p.x, p.y - threatened_p.y)
        )
        for donor in donors:
            avail = donor.ships - pending_ships[donor.id]
            if avail < MIN_FLEET:
                continue
            dist = math.hypot(donor.x - threatened_p.x, donor.y - threatened_p.y)
            eta = math.ceil(dist / fleet_speed(max(MIN_FLEET, needed)))
            if eta >= tl.fall_turn - 1:
                continue
            reinforce = min(avail, needed + 1)
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
                print(f"[DEF] {donor.id}->{threatened_p.id}: {reinforce}")
            if needed <= 0:
                break

    if not targets:
        return moves

    my_planets_sorted = sorted(my_planets, key=lambda p: p.ships, reverse=True)
    my_inbound: dict = {}
    for pid, arrivals in world.arrivals_by_planet.items():
        total = sum(s for (_, o, s) in arrivals if o == player)
        if total > 0:
            my_inbound[pid] = total

    enemy_planets = [p for p in planets if p.owner not in (-1, player)]
    n_neutral = sum(1 for t in targets if t.owner < 0)
    neutral_ratio = n_neutral / max(1, len(targets))
    remaining_turns = max(1, TOTAL_STEPS - world.step)

    committed: dict = {}
    attack_targeted = set()

    # NOTE(v11): Hammer/Multiprong은 일반 공격·협공이 끝난 뒤 "잉여 함대"로만
    # 발사한다(섹션 9). 주력 행성을 미리 벤치에 앉히지 않는다 — v10 교훈.

    # ── 7. 일반 공격 (v10 스타일: Melis 보너스 포함) ─────────────────────────
    iwealth_cache = {t.id: indirect_wealth(t, planets, player) for t in targets}
    race_margins = {}
    for t in targets:
        if t.owner < 0:
            race_margins[t.id] = race_margin(t, my_planets, enemy_planets)

    baseline = melis_score(world)
    melis_gain_cache: dict = {}

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

                if t.id not in melis_gain_cache:
                    m = melis_score(world, t.id, int(turns), ships_needed)
                    melis_gain_cache[t.id] = m - baseline
                gain = melis_gain_cache[t.id]

                score = target_score(t, dist, ships_needed, turns, world.step,
                                     gain, margin, iw, neutral_ratio)
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
                gain = melis_gain_cache.get(t.id, 0.0)
                print(f"[ATK] {mine.id}->{t.id}: {ships_needed} ships (gain={gain:.1f})")

    # ── 8. 협공(All-game Sequential Coop) ────────────────────────────────────
    COOP_MIN_GARRISON = 15
    COOP_MAX_T        = 20
    COOP_SAFETY       = 5

    for t in targets:
        if t.id in attack_targeted:
            continue
        if t.owner < 0:
            continue

        source_candidates = []
        for mine in my_planets:
            avail = mine.ships - pending_ships[mine.id]
            if avail < MIN_FLEET:
                continue
            dist_now = math.hypot(mine.x - t.x, mine.y - t.y)
            if dist_now == 0:
                continue
            min_eta = math.ceil(dist_now / MAX_SPEED)
            if min_eta > COOP_MAX_T:
                continue
            source_candidates.append((mine, avail, min_eta))

        if len(source_candidates) < 2:
            continue

        source_candidates.sort(key=lambda c: c[2])
        unique_T_values = sorted(set(c[2] for c in source_candidates))

        best_coop = None
        for T in unique_T_values:
            if T > COOP_MAX_T:
                break
            tx, ty = predict_position(t, world.ang_vel, T)
            garrison_T = int(t.ships) + t.production * T + COOP_SAFETY
            if garrison_T < COOP_MIN_GARRISON:
                continue
            remaining_after = max(0, remaining_turns - T)
            if t.production * remaining_after < garrison_T - COOP_SAFETY:
                continue
            already = my_inbound.get(t.id, 0) + committed.get(t.id, 0)
            need = garrison_T - already
            if need <= 0:
                continue

            contributors = []
            for mine, avail, _ in source_candidates:
                result = aim_iterative(mine, t, avail, world.ang_vel)
                if result is None:
                    continue
                angle, ships_needed, coop_turns = result
                if ships_needed > avail or coop_turns > T + 3:
                    continue
                if path_hits_sun(mine.x, mine.y, tx, ty):
                    continue
                contributors.append((mine, angle, ships_needed, avail))

            if len(contributors) < 2:
                continue
            if sum(c[2] for c in contributors) < need:
                continue

            best_coop = (T, garrison_T, need, contributors)
            break

        if best_coop is None:
            continue

        T, garrison_T, need, contributors = best_coop
        contributors.sort(key=lambda c: c[2], reverse=True)
        executed = []
        remaining_need = need
        for mine, angle, ships_send, avail in contributors:
            if remaining_need <= 0:
                break
            executed.append((mine, angle, ships_send))
            remaining_need -= ships_send

        if remaining_need > 0 or len(executed) < 2:
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

    # ── 9. Hammer/Multiprong: 일반 공격·협공 후 "잉여 함대"로만 발사 ──────────
    # 주력을 벤치에 앉히지 않고, 공격에 다 쓰고도 남은 함대가 충분할 때만
    # 적 요새를 일격한다. 발사 못 하면 손해 0 (= v10 동작).
    if HAMMER_ENABLED and enemy_planets:
        lead = max(my_planets, key=lambda p: p.ships - pending_ships[p.id])
        lead_avail = lead.ships - pending_ships[lead.id]

        if lead_avail >= HAMMER_STOCKPILE_MIN:
            best = None
            best_score = -math.inf
            for t in enemy_planets:
                if t.id in attack_targeted or t.production < HAMMER_TARGET_PROD_MIN:
                    continue
                result = aim_iterative(lead, t, lead_avail, world.ang_vel)
                if result is None:
                    continue
                angle, ships_needed, turns = result
                if turns > HAMMER_MAX_TRAVEL:
                    continue
                required = int(ships_needed * HAMMER_OVERKILL_RATIO)
                if required > lead_avail:
                    continue
                remaining_after = max(0, remaining_turns - turns)
                roi = t.production * remaining_after - required
                if roi < 0:
                    continue
                score = t.production * remaining_after - turns * 2
                if score > best_score:
                    best_score = score
                    best = (t, angle, required, turns)

            if best is not None:
                t_hammer, angle, required, turns = best
                moves.append([lead.id, angle, required])
                pending_ships[lead.id] += required
                committed[t_hammer.id] = committed.get(t_hammer.id, 0) + required
                attack_targeted.add(t_hammer.id)
                if DEBUG:
                    print(f"[HAMMER] {lead.id}->{t_hammer.id}: {required} ships "
                          f"(turns={turns:.1f}, surplus={lead_avail})")

                if MULTIPRONG_ENABLED:
                    secondary = pick_multiprong_secondary(
                        world, t_hammer, my_planets, enemy_planets,
                        pending_ships, lead.id, attack_targeted, remaining_turns
                    )
                    if secondary is not None:
                        src_mine, sec_tgt, sec_angle, sec_ships = secondary
                        if sec_ships <= src_mine.ships - pending_ships[src_mine.id]:
                            moves.append([src_mine.id, sec_angle, sec_ships])
                            pending_ships[src_mine.id] += sec_ships
                            committed[sec_tgt.id] = committed.get(sec_tgt.id, 0) + sec_ships
                            attack_targeted.add(sec_tgt.id)
                            if DEBUG:
                                print(f"[MULTI] {src_mine.id}->{sec_tgt.id}: {sec_ships} "
                                      f"ships (near hammer {t_hammer.id})")

    return moves
