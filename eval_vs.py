"""hero 에이전트를 여러 상대와 대결 (양방향, 16코어 병렬). ex-vs-ex 낭비 없음."""
import importlib.util, sys, io, os, argparse
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DEFAULT_WORKERS = max(1, (os.cpu_count() or 2) // 2)

_CACHE = {}
def _load(name):
    if name not in _CACHE:
        spec = importlib.util.spec_from_file_location(name, f"agents/{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CACHE[name] = mod
    return _CACHE[name]

def _worker(task):
    hero, opp, seed, side = task
    from kaggle_environments import make
    h, o = _load(hero), _load(opp)
    env = make("orbit_wars", configuration={"seed": seed}, debug=False)
    if side == 0:
        env.run([h.agent, o.agent]); r0, r1 = env.steps[-1][0].reward, env.steps[-1][1].reward
    else:
        env.run([o.agent, h.agent]); r1, r0 = env.steps[-1][0].reward, env.steps[-1][1].reward
    return opp, ("W" if r0 > r1 else ("L" if r0 < r1 else "D"))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("hero")
    ap.add_argument("opponents", nargs="+")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("-j", type=int, default=DEFAULT_WORKERS)
    args = ap.parse_args()

    tasks = [(args.hero, opp, seed, side)
             for opp in args.opponents for seed in range(args.n) for side in (0, 1)]
    print(f"=== {args.hero} vs {len(args.opponents)} opponents "
          f"({args.n}×2={args.n*2} games each, j={args.j}) ===")
    agg = {opp: {"W": 0, "D": 0, "L": 0} for opp in args.opponents}
    with ProcessPoolExecutor(max_workers=args.j) as ex:
        for fut in as_completed([ex.submit(_worker, t) for t in tasks]):
            opp, res = fut.result(); agg[opp][res] += 1
    for opp in args.opponents:
        a = agg[opp]; tot = a["W"]+a["D"]+a["L"]; dec = a["W"]+a["L"]
        wr = a["W"]/tot*100 if tot else 0; dwr = a["W"]/dec*100 if dec else 0
        print(f"{args.hero} vs {opp:16s} W{a['W']:3d} D{a['D']:2d} L{a['L']:3d}  "
              f"({wr:5.1f}% all | {dwr:5.1f}% decisive)")
