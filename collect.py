# -*- coding: utf-8 -*-
"""관광지 혼잡도(집중률) 30일 예측 + 좌표/분류를 모아 data.json 으로 저장한다.

- 혼잡도: 한국관광공사 TatsCnctrRateService (시군구별 조회, 좌표 없음)
- 좌표/분류: 한국관광공사 KorService2 areaBasedList2 (전국 목록)
- 두 자료를 '시군구 + 이름'으로 맞춘다.
표준 라이브러리만 사용한다. 인증키는 환경변수 DATA_GO_KR_KEY 로 받는다.
"""
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import geo

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
BASE = "https://apis.data.go.kr/B551011"
APP = "crowdmap"
KST = timezone(timedelta(hours=9))
MAX_CALLS = {"tats": 900, "kor": 600, "hub": 700}   # 개발계정 하루 1,000건 한도 안쪽으로 제한
calls = {"tats": 0, "kor": 0, "hub": 0}

GROUPS = ["자연", "역사·유적", "공원·테마파크", "전시·문화시설", "거리·랜드마크",
          "체험", "레저·스포츠", "쇼핑·시장", "기타"]
IO_OUT, IO_IN, IO_MIX, IO_UNKNOWN = 0, 1, 2, 3
IO_WORD = {"실외": IO_OUT, "실내": IO_IN, "혼합": IO_MIX}

# 시도 코드 -> 주소 앞머리(행정구역 개편으로 이름이 바뀐 경우까지 포함)
SIDO_PREFIX = {
    11: ["서울"], 26: ["부산"], 27: ["대구"], 28: ["인천"], 29: ["광주", "전남광주"],
    30: ["대전"], 31: ["울산"], 36: ["세종"], 41: ["경기"], 43: ["충청북도", "충북"],
    44: ["충청남도", "충남"], 46: ["전라남도", "전남"], 47: ["경상북도", "경북"],
    48: ["경상남도", "경남"], 50: ["제주"], 51: ["강원"], 52: ["전북", "전라북도"],
}
SIDO_SHORT = {11: "서울", 26: "부산", 27: "대구", 28: "인천", 29: "광주", 30: "대전",
              31: "울산", 36: "세종", 41: "경기", 43: "충북", 44: "충남", 46: "전남",
              47: "경북", 48: "경남", 50: "제주", 51: "강원", 52: "전북"}
TYPE_PRIORITY = {"12": 0, "14": 1, "28": 2, "38": 3, "15": 4, "25": 5, "32": 6, "39": 7}


class ApiStop(Exception):
    """더 호출해도 소용없는 오류(한도 초과, 인증키 문제)."""


def log(*a):
    print(*a, flush=True)


def parse_body(raw):
    """응답 본문 해석 -> ("ok", (items, total)) | ("retry", 사유) | ("stop", 사유).
    서비스에 따라 _type=json 을 줘도 XML 로 내려오므로 둘 다 처리한다."""
    text = raw.lstrip()
    if text.startswith("{"):
        try:
            body = json.loads(text)
        except ValueError:
            return "retry", text[:120]
        resp = body.get("response", body)
        head = resp.get("header", {})
        code = str(head.get("resultCode", ""))
        if code not in ("0000", "00"):
            msg = "%s %s" % (code, head.get("resultMsg", ""))
            return ("stop" if code in ("20", "22", "30", "31") else "retry"), msg
        b = resp.get("body") or {}
        items = b.get("items") or {}
        item = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(item, dict):
            item = [item]
        return "ok", (item, int(b.get("totalCount") or 0))
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "retry", text[:120]
    code = (root.findtext(".//resultCode") or "").strip()
    msg = (root.findtext(".//returnAuthMsg") or root.findtext(".//resultMsg") or "").strip()
    if code not in ("0000", "00"):
        stop = code in ("20", "22", "30", "31") or re.search(r"LIMITED_NUMBER|SERVICE_KEY|SERVICE_ACCESS|DEADLINE", msg)
        return ("stop" if stop else "retry"), ("%s %s" % (code, msg)).strip() or text[:120]
    items = [{c.tag: (c.text or "") for c in it} for it in root.iter("item")]
    return "ok", (items, int(root.findtext(".//totalCount") or 0))


def call(kind, path, params, tries=4):
    """API 한 번 호출 -> (item 목록, totalCount). 인증키는 로그에 남기지 않는다."""
    if calls[kind] >= MAX_CALLS[kind]:
        raise ApiStop("하루 호출 예산(%d건) 도달: %s" % (MAX_CALLS[kind], kind))
    q = {"serviceKey": KEY, "MobileOS": "ETC", "MobileApp": APP, "_type": "json"}
    q.update(params)
    url = "%s/%s?%s" % (BASE, path, urllib.parse.urlencode(q))
    last = ""
    for i in range(tries):
        calls[kind] += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 crowdmap"})
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (401, 403):
                raise ApiStop("인증 거부(HTTP %s). 인증키 또는 활용신청 상태를 확인하세요." % e.code)
            if e.code == 429:
                raise ApiStop("호출 한도 초과(HTTP 429)")
        except Exception as e:  # 시간 초과, 연결 끊김 등
            last = type(e).__name__
        else:
            kind_, payload = parse_body(raw)
            if kind_ == "ok":
                return payload
            last = payload
            if kind_ == "stop":
                raise ApiStop(last)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("호출 실패: %s (%s)" % (path, last))


def fetch_all(kind, path, params, rows):
    out, page = [], 1
    while True:
        p = dict(params, numOfRows=rows, pageNo=page)
        items, total = call(kind, path, p)
        out.extend(items)
        if not items or len(out) >= total:
            return out
        page += 1


# ---------- 분류 ----------
def group_of(l3, ctype):
    l3 = l3 or ""
    a, b = l3[:2], l3[:4]
    if a == "NA":
        return 0
    if a == "HS":
        return 1
    if b in ("VE02", "VE03"):
        return 2
    if b in ("VE06", "VE07", "VE08", "VE09", "VE12"):
        return 3
    if b in ("VE01", "VE04", "VE05"):
        return 4
    if a == "EX":
        return 5
    if a == "LS" or b in ("VE10", "AC05"):
        return 6
    if a == "SH":
        return 7
    if not l3:  # 신분류가 비어 있으면 관광타입으로 대략 분류
        return {"14": 3, "28": 6, "38": 7}.get(str(ctype), 8)
    return 8


IN_PREFIX = ("VE06", "VE07", "VE08", "VE09", "VE12", "SH01", "SH02", "SH03", "SH04",
             "EX0501", "EX0502", "EX0503", "EX0505", "VE0204", "FD", "AC01", "AC02",
             "AC03", "AC04", "AC06")
MIX_PREFIX = ("NA0301", "HS03", "HS0402", "VE0101", "VE0102", "VE0106", "VE0109",
              "VE0201", "VE0202", "VE0205", "VE05", "VE10", "LS0101", "LS0109",
              "LS0112", "LS0207", "LS04", "SH05", "SH06", "SH07", "EV", "C01")
OUT_PREFIX = ("NA", "HS01", "HS02", "HS04", "VE01", "VE0203", "VE03", "VE04", "LS",
              "EX03", "AC05")


def io_of(l3, ctype):
    """실내/실외/혼합. 유형으로 미루어 정한 규칙이라 개별 장소와 다를 수 있다."""
    l3 = l3 or ""
    if not l3:
        return {"14": IO_IN}.get(str(ctype), IO_UNKNOWN)
    for pre in sorted(IN_PREFIX + MIX_PREFIX + OUT_PREFIX, key=len, reverse=True):
        if l3.startswith(pre):
            if pre in IN_PREFIX:
                return IO_IN
            if pre in MIX_PREFIX:
                return IO_MIX
            return IO_OUT
    return IO_MIX


# ---------- 이름 맞추기 ----------
_paren = re.compile(r"[\(\[（][^\)\]）]*[\)\]）]")
_junk = re.compile(r"[\s\-·ㆍ‧.,'\"/&~!?:]+")


def norm(s):
    return _junk.sub("", _paren.sub("", s or "")).lower()


def norm_keep(s):
    return _junk.sub("", s or "").lower()


def in_sido(addr, area):
    return any(addr.startswith(p) for p in SIDO_PREFIX.get(area, []))


def in_sigungu(addr, area, sgname):
    if not in_sido(addr, area):
        return False
    if area == 36 or sgname.startswith("세종"):
        return True
    return (" " + sgname + " ") in (" " + addr + " ")


def best(cands):
    return min(cands, key=lambda p: (TYPE_PRIORITY.get(str(p["t"]), 9), len(p["title"])))


def match_one(name, area, sgname, local, sido_index):
    """(장소, 방식) 또는 (None, None)"""
    n_raw, n = norm_keep(name), norm(name)
    hit = [p for p in local if p["nk"] == n_raw]
    if hit:
        return best(hit), "exact"
    hit = [p for p in local if p["n"] == n]
    if hit:
        return best(hit), "norm"
    # 주소가 비었거나 경계에 걸친 경우: 같은 시도 안에서 이름이 완전히 같은 것
    hit = sido_index.get((area, n_raw), [])
    if hit:
        return best(hit), "sido"
    hit = sido_index.get((0, n_raw), [])       # 주소가 빈 장소: 전국에 하나뿐일 때만
    if len(hit) == 1:
        return hit[0], "noaddr"
    if len(n) >= 3:
        hit = [p for p in local if len(p["n"]) >= 3 and (n in p["n"] or p["n"] in n)]
        if hit:
            hit.sort(key=lambda p: (abs(len(p["n"]) - len(n)), TYPE_PRIORITY.get(str(p["t"]), 9)))
            h = hit[0]["n"]
            if min(len(h), len(n)) / max(len(h), len(n)) >= 0.5:
                return hit[0], "contain"
        scored = [(difflib.SequenceMatcher(None, n, p["n"]).ratio(), p) for p in local if p["n"]]
        if scored:
            r, p = max(scored, key=lambda x: x[0])
            if r >= 0.86:
                return p, "fuzzy"
    return None, None


# ---------- 수집 ----------
def place_lc(it):
    """법정동 시군구 코드 5자리(세종은 시도 코드가 이미 5자리)"""
    r, s = str(it.get("lDongRegnCd") or ""), str(it.get("lDongSignguCd") or "")
    return r if len(r) == 5 else (r + s if r and s else "")


def load_places():
    """전국 관광정보 목록(약 5만 건)."""
    items = fetch_all("kor", "KorService2/areaBasedList2", {"arrange": "C"}, 1000)
    places = []
    for it in items:
        try:
            x, y = float(it.get("mapx") or 0), float(it.get("mapy") or 0)
        except ValueError:
            continue
        if not (124 < x < 132 and 33 < y < 39):
            continue
        title = (it.get("title") or "").strip()
        if not title:
            continue
        places.append({"title": title, "nk": norm_keep(title), "n": norm(title),
                       "addr": (it.get("addr1") or "").strip(), "x": x, "y": y,
                       "t": str(it.get("contenttypeid") or ""), "l3": it.get("lclsSystm3") or "",
                       "id": str(it.get("contentid") or ""), "lc": place_lc(it)})
    return places


def load_rates(sigungu):
    """시군구별 혼잡도. 반환: {(areaCd, signguCd): {관광지명: {yyyymmdd: rate}}}, 이름표, 실패 목록"""
    result, names, failed, stop = {}, {}, [], []

    def one(row):
        area, _, sg, sgname = row
        if stop:
            return
        try:
            items = fetch_all("tats", "TatsCnctrRateService/tatsCnctrRatedList",
                              {"areaCd": area, "signguCd": sg}, 1000)
        except ApiStop as e:
            stop.append(str(e))
            failed.append(sg)
            return
        except Exception as e:
            log("  실패", sg, sgname, e)
            failed.append(sg)
            return
        spots = {}
        for it in items:
            nm = (it.get("tAtsNm") or "").strip()
            day = str(it.get("baseYmd") or "")
            try:
                rate = float(it.get("cnctrRate"))
            except (TypeError, ValueError):
                continue
            if nm and len(day) == 8:
                spots.setdefault(nm, {})[day] = rate
        result[(area, sg)] = spots
        names[(area, sg)] = sgname

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, sigungu))
    if stop:
        log("중단 사유:", stop[0])
    return result, names, failed


HUB_GROUP = {"자연관광": 0, "역사관광": 1, "문화관광": 3, "체험관광": 5, "레저스포츠": 6, "쇼핑": 7}


def load_hub(sigungu, path):
    """기초지자체 중심 관광지(시군구별 100위). 월 단위 자료라 한 달에 한 번만 받는다.
    반환: {시군구코드(str): [[순위, 이름, 경도, 위도, 중분류], ...]} (관광지만, 최근 두 달 중 좋은 순위)"""
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = None
    today = datetime.now(KST).date()
    if old and old.get("items"):
        try:
            age = (today - datetime.strptime(old.get("fetched", ""), "%Y-%m-%d").date()).days
        except ValueError:
            age = 999
        if age < 20:
            return old["items"], old.get("ym", "")
    # 가장 최근에 자료가 있는 달 찾기
    months, y, m = [], today.year, today.month
    for _ in range(10):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append("%04d%02d" % (y, m))
    latest = None
    try:
        for i, ym in enumerate(months):
            items, total = call("hub", "LocgoHubTarService1/areaBasedList1",
                                {"baseYm": ym, "areaCd": 11, "signguCd": 11110, "numOfRows": 1, "pageNo": 1}, tries=2)
            if total > 0:
                latest = i
                break
    except Exception as e:
        log("   중심 관광지 자료 확인 실패:", e)
    if latest is None:
        return (old or {}).get("items", {}), (old or {}).get("ym", "")
    use = months[latest:latest + 2]
    best, stop = {}, []

    def one(job):
        ym, (area, _, sg, _) = job
        if stop:
            return
        try:
            items = fetch_all("hub", "LocgoHubTarService1/areaBasedList1",
                              {"baseYm": ym, "areaCd": area, "signguCd": sg}, 100)
        except ApiStop as e:
            stop.append(str(e)); return
        except Exception:
            return
        for it in items:
            if (it.get("hubCtgryLclsNm") or "") != "관광지":
                continue
            try:
                rank, x, y_ = int(it.get("hubRank")), float(it.get("mapX")), float(it.get("mapY"))
            except (TypeError, ValueError):
                continue
            name = (it.get("hubTatsNm") or "").strip()
            if not name:
                continue
            key = (str(sg), name)
            if key not in best or rank < best[key][0]:
                best[key] = [rank, name, round(x, 5), round(y_, 5), it.get("hubCtgryMclsNm") or ""]

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, [(ym, row) for ym in use for row in sigungu]))
    if stop:
        log("   중심 관광지 중단 사유:", stop[0])
    out = {}
    for (sg, _), v in best.items():
        out.setdefault(sg, []).append(v)
    for v in out.values():
        v.sort()
    if len(out) < len(sigungu) * 0.5:          # 절반도 못 받았으면 예전 자료 유지
        return (old or {}).get("items", out), (old or {}).get("ym", use[0])
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ym": use[0], "fetched": today.strftime("%Y-%m-%d"), "items": out}, f,
                  ensure_ascii=False, separators=(",", ":"))
    return out, use[0]


def dist_m(lat1, lng1, lat2, lng2):
    import math
    p = math.pi / 180
    x = (lng2 - lng1) * p * math.cos((lat1 + lat2) * p / 2)
    return math.hypot(x, (lat2 - lat1) * p) * 6371000


def apply_hub(spots, hub):
    """혼잡도 장소에 지역 인기 순위를 붙인다. 좌표를 못 찾았던 장소는 이름이 맞으면 좌표도 채운다."""
    ranked = filled = 0
    cache = {}
    for rec in spots:
        rec.append(0)                                   # [10] 지역 거점 순위(없으면 0)
        rec.append(0)                                   # [11] 1이면 쇼핑(시장·백화점)끼리 매긴 순위
        cands = hub.get(str(rec[9]))
        if not cands:
            continue
        if rec[9] not in cache:
            # 쇼핑은 따로 떼어, 쇼핑끼리/나머지끼리 순위를 다시 매긴다
            rows, n_shop, n_etc = [], 0, 0
            for h in sorted(cands):
                shop = 1 if h[4] == "쇼핑" else 0
                if shop:
                    n_shop += 1
                else:
                    n_etc += 1
                rows.append((h, norm(h[1]), norm_keep(h[1]), n_shop if shop else n_etc, shop))
            cache[rec[9]] = rows
        n, nk = norm(rec[0]), norm_keep(rec[0])
        if len(n) < 2:
            continue
        found = None
        for h, hn, hnk, new_rank, shop in cache[rec[9]]:
            if not hn:
                continue
            same = hnk == nk or hn == n
            part = len(n) >= 3 and len(hn) >= 3 and (n in hn or hn in n) and min(len(n), len(hn)) / max(len(n), len(hn)) >= 0.5
            if rec[6]:                                  # 좌표 없는 장소: 이름만으로 판단하므로 엄격하게
                ok = same or (part and min(len(n), len(hn)) / max(len(n), len(hn)) >= 0.6)
            else:
                d = dist_m(rec[2], rec[3], h[3], h[2])
                sim = difflib.SequenceMatcher(None, n, hn).ratio()
                ok = (same and d <= 3000) or (part and d <= 800) or (sim >= 0.6 and d <= 250)
            if ok and (found is None or h[0] < found[0]):
                found, found_rank, found_shop = h, new_rank, shop
        if found:
            rec[10], rec[11] = found_rank, found_shop
            ranked += 1
            if rec[6]:
                rec[2], rec[3], rec[6] = found[3], found[2], 0
                rec[4] = HUB_GROUP.get(found[4], 8)
                filled += 1
    return ranked, filled


def main():
    if not KEY:
        log("환경변수 DATA_GO_KR_KEY 가 없습니다."); return 2
    sigungu = json.load(open(os.path.join(HERE, "sigungu.json"), encoding="utf-8"))
    try:
        overrides = json.load(open(os.path.join(HERE, "overrides.json"), encoding="utf-8"))
    except Exception:
        overrides = {}
    out_path = os.path.join(HERE, "data.json")
    prev = {}
    try:
        old = json.load(open(out_path, encoding="utf-8"))
        for s in old.get("spots", []):
            prev[(s[9], s[0])] = s
    except Exception:
        old = None

    # 예약 실행은 하루에 여러 번 걸어 둔다(GitHub 예약이 가끔 빠지므로). 오늘 자료가 이미 있으면 바로 끝낸다.
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if old and not os.environ.get("FORCE") and str(old.get("updated", "")).startswith(today) \
            and (old.get("days") or [""])[0] == today:
        log("오늘(%s) 자료가 이미 있어 건너뜁니다." % today)
        return 0

    log("1) 혼잡도 수집: 시군구 %d곳" % len(sigungu))
    rates, sgnames, failed = load_rates(sigungu)
    n_spots = sum(len(v) for v in rates.values())
    log("   관광지 %d곳, 실패 시군구 %d곳, 호출 %d건" % (n_spots, len(failed), calls["tats"]))
    if len(failed) > len(sigungu) * 0.3 or n_spots == 0:
        log("수집 실패가 많아 기존 data.json 을 그대로 둡니다."); return 1

    log("2) 전국 관광정보(좌표·분류) 수집")
    try:
        places = load_places()
    except Exception as e:
        log("   실패:", e, "-> 어제 좌표를 재사용합니다.")
        places = []
    log("   장소 %d건, 호출 %d건" % (len(places), calls["kor"]))

    sido_index = {}
    for p in places:
        if not p["addr"]:
            sido_index.setdefault((0, p["nk"]), []).append(p)
        for area in SIDO_PREFIX:
            if in_sido(p["addr"], area):
                sido_index.setdefault((area, p["nk"]), []).append(p)

    days = sorted({d for v in rates.values() for s in v.values() for d in s})[:31]
    spots, how, fixes = [], {}, {}
    pend = []          # 좌표를 못 찾은 곳(+예전에 자동 보충한 곳): 4단계에서 공공 자료로 다시 찾는다
    for (area, sg), by_name in sorted(rates.items()):
        sgname = sgnames[(area, sg)]
        alias = set(geo.REGION_ALIAS.get(str(sg), ()))   # 행정구역 개편으로 새 법정동 코드를 쓰는 곳(인천·화성)
        local = [p for p in places if in_sigungu(p["addr"], area, sgname) or (alias and p["lc"] in alias)]
        label = (SIDO_SHORT.get(area, "") + " " + sgname).strip() if area != 36 else "세종"
        center = (round(sum(p["y"] for p in local) / len(local), 5),
                  round(sum(p["x"] for p in local) / len(local), 5)) if local else None
        for name, series in sorted(by_name.items()):
            key = "%s|%s" % (sg, name)
            r = [None if series.get(d) is None else int(round(series[d])) for d in days]
            ov = overrides.get(key)
            rec = None
            fix = ov if isinstance(ov, dict) else None      # {"유형": ..., "장소": ...} : 분류만 바로잡기
            if fix:
                fixes[key] = fix
            if isinstance(ov, list) and len(ov) >= 2:
                io = IO_WORD.get(ov[2], IO_UNKNOWN) if len(ov) > 2 else IO_UNKNOWN
                old_s = prev.get((sg, name))
                rec = [name, label, round(float(ov[0]), 5), round(float(ov[1]), 5),
                       old_s[4] if old_s else 8, io, 0, r, "", sg]
                how["override"] = how.get("override", 0) + 1
            if rec is None and places:
                p, m = match_one(name, area, sgname, local, sido_index)
                if p:
                    rec = [name, label, round(p["y"], 5), round(p["x"], 5),
                           group_of(p["l3"], p["t"]), io_of(p["l3"], p["t"]), 0, r, p["id"], sg]
                    how[m] = how.get(m, 0) + 1
            if rec is None and (sg, name) in prev and (not places or not prev[(sg, name)][6]):
                o = prev[(sg, name)]           # 어제 찾아 둔 좌표 재사용
                rec = [name, label, o[2], o[3], o[4], o[5], o[6], r, o[8], sg]
                how["prev"] = how.get("prev", 0) + 1
                if str(o[8]).startswith("geo:"):  # 자동 보충했던 곳은 오늘 근거로 다시 확인
                    pend.append({"rec": rec, "key": key, "sg": sg, "name": name, "label": label,
                                 "center": center, "r": r, "geo": True})
            if rec is None:
                if center:
                    rec = [name, label, center[0], center[1], 8, IO_UNKNOWN, 1, r, "", sg]
                pend.append({"rec": rec, "key": key, "sg": sg, "name": name, "label": label,
                             "center": center, "r": r, "geo": False})
                if rec is None:
                    continue                    # 시군구 위치조차 모르면 좌표를 찾았을 때만 올린다
            if fix:
                apply_fix(rec, fix)
                how["fix"] = how.get("fix", 0) + 1
            spots.append(rec)

    log("3) 지역 인기 순위(중심 관광지) 반영")
    try:
        hub, hub_ym = load_hub(sigungu, os.path.join(HERE, "hub.json"))
    except Exception as e:
        log("   실패:", e); hub, hub_ym = {}, ""
    ranked, filled = apply_hub(spots, hub)
    log("   기준월 %s, 순위 붙은 장소 %d곳, 좌표 보충 %d곳, 호출 %d건" % (hub_ym or "-", ranked, filled, calls["hub"]))

    log("4) 좌표 보충(관광공사 목록 다시 맞추기·중심 관광지·국가유산청)")
    n_geo = n_review = n_auto = 0
    todo = [q for q in pend if q["rec"] is None or q["rec"][6] or q["geo"]]
    if places and todo:
        ids = {}
        for s in spots:
            if not s[6] and s[8] and not str(s[8]).startswith("geo:"):
                ids.setdefault(str(s[9]), []).append(str(s[8]))
        try:
            finder = geo.Finder(places, hub, sigungu, ids)
            res, n_review, n_auto = geo.resolve([(str(q["sg"]), q["name"], q["label"]) for q in todo],
                                                finder, HERE, log)
        except Exception as e:                  # 보충이 실패해도 수집 결과는 저장한다
            log("   실패:", type(e).__name__)
            res, finder = None, None
        for q in todo if res is not None else []:
            a, rec = res.get(q["key"]), q["rec"]
            if a:
                if rec is None:
                    rec = q["rec"] = [q["name"], q["label"], 0, 0, 8, IO_UNKNOWN, 1, q["r"], "", q["sg"], 0, 0]
                    spots.append(rec)
                rec[2], rec[3] = round(float(a["lat"]), 5), round(float(a["lng"]), 5)
                rec[4], rec[5] = geo_kind(a, finder, str(q["sg"]))
                rec[6], rec[8] = 0, "geo:%s:%s" % (a["src"], a["id"])
                n_geo += 1
            elif q["geo"] and rec is not None and q["center"]:
                # 예전에 자동으로 넣은 좌표인데 이제 근거가 없음(확인 목록에서 X 등) -> 대략 위치로 되돌림
                rec[2], rec[3], rec[4], rec[5], rec[6], rec[8] = q["center"][0], q["center"][1], 8, IO_UNKNOWN, 1, ""
            else:
                continue
            if q["key"] in fixes:
                apply_fix(rec, fixes[q["key"]])
        log("   대상 %d곳 -> 좌표 보충 %d곳(자동 %d, 확인함 %d), 확인 대기 %d곳"
            % (len(todo), n_geo, n_auto, n_geo - n_auto, n_review))
    unmatched = ["%s|%s  (%s)" % (q["sg"], q["name"], q["label"]) for q in pend if q["rec"] is None or q["rec"][6]]

    exact = len(spots) - sum(1 for s in spots if s[6])
    data = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "days": ["%s-%s-%s" % (d[:4], d[4:6], d[6:]) for d in days],
        "groups": GROUPS,
        "stats": {"total": n_spots, "placed": exact, "approx": len(spots) - exact,
                  "dropped": n_spots - len(spots), "failedRegions": len(failed),
                  "ranked": ranked, "hubYm": hub_ym, "geo": n_geo, "review": n_review},
        "spots": spots,
    }
    save(data, unmatched)
    log("5) 저장 완료: 전체 %d곳 / 위치 확정 %d곳 / 대략 위치 %d곳 / 제외 %d곳"
        % (n_spots, exact, len(spots) - exact, n_spots - len(spots)))
    log("   맞춘 방식:", how)
    return 0


def apply_fix(rec, fix):
    if fix.get("유형") in GROUPS:
        rec[4] = GROUPS.index(fix["유형"])
    if fix.get("장소") in IO_WORD:
        rec[5] = IO_WORD[fix["장소"]]


def geo_kind(a, finder, sg):
    """보충한 좌표의 출처로 유형·실내외를 정한다."""
    src = a.get("src")
    if src == "kor":
        t, l3 = a.get("t") or "", a.get("l3") or ""
        if not (t or l3) and finder:
            p = finder.by_id.get(str(a.get("id")))
            if p:
                t, l3 = p.get("t") or "", p.get("l3") or ""
        return group_of(l3, t), io_of(l3, t)
    if src == "hub":
        m = a.get("mcls") or ""
        if not m and finder:
            m = next((h["mcls"] for h in finder.hub.get(sg, ()) if h["title"] == a.get("id")), "")
        return HUB_GROUP.get(m, 8), IO_UNKNOWN
    if src == "khs":
        return 1, IO_UNKNOWN
    return 8, IO_UNKNOWN


def save(data, unmatched):
    with open(os.path.join(HERE, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(HERE, "unmatched.txt"), "w", encoding="utf-8") as f:
        f.write("# 위치를 자동으로 못 찾은 관광지 (시군구코드|관광지명). overrides.json 에 직접 넣거나, "
                "geo_review.md 의 후보를 O 로 표시할 수 있음\n")
        f.write("\n".join(unmatched) + "\n")


def review_only():
    """사람이 고친 overrides.json·geo_review.md 를 지금 data.json 에 바로 반영한다(API 호출 없음)."""
    try:
        data = json.load(open(os.path.join(HERE, "data.json"), encoding="utf-8"))
    except Exception:
        log("data.json 이 없어 건너뜁니다."); return 0
    try:
        overrides = json.load(open(os.path.join(HERE, "overrides.json"), encoding="utf-8"))
    except Exception as e:
        log("overrides.json 을 읽지 못했습니다(쉼표·따옴표 확인):", type(e).__name__); overrides = {}
    spots = data["spots"]
    keys = {"%s|%s" % (s[9], s[0]) for s in spots}
    decisions, store = geo.refresh_review(HERE, keys)
    wait = store["확인대기"]
    n_ov = n_ok = 0
    for s in spots:
        key = "%s|%s" % (s[9], s[0])
        ov = overrides.get(key)
        if isinstance(ov, list) and len(ov) >= 2:
            s[2], s[3], s[6], s[8] = round(float(ov[0]), 5), round(float(ov[1]), 5), 0, ""
            if len(ov) > 2:
                s[5] = IO_WORD.get(ov[2], IO_UNKNOWN)
            n_ov += 1
        elif s[6]:
            ok = [d for d in decisions.get(key, []) if d[0] == "O"]
            if ok:
                _, src, cid, lat, lng = ok[0]
                meta = wait.get(key) or {}
                a = {"src": src, "id": cid, "t": meta.get("t"), "l3": meta.get("l3"), "mcls": meta.get("mcls")}
                s[2], s[3], s[6], s[8] = round(lat, 5), round(lng, 5), 0, "geo:%s:%s" % (src, cid)
                s[4], s[5] = geo_kind(a, None, str(s[9]))
                n_ok += 1
        if isinstance(ov, dict):
            apply_fix(s, ov)
    placed = {"%s|%s" % (s[9], s[0]) for s in spots if not s[6]}
    try:
        lines = [ln.rstrip("\n") for ln in open(os.path.join(HERE, "unmatched.txt"), encoding="utf-8")
                 if ln.strip() and not ln.startswith("#")]
    except Exception:
        lines = []
    unmatched = [ln for ln in lines if ln.split("  (")[0] not in placed]
    st = data.get("stats", {})
    st["placed"] = len(spots) - sum(1 for s in spots if s[6])
    st["approx"] = len(spots) - st["placed"]
    st["review"] = sum(1 for k in wait if k in keys and not any(d[0] in "OX" for d in decisions.get(k, [])))
    save(data, unmatched)
    log("직접 고친 내용 반영: overrides 좌표 %d곳, 확인 목록 O %d곳, 확인 대기 %d곳" % (n_ov, n_ok, st["review"]))
    return 0


if __name__ == "__main__":
    sys.exit(review_only() if "--review" in sys.argv else main())
