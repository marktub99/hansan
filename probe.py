# -*- coding: utf-8 -*-
"""geo-dev 전용 시험 3: 새 collect.py 전체 흐름을 실제 관광공사 목록·국가유산청으로 돌려 본다.
혼잡도는 다시 받지 않고(호출 아끼기) 지금 data.json·unmatched.txt 에서 되살린다. 결과는 probe_out/test 에만 남긴다."""
import json, os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["FORCE"] = "1"
import collect as C

d0 = json.load(open(os.path.join(HERE, "data.json"), encoding="utf-8"))
days = [x.replace("-", "") for x in d0["days"]]
rows = {r[2]: r for r in json.load(open(os.path.join(HERE, "sigungu.json"), encoding="utf-8"))}
rates, names = {}, {}
for s in d0["spots"]:
    r = rows[s[9]]
    rates.setdefault((r[0], r[2]), {})[s[0]] = {dd: v for dd, v in zip(days, s[7]) if v is not None}
    names[(r[0], r[2])] = r[3]
for ln in open(os.path.join(HERE, "unmatched.txt"), encoding="utf-8"):
    if ln.startswith("#") or not ln.strip():
        continue
    sg, rest = ln.split("|", 1)
    r = rows[int(sg)]
    rates.setdefault((r[0], r[2]), {}).setdefault(rest.rsplit("  (", 1)[0], {days[0]: 50})
    names[(r[0], r[2])] = r[3]
C.load_rates = lambda sigungu: (rates, names, [])
rc = C.main()
out = os.path.join(HERE, "probe_out", "test")
os.makedirs(out, exist_ok=True)
for f in ("data.json", "unmatched.txt", "geo_auto.json", "geo_review.md"):
    if os.path.exists(os.path.join(HERE, f)):
        shutil.copy(os.path.join(HERE, f), out)
print("끝 rc=%s" % rc, flush=True)
