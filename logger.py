"""2단계 · 실시간 기록 로그 수집기. 노트북을 켜 둔 채 계속 돌립니다.

사용:  python logger.py            (config.json의 시간대에만 기록)
       python logger.py --now 20   (지금부터 20분만 바로 기록 · 시험용)
       python logger.py --estimate (하루 호출 수만 계산)
결과:  logs/YYYY-MM-DD.jsonl  (한 줄 = 한 번 호출한 원본 응답)
"""
import argparse, json, sys, time, traceback
from datetime import datetime, timedelta
from pathlib import Path
from common import load_config, fetch_subway, fetch_bus_arrivals, fetch_bus_positions, HERE


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
        sub += m * 60 / L["subway_interval_sec"] * len(cfg["subway_stations"])
        bus += m * 60 / L["bus_interval_sec"] * 2
    print("하루 평균 예상 호출 수 · 지하철(서울열린데이터광장 키): %d회 · 버스(공공데이터포털 키, 도착+위치): %d회" % (sub, bus))
    print("→ 각 사이트 마이페이지의 '일일 트래픽'보다 작아야 합니다. 넘으면 interval을 늘리거나 시간대를 줄이세요.")


def write(log_dir, rec):
    p = Path(log_dir) / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def poll(cfg, kind, fn, log_dir, **meta):
    t0 = datetime.now()
    try:
        res = fn()
        rec = {"ts": t0.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "ok": bool(res["rows"]),
               "code": res["code"], "rows": res["rows"], **meta}
    except Exception as e:
        rec = {"ts": t0.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "ok": False, "error": repr(e), "rows": [], **meta}
    write(log_dir, rec)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--now", type=int, help="지금부터 N분만 기록")
    ap.add_argument("--estimate", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.estimate:
        estimate(cfg)
        return
    L = cfg["logger"]
    log_dir = HERE / L["log_dir"]
    log_dir.mkdir(exist_ok=True)
    stop_at = datetime.now() + timedelta(minutes=a.now) if a.now else None
    last_sub = last_bus = 0.0
    fails = 0
    print("기록 시작. Ctrl+C로 중지. 로그 폴더:", log_dir)
    while True:
        now = datetime.now()
        if stop_at and now >= stop_at:
            break
        active = bool(stop_at) or any(in_window(now, w) for w in L["windows"])
        if not active:
            time.sleep(20)
            continue
        mono = time.monotonic()
        if mono - last_sub >= L["subway_interval_sec"]:
            last_sub = mono
            for st in cfg["subway_stations"]:
                r = poll(cfg, "subway_arrival", lambda: fetch_subway(cfg, st), log_dir, station=st)
                fails = 0 if r["ok"] else fails + 1
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
