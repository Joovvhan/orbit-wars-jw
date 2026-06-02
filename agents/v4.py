"""
v4: simulator.py 기반 snapshot 평가 (수정판)
- greedy 점수로 top-K 후보 필터
- top-K를 고정 snap_turns=(4,8,14,20)으로 동일 기준 비교 (델타 방식)
- _committed 역추적 제거: 단순하고 안정적인 단일 행동 평가
- 한 행성에서 여러 타깃 연속 공격 (v3 방식 유지)
"""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from simulator import (
    World, aim, simulate, score_state, snapshot_score, greedy_score,
    MIN_FLEET
)

DEBUG = False

SNAP_TURNS = (4, 8, 14, 20)
TOP_K      = 8   # greedy 필터 후 snapshot 평가할 후보 수


def agent(obs):
    moves     = []
    world     = World(obs)

    my_planets = sorted([p for p in world.planets if p.owner == world.player],
                        key=lambda p: p.ships, reverse=True)
    targets    = [p for p in world.planets if p.owner != world.player]

    if not targets or not my_planets:
        return moves

    # noop baseline (한 번만 계산)
    noop_score = snapshot_score(world, [], snap_turns=SNAP_TURNS)

    targeted      = set()
    pending_ships = {p.id: 0 for p in my_planets}

    for mine in my_planets:
        while True:
            available = mine.ships - pending_ships[mine.id]
            if available < MIN_FLEET:
                break

            # ── 후보 생성 + greedy 점수 ──────────────────────────────
            candidates = []
            for t in targets:
                if t.id in targeted:
                    continue
                dist   = math.hypot(mine.x - t.x, mine.y - t.y)
                # available(pending 차감)로 aim하면 속도가 느려져 garrison이 커지고
                # ships_needed > available인 악순환 발생 → mine.ships(전체)로 속도 계산
                result = aim(mine, t, mine.ships, world.ang_vel)
                if result is None:
                    continue
                angle, ships_needed, turns = result
                if available < ships_needed:
                    continue
                g = greedy_score(
                    world,
                    {"src_id": mine.id, "target_id": t.id, "ships": ships_needed},
                    dist, ships_needed, turns
                )
                candidates.append({
                    "src_id":    mine.id,
                    "target_id": t.id,
                    "ships":     ships_needed,
                    "angle":     angle,
                    "g_score":   g,
                })

            if not candidates:
                break

            # ── greedy로 top-K 필터 ──────────────────────────────────
            candidates.sort(key=lambda c: c["g_score"], reverse=True)
            top_k = candidates[:TOP_K]

            # ── top-K를 snapshot 델타로 재정렬 ──────────────────────
            best_delta = -math.inf
            best_cand  = None

            for cand in top_k:
                action = {"src_id": cand["src_id"],
                          "target_id": cand["target_id"],
                          "ships": cand["ships"]}
                s = snapshot_score(world, [action], snap_turns=SNAP_TURNS)
                delta = s - noop_score
                if delta > best_delta:
                    best_delta = delta
                    best_cand  = cand

            # snapshot 델타가 모두 음수이면 greedy #1 선택
            if best_cand is None or best_delta <= -math.inf:
                best_cand = candidates[0]

            moves.append([mine.id, best_cand["angle"], best_cand["ships"]])
            pending_ships[mine.id] += best_cand["ships"]
            targeted.add(best_cand["target_id"])

            if DEBUG:
                print(f"[SEND] {mine.id}->{best_cand['target_id']}: "
                      f"{best_cand['ships']} ships "
                      f"g={best_cand['g_score']:.1f} snap_delta={best_delta:.2f}")

    return moves
