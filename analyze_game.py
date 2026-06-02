"""
게임 분석 도구: 실제 게임 진행을 턴별로 기록하고 검증
사용법:
  uv run python analyze_game.py v7 v6 --seed 0
  uv run python analyze_game.py v7 ex_proto --seed 3 --turns 30
"""
import argparse
import importlib.util
import math
from collections import defaultdict
from kaggle_environments import make


def load_agent(name: str):
    path = f"agents/{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.agent


def fleet_speed(ships):
    if ships <= 1: return 1.0
    return 1.0 + 5.0 * (math.log(ships) / math.log(1000)) ** 1.5


def analyze(name_a: str, name_b: str, seed: int, max_turns: int):
    agent_a = load_agent(name_a)
    agent_b = load_agent(name_b)

    env = make("orbit_wars", configuration={"seed": seed}, debug=False)
    env.run([agent_a, agent_b])

    steps = env.steps
    r = steps[-1]
    winner = name_a if r[0].reward > r[1].reward else (name_b if r[1].reward > r[0].reward else "DRAW")
    print(f"\n{'='*60}")
    print(f"게임: {name_a}(P0) vs {name_b}(P1)  seed={seed}")
    print(f"결과: {winner} 승  ({len(steps)}턴)")
    print(f"{'='*60}")

    prev_owners = {}
    prev_prod = {0: 0, 1: 0}

    for t, step in enumerate(steps[:max_turns]):
        obs = step[0].observation
        planets = obs.get("planets", [])
        fleets = obs.get("fleets", [])

        # 소유 현황
        owners = {p[0]: p[1] for p in planets}
        prod = {
            0: sum(p[6] for p in planets if p[1] == 0),
            1: sum(p[6] for p in planets if p[1] == 1),
        }
        my_cnt  = sum(1 for p in planets if p[1] == 0)
        opp_cnt = sum(1 for p in planets if p[1] == 1)
        neu_cnt = sum(1 for p in planets if p[1] == -1)
        my_ships  = sum(p[5] for p in planets if p[1] == 0)
        opp_ships = sum(p[5] for p in planets if p[1] == 1)
        fl_my  = sum(f[6] for f in fleets if f[1] == 0)
        fl_opp = sum(f[6] for f in fleets if f[1] == 1)

        # 소유권 변경 감지
        captures = []
        for pid, owner in owners.items():
            if pid in prev_owners and prev_owners[pid] != owner:
                prev = prev_owners[pid]
                prev_name = name_a if prev == 0 else (name_b if prev == 1 else "중립")
                new_name  = name_a if owner == 0 else (name_b if owner == 1 else "중립")
                p_info = next(p for p in planets if p[0] == pid)
                captures.append(f"  ★ planet {pid}(prod={p_info[6]}) {prev_name}→{new_name}")

        prod_change_a = prod[0] - prev_prod[0]
        prod_change_b = prod[1] - prev_prod[1]

        print(f"\n[턴 {t:3d}] 중립:{neu_cnt:2d}개 "
              f"| {name_a}: {my_cnt}행성 prod={prod[0]}(+{prod_change_a:+d}) {my_ships}+{fl_my}함대 "
              f"| {name_b}: {opp_cnt}행성 prod={prod[1]}(+{prod_change_b:+d}) {opp_ships}+{fl_opp}함대")

        for cap in captures:
            print(cap)

        prev_owners = owners.copy()
        prev_prod = prod.copy()

    # 최종 요약
    final_obs = steps[-1][0].observation
    planets = final_obs.get("planets", [])
    fleets = final_obs.get("fleets", [])
    print(f"\n{'='*60}")
    print(f"최종 상태")
    for player, name in [(0, name_a), (1, name_b)]:
        p_cnt = sum(1 for p in planets if p[1] == player)
        p_ships = sum(p[5] for p in planets if p[1] == player)
        f_ships = sum(f[6] for f in fleets if f[1] == player)
        p_prod = sum(p[6] for p in planets if p[1] == player)
        print(f"  {name}: {p_cnt}행성, prod={p_prod}, ships={p_ships}(행성)+{f_ships}(비행)")


def verify_v7_behavior(seed: int):
    """v7의 핵심 로직(race_margin, 수비)이 실제로 발동하는지 검증"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("v7", "agents/v7.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    log = {"race_skip": 0, "race_bonus": 0, "defense": 0, "evac": 0, "atk": 0}

    original_agent = mod.agent

    def patched(obs):
        world = mod.World(obs)
        player = world.player
        planets = world.planets
        my_planets = [p for p in planets if p.owner == player]
        targets = [p for p in planets if p.owner != player]
        enemy_planets = [p for p in planets if p.owner not in (-1, player)]

        if not my_planets or not targets:
            return original_agent(obs)

        # race_margin 발동 확인
        for t in targets:
            if t.owner < 0:
                m = mod.race_margin(t, my_planets, enemy_planets)
                if m < -3:
                    log["race_skip"] += 1
                elif m > 0:
                    log["race_bonus"] += 1

        return original_agent(obs)

    mod.agent = patched

    from kaggle_environments import make
    import importlib.util as ilu
    spec2 = ilu.spec_from_file_location("v6", "agents/v6.py")
    mod2 = ilu.module_from_spec(spec2)
    spec2.loader.exec_module(mod2)

    env = make("orbit_wars", configuration={"seed": seed}, debug=False)
    env.run([mod.agent, mod2.agent])
    r = env.steps[-1]

    total_turns = len(env.steps)
    print(f"\n[v7 동작 검증] seed={seed}  결과: {'v7 승' if r[0].reward > r[1].reward else 'v6 승'}")
    print(f"  총 {total_turns}턴, 에이전트 호출 약 {total_turns}회")
    print(f"  race_skip   (건너뜀): {log['race_skip']}회  — 적이 3턴 이상 빠른 중립 스킵")
    print(f"  race_bonus  (우선공략): {log['race_bonus']}회  — 내가 먼저 도착 가능한 중립")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("agent_a", help="에이전트 A (예: v7)")
    parser.add_argument("agent_b", help="에이전트 B (예: v6)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--turns", type=int, default=50, help="출력할 최대 턴 수")
    parser.add_argument("--verify", action="store_true", help="v7 동작 검증 모드")
    args = parser.parse_args()

    if args.verify:
        verify_v7_behavior(args.seed)
    else:
        analyze(args.agent_a, args.agent_b, args.seed, args.turns)
