# ROUND1 evaluation (TRAIN+VALIDATION, pooled over slices; rules pre-registered)

| experiment | config | verdict | net base TMN | net adverse TMN | net low TMN | trades | PF | +slices | worst slice % | fee share | vs CTRL TMN | failed rules |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| p04-r1-c1 | lookback=24 stop=2 hold=24h | REJECT | 17025 | -75806 | 24849 | 19 | 1.07 | 3/5 | -1.47 | 0.86 | -146295 | R2; R4; R5; R7 |
| p04-r1-c1 | lookback=24 stop=3 hold=24h | REJECT | 15810 | -55209 | 55837 | 18 | 1.09 | 3/5 | -1.01 | 0.82 | -146295 | R2; R4; R5; R7 |
| p04-r1-c2 | trend_gate=True entry_trend=1 stop=3 hold=24h | REJECT | -6480 | -44370 | 77323 | 32 | 0.98 | 3/5 | -1.31 | 1.05 | -146295 | R1; R4; R5; R7 |
| p04-r1-c1 | lookback=48 stop=2 hold=24h | REJECT | -17861 | -91261 | -9708 | 17 | 0.92 | 2/5 | -1.34 | 1.24 | -146295 | R1; R2; R3; R4; R5; R7 |
| p04-r1-c2 | trend_gate=True entry_trend=0.5 stop=3 hold=24h | REJECT | -19560 | -96072 | 55657 | 40 | 0.94 | 3/5 | -1.64 | 1.14 | -146295 | R1; R4; R5; R7 |
| p04-r1-c1 | lookback=48 stop=3 hold=24h | REJECT | -22442 | -66754 | 22557 | 15 | 0.87 | 2/5 | -1.03 | 1.59 | -146295 | R1; R2; R3; R4; R5; R7 |
| p04-r1-c1 | lookback=24 stop=3 hold=12h | REJECT | -96840 | -63485 | -41147 | 22 | 0.52 | 1/5 | -1.23 | — | -253243 | R1; R2; R3; R4; R5; R7 |
| p04-r1-c1 | lookback=48 stop=3 hold=12h | REJECT | -104454 | -73968 | -16994 | 18 | 0.45 | 1/5 | -1.07 | — | -253243 | R1; R2; R3; R4; R5; R7 |
| p04-r1-c1 | lookback=24 stop=2 hold=12h | REJECT | -112300 | -77906 | -61326 | 22 | 0.57 | 2/5 | -1.52 | 48.69 | -253243 | R1; R2; R3; R4; R5; R7 |
| p04-r1-c1 | lookback=48 stop=2 hold=12h | REJECT | -116864 | -91940 | -34589 | 19 | 0.51 | 1/5 | -1.34 | — | -253243 | R1; R2; R3; R4; R5; R7 |
| p04-r1-c2 | trend_gate=True entry_trend=0.5 stop=2 hold=24h | REJECT | -144021 | -186100 | 2975 | 46 | 0.74 | 2/5 | -3.42 | 2.38 | -146295 | R1; R3; R4; R5; R7 |
| p04-r1-c2 | trend_gate=True entry_trend=1 stop=3 hold=12h | REJECT | -145350 | -131776 | -127290 | 46 | 0.59 | 1/5 | -1.64 | 4.86 | -253243 | R1; R3; R4; R5; R7 |
| p04-r1-c2 | trend_gate=True entry_trend=1 stop=2 hold=24h | REJECT | -180831 | -149083 | -129474 | 38 | 0.65 | 1/5 | -1.96 | 8.15 | -146295 | R1; R3; R4; R5; R6; R7 |
| p04-r1-c2 | trend_gate=True entry_trend=1 stop=2 hold=12h | REJECT | -257247 | -228223 | -275871 | 50 | 0.54 | 1/5 | -2.90 | 77.17 | -253243 | R1; R3; R4; R5; R6; R7 |
| p04-r1-c2 | trend_gate=True entry_trend=0.5 stop=3 hold=12h | REJECT | -341639 | -140214 | -217712 | 64 | 0.40 | 1/5 | -3.34 | — | -253243 | R1; R3; R4; R5; R6; R7 |
| p04-r1-c2 | trend_gate=True entry_trend=0.5 stop=2 hold=12h | REJECT | -358040 | -214837 | -295372 | 63 | 0.47 | 1/5 | -3.42 | — | -253243 | R1; R3; R4; R5; R6; R7 |

Control (drift benchmark):

| config | cost | net TMN | trades | PF | +slices | worst slice % | fees TMN |
|---|---|---:|---:|---:|---:|---:|---:|
| trend_gate=False entry_trend=None stop=3 hold=12h | adverse | -156451 | 44 | 0.34 | 2/5 | -2.09 | 185408 |
| trend_gate=False entry_trend=None stop=3 hold=12h | base | -253243 | 99 | 0.62 | 1/5 | -2.99 | 385029 |
| trend_gate=False entry_trend=None stop=3 hold=12h | low | -52403 | 107 | 0.92 | 2/5 | -3.05 | 361470 |
| trend_gate=False entry_trend=None stop=3 hold=24h | adverse | -143352 | 32 | 0.45 | 2/5 | -2.68 | 136242 |
| trend_gate=False entry_trend=None stop=3 hold=24h | base | -146295 | 65 | 0.73 | 2/5 | -3.45 | 255336 |
| trend_gate=False entry_trend=None stop=3 hold=24h | low | -61687 | 70 | 0.90 | 3/5 | -3.57 | 242903 |

Buy & hold (equal-weight, 100% invested, no costs; benchmark only):

| slice | role | BTCTMN % | ETHTMN % | EW % |
|---|---|---:|---:|---:|
| t1 | TRAIN | -5.40 | -4.98 | -5.19 |
| t2 | TRAIN | 11.42 | 11.67 | 11.55 |
| t3 | TRAIN | 8.70 | 9.89 | 9.29 |
| v1 | VALIDATION | 7.64 | 6.34 | 6.99 |
| v2 | VALIDATION | -6.94 | -12.37 | -9.65 |
