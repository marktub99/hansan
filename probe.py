# -*- coding: utf-8 -*-
"""좌표 보충 설계용 1회성 시험 (geo-dev 브랜치 전용, main 에는 넣지 않음).
로그에는 개수와 결과코드만 찍는다. 인증키·요청 주소는 절대 출력하지 않는다."""
import gzip, json, os, re, sys, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C

OUT = os.path.join(C.HERE, "probe_out")
os.makedirs(OUT, exist_ok=True)
C.MAX_CALLS["kor"] = 200
C.MAX_CALLS["tats"] = 30


def save(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def safe(fn, *a, **k):
    try:
        return fn(*a, **k), ""
    except Exception as e:  # 메시지에 주소가 섞일 수 있어 종류만 남긴다
        return None, type(e).__name__ + (": " + str(e)[:80] if isinstance(e, C.ApiStop) else "")


def raw_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 crowdmap"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


# 1) 관광공사 전국 목록(원자료 일부 필드)
items, err = safe(C.fetch_all, "kor", "KorService2/areaBasedList2", {"arrange": "C"}, 1000)
keep = ("contentid", "contenttypeid", "title", "addr1", "addr2", "mapx", "mapy", "lDongRegnCd",
        "lDongSignguCd", "lclsSystm3", "areacode", "sigungucode")
rows = [{k: it.get(k, "") for k in keep} for it in (items or [])]
with gzip.open(os.path.join(OUT, "places.json.gz"), "wt", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False)
print("1) 전국 목록", len(rows), "건", err, "호출", C.calls["kor"], flush=True)

# 2) 키워드 검색 시험
SAMPLE = ["11110|돈의문박물관마을", "11110|그라운드시소 서촌", "11200|뚝도시장", "11710|서울책보고",
          "11140|신당동 떡볶이 골목", "11680|코엑스", "41360|국립수목원", "43150|청풍문화재단지",
          "51760|대관령알프스양떼목장", "48220|통영루지", "50110|제주공룡랜드", "50110|넥슨컴퓨터박물관",
          "28110|월미테마파크", "28260|인천로봇랜드", "28140|수도국산달동네박물관", "43130|활옥동굴",
          "44180|보령머드박물관", "47920|백두대간협곡열차", "50130|하멜상선전시관", "51110|강촌레일바이크"]
kw = []
for s in SAMPLE:
    sg, word = s.split("|")
    for w in dict.fromkeys([word, word.replace(" ", "")]):
        res, err = safe(C.call, "kor", "KorService2/searchKeyword2", {"keyword": w, "numOfRows": 20, "pageNo": 1})
        its, total = res if res else ([], 0)
        kw.append({"sg": sg, "kw": w, "total": total, "err": err,
                   "items": [{k: it.get(k, "") for k in keep} for it in its]})
save("kw.json", kw)
print("2) 키워드 검색", len(kw), "회, 결과 있음", sum(1 for k in kw if k["total"]),
      "회, 오류", sum(1 for k in kw if k["err"]), "회", flush=True)

# 3) 법정동 시도 코드(행정구역 개편 확인)
ld, err = safe(C.call, "kor", "KorService2/ldongCode2", {"lDongListYn": "N", "numOfRows": 50, "pageNo": 1})
sido = (ld or ([], 0))[0]
save("ldong_sido.json", sido)
print("3) 법정동 시도", len(sido), "건", err, flush=True)
new = [r for r in sido if "전남광주" in json.dumps(r, ensure_ascii=False)]
sgs = []
for r in new:
    code = r.get("code") or r.get("lDongRegnCd") or ""
    res, err = safe(C.call, "kor", "KorService2/ldongCode2", {"lDongRegnCd": code, "lDongListYn": "N", "numOfRows": 100, "pageNo": 1})
    sgs.append({"sido": r, "sigungu": (res or ([], 0))[0], "err": err})
save("ldong_jn.json", sgs)
print("   전남광주 시군구 목록", sum(len(x["sigungu"]) for x in sgs), "건", flush=True)

# 4) 혼잡도: 광주·전남 (옛 코드 / 새 코드)
tries = [(29, 29110), (46, 46130)]
for x in sgs:
    code = x["sido"].get("code") or ""
    for s in x["sigungu"][:3]:
        c2 = s.get("code") or ""
        if code and c2:
            tries.append((code, c2 if len(str(c2)) == 5 else "%s%s" % (code, c2)))
tats = []
for a, s in tries[:6]:
    res, err = safe(C.call, "tats", "TatsCnctrRateService/tatsCnctrRatedList", {"areaCd": a, "signguCd": s, "numOfRows": 3, "pageNo": 1})
    its, total = res if res else ([], 0)
    tats.append({"areaCd": a, "signguCd": s, "total": total, "err": err, "items": its[:3]})
    print("4) 혼잡도", a, s, "→", total, "건", err, flush=True)
save("tats_jn.json", tats)

# 5) 다른 공공 자료 접근 여부(결과코드만)
KEY = C.KEY
std = {}
for api in ["tn_pubr_public_cty_park_info_api", "tn_pubr_public_trdit_mrkt_api",
            "tn_pubr_public_museum_artgr_info_api", "tn_pubr_public_trrsrt_api"]:
    url = "https://api.data.go.kr/openapi/%s?%s" % (api, urllib.parse.urlencode(
        {"serviceKey": KEY, "pageNo": 1, "numOfRows": 1, "type": "json"}))
    res, err = safe(raw_get, url)
    code = ""
    if res:
        m = re.search(r'"resultCode"\s*:\s*"?(\w+)', res[1]) or re.search(r"<resultCode>(\w+)", res[1]) \
            or re.search(r"<returnReasonCode>(\w+)", res[1])
        code = m.group(1) if m else res[1][:60].replace(KEY, "***")
    std[api] = {"http": res[0] if res else None, "code": code, "err": err}
    print("5)", api, std[api], flush=True)
save("std.json", std)

# 6) 키 없이 쓰는 공공 자료 / 다른 지오코딩 서비스 도달 여부
reach = {}
for name, url in [("khs", "https://www.khs.go.kr/cha/SearchKindOpenapiList.do?" + urllib.parse.urlencode({"ccbaMnm1": "성락원", "pageUnit": 3})),
                  ("vworld", "https://api.vworld.kr/req/search?service=search&request=search&query=test&type=place&key=x"),
                  ("juso", "https://business.juso.go.kr/addrlink/addrLinkApi.do?confmKey=x&keyword=test&resultType=json")]:
    res, err = safe(raw_get, url)
    reach[name] = {"http": res[0] if res else None, "err": err, "head": res[1][:1500] if res else ""}
    print("6)", name, reach[name]["http"], err, flush=True)
save("reach.json", reach)
print("끝. 관광공사 호출", C.calls["kor"], "건, 혼잡도 호출", C.calls["tats"], "건", flush=True)
