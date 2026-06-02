"""v11 Hammer/Accumulator/Multiprong 발동 검증"""
import importlib.util, sys, io

spec = importlib.util.spec_from_file_location("v11", "agents/v11.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.DEBUG = True

original_agent = mod.agent
log_lines = []

def debug_agent(obs):
    buf = io.StringIO()
    old = sys.stdout; sys.stdout = buf
    result = original_agent(obs)
    sys.stdout = old
    out = buf.getvalue()
    if out:
        log_lines.extend(out.strip().split('\n'))
    return result

from kaggle_environments import make

print("=== v11 vs v10 (3게임) ===")
for seed in range(3):
    env = make("orbit_wars", configuration={"seed": seed}, debug=False)
    env.run([debug_agent, "agents/v10.py"])
    r0 = env.steps[-1][0].reward
    r1 = env.steps[-1][1].reward
    print(f"Seed {seed}: v11={r0:.0f} v10={r1:.0f} -> {'v11 WIN' if r0>r1 else 'v10 WIN' if r1>r0 else 'DRAW'}")

print()
categories = {
    "ATK": [l for l in log_lines if l.startswith("[ATK]")],
    "ACCUM": [l for l in log_lines if l.startswith("[ACCUM]")],
    "HAMMER": [l for l in log_lines if l.startswith("[HAMMER]")],
    "MULTI": [l for l in log_lines if l.startswith("[MULTI]")],
    "COOP": [l for l in log_lines if l.startswith("[COOP]")],
}
for name, logs in categories.items():
    print(f"{name}: {len(logs)}회", end="")
    if logs:
        print(f" | 샘플: {logs[0]}")
    else:
        print()
