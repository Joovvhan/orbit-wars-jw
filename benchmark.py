"""
벤치마크: 에이전트 간 성능 비교
사용법:
  uv run python benchmark.py v2 v1          # v2 vs v1 (30게임)
  uv run python benchmark.py v2 v1 v3 -n 50 # 50게임씩
  uv run python benchmark.py v3 ex_proto -n 20 --out results/v3_vs_examples.json
  uv run python benchmark.py v3 ex_proto -n 20 -j 8  # 8 workers
"""
import argparse
import importlib.util
import io
import json
import os
import sys
import time

# 옵션 미지정 시 기본값: 논리코어의 절반
DEFAULT_WORKERS = max(1, (os.cpu_count() or 2) // 2)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from kaggle_environments import make


def load_agent(name: str):
    """agents/{name}.py 에서 agent 함수 로드"""
    path = f"agents/{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.agent


def run_match(agent_a, agent_b, seed: int):
    env = make("orbit_wars", configuration={"seed": seed}, debug=False)
    env.run([agent_a, agent_b])
    r0 = env.steps[-1][0].reward
    r1 = env.steps[-1][1].reward
    if r0 > r1:   return "W"
    if r0 < r1:   return "L"
    return "D"


def _run_match_worker(args):
    """병렬 워커: (name_a, name_b, seed, side) → record dict"""
    name_a, name_b, seed, side = args
    agent_a = load_agent(name_a)
    agent_b = load_agent(name_b)
    t0 = time.time()
    if side == 0:
        r = run_match(agent_a, agent_b, seed)
    else:
        raw = run_match(agent_b, agent_a, seed)
        r = "W" if raw == "L" else ("L" if raw == "W" else "D")
    return {"a": name_a, "b": name_b, "seed": seed, "side": side,
            "result": r, "secs": round(time.time() - t0, 2)}


def benchmark(names: list[str], n_games: int, out_path: str = None,
              workers: int = DEFAULT_WORKERS):
    seeds = list(range(n_games))
    pairs = [(a, b) for i, a in enumerate(names) for b in names[i+1:]]
    total_matches = len(pairs) * n_games * 2

    print(f"\n{'='*55}")
    print(f"벤치마크: {len(pairs)}쌍 × {n_games}게임 × 2방향 = {total_matches}게임  (workers={workers})")
    print(f"에이전트: {', '.join(names)}")
    print(f"{'='*55}\n")

    # 태스크 목록 생성
    tasks = [
        (a, b, seed, side)
        for a, b in pairs
        for seed in seeds
        for side in (0, 1)
    ]

    all_records = []
    t_start = time.time()

    if HAS_TQDM:
        pbar = tqdm(total=total_matches, unit="game", ncols=70)
    else:
        print(f"(tqdm 미설치 — 설치: uv add tqdm)\n")
        pbar = None

    if workers <= 1:
        # 순차 실행
        for task in tasks:
            rec = _run_match_worker(task)
            all_records.append(rec)
            if pbar: pbar.update(1)
    else:
        # 병렬 실행
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_match_worker, task): task for task in tasks}
            for fut in as_completed(futures):
                rec = fut.result()
                all_records.append(rec)
                if pbar: pbar.update(1)

    if pbar: pbar.close()

    # 결과 집계
    results = {}
    for a, b in pairs:
        recs = [r for r in all_records if r["a"] == a and r["b"] == b]
        w = sum(1 for r in recs if r["result"] == "W")
        d = sum(1 for r in recs if r["result"] == "D")
        l = sum(1 for r in recs if r["result"] == "L")
        total = w + d + l
        pct = w / total * 100 if total else 0
        results[(a, b)] = {"w": w, "d": d, "l": l, "total": total, "win_pct": round(pct, 1)}

    elapsed_total = time.time() - t_start
    avg_per_game = elapsed_total / total_matches if total_matches else 0

    print(f"\n{'='*55}")
    print(f"결과  ({n_games}게임 × 2방향 = {n_games*2}게임/쌍)")
    print(f"{'='*55}")
    for (a, b), r in results.items():
        bar = "#" * int(r["win_pct"] / 5) + "." * (20 - int(r["win_pct"] / 5))
        print(f"  {a:20s} vs {b:20s}  W{r['w']:3d} D{r['d']:2d} L{r['l']:3d}  ({r['win_pct']:5.1f}%)  {bar}")
    print(f"\n총 소요: {elapsed_total:.1f}초  (게임당 평균 {avg_per_game:.2f}초)")

    if out_path:
        save_path = Path(out_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        save_path = Path("results") / f"bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        save_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "timestamp": datetime.now().isoformat(),
        "agents": names,
        "n_games": n_games,
        "workers": workers,
        "total_matches": total_matches,
        "elapsed_secs": round(elapsed_total, 1),
        "avg_secs_per_game": round(avg_per_game, 2),
        "summary": {f"{a}_vs_{b}": v for (a, b), v in results.items()},
        "records": all_records,
    }
    save_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"결과 저장: {save_path}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("agents", nargs="+", help="비교할 에이전트 이름 (예: v1 v2)")
    parser.add_argument("-n", "--games", type=int, default=30, help="게임 수 (기본 30)")
    parser.add_argument("-j", "--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"병렬 워커 수 (기본=논리코어/2={DEFAULT_WORKERS}, 1이면 순차)")
    parser.add_argument("--out", type=str, default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    if len(args.agents) < 2:
        print("에이전트를 2개 이상 지정하세요.")
        sys.exit(1)

    benchmark(args.agents, args.games, out_path=args.out, workers=args.workers)
