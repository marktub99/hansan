# -*- coding: utf-8 -*-
"""좌표 보충 설계용 시험 2: 국가유산청 목록(키 없음)을 시도별로 받아 본다. geo-dev 전용."""
import gzip, json, os, re, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_out")
os.makedirs(OUT, exist_ok=True)
URL = "https://www.khs.go.kr/cha/SearchKindOpenapiList.do?"
FIELDS = ("ccbaKdcd", "ccbaAsno", "ccbaCtcd", "ccmaName", "ccbaMnm1", "ccbaCtcdNm", "ccsiName",
          "ccbaCncl", "longitude", "latitude", "ccbaAdmin")


def get(params):
    req = urllib.request.Request(URL + urllib.parse.urlencode(params), headers={"User-Agent": "Mozilla/5.0 crowdmap"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


calls, rows, info = 0, [], {}
for ctcd in ["11", "21", "22", "23", "24", "25", "26", "45", "31", "32", "33", "34", "35", "36", "37", "38", "50"]:
    page, got, total = 1, 0, None
    while True:
        try:
            raw = get({"ccbaCtcd": ctcd, "pageUnit": 1000, "pageIndex": page})
        except Exception as e:
            info[ctcd] = {"err": type(e).__name__, "page": page}
            break
        calls += 1
        root = ET.fromstring(raw)
        total = int(root.findtext("totalCnt") or 0)
        items = root.findall("item")
        for it in items:
            rows.append({f: (it.findtext(f) or "").strip() for f in FIELDS})
        got += len(items)
        if not items or got >= total or page >= 15:
            break
        page += 1
        time.sleep(0.5)
    info.setdefault(ctcd, {"total": total, "got": got, "pages": page})
    print("국가유산청", ctcd, info[ctcd], flush=True)
    time.sleep(0.5)
with gzip.open(os.path.join(OUT, "khs.json.gz"), "wt", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False)
# 이름 검색 방식 확인(띄어쓰기)
test = {}
for q in ["경운동민병옥가옥", "민병옥", "가학동지석묘", "가학동 지석묘"]:
    try:
        raw = get({"ccbaMnm1": q, "pageUnit": 5})
        calls += 1
        root = ET.fromstring(raw)
        test[q] = [int(root.findtext("totalCnt") or 0)] + [it.findtext("ccbaMnm1") for it in root.findall("item")]
    except Exception as e:
        test[q] = type(e).__name__
    time.sleep(0.5)
json.dump({"info": info, "test": test}, open(os.path.join(OUT, "khs_info.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("이름 검색:", test, flush=True)
print("끝. 호출", calls, "건, 항목", len(rows), "건", flush=True)
