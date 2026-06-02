# Orbit Wars — JW Agent

## Competition Overview

**Orbit Wars** is a multi-agent real-time strategy game on a 100×100 board. Players compete to control planets by sending fleets of ships, with the goal of accumulating the most total ships when time runs out.

- **Competition**: [Kaggle Orbit Wars](https://www.kaggle.com/competitions/orbit-wars)
- **Final Submission Deadline**: 2026-06-23
- **Prize**: $5,000 × 10 winners (1st–10th place)
- **Daily Submission Limit**: 5 (only latest 2 are tracked for ranking)

---

## Game Rules Summary

### Board
- 100×100 continuous space, origin at top-left
- **Sun** at (50, 50) with radius 10 — fleets whose path crosses the sun are destroyed

### Planets
- Fields: `[id, owner, x, y, radius, ships, production]`
- `production` 1–5 ships/turn; `radius = 1 + ln(production)`
- **Inner planets** (orbital radius < `ROTATION_RADIUS_LIMIT`): rotate around the sun at `angular_velocity` rad/turn
- **Outer planets**: static
- 20–40 planets total, placed with 4-fold mirror symmetry

### Fleets
- Action format: `[from_planet_id, angle_radians, num_ships]`
- Travel in a straight line at a fixed angle
- Speed formula: `1.0 + 5.0 × (log(ships) / log(1000))^1.5` → range ~1 to 6 units/turn
- Destroyed if path crosses the sun or leaves the 100×100 boundary
- Collision detection is **continuous** (entire path segment is checked each turn)

### Comets
- Spawn in groups of 4 at turns 50, 150, 250, 350, 450
- Follow elliptical paths; garrison is lost when a comet leaves the board
- Identified via `comet_planet_ids` in the observation

### Combat
- When a fleet reaches a planet, the largest two forces fight first (difference survives)
- Surviving attacker fights the garrison; if attacker > garrison, ownership flips

### Win Condition
- Game ends at turn 500 or when only one player remains
- Score = ships on owned planets + ships in owned fleets

---

## Project Structure

```
orbit-wars-jw/
├── getting-started.ipynb   # Tutorial notebook (nearest_planet_sniper baseline)
├── main.py                 # Current submission agent
└── README.md
```

---

## How to Run Locally

```bash
pip install "kaggle-environments>=1.28.0"
```

```python
from kaggle_environments import make

# Test agent against random
env = make("orbit_wars", debug=True)
env.run(["main.py", "random"])

final = env.steps[-1]
for i, s in enumerate(final):
    print(f"Player {i}: reward={s.reward}, status={s.status}")

# Visualize in Jupyter
env.render(mode="ipython", width=800, height=600)
```

---

## Agent Roadmap

| Version | Name | Key Ideas |
|---------|------|-----------|
| v0 | `nearest_planet_sniper` | 가장 가까운 비소유 행성 공격 (튜토리얼 기본) |
| v1 | `smart_sniper` | 이동 시간 보정, 공전 예측, 태양 회피, 중복 공격 방지 |
| v2 | `economic_sniper` | 생산량 기반 확장 우선순위, 병력 분산 최소화 |
| v3 | `defensive_sniper` | 수비 감지 (incoming fleet 체크), 중요 행성 보호 |
| v4 | `adaptive_agent` | 상대 행동 모델링, 인터셉트/반격 |

---

## Submission

```bash
# 단일 파일 제출
kaggle competitions submit orbit-wars -f main.py -m "smart_sniper v1"

# 멀티 파일 제출 (tar.gz)
tar -czf submission.tar.gz main.py
kaggle competitions submit orbit-wars -f submission.tar.gz -m "v1"

# 제출 상태 확인
kaggle competitions submissions orbit-wars

# 리더보드 확인
kaggle competitions leaderboard orbit-wars -s
```
