"""시험용 모의 로그 생성기. 서울시 공식 응답 필드 그대로 만들어 logger.py와 같은 형식으로 저장.
실제 운행처럼 배차 흔들림·구간 지연·예측 오차를 넣는다. (발표용 결과로 쓰면 안 됨!)

사용:  python mock_logs.py --days 3 --out mock_logs
"""
import argparse, json, math, random
from datetime import datetime, timedelta
from pathlib import Path

# 노선: (역, 출발점 기준 분), 역별 방면 표기
LINES = {
    "1002": {"h": 6, "stops": [("홍대입구", 0, "신촌방면"), ("충정로", 7.5, "시청방면"), ("을지로3가", 14.5, "을지로4가방면")],
             "opp": {"홍대입구": "합정방면", "충정로": "아현방면", "을지로3가": "을지로입구방면"}, "dest": "성수행", "no": 2000},
    "1005": {"h": 7, "stops": [("충정로", 0, "서대문방면"), ("광화문", 3.5, "종로3가방면")],
             "opp": {"충정로": "애오개방면", "광화문": "서대문방면"}, "dest": "하남검단산행", "no": 5000},
    "1003": {"h": 7, "stops": [("을지로3가", 0, "종로3가방면"), ("경복궁", 5.5, "독립문방면")],
             "opp": {"을지로3가": "충무로방면", "경복궁": "안국방면"}, "dest": "대화행", "no": 3000},
}
BUS_STOPS = [("합정역", -3), ("홍대입구역", 0), ("신촌오거리", 6), ("이대입구", 10), ("충정로역", 17),
             ("서울역사박물관", 25), ("광화문", 27), ("종로1가", 30)]
WINDOWS = [("12:30", "14:30"), ("18:00", "19:30")]


def gen_trains(rng, sid, t0, t1):
    L = LINES[sid]
    trains, t, k, delay = [], t0 - timedelta(minutes=30), 0, 0.0
    while t < t1 + timedelta(minutes=30):
        if rng.random() < 0.06:
            delay = rng.uniform(2, 6)        # 돌발 지연이 이후 몇 대에 이어짐
        times, cum = {}, 0.0
        for st, off, lab in L["stops"]:
            cum = off * rng.uniform(0.95, 1.12) + delay
            times[st] = t + timedelta(minutes=cum)
        trains.append({"no": str(L["no"] + k), "times": times})
        delay = max(0.0, delay - rng.uniform(0.5, 2))
        t += timedelta(minutes=L["h"] * rng.uniform(0.75, 1.25))
        k += 1
    return trains


def subway_rows(rng, station, now, trains_by_sid):
    rows = []
    for sid, trains in trains_by_sid.items():
        L = LINES[sid]
        lab = next((l for s, _, l in L["stops"] if s == station), None)
        if not lab:
            continue
        up = [(tr["times"][station], tr) for tr in trains if (tr["times"][station] - now).total_seconds() > -40]
        up.sort(key=lambda x: x[0])
        for order, (ta, tr) in enumerate(up[:2]):
            eta = (ta - now).total_seconds()
            if -40 < eta <= -10: code = "2"
            elif -10 < eta <= 20: code = "1"
            elif 20 < eta <= 50: code = "0"
            else: code = "99"
            noisy = max(0, int(eta + rng.gauss(0, 15 + 0.08 * max(eta, 0))))
            msg = "%d분 %d초 후" % (noisy // 60, noisy % 60)
            if code == "99" and rng.random() < 0.3:     # barvlDt=0 + "[n]번째 전역" 형태
                noisy, msg = 0, "[%d]번째 전역 (가상역)" % max(1, math.ceil(eta / 120))
            rows.append({"subwayId": sid, "updnLine": "내선" if sid == "1002" else "하행",
                         "trainLineNm": "%s - %s" % (L["dest"], lab), "statnNm": station,
                         "btrainNo": tr["no"], "barvlDt": str(noisy if code == "99" else 0),
                         "recptnDt": (now - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S"),
                         "arvlMsg2": msg if code == "99" else ("%s 도착" % station), "arvlMsg3": station,
                         "arvlCd": code, "ordkey": "0%d" % order})
        # 반대 방면 열차 (필터 시험용)
        rows.append({"subwayId": sid, "trainLineNm": "반대행 - %s" % L["opp"][station], "statnNm": station,
                     "btrainNo": "9999", "barvlDt": "120", "arvlCd": "99", "arvlMsg2": "2분 후",
                     "recptnDt": now.strftime("%Y-%m-%d %H:%M:%S")})
    return rows


def gen_buses(rng, t0, t1):
    buses, t, k = [], t0 - timedelta(minutes=40), 0
    while t < t1 + timedelta(minutes=10):
        f = rng.uniform(0.85, 1.25)
        buses.append({"veh": str(111000000 + k), "times": {nm: t + timedelta(minutes=off * f) for nm, off in BUS_STOPS}})
        t += timedelta(minutes=8 * rng.uniform(0.7, 1.3))
        k += 1
    return buses


def bus_arr_rows(rng, now, buses):
    rows = []
    for i, (nm, _) in enumerate(BUS_STOPS):
        up = sorted((b["times"][nm], b) for b in buses if b["times"][nm] > now)[:2]
        r = {"stNm": nm, "stId": str(113000100 + i), "arsId": str(14100 + i), "staOrd": str(10 + i), "rtNm": "273"}
        for n, (ta, b) in enumerate(up, 1):
            eta = (ta - now).total_seconds()
            e = max(0, int(eta * rng.uniform(0.85, 1.15)))
            r["vehId%d" % n] = b["veh"]
            r["exps%d" % n] = str(e)
            r["arrmsg%d" % n] = "곧 도착" if e < 60 else "%d분%d초후[%d번째 전]" % (e // 60, e % 60, max(1, e // 150))
        rows.append(r)
    return rows


def bus_pos_rows(now, buses):
    rows = []
    for b in buses:
        passed = [(t, i) for i, (nm, _) in enumerate(BUS_STOPS) if (t := b["times"][nm]) <= now]
        if not passed or b["times"][BUS_STOPS[-1][0]] < now:
            continue
        t, i = max(passed)
        rows.append({"vehId": b["veh"], "lastStnId": str(113000100 + i), "sectOrd": str(10 + i),
                     "stopFlag": "1" if (now - t).total_seconds() < 30 else "0",
                     "dataTm": now.strftime("%Y%m%d%H%M%S")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", default="mock_logs")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    base = datetime(2026, 11, 16)
    for d in range(a.days):
        day = base + timedelta(days=d)
        f = open(out / (day.strftime("%Y-%m-%d") + ".jsonl"), "w", encoding="utf-8")
        for ws, we in WINDOWS:
            t0 = datetime.combine(day.date(), datetime.strptime(ws, "%H:%M").time())
            t1 = datetime.combine(day.date(), datetime.strptime(we, "%H:%M").time())
            trains = {sid: gen_trains(rng, sid, t0, t1) for sid in LINES}
            buses = gen_buses(rng, t0, t1)
            now = t0
            while now < t1:
                for st in ["홍대입구", "충정로", "을지로3가", "광화문", "경복궁"]:
                    f.write(json.dumps({"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "kind": "subway_arrival", "ok": True,
                                        "station": st, "rows": subway_rows(rng, st, now, trains)}, ensure_ascii=False) + "\n")
                if now.second == 0:
                    f.write(json.dumps({"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "kind": "bus_arrival", "ok": True,
                                        "rows": bus_arr_rows(rng, now, buses)}, ensure_ascii=False) + "\n")
                    f.write(json.dumps({"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "kind": "bus_pos", "ok": True,
                                        "rows": bus_pos_rows(now, buses)}, ensure_ascii=False) + "\n")
                now += timedelta(seconds=30)
        f.close()
    print("모의 로그 생성:", out)


if __name__ == "__main__":
    main()
