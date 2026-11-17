"""6조 약속 탑승권 · 실시간 정확도 검증 공통 모듈 (파이썬 표준 라이브러리만 사용)."""
import json, re, time, urllib.request, urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBWAY_ID = {"2호선": "1002", "3호선": "1003", "5호선": "1005"}


def load_config(path=None):
    p = Path(path) if path else HERE / "config.json"
    if not p.exists():
        p = HERE / "config.example.json"
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    # 클라우드 실행(GitHub Actions)에서는 인증키를 환경변수로 받는다
    import os
    if os.environ.get("SEOUL_SUBWAY_KEY"):
        cfg["keys"]["seoul_subway"] = os.environ["SEOUL_SUBWAY_KEY"].strip()
    if os.environ.get("DATA_GO_KR_KEY"):
        cfg["keys"]["data_go_kr"] = os.environ["DATA_GO_KR_KEY"].strip()
    return cfg


# ---------------- 호출 ----------------
def http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "promise-pass-eval/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def fetch_subway(cfg, station):
    key = cfg["keys"]["seoul_subway"]
    url = ("http://swopenapi.seoul.go.kr/api/subway/%s/json/realtimeStationArrival/0/40/%s"
           % (key, urllib.parse.quote(station)))
    data = http_json(url)
    err = data.get("errorMessage") or {}
    rows = data.get("realtimeArrivalList") or []
    return {"code": err.get("code"), "msg": err.get("message"), "rows": rows}


def fetch_bus_arrivals(cfg):
    q = urllib.parse.urlencode({"serviceKey": cfg["keys"]["data_go_kr"],
                                "busRouteId": cfg["bus"]["route_id"], "resultType": "json"}, safe="%")
    data = http_json("http://ws.bus.go.kr/api/rest/arrive/getArrInfoByRouteAll?" + q)
    return _bus_unwrap(data)


def fetch_bus_positions(cfg):
    q = urllib.parse.urlencode({"serviceKey": cfg["keys"]["data_go_kr"],
                                "busRouteId": cfg["bus"]["route_id"], "resultType": "json"}, safe="%")
    data = http_json("http://ws.bus.go.kr/api/rest/buspos/getBusPosByRtid?" + q)
    return _bus_unwrap(data)


def _bus_unwrap(data):
    head = data.get("msgHeader") or {}
    body = data.get("msgBody") or {}
    rows = body.get("itemList") or []
    if isinstance(rows, dict):
        rows = [rows]
    return {"code": head.get("headerCd"), "msg": head.get("headerMsg"), "rows": rows}


# ---------------- 해석 ----------------
def parse_dt(s):
    """'2026-11-15 14:30:12' 또는 '20261115143012' → datetime"""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19] if "-" in s else s[:14], fmt)
        except ValueError:
            pass
    return None


_MSG_N = re.compile(r"\[(\d+)\]번째 전역")
_MSG_MS = re.compile(r"(\d+)분\s*(\d+)?초?\s*후")


def subway_eta_seconds(row, per_station_sec=120):
    """열차가 이 역에 오기까지 남은 초. barvlDt가 0이면 메시지로 보완. 모르면 None."""
    try:
        b = int(row.get("barvlDt") or 0)
    except ValueError:
        b = 0
    if b > 0:
        return b
    code = str(row.get("arvlCd", ""))
    if code in ("0", "1"):          # 진입 / 도착
        return 0
    if code == "2":                 # 출발 (이미 지나감)
        return -30
    if code == "3":                 # 전역 출발
        return 90
    if code in ("4", "5"):          # 전역 진입 / 전역 도착
        return 120
    msg = (row.get("arvlMsg2") or "")
    m = _MSG_N.search(msg)
    if m:
        return int(m.group(1)) * per_station_sec
    m = _MSG_MS.search(msg)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    return None


_BUS_MS = re.compile(r"(\d+)분(?:(\d+)초)?후")


def bus_eta_seconds(row, n):
    """getArrInfoByRouteAll 한 줄에서 n번째(1,2) 차량의 남은 초."""
    for k in ("exps%d" % n, "kals%d" % n, "neus%d" % n):
        try:
            v = int(row.get(k) or 0)
            if v > 0:
                return v
        except ValueError:
            pass
    msg = (row.get("arrmsg%d" % n) or "").replace(" ", "")
    if "곧도착" in msg:
        return 30
    m = _BUS_MS.search(msg)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    return None


def pick_bus_stops(rows, board_name, alight_name, alight_prev_hint):
    """노선 전체 정류소 중 승차·하차 정류소 자동 선택. (승차행, 하차행, 후보목록)"""
    rows = sorted(rows, key=lambda r: int(r.get("staOrd") or 0))
    alight = None
    for i, r in enumerate(rows):
        if alight_name in (r.get("stNm") or ""):
            prev = rows[i - 1].get("stNm", "") if i else ""
            if not alight_prev_hint or alight_prev_hint in prev:
                alight = r
                break
    board = None
    if alight:
        for r in rows:
            if board_name in (r.get("stNm") or "") and int(r["staOrd"]) < int(alight["staOrd"]):
                board = r
    cands = [r for r in rows if board_name in (r.get("stNm") or "") or alight_name in (r.get("stNm") or "")]
    return board, alight, cands


def dir_match(row, line, label):
    return str(row.get("subwayId")) == SUBWAY_ID[line] and label in (row.get("trainLineNm") or "")


def iter_logs(log_dir):
    for p in sorted(Path(log_dir).glob("*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def fmt(t):
    return t.strftime("%H:%M:%S") if t else "-"
