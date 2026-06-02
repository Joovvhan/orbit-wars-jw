"""v11 hammer 설정 스윕: v11(설정 패치) vs v10, 양방향. 멀티코어 병렬."""
import importlib.util, sys, io, os, argparse
from concurrent.futures import ProcessPoolExecutor, as_completed

DEFAULT_WORKERS = max(1, (os.cpu_count() or 2) // 2)  # 논리코어의 절반

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CONFIGS = {
    "t12 s70 (winner)":  dict(HAMMER_ENABLED=True, HAMMER_MAX_TRAVEL=12, HAMMER_STOCKPILE_MIN=70, HAMMER_OVERKILL_RATIO=1.35),
    "t14 s70":           dict(HAMMER_ENABLED=True, HAMMER_MAX_TRAVEL=14, HAMMER_STOCKPILE_MIN=70, HAMMER_OVERKILL_RATIO=1.35),
    "t12 s60":           dict(HAMMER_ENABLED=True, HAMMER_MAX_TRAVEL=12, HAMMER_STOCKPILE_MIN=60, HAMMER_OVERKILL_RATIO=1.35),
    "t12 s80":           dict(HAMMER_ENABLED=True, HAMMER_MAX_TRAVEL=12, HAMMER_STOCKPILE_MIN=80, HAMMER_OVERKILL_RATIO=1.35),
    "t12 s70 ovk1.5":    dict(HAMMER_ENABLED=True, HAMMER_MAX_TRAVEL=12, HAMMER_STOCKPILE_MIN=70, HAMMER_OVERKILL_RATIO=1.5),
}

# ─── 워커 (프로세스별 1회 모듈 로드 후 캐시) ───────────────────────────────
_CACHE = {}

def _load(name):
    if name not in _CACHE:
        spec = importlib.util.spec_from_file_location(name, f"agents/{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CACHE[name] = mod
    return _CACHE[name]

def _worker(task):
    cfg_name, cfg, opp, seed, side = task
    from kaggle_environments import make
    v11 = _load("v11")
    other = _load(opp)
    for k, val in cfg.items():
        setattr(v11, k, val)

    def play(a, b):
        env = make("orbit_wars", configuration={"seed": seed}, debug=False)
        env.run([a, b])
        return env.steps[-1][0].reward, env.steps[-1][1].reward

    if side == 0:
        r0, r1 = play(v11.agent, other.agent)
    else:
        r1, r0 = play(other.agent, v11.agent)
    res = "W" if r0 > r1 else ("L" if r0 < r1 else "D")
    return cfg_name, res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=24, help="시드 수")
    ap.add_argument("-j", type=int, default=DEFAULT_WORKERS,
                    help=f"병렬 워커 수 (기본=논리코어/2={DEFAULT_WORKERS})")
    ap.add_argument("--opp", type=str, default="v10", help="상대 에이전트")
    args = ap.parse_args()

    tasks = [(name, cfg, args.opp, seed, side)
             for name, cfg in CONFIGS.items()
             for seed in range(args.n)
             for side in (0, 1)]

    print(f"=== v11 hammer sweep vs {args.opp} "
          f"({args.n} seeds × 2 = {args.n*2} games/cfg, j={args.j}) ===")

    agg = {name: {"W": 0, "D": 0, "L": 0} for name in CONFIGS}
    with ProcessPoolExecutor(max_workers=args.j) as ex:
        for fut in as_completed([ex.submit(_worker, t) for t in tasks]):
            name, res = fut.result()
            agg[name][res] += 1

    for name in CONFIGS:
        a = agg[name]
        tot = a["W"] + a["D"] + a["L"]
        dec = a["W"] + a["L"]
        wr = a["W"] / tot * 100 if tot else 0
        dwr = a["W"] / dec * 100 if dec else 0
        print(f"{name:20s} W{a['W']:3d} D{a['D']:2d} L{a['L']:3d}  "
              f"({wr:5.1f}% all | {dwr:5.1f}% decisive)")
