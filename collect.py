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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
BASE = "https://apis.data.go.kr/B551011"
APP = "crowdmap"
KST = timezone(timedelta(hours=9))
MAX_CALLS = {"tats": 900, "kor": 600}   # 개발계정 하루 1,000건 한도 안쪽으로 제한
calls = {"tats": 0, "kor": 0}

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
            try:
                body = json.loads(raw)
            except ValueError:
                # 오류일 때는 JSON 대신 XML 로 내려온다
                m = re.search(r"<returnAuthMsg>([^<]*)", raw) or re.search(r"<resultMsg>([^<]*)", raw)
                last = (m.group(1) if m else raw[:120]).strip()
                if re.search(r"LIMITED_NUMBER|SERVICE_KEY|SERVICE_ACCESS|DEADLINE", raw):
                    raise ApiStop(last)
            else:
                resp = body.get("response", body)
                head = resp.get("header", {})
                code = str(head.get("resultCode", ""))
                if code not in ("0000", "00"):
                    last = "%s %s" % (code, head.get("resultMsg", ""))
                    if code in ("20", "22", "30", "31"):
                        raise ApiStop(last)
                else:
                    b = resp.get("body") or {}
                    items = b.get("items") or {}
                    item = items.get("item", []) if isinstance(items, dict) else []
                    if isinstance(item, dict):
                        item = [item]
                    return item, int(b.get("totalCount") or 0)
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
                       "id": str(it.get("contentid") or "")})
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
    spots, unmatched, how = [], [], {}
    for (area, sg), by_name in sorted(rates.items()):
        sgname = sgnames[(area, sg)]
        local = [p for p in places if in_sigungu(p["addr"], area, sgname)]
        label = (SIDO_SHORT.get(area, "") + " " + sgname).strip() if area != 36 else "세종"
        cx = sum(p["x"] for p in local) / len(local) if local else None
        cy = sum(p["y"] for p in local) / len(local) if local else None
        for name, series in sorted(by_name.items()):
            r = [None if series.get(d) is None else int(round(series[d])) for d in days]
            ov = overrides.get("%s|%s" % (sg, name))
            rec = None
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
            if rec is None:
                unmatched.append("%s|%s  (%s)" % (sg, name, label))
                if cx is None:
                    continue                    # 시군구 위치조차 모르면 지도에 못 올린다
                rec = [name, label, round(cy, 5), round(cx, 5), 8, IO_UNKNOWN, 1, r, "", sg]
            spots.append(rec)

    exact = len(spots) - sum(1 for s in spots if s[6])
    data = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "days": ["%s-%s-%s" % (d[:4], d[4:6], d[6:]) for d in days],
        "groups": GROUPS,
        "stats": {"total": n_spots, "placed": exact, "approx": len(spots) - exact,
                  "dropped": n_spots - len(spots), "failedRegions": len(failed)},
        "spots": spots,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(HERE, "unmatched.txt"), "w", encoding="utf-8") as f:
        f.write("# 위치를 자동으로 못 찾은 관광지 (시군구코드|관광지명). overrides.json 에 직접 넣을 수 있음\n")
        f.write("\n".join(unmatched) + "\n")
    log("3) 저장 완료: 전체 %d곳 / 위치 확정 %d곳 / 대략 위치 %d곳 / 제외 %d곳"
        % (n_spots, exact, len(spots) - exact, n_spots - len(spots)))
    log("   맞춘 방식:", how)
    return 0


if __name__ == "__main__":
    sys.exit(main())
