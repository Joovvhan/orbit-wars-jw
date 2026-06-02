"""
v8: v7 + Reserve + Hammer
- Reserve: 각 행성의 수비 예약 병력 계산 → 잉여만 공격에 사용
- Hammer: 임계치(HAMMER_MIN) 달성 시 적 고생산 행성 집중 타격
- 검증: reserve가 실제 수비에 쓰이는지, hammer가 올바른 타이밍에 발사되는지
- v6의 수비/도주/경로추적 유지
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


# ─── Reserve: 행성별 수비 예약 병력 계산 ─────────────────────────────────────

def compute_reserve(planet, arrivals: list, player: int) -> int:
    """
    이 행성에 예약해야 할 최소 병력 (수비용).

    원리: 적 함대가 도착하기 전까지 누적 생산량을 더해도
          적 병력 합계를 버티려면 얼마가 필요한가?

    반환값: reserve (이 이상은 공격에 쓰면 안 됨)
    """
    ships = float(planet.ships)
    arr_idx = 0
    n = len(arrivals)
    shortfall = 0

    for turn in range(1, 61):
        # 생산
        ships += planet.production

        # 도착 처리
        friendly = 0
        enemy = 0
        while arr_idx < n and arrivals[arr_idx][0] == turn:
            _, owner, s = arrivals[arr_idx]
            if owner == player:
                friendly += s
            else:
                enemy += s
            arr_idx += 1

        ships += friendly
        if enemy > 0:
            ships -= enemy
            if ships < 0:
                shortfall = max(shortfall, int(-ships) + 1)
                ships = 0  # 함락 후 0 (최악 케이스 계속 계산)

    return shortfall  # 이 만큼은 지금 행성에 남겨야 함


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
            wealth += factor * 0.35
        elif p.owner == -1:
            wealth += factor * 0.90
        else:
            wealth += factor * 1.25
    return wealth


TOTAL_STEPS = 500

def race_margin(target, my_planets, enemy_planets) -> float:
    """
    중립 행성 쟁탈 여유: 적 최소 도착 - 내 최소 도착
    양수 → 내가 먼저 도착 (여유가 클수록 확실히 확보 가능)
    음수 → 적이 먼저 도착 (현실적으로 뺏기기 어려움)
    """
    my_min = min(
        travel_turns(math.hypot(m.x - target.x, m.y - target.y), max(1, int(m.ships)))
        for m in my_planets
    )
    if not enemy_planets:
        return my_min  # 적 없으면 무조건 확보 가능
    enemy_min = min(
        travel_turns(math.hypot(e.x - target.x, e.y - target.y), max(1, int(e.ships)))
        for e in enemy_planets
    )
    return enemy_min - my_min


# ─── Hammer: 임계치 달성 시 적 집중 타격 ──────────────────────────────────────

HAMMER_MIN = 40           # 발사 기준 최소 가용 병력
HAMMER_OVERKILL = 1.2     # 필요 병력 × 1.2 (안전 마진)
HAMMER_MAX_TRAVEL = 35    # 이 이상 거리는 hammer 제외
HAMMER_PROD_MIN = 2       # 이 이상 생산량 타깃만 hammer
HAMMER_NEUTRAL_RATIO_MAX = 0.3  # 중립이 30% 이하일 때만 hammer 발동


def find_hammer_target(my_planets, enemy_planets, available, ang_vel, step):
    """
    Hammer: 총 가용 병력이 HAMMER_MIN 이상일 때, 가장 생산량 높은 적 행성을
    가장 가까운 내 행성에서 집중 타격. 계산 최소화(O(1) 타깃).
    """
    total_available = sum(available.get(p.id, 0) for p in my_planets)
    if total_available < HAMMER_MIN:
        return None

    # 타깃: 생산량 가장 높은 적 행성 하나만 선택
    candidates = [t for t in enemy_planets if t.production >= HAMMER_PROD_MIN]
    if not candidates:
        return None
    tgt = max(candidates, key=lambda t: t.production)

    # 가장 가까운 내 행성에서만 발사 (계산량 O(N_my_planets))
    best_src = None
    best_dist = math.inf
    for src in my_planets:
        if available.get(src.id, 0) < HAMMER_MIN:
            continue
        d = math.hypot(src.x - tgt.x, src.y - tgt.y)
        if d < best_dist:
            best_dist = d
            best_src = src

    if best_src is None:
        return None

    avail = available.get(best_src.id, 0)
    result = aim_iterative(best_src, tgt, avail, ang_vel)
    if result is None:
        return None

    angle, ships_needed, turns = result
    if turns > HAMMER_MAX_TRAVEL:
        return None
    if avail < ships_needed:
        return None

    return tgt, [(best_src, angle, ships_needed)]


def target_score(t, dist, ships_needed, turns, step, margin=0.0, iwealth=0.0) -> float:
    """
    v6 기본 스코어 + 중립 쟁탈 여유 반영
    - 내가 먼저 도착 가능한 중립: 보너스
    - 적이 먼저 도착하는 중립: 페널티 (건너뜀 처리는 호출부에서)
    - 적 행성: v6와 동일
    """
    enemy_bonus = 10 if t.owner >= 0 else 0
    base = (100 - dist) + 15 * t.production + enemy_bonus \
           - 0.7 * ships_needed - 2 * turns + iwealth * 0.15

    if t.owner < 0:  # 중립만 race margin 반영
        # 여유 +5턴: 큰 보너스 (확실히 확보 가능)
        # 여유 -2턴 이하: 사실상 불가 (큰 페널티)
        base += margin * 3.0

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

    # pending_ships[pid]: 이번 턴 이미 발사 예약된 함대
    pending_ships = {p.id: 0 for p in my_planets}

    # ── 1. Reserve 계산: 행성별 수비 예약 병력 ────────────────────────────────
    reserves: dict[int, int] = {}
    timelines: dict[int, PlanetTimeline] = {}
    for p in my_planets:
        arrivals = world.arrivals_by_planet.get(p.id, [])
        reserves[p.id] = compute_reserve(p, arrivals, player)
        timelines[p.id] = simulate_planet_timeline(p, arrivals, player)

    # available_for_attack: v7과 동일하게 전체 병력 사용
    # reserve 정보는 방어(defense) 판단에만 활용, 공격 자원은 제한하지 않음
    # (reserve로 공격 제한 시 초반 중립 확장이 심각하게 저하됨)
    available_for_attack = {p.id: int(p.ships) for p in my_planets}

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

    # ── 4. Hammer: 임계치 달성 시 적 집중 타격 ────────────────────────────────
    enemy_planets = [p for p in planets if p.owner not in (-1, player)]
    hammer_result = None

    n_neutral = sum(1 for p in planets if p.owner < 0)
    neutral_ratio = n_neutral / max(1, len(targets))

    # Hammer: 5턴마다만 실행 (매 턴 실행 시 aim_iterative 누적으로 느려짐)
    if enemy_planets and neutral_ratio <= HAMMER_NEUTRAL_RATIO_MAX and world.step % 5 == 0:
        # pending 반영한 현재 가용 병력
        cur_available = {
            p.id: max(0, available_for_attack[p.id] - pending_ships[p.id])
            for p in my_planets
        }
        hammer_result = find_hammer_target(
            my_planets, enemy_planets, cur_available, world.ang_vel, world.step
        )

    hammer_committed: dict = {}  # hammer가 커밋한 병력 (regular attack이 중복 방지에 활용)
    if hammer_result:
        tgt, plan = hammer_result
        for src, angle, send in plan:
            moves.append([src.id, angle, send])
            pending_ships[src.id] += send
            hammer_committed[tgt.id] = hammer_committed.get(tgt.id, 0) + send
            if DEBUG:
                print(f"[HAMMER] {src.id}->{tgt.id}: {send}척 (prod={tgt.production})")

    # ── 5. 일반 공격: reserve 기반 가용 병력으로 ──────────────────────────────
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


    # race_margin 미리 계산 (중립 타깃별)
    race_margins = {}
    for t in targets:
        if t.owner < 0:
            race_margins[t.id] = race_margin(t, my_planets, enemy_planets)

    committed: dict = {}
    attack_targeted = set()

    # ── 5a. 단일 행성 공격 (reserve 차감 후 가용 병력 사용) ───────────────────
    RACE_SKIP_THRESHOLD = -3
    for mine in my_planets_sorted:
        while True:
            # reserve 차감: 수비 예약 후 남은 것만 공격에 사용
            available = available_for_attack[mine.id] - pending_ships[mine.id]
            if available < MIN_FLEET:
                break

            best_score  = -math.inf
            best_result = None

            for t in targets:
                if t.id in attack_targeted:
                    continue
                dist = math.hypot(mine.x - t.x, mine.y - t.y)
                # hammer 커밋도 포함해서 중복 공격 방지
                already = my_inbound.get(t.id, 0) + committed.get(t.id, 0) + hammer_committed.get(t.id, 0)

                result = aim_iterative(mine, t, mine.ships, world.ang_vel)
                if result is None:
                    continue

                angle, ships_needed, turns = result

                if already >= ships_needed:
                    continue

                if available < ships_needed:
                    continue

                # 중립 행성: 적이 명백히 더 빠르면 건너뜀
                margin = race_margins.get(t.id, 0.0)
                if t.owner < 0 and margin < RACE_SKIP_THRESHOLD:
                    continue

                iw = iwealth_cache.get(t.id, 0.0)
                score = target_score(t, dist, ships_needed, turns, world.step, margin, iw)

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
