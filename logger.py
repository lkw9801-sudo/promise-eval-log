"""2단계 · 실시간 기록 로그 수집기. 노트북을 켜 둔 채 계속 돌립니다.

사용:  python logger.py            (config.json의 시간대에만 기록)
       python logger.py --now 20   (지금부터 20분만 바로 기록 · 시험용)
       python logger.py --estimate (하루 호출 수만 계산)
결과:  logs/YYYY-MM-DD/HH_실행시작.jsonl(.gz)  (한 줄 = 한 번 호출한 원본 응답)
"""
import argparse, json, sys, time, traceback
from datetime import datetime, timedelta
from pathlib import Path
from common import load_config, fetch_subway, fetch_subway_all, fetch_bus_arrivals, fetch_bus_positions, HERE


def in_window(now, w):
    wd = now.weekday() < 5
    if w["days"] == "평일" and not wd:
        return False
    if w["days"] == "주말" and wd:
        return False
    s = datetime.strptime(w["start"], "%H:%M").time()
    e = datetime.strptime(w["end"], "%H:%M").time()
    t = now.time()
    return (s <= t < e) if s < e else (t >= s or t < e)


def window_minutes(w):
    s = datetime.strptime(w["start"], "%H:%M")
    e = datetime.strptime(w["end"], "%H:%M")
    m = (e - s).total_seconds() / 60
    return m if m > 0 else m + 1440


def estimate(cfg):
    L = cfg["logger"]
    sub = bus = 0
    for w in L["windows"]:
        days = 5 if w["days"] == "평일" else 2 if w["days"] == "주말" else 7
        m = window_minutes(w) * days / 7
        sub += m * 60 / L["subway_interval_sec"] * (1 if L.get("subway_mode", "all") == "all" else len(cfg["subway_stations"]))
        bus += m * 60 / L["bus_interval_sec"] * 2
    print("하루 평균 예상 호출 수 · 지하철(서울열린데이터광장 키): %d회 · 버스(공공데이터포털 키, 도착+위치): %d회" % (sub, bus))
    print("→ 각 사이트 마이페이지의 '일일 트래픽'보다 작아야 합니다. 넘으면 interval을 늘리거나 시간대를 줄이세요.")


RUN_ID = datetime.now().strftime("%H%M%S")


def write(log_dir, rec):
    """logs/날짜/시_실행시작.jsonl 로 나눠 쓴다(GitHub 파일 100MB 제한 → 커밋 때 gzip)."""
    now = datetime.now()
    d = Path(log_dir) / now.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s_%s.jsonl" % (now.strftime("%H"), RUN_ID))
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def poll(cfg, kind, fn, log_dir, **meta):
    t0 = datetime.now()
    try:
        res = fn()
        rec = {"ts": t0.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "ok": bool(res["rows"]),
               "code": res["code"], "rows": res["rows"], "api_call": 1, **meta}
        if not res["rows"]:
            rec["msg"] = res.get("msg")
    except Exception as e:
        rec = {"ts": t0.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "ok": False, "error": repr(e), "rows": [], "api_call": 1, **meta}
    write(log_dir, rec)
    return rec


def subway_calls_today(log_dir):
    """오늘 이미 쓴 지하철 키 호출 수 (하루 한도 관리용)."""
    from common import iter_files
    import gzip
    day = datetime.now().strftime("%Y-%m-%d")
    n = 0
    for p in iter_files(log_dir):
        if day not in str(p):
            continue
        op = gzip.open if str(p).endswith(".gz") else open
        with op(p, "rt", encoding="utf-8") as f:
            for line in f:
                if '"subway_arrival"' in line and ('"api_call": 1' in line or '"api_call"' not in line):
                    n += 1
    return n


def poll_subway_all(cfg, log_dir):
    """일괄 1회 호출 → 역별 기록으로 나눠 저장 (분석 코드는 그대로)."""
    t0 = datetime.now()
    ts = t0.strftime("%Y-%m-%d %H:%M:%S")
    try:
        res = fetch_subway_all(cfg)
    except Exception as e:
        res = {"code": None, "msg": repr(e), "rows": []}
    if not res["rows"]:
        write(log_dir, {"ts": ts, "kind": "subway_arrival", "ok": False, "code": res["code"], "msg": res.get("msg"),
                        "rows": [], "api_call": 1, "station": "ALL"})
        return False
    by = {}
    for r in res["rows"]:
        by.setdefault(r.get("statnNm"), []).append(r)
    for i, st in enumerate(cfg["subway_stations"]):
        rows = by.get(st, [])
        write(log_dir, {"ts": ts, "kind": "subway_arrival", "ok": bool(rows), "code": res["code"], "rows": rows,
                        "api_call": 1 if i == 0 else 0, "station": st, "via": "ALL"})
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--now", type=int, help="지금부터 N분만 기록")
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--scheduled", type=int, metavar="MAX_MIN",
                    help="예약 실행용: 기록 시간대 안일 때만 기록하고, 시간대가 끝나거나 MAX_MIN분이 지나면 종료")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.estimate:
        estimate(cfg)
        return
    L = cfg["logger"]
    log_dir = HERE / L["log_dir"]
    log_dir.mkdir(exist_ok=True)
    stop_at = datetime.now() + timedelta(minutes=a.now) if a.now else None
    if a.scheduled:
        soon = datetime.now() + timedelta(minutes=50)   # 시간대 시작 50분 안이면 기다렸다가 시작
        if not any(in_window(datetime.now(), w) or in_window(soon, w) for w in L["windows"]):
            print("지금은 기록 시간대가 아니라 종료합니다.")
            return
        while not any(in_window(datetime.now(), w) for w in L["windows"]):
            time.sleep(30)
        hard_stop = datetime.now() + timedelta(minutes=a.scheduled)
    last_sub = last_bus = 0.0
    fails = 0
    mode, all_fail = L.get("subway_mode", "all"), 0
    limit = L.get("subway_daily_limit", 950)
    used = subway_calls_today(log_dir)
    print("오늘 지하철 호출 %d/%d회 사용" % (used, limit))
    print("기록 시작. Ctrl+C로 중지. 로그 폴더:", log_dir)
    while True:
        now = datetime.now()
        if stop_at and now >= stop_at:
            break
        active = bool(stop_at) or any(in_window(now, w) for w in L["windows"])
        if a.scheduled and (not active or now >= hard_stop):
            print(now.strftime("%H:%M:%S"), "예약 기록 종료")
            break
        if not active:
            time.sleep(20)
            continue
        mono = time.monotonic()
        if mono - last_sub >= L["subway_interval_sec"] and used < limit:
            last_sub = mono
            ok = False
            if mode == "all":
                ok = poll_subway_all(cfg, log_dir)
                used += 1
                all_fail = 0 if ok else all_fail + 1
                if all_fail >= 3:       # 일괄 방식이 안 되면 역별 방식으로 (호출 간격은 넓혀서)
                    mode, L["subway_interval_sec"] = "each", max(60, L["subway_interval_sec"])
                    print(now.strftime("%H:%M:%S"), "일괄 호출이 안 돼서 역별 호출로 바꿉니다.")
            else:
                for st in cfg["subway_stations"]:
                    r = poll(cfg, "subway_arrival", lambda: fetch_subway(cfg, st), log_dir, station=st)
                    used += 1
                    ok = ok or r["ok"]
            fails = 0 if ok else fails + 1
            if used >= limit:
                print(now.strftime("%H:%M:%S"), "오늘 지하철 호출 한도(%d회)에 가까워 지하철 기록을 멈춥니다." % limit)
        if mono - last_bus >= L["bus_interval_sec"]:
            last_bus = mono
            poll(cfg, "bus_arrival", lambda: fetch_bus_arrivals(cfg), log_dir)
            r = poll(cfg, "bus_pos", lambda: fetch_bus_positions(cfg), log_dir)
            fails = 0 if r["ok"] else fails + 1
        if fails >= 10:
            print(now.strftime("%H:%M:%S"), "⚠️ 10번 연속 실패. 인증키·트래픽 한도·인터넷을 확인하세요. 마지막 오류:", r.get("error") or r.get("code"))
            fails = 0
        print(now.strftime("%H:%M:%S"), "기록 중", end="\r", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n중지했습니다.")
