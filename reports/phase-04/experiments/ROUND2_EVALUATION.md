# ROUND2 evaluation (TRAIN+VALIDATION, pooled over slices; rules pre-registered)

| experiment | config | verdict | net base TMN | net adverse TMN | net low TMN | trades | PF | +slices | worst slice % | fee share | vs CTRL TMN | failed rules |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| p04-r2-c2 | trend_gate=True entry_trend=0.5 stop=3 hold=48h | REJECT | 85292 | -15015 | 78708 | 26 | 1.36 | 3/5 | -1.88 | 0.56 | 62163 | R2; R5 |
| p04-r2-c2 | trend_gate=True entry_trend=1 stop=3 hold=48h | REJECT | 29462 | -31538 | 67869 | 23 | 1.13 | 3/5 | -2.34 | 0.76 | 62163 | R2; R5; R6 |

Control (drift benchmark):

| config | cost | net TMN | trades | PF | +slices | worst slice % | fees TMN |
|---|---|---:|---:|---:|---:|---:|---:|
| trend_gate=False entry_trend=None stop=3 hold=48h | adverse | -109753 | 22 | 0.49 | 3/5 | -2.75 | 91832 |
| trend_gate=False entry_trend=None stop=3 hold=48h | base | 62163 | 41 | 1.18 | 3/5 | -2.50 | 161416 |
| trend_gate=False entry_trend=None stop=3 hold=48h | low | 69085 | 47 | 1.16 | 3/5 | -3.01 | 162710 |

Buy & hold (equal-weight, 100% invested, no costs; benchmark only):

| slice | role | BTCTMN % | ETHTMN % | EW % |
|---|---|---:|---:|---:|
| t1 | TRAIN | -5.40 | -4.98 | -5.19 |
| t2 | TRAIN | 11.42 | 11.67 | 11.55 |
| t3 | TRAIN | 8.70 | 9.89 | 9.29 |
| v1 | VALIDATION | 7.64 | 6.34 | 6.99 |
| v2 | VALIDATION | -6.94 | -12.37 | -9.65 |
