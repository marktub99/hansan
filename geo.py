# -*- coding: utf-8 -*-
"""위치를 못 찾은 관광지의 좌표를 공공기관 자료로 보충한다. collect.py 가 부른다(표준 라이브러리만 사용).

찾는 곳(모두 저장·공개해도 되는 공공기관 자료)
  1) 한국관광공사 국문 관광정보: collect.py 가 매일 받는 전국 목록을 별칭·표기 차이·행정구역 개편까지 감안해 다시 맞춤(추가 호출 없음)
  2) 한국관광공사 중심 관광지(hub.json): 이미 받아 둔 자료(추가 호출 없음)
  3) 국가유산청 국가유산 목록(인증키 없음): 아직 안 찾아본 이름이 있는 시도만 받아 맞춤. 찾은 결과는 저장해 60일 동안 다시 받지 않음
  카카오 로컬 API는 결과 좌표의 저장·공개가 운영정책상 금지라 쓰지 않는다.

파일
  geo_auto.json  자동 결과(사람이 고치지 않음): {"적용": {키: 결과}, "검색기록": {키: {"at": 날짜, "c": 후보}}}
  geo_review.md  이름이 애매한 후보. 줄 맨 앞 ? 를 O(맞음) 또는 X(아님)로 바꾸면 다음 수집 때 반영
  overrides.json 사람이 직접 고치는 파일. 이 모듈은 읽지도 쓰지도 않는다(collect.py 가 가장 먼저 적용).
로그에는 개수만 남긴다(공개 저장소라 Actions 기록도 공개됨).
"""
import difflib
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
AUTO_FILE, REVIEW_FILE = "geo_auto.json", "geo_review.md"
KHS_URL = "https://www.khs.go.kr/cha/SearchKindOpenapiList.do?"
KHS_RETRY_DAYS = 60
KHS_MAX_CALLS = 60
AUTO_MIN = 0.96            # 이 점수 이상이면 사실상 같은 이름(같은 이름·표기 차이)
CAND_MIN = 0.75            # 확인 목록에 올릴 최소 점수
SAME_SPOT_M = 300          # 이름이 같은 후보가 이 거리 안에 모여 있으면 같은 곳으로 본다

# 행정구역 개편으로 혼잡도 자료(옛 코드)와 관광정보(새 법정동 코드)가 어긋나는 곳
REGION_ALIAS = {
    "28110": ("28125", "28155"),                    # 인천 중구 -> 제물포구·영종구 (2026.7.1.)
    "28140": ("28125",),                            # 인천 동구 -> 제물포구
    "28260": ("28275", "28290"),                    # 인천 서구 -> 서해구·검단구
    "41590": ("41591", "41593", "41595", "41597"),  # 화성시 -> 4개 구 (2026.2.1.)
}
# 국가유산청 시도 코드 -> 혼잡도 지역 표기의 첫 단어
KHS_SIDO = {"11": "서울", "21": "부산", "22": "대구", "23": "인천", "25": "대전", "26": "울산", "45": "세종",
            "31": "경기", "32": "강원", "33": "충북", "34": "충남", "35": "전북", "37": "경북", "38": "경남",
            "50": "제주", "24": "광주", "36": "전남"}
SIDO_WORDS = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남",
              "전북", "전남", "경북", "경남", "제주", "전남광주", "서울특별시", "부산광역시", "대구광역시", "인천광역시",
              "광주광역시", "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원도", "강원특별자치도",
              "충청북도", "충청남도", "전라북도", "전북특별자치도", "전라남도", "경상북도", "경상남도", "제주도",
              "제주특별자치도", "전남광주통합특별시"]
# 이것만으로는 어디인지 알 수 없는 말(별칭으로 쓰지 않음)
GENERIC = {"박물관", "미술관", "공원", "시장", "해변", "해수욕장", "유원지", "전망대", "놀이터", "체험장", "주차장",
           "광장", "선착장", "등대", "폭포", "계곡", "사찰", "성당", "교회", "서원", "향교", "유적지", "본관", "구",
           "기념관", "문학관", "전시관", "체험관", "생태공원", "유아숲체험장", "산책로", "캠핑장", "오름", "온천",
           "리조트", "호텔", "옛미군기지", "슬로시티", "본점", "분관", "남산분관", "남부분관", "일대", "마을",
           "생태체험장", "체험마을", "관광농원", "농원", "시민공원", "체육공원", "산림욕장", "자연휴양림", "곶자왈"}
# 장소 종류를 나타내는 꼬리말. 종류가 같으면 앞부분(고유한 이름)끼리만 비교한다(쉼박물관≠떡박물관).
KIND_TAIL = sorted(["농촌체험휴양마을", "산촌생태마을", "체험휴양마을", "체험마을", "테마파크", "근린공원", "체육공원",
                    "어린이공원", "생태공원", "공원", "종합시장", "전통시장", "시장", "박물관", "미술관", "갤러리",
                    "기념관", "문학관", "역사관", "과학관", "전시관", "체험관", "생태관", "회관", "도서관", "수목원",
                    "식물원", "휴양림", "캠핑장", "야영장", "썰매장", "스키장", "관광농원", "농원", "농장", "목장",
                    "다원", "마을", "해수욕장", "해변", "해안", "선착장", "유람선", "서원", "향교", "사당", "고택",
                    "가옥", "성당", "교회", "폭포", "계곡", "저수지", "호수", "습지", "오름", "산성", "고분군",
                    "묘역", "센터", "체험장", "놀이터", "랜드", "파크", "리조트", "호텔", "콘도미니엄", "콘도", "스파",
                    "온천", "거리", "골목"], key=len, reverse=True)
GENERIC_TAIL = re.compile(r"(지질공원|세계유산|세계자연유산|세계문화유산|국립공원|도립공원|군립공원|슬로시티|점)$")
FILLER = ("근린", "전통")
SYNONYM = (("해수욕장", "해변"), ("해안", "해변"), ("저수지", "지"))

_paren_all = re.compile(r"[\(\[（][^\)\]）]*[\)\]）]")
_junk = re.compile(r"[\s\-·ㆍ‧.,'\"/&~!?:]+")
_tag = re.compile(r"\[[^\]]*\]")
_par = re.compile(r"[\(（]([^\)）]*)[\)）]?")
_split = re.compile(r"\s*/\s*|\s*&\s*|·|,|\s+및\s+|(?<=[가-힣])[과와]\s+(?=[가-힣])|\s+일대$")


def nk(s):
    return _junk.sub("", s or "").lower()


def canon(n):
    for f in FILLER:
        n = n.replace(f, "")
    for a, b in SYNONYM:
        n = n.replace(a, b)
    return n


def kind(n):
    """'뚝도시장' -> ('뚝도', '시장'). 꼬리말이 없으면 (n, '')"""
    for t in KIND_TAIL:
        if n.endswith(t) and len(n) > len(t):
            return n[:-len(t)], t
    return n, ""


def dist_m(lat1, lng1, lat2, lng2):
    p = math.pi / 180
    x = (lng2 - lng1) * p * math.cos((lat1 + lat2) * p / 2)
    return math.hypot(x, (lat2 - lat1) * p) * 6371000


def region_words(sigungu):
    w = {nk(x) for x in SIDO_WORDS}
    for row in sigungu:
        for tok in str(row[3]).split():
            w.add(nk(tok))
            if len(tok) > 2:
                w.add(nk(re.sub(r"(시|군|구)$", "", tok)))
    w.discard("")
    return w


def aliases(name, rw):
    """이름 -> [(정규화한 별칭, 괄호 밖 이름인지)]. 괄호·'/'·'및'·'과' 로 나뉜 이름을 각각 별칭으로 본다."""
    base = _tag.sub(" ", name or "")
    inner = _par.findall(base)
    main = _par.sub(" ", base).strip()
    out = []

    def add(s, is_main, whole=False):
        s = s.strip()
        if not s:
            return
        toks = s.split()
        if len(toks) > 1 and nk(toks[0]) in rw:          # '충주 탄금대' -> '탄금대'도 별칭
            add(" ".join(toks[1:]), is_main, whole)
        n = nk(s)
        if len(n) < 2 or n in rw or n in GENERIC:
            return
        if not whole and (len(n) < 3 or GENERIC_TAIL.search(n)):   # 조각 이름('창고' 등)은 3글자 이상만
            return
        if all(n != o[0] for o in out):
            out.append((n, is_main))

    add(main, True, True)
    for part in _split.split(main):
        add(part, True)
    for p in inner:
        if nk(p) in rw:                                   # (서울), (고양) 같은 지역 꼬리표
            continue
        add(p, False)
        for part in _split.split(p):
            add(part, False)
    return out


def score(al, bl):
    """두 별칭 목록의 가장 좋은 일치 -> (점수, 이유). AUTO_MIN 이상이면 사실상 같은 이름."""
    best = (0.0, "")
    for a, a_main in al:
        for b, b_main in bl:
            if a == b:
                s, why = 1.0, "같은 이름"
            elif len(canon(a)) >= 2 and canon(a) == canon(b):
                s, why = 0.97, "표기 차이"
            else:
                (sa, ka), (sb, kb) = kind(a), kind(b)
                if ka and kb:
                    if canon(ka) != canon(kb):               # 썰매장 ≠ 캠핑장
                        continue
                    a2, b2, need = sa, sb, 2                 # 종류가 같으면 고유한 앞부분만 비교
                else:
                    a2, b2, need = a, b, 3
                if len(a2) >= need and len(b2) >= need and (a2 in b2 or b2 in a2) and \
                        (ka and kb or min(len(a2), len(b2)) / max(len(a2), len(b2)) >= 0.6):
                    s, why = 0.85, "일부 포함"
                else:
                    m = difflib.SequenceMatcher(None, a2, b2)
                    if m.real_quick_ratio() < CAND_MIN or m.quick_ratio() < CAND_MIN:
                        continue
                    r = m.ratio()
                    if r < CAND_MIN:
                        continue
                    s, why = min(r, 0.9), "비슷한 이름"
            if not a_main:
                s -= 0.01 if b_main else 0.03              # 괄호 안 별칭으로 맞춘 경우 조금 낮게(양쪽 다 괄호면 더)
            if s > best[0]:
                best = (s, why)
    return best


def decide(cands):
    """같은 시군구에서 사실상 같은 이름이고 한 곳으로 모이면 자동 -> (자동 결과, None).
    아니면 (None, 확인 후보 목록: 자리가 겹치지 않게 점수순)."""
    top = [c for c in cands if c["home"] and c["s"] >= AUTO_MIN]
    if top:
        top.sort(key=lambda c: (-c["s"], ("kor", "khs", "hub").index(c["src"])))
        a = top[0]
        if all(dist_m(a["lat"], a["lng"], c["lat"], c["lng"]) <= SAME_SPOT_M for c in top):
            return a, None
    order = sorted(cands, key=lambda c: (-(c["s"] + (0.05 if c["home"] else 0)), ("kor", "khs", "hub").index(c["src"])))
    picked = []
    for c in order:
        if all(dist_m(c["lat"], c["lng"], p["lat"], p["lng"]) > SAME_SPOT_M for p in picked):
            picked.append(c)
    return None, picked


# ---------- 관광공사 목록·중심 관광지 ----------
class Finder:
    def __init__(self, places, hub, sigungu, matched_ids):
        """places: collect.load_places() 결과(lc=법정동 시군구 코드), hub: {시군구코드: [[순위, 이름, 경도, 위도, 중분류]]},
        matched_ids: {시군구코드: [이미 맞춘 장소의 contentid]} -> 혼잡도 코드가 실제로 가리키는 법정동 코드를 알아낸다."""
        self.rw = region_words(sigungu)
        self.by_lc, self.by_alias, self.by_id = {}, {}, {}
        for p in places:
            p["al"] = aliases(p["title"], self.rw)
            self.by_lc.setdefault(p.get("lc", ""), []).append(p)
            self.by_id[p["id"]] = p
            for a, is_main in p["al"]:
                if is_main and len(a) >= 4:
                    self.by_alias.setdefault(a, []).append(p)
        self.hub = {}
        for sg, rows in (hub or {}).items():
            self.hub[str(sg)] = [{"title": h[1], "x": h[2], "y": h[3], "mcls": h[4],
                                  "al": aliases(h[1], self.rw)} for h in rows]
        self.emp, self.box = {}, {}
        for sg, ids in matched_ids.items():
            cnt = {}
            for i in ids:
                p = self.by_id.get(i)
                if p and p.get("lc"):
                    cnt[p["lc"]] = cnt.get(p["lc"], 0) + 1
            tot = sum(cnt.values()) or 1
            self.emp[sg] = {c for c, n in cnt.items() if n >= 2 and n / tot >= 0.1 and c[:4] == sg[:4]}

    def codes(self, sg):
        """-> (자동으로 인정할 법정동 코드, 확인 목록까지 볼 코드=같은 시의 다른 구 포함)"""
        home = {sg} | set(REGION_ALIAS.get(sg, ())) | self.emp.get(sg, set())
        wide = set(home)
        for c in self.by_lc:
            if c and any(c[:4] == h[:4] for h in home):
                wide.add(c)
        return home, wide

    def near(self, sg, lat, lng, km=5):
        """후보가 그 시군구 둘레(관광정보 장소들의 범위 + km) 안에 있는지"""
        if sg not in self.box:
            ys, xs = [], []
            for c in self.codes(sg)[0]:
                for p in self.by_lc.get(c, ()):
                    ys.append(p["y"]); xs.append(p["x"])
            self.box[sg] = (min(ys), max(ys), min(xs), max(xs)) if ys else None
        b = self.box[sg]
        if not b:
            return False
        dy, dx = km / 111.0, km / 89.0
        return b[0] - dy <= lat <= b[1] + dy and b[2] - dx <= lng <= b[3] + dx

    def find(self, sg, name):
        al = aliases(name, self.rw)
        if not al:
            return []
        home, wide = self.codes(sg)
        cands, seen = [], set()
        for c in wide:
            for p in self.by_lc.get(c, ()):
                s, why = score(al, p["al"])
                if s >= CAND_MIN:
                    seen.add(p["id"])
                    cands.append(kor_cand(p, s, why, c in home))
        for h in self.hub.get(sg, ()):
            s, why = score(al, h["al"])
            if s >= CAND_MIN:
                cands.append({"src": "hub", "id": h["title"], "name": h["title"], "lat": h["y"], "lng": h["x"],
                              "addr": "", "s": s, "why": why, "home": True, "mcls": h["mcls"]})
        for a, is_main in al:                               # 다른 지역에 이름이 똑같은 곳(확인 목록으로만)
            if is_main and len(a) >= 4:
                for p in self.by_alias.get(a, ()):
                    if p["id"] not in seen:
                        seen.add(p["id"])
                        cands.append(kor_cand(p, 0.9, "옆 지역의 같은 이름", False))
        # 다른 시군구 후보는 경계 근처(5km)만 남긴다 — 이름만 같은 먼 곳(덕양서원 고양≠곡성) 제외
        return [c for c in cands if c["home"] or self.near(sg, c["lat"], c["lng"])]


def kor_cand(p, s, why, home):
    return {"src": "kor", "id": p["id"], "name": p["title"], "lat": p["y"], "lng": p["x"], "addr": p.get("addr", ""),
            "s": s, "why": why, "home": home, "t": p.get("t", ""), "l3": p.get("l3", "")}


# ---------- 국가유산청 ----------
def khs_get(params, tries=3):
    q = urllib.parse.urlencode(params)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(KHS_URL + q, headers={"User-Agent": "Mozilla/5.0 crowdmap"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return ET.fromstring(r.read())
        except Exception as e:                                # 서버가 연결을 자주 끊어 몇 번 다시 시도
            last = e
            time.sleep(3 * (i + 1))
    raise last


def khs_fetch(ctcds, log, budget=KHS_MAX_CALLS):
    """시도별 국가유산 목록 -> (항목 목록, 다 받은 시도 이름들, 호출 수). 좌표 없는 항목은 뺀다."""
    rows, done, calls = [], set(), 0
    for ctcd in ctcds:
        page, got, ok = 1, 0, True
        while True:
            if calls >= budget:
                ok = False
                break
            try:
                root = khs_get({"ccbaCtcd": ctcd, "pageUnit": 1000, "pageIndex": page})
            except Exception as e:
                log("   국가유산청 목록 실패:", KHS_SIDO.get(ctcd, ctcd), type(e).__name__)
                ok = False
                break
            calls += 1
            total = int(root.findtext("totalCnt") or 0)
            items = root.findall("item")
            for it in items:
                try:
                    lat, lng = float(it.findtext("latitude") or 0), float(it.findtext("longitude") or 0)
                except ValueError:
                    continue
                if 33 < lat < 39 and 124 < lng < 132:
                    rows.append({"name": (it.findtext("ccbaMnm1") or "").strip(), "sido": KHS_SIDO.get(ctcd, ""),
                                 "sgg": (it.findtext("ccsiName") or "").strip(), "lat": lat, "lng": lng,
                                 "id": "%s-%s-%s" % (it.findtext("ccbaKdcd"), it.findtext("ccbaAsno"), ctcd)})
            got += len(items)
            if not items or got >= total:
                break
            page += 1
            time.sleep(1)
        if ok:
            done.add(KHS_SIDO.get(ctcd, ""))
        time.sleep(1)
    return rows, done, calls


def khs_cands(rows, rw, name, label):
    al = aliases(name, rw)
    toks = (label or "").split()
    sido, sggs = (toks[0] if toks else ""), set(toks[1:])
    out = []
    for r in rows:
        if r["sido"] != sido:
            continue
        s, why = score(al, r["al"])
        if s >= CAND_MIN:
            g = r["sgg"].split()
            home = sido == "세종" or bool(g and g[0] in sggs)
            out.append({"src": "khs", "id": r["id"], "name": r["name"], "lat": r["lat"], "lng": r["lng"],
                        "addr": (r["sido"] + " " + r["sgg"]).strip(), "s": round(s, 3), "why": why, "home": home})
    out.sort(key=lambda c: -c["s"])
    return out[:3]


# ---------- 결과 파일 ----------
def load_auto(here):
    try:
        d = json.load(open(os.path.join(here, AUTO_FILE), encoding="utf-8"))
        return {"적용": d.get("적용", {}), "검색기록": d.get("검색기록", {}), "확인대기": d.get("확인대기", {})}
    except Exception:
        return {"적용": {}, "검색기록": {}, "확인대기": {}}


_line = re.compile(r"^\s*[-*]?\s*([?？OoXx○ㅇ×ㅐㅌ])\s+(\d{5}\|.+?)\s+→\s.*`([a-z]+):([^@`]*)@(-?\d+\.\d+),(-?\d+\.\d+)`\s*$")


def load_decisions(here):
    """geo_review.md 에서 사람이 표시한 결정 -> {키: [(표시, 출처, id, 위도, 경도)]}"""
    out = {}
    try:
        lines = open(os.path.join(here, REVIEW_FILE), encoding="utf-8").read().splitlines()
    except Exception:
        return out
    for ln in lines:
        m = _line.match(ln)
        if m:
            mark = m.group(1)
            mark = "O" if mark in "Oo○ㅇㅐ" else ("X" if mark in "Xx×ㅌ" else "?")   # ㅐ·ㅌ: 한글 자판으로 O·X 를 누른 경우
            out.setdefault(m.group(2), []).append((mark, m.group(3), m.group(4), float(m.group(5)), float(m.group(6))))
    return out


def slim(a, at):
    d = {k: a[k] for k in ("src", "id", "name", "why") if a.get(k) not in (None, "")}
    d["lat"], d["lng"], d["at"] = round(float(a["lat"]), 6), round(float(a["lng"]), 6), at
    for k in ("t", "l3", "mcls", "addr"):
        if a.get(k):
            d[k] = a[k]
    return d


# ---------- 한 번에 처리 ----------
def resolve(entries, finder, here, log, today=None, use_khs=True):
    """entries: [(시군구코드, 이름, 지역표기)] -> (결과 {키: 결과}, 확인 대기 수, 자동 수).
    결과: {"lat","lng","src","id","name","why", ...}. geo_auto.json 과 geo_review.md 를 새로 쓴다."""
    today = today or datetime.now(KST).strftime("%Y-%m-%d")
    store = load_auto(here)
    decisions = load_decisions(here)
    out, review, later = {}, {}, []
    for sg, name, label in entries:
        key = "%s|%s" % (sg, name)
        ds = decisions.get(key, [])
        ok = [d for d in ds if d[0] == "O"]
        if ok:                                               # 사람이 맞다고 한 후보
            _, src, cid, lat, lng = ok[0]
            out[key] = {"lat": lat, "lng": lng, "src": src, "id": cid, "why": "확인함"}
            continue
        rejected = {(d[1], d[2]) for d in ds if d[0] == "X"}
        cands = [c for c in finder.find(sg, name) if (c["src"], str(c["id"])) not in rejected]
        later.append([sg, name, label, key, cands, rejected])

    # 국가유산청: 찾아본 적 없거나 오래된 이름이 있는 시도만 받는다
    fresh, rows = set(), []
    if use_khs:
        limit = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KHS_RETRY_DAYS)).strftime("%Y-%m-%d")
        need = {e[2].split()[0] for e in later if e[2] and (store["검색기록"].get(e[3]) or {}).get("at", "") < limit}
        ctcds = [c for c, nm in KHS_SIDO.items() if nm in need]
        if ctcds:
            rows, fresh, calls = khs_fetch(ctcds, log)
            for r in rows:
                r["al"] = aliases(r["name"], finder.rw)
            log("   국가유산청 목록: 시도 %d곳, 항목 %d건, 호출 %d건" % (len(fresh), len(rows), calls))

    n_auto = 0
    new_rec = {}
    for sg, name, label, key, cands, rejected in later:
        sido = label.split()[0] if label else ""
        if sido in fresh:
            kc = khs_cands(rows, finder.rw, name, label)
            new_rec[key] = {"at": today, "c": [slim(c, today) | {"s": c["s"], "home": c["home"]} for c in kc]}
        else:
            old = store["검색기록"].get(key)
            if old:
                new_rec[key] = old
            kc = [dict(c) for c in (old or {}).get("c", [])]
        kc = [c for c in kc if (c["src"], str(c["id"])) not in rejected and
              (c["home"] or finder.near(sg, c["lat"], c["lng"]))]
        a, picked = decide(cands + kc)
        stored = store["적용"].get(key)
        if a:
            out[key] = a
            n_auto += 1
        elif stored and (stored.get("src"), str(stored.get("id"))) not in rejected:
            out[key] = dict(stored)                         # 예전에 자동으로 찾은 결과는 오늘 못 찾아도 유지
            n_auto += 1
        elif picked:
            review[key] = (label, picked[0])

    applied = {}
    for key, a in out.items():
        if a.get("why") != "확인함":
            prev = store["적용"].get(key) or {}
            same = prev.get("src") == a["src"] and str(prev.get("id")) == str(a["id"])
            applied[key] = slim(a, prev.get("at") if same else today)
    keys = {"%s|%s" % (e[0], e[1]) for e in entries}
    wait = {k: slim(c, today) | {"s": round(c["s"], 3), "home": c["home"], "label": label}
            for k, (label, c) in review.items()}
    save_auto(here, applied, {k: v for k, v in new_rec.items() if k in keys}, wait)
    write_review(here, review, decisions, keys | set(out))
    return out, len(review), n_auto


def save_auto(here, applied, records, wait):
    with open(os.path.join(here, AUTO_FILE), "w", encoding="utf-8") as f:
        json.dump({"_설명": "자동으로 찾은 좌표(사람이 고치지 않음). 직접 고칠 때는 overrides.json 을 쓰세요.",
                   "적용": dict(sorted(applied.items())), "확인대기": dict(sorted(wait.items())),
                   "검색기록": dict(sorted(records.items()))}, f, ensure_ascii=False, indent=0)


def refresh_review(here, keys):
    """사람이 고친 geo_review.md 를 다시 정리(호출 없음): 표시한 줄은 '처리함'으로, 나머지는 그대로."""
    store, decisions = load_auto(here), load_decisions(here)
    review = {k: (c.get("label", ""), c) for k, c in store["확인대기"].items()
              if k in keys and not any(d[0] in "OX" for d in decisions.get(k, []))}
    write_review(here, review, decisions, keys)
    return decisions, store


SRC_NAME = {"kor": "관광공사", "hub": "중심관광지", "khs": "국가유산청"}


def write_review(here, review, decisions, keys):
    lines = ["# 좌표 확인 목록", "",
             "이름이 애매해서 지도에 바로 넣지 않은 후보입니다(장소마다 가장 그럴듯한 후보 1개). "
             "**[지도]** 를 눌러 위치를 본 뒤, 이 파일을 편집(연필 아이콘)해 줄 맨 앞 `?` 를 "
             "`O`(맞음) 또는 `X`(아님)로 바꾸고 **Commit changes** 를 누르세요. 다음 수집 때 반영되고, "
             "X 로 표시한 곳은 다른 후보가 있으면 새 줄로 나옵니다. 나머지 글자는 고치지 마세요.", ""]
    by_region = {}
    for key, (label, c) in review.items():
        by_region.setdefault(label, []).append((key, c))
    lines.append("대기 %d곳" % len(review))
    lines.append("")
    for label in sorted(by_region):
        lines.append("## " + label)
        for key, c in sorted(by_region[label], key=lambda kc: kc[0]):
            lines.append(review_line("?", key, c))
        lines.append("")
    done = [(k, d) for k, ds in sorted(decisions.items()) if k in keys for d in ds if d[0] in "OX"]
    if done:
        lines.append("## 처리함 (지우면 다시 물어봅니다)")
        for key, (mark, src, cid, lat, lng) in done:
            lines.append("- %s %s → %s `%s:%s@%.6f,%.6f`" % (mark, key, SRC_NAME.get(src, src), src, cid, lat, lng))
        lines.append("")
    with open(os.path.join(here, REVIEW_FILE), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def review_line(mark, key, c):
    nm = (c.get("name") or "").replace(",", " ").replace("`", "'")
    link = "https://map.kakao.com/link/map/%s,%.6f,%.6f" % (urllib.parse.quote(nm or "후보"), c["lat"], c["lng"])
    where = "" if c["home"] else " · 다른 지역(%s)" % (" ".join((c.get("addr") or "").split()[:2]) or "?")
    return "- %s %s → %s · %s %d%% · %s%s · [지도](%s) `%s:%s@%.6f,%.6f`" % (
        mark, key, nm, c["why"], round(c["s"] * 100), SRC_NAME.get(c["src"], c["src"]), where, link,
        c["src"], str(c["id"]).replace("`", "").replace("@", ""), c["lat"], c["lng"])
