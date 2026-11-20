"""4단계 · 정확도 검증 (연구 1, RQ3: 권장 출발 시각은 실제로 맞는가?)

생각: 앱은 기록된 그 순간의 실시간 응답만 보고 권장 출발을 계산한다(미래 정보 사용 금지).
사용자는 앱이 '지금 출발'이라고 할 때 나간다. 그 뒤로는 실제로 열차·버스가 역에 도착한
기록(같은 열차번호/차량번호 추적)으로 이동을 다시 재생해 실제 도착 시각을 구한다.
비교 기준: '소요시간만 보고 거꾸로 계산'(설문 89.4%가 앱 밖에서 하는 방식).

사용:  python evaluate.py               (logs/ 전체)
       python evaluate.py --logs mock_logs
결과:  out/results.csv, out/summary.md, out/summary.json
"""
import argparse, csv, json, math, statistics as stats
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from common import (load_config, iter_logs, parse_dt, subway_eta_seconds, bus_eta_seconds,
                    pick_bus_stops, dir_match, SUBWAY_ID, HERE, fmt)

MIN = timedelta(minutes=1)


def svc(t):
    """운행일: 새벽 3시 전은 전날로 (막차가 자정을 넘어도 같은 운행으로 묶임)"""
    return (t - timedelta(hours=3)).date().isoformat()


def floor_min(t):
    return t.replace(second=0, microsecond=0)


def ceil_min(t):
    f = floor_min(t)
    return f if f == t else f + MIN


def band(t):
    h = t.hour
    if 7 <= h < 10: return "출근(07-10)"
    if 10 <= h < 17: return "낮(10-17)"
    if 17 <= h < 20: return "퇴근(17-20)"
    if 20 <= h < 23: return "저녁(20-23)"
    return "심야·막차(23-)"


class Data:
    def __init__(self, cfg, log_dir):
        self.cfg = cfg
        self.sub = defaultdict(list)       # 역 -> [(ts, rows)]
        self.bus_arr, self.bus_pos = [], []
        for rec in iter_logs(log_dir):
            ts = parse_dt(rec.get("ts"))
            if not ts or not rec.get("ok"):
                continue
            k = rec.get("kind")
            if k == "subway_arrival":
                self.sub[rec["station"]].append((ts, rec["rows"]))
            elif k == "bus_arrival":
                self.bus_arr.append((ts, rec["rows"]))
            elif k == "bus_pos":
                self.bus_pos.append((ts, rec["rows"]))
        for v in self.sub.values():
            v.sort(key=lambda x: x[0])
        self.bus_arr.sort(key=lambda x: x[0])
        self.bus_pos.sort(key=lambda x: x[0])
        self.sub_ts = {s: [x[0] for x in v] for s, v in self.sub.items()}
        self.bus_ts = [x[0] for x in self.bus_arr]
        self._bus_stops()
        self._subway_truth()
        self._bus_truth()

    # ---- 버스 정류소 ----
    def _bus_stops(self):
        b = self.cfg["bus"]
        self.board_st, self.alight_st = b.get("board_stId"), b.get("alight_stId")
        if (not self.board_st or not self.alight_st) and self.bus_arr:
            bo, al, _ = pick_bus_stops(self.bus_arr[0][1], b["board_name"], b["alight_name"], b["alight_prev_hint"])
            self.board_st = self.board_st or (bo and str(bo["stId"]))
            self.alight_st = self.alight_st or (al and str(al["stId"]))

    # ---- 실제 도착(정답) 만들기 ----
    TRIP_GAP = timedelta(minutes=40)   # 같은 번호라도 40분 넘게 떨어지면 다른 운행 회차

    def _subway_truth(self):
        """(역, 노선ID) -> {(날짜, 열차번호): [(실제 도착, 방면표기, 출처), ...]}  (하루 여러 회차)"""
        self.truth = defaultdict(lambda: defaultdict(list))
        fallback = defaultdict(list)
        for st, snaps in self.sub.items():
            for ts, rows in snaps:
                for r in rows:
                    key = (st, str(r.get("subwayId")))
                    tid = (svc(ts), str(r.get("btrainNo")))
                    ev = self.truth[key][tid]
                    if ev and abs(ts - ev[-1][0]) < self.TRIP_GAP:
                        continue
                    code = str(r.get("arvlCd", ""))
                    lab = r.get("trainLineNm", "")
                    if code in ("0", "1"):
                        ev.append((ts + timedelta(seconds=15 if code == "0" else 0), lab, "관측"))
                    elif code == "2":
                        ev.append((ts - timedelta(seconds=30), lab, "관측"))
                    else:
                        eta = subway_eta_seconds(r)
                        if eta is not None and 0 <= eta <= 120:
                            fb = fallback[(key, tid)]
                            t = ts + timedelta(seconds=eta)
                            if fb and abs(t - fb[-1][0]) < self.TRIP_GAP:
                                fb[-1] = (t, lab)        # 같은 회차면 최신 값으로
                            else:
                                fb.append((t, lab))
        for (key, tid), fbs in fallback.items():          # 진입/도착 순간을 못 잡은 열차 보완
            ev = self.truth[key][tid]
            for t, lab in fbs:
                if not any(abs(t - e[0]) < self.TRIP_GAP for e in ev):
                    ev.append((t, lab, "보완"))
            ev.sort()
        self.truth_list = defaultdict(list)                 # (역, 노선ID, 방면) -> [(t, 열차)]
        for (st, sid), d in self.truth.items():
            for tid, evs in d.items():
                for t, lab, _ in evs:
                    self.truth_list[(st, sid, lab)].append((t, tid))
        for v in self.truth_list.values():
            v.sort()

    @staticmethod
    def next_event(evs, after, within=timedelta(minutes=70)):
        """같은 열차·차량이 after 이후 처음 도착한 시각 (같은 회차 범위 안)."""
        for e in evs:
            t = e[0] if isinstance(e, tuple) else e
            if after < t <= after + within:
                return t
        return None

    def _bus_truth(self):
        """정류소 stId -> {(날짜, vehId): [도착, ...]}  (차량은 하루 여러 번 왕복)"""
        self.btruth = defaultdict(lambda: defaultdict(list))
        prev = {}
        gap = self.cfg["logger"]["bus_interval_sec"] / 2
        for ts, rows in self.bus_pos:
            for r in rows:
                v, st = str(r.get("vehId")), str(r.get("lastStnId"))
                vid = (svc(ts), v)
                t = parse_dt(r.get("dataTm")) or ts
                if prev.get(vid) != st:
                    flag = str(r.get("stopFlag")) == "1"
                    self.btruth[st][vid].append(t if flag else t - timedelta(seconds=gap))
                prev[vid] = st
        self.btruth_list = {st: sorted((t, vid) for vid, ts_ in d.items() for t in ts_) for st, d in self.btruth.items()}

    # ---- 그 시각 화면에 보였을 응답 ----
    def sub_snap(self, st, t, stale=180):
        ts = self.sub_ts.get(st, [])
        i = bisect_right(ts, t) - 1
        if i < 0 or (t - ts[i]).total_seconds() > stale:
            return None
        return self.sub[st][i]

    def bus_snap(self, t, stale=240):
        i = bisect_right(self.bus_ts, t) - 1
        if i < 0 or (t - self.bus_ts[i]).total_seconds() > stale:
            return None
        return self.bus_arr[i]


class Engine:
    """프로토타입 계산 엔진의 파이썬 재현: 다음 1~2대는 실시간, 그 뒤는 배차 간격으로 이어 붙임."""

    def __init__(self, cfg, data):
        self.cfg, self.d = cfg, data
        self.ride_hist = self._learn_rides()
        self._wait_cache = {}
        E = cfg.get("evaluation", {})
        self.q_show = E.get("wait_quantile_show", 0.5)      # 화면 예상 도착: 환승 대기 중앙값
        self.q_plan = E.get("wait_quantile_plan", 0.9)      # 권장 출발: 환승 대기 상위 10%까지 대비

    def arrivals(self, leg):
        """승차 지점의 실제 도착 시각 목록(모든 날)."""
        if leg["mode"] == "subway":
            sid = SUBWAY_ID[leg["line"]]
            lst = []
            for (st, s2, lab), v in self.d.truth_list.items():
                if st == leg["board"] and s2 == sid and leg["board_dir"] in lab:
                    lst.extend(x[0] for x in v)
            return sorted(lst)
        return [x[0] for x in self.d.btruth_list.get(self.d.board_st, [])]

    def wait_q(self, leg, t, q):
        """다른 날 같은 시간대(±1시간)에 승차 지점에 '아무 때나' 도착했을 때 기다린 시간의 분위수(분)."""
        day, hr = svc(t), t.hour
        key = (id(leg), day, hr)
        if key not in self._wait_cache:
            arr = self.arrivals(leg)
            xs = []
            for a, b in zip(arr, arr[1:]):
                if svc(a) == day or (b - a).total_seconds() > 40 * 60:
                    continue
                if min(abs(a.hour - hr), 24 - abs(a.hour - hr)) > 1:
                    continue
                g = (b - a).total_seconds() / 60
                xs.extend(g - k / 2 for k in range(int(g * 2)))   # 30초마다 도착했다고 보고 남은 대기
            xs.sort()
            self._wait_cache[key] = xs
        xs = self._wait_cache[key]
        if len(xs) < 20:
            return self.cfg["headway_min"][leg["line"]] * q
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    def leg_key(self, leg):
        if leg["mode"] == "bus":
            return "%s:board>alight" % leg["line"]
        return "%s:%s>%s" % (leg["line"], leg["board"], leg["alight"])

    def _learn_rides(self):
        """같은 열차의 승차역·하차역 실제 도착 차이 (날짜별)."""
        out = defaultdict(lambda: defaultdict(list))
        for r in self.cfg["routes"].values():
            for leg in r["legs"]:
                k = self.leg_key(leg)
                if leg["mode"] == "subway":
                    sid = SUBWAY_ID[leg["line"]]
                    A, B = self.d.truth.get((leg["board"], sid), {}), self.d.truth.get((leg["alight"], sid), {})
                    for tid, evs in A.items():
                        for ta, lab, _ in evs:
                            tb = leg["board_dir"] in lab and Data.next_event(B.get(tid, []), ta)
                            if tb:
                                out[k][tid[0]].append((tb - ta).total_seconds() / 60)
                else:
                    A = self.d.btruth.get(self.d.board_st, {})
                    B = self.d.btruth.get(self.d.alight_st, {})
                    for vid, tas in A.items():
                        for ta in tas:
                            tb = Data.next_event(B.get(vid, []), ta)
                            if tb:
                                out[k][vid[0]].append((tb - ta).total_seconds() / 60)
        return out

    def ride_min(self, leg, day):
        """그 날을 뺀 다른 날들의 중앙값(미래·당일 정보 누설 방지). 부족하면 기본값."""
        k = self.leg_key(leg)
        xs = [x for d, v in self.ride_hist.get(k, {}).items() if d != day for x in v]
        return stats.median(xs) if len(xs) >= 5 else self.cfg["default_ride_min"][k]

    def board_cands(self, leg, t_now):
        """t_now 시각 화면 기준 승차 지점의 다음 차들 [(예상 도착, id)] + 배차 간격 연장."""
        h = self.cfg["headway_min"][leg["line"]]
        out = []
        if leg["mode"] == "subway":
            snap = self.d.sub_snap(leg["board"], t_now)
            if not snap:
                return None
            ts, rows = snap
            for r in rows:
                if dir_match(r, leg["line"], leg["board_dir"]):
                    e = subway_eta_seconds(r)
                    if e is not None and e >= -10:
                        out.append((ts + timedelta(seconds=e), (svc(ts), str(r.get("btrainNo")))))
        else:
            snap = self.d.bus_snap(t_now)
            if not snap:
                return None
            ts, rows = snap
            row = next((r for r in rows if str(r.get("stId")) == self.d.board_st), None)
            if row:
                for n in (1, 2):
                    e = bus_eta_seconds(row, n)
                    if e is not None and row.get("vehId%d" % n) not in (None, "", "0"):
                        out.append((ts + timedelta(seconds=e), (svc(ts), str(row["vehId%d" % n]))))
        out.sort()
        last = out[-1][0] if out else t_now
        for k in range(1, 40):
            out.append((last + timedelta(minutes=h * k), None))
        return out

    def alight_pred(self, leg, vid, t_board, t_now):
        if vid:
            if leg["mode"] == "subway":
                snap = self.d.sub_snap(leg["alight"], t_now)
                if snap:
                    ts, rows = snap
                    for r in rows:
                        if str(r.get("btrainNo")) == vid[1] and dir_match(r, leg["line"], leg["alight_dir"]):
                            e = subway_eta_seconds(r)
                            # 하차역 기준 안내는 열차가 멀면(3분 넘게) 낙관적으로 짧게 나온다 → 가까울 때만 쓰고, 아니면 학습한 주행시간
                            if e is not None and e <= 180 and ts + timedelta(seconds=e) > t_board:
                                return ts + timedelta(seconds=e)
            else:
                snap = self.d.bus_snap(t_now)
                if snap:
                    ts, rows = snap
                    row = next((r for r in rows if str(r.get("stId")) == self.d.alight_st), None)
                    if row:
                        for n in (1, 2):
                            if str(row.get("vehId%d" % n)) == vid[1]:
                                e = bus_eta_seconds(row, n)
                                if e is not None and e <= 300 and ts + timedelta(seconds=e) > t_board:
                                    return ts + timedelta(seconds=e)
        return t_board + timedelta(minutes=self.ride_min(leg, svc(t_now)))

    def predict_arrival(self, route, D, t_now, cache, q=None):
        q = self.q_show if q is None else q
        t = D + timedelta(minutes=route["origin_walk_min"])
        for leg in route["legs"]:
            key = id(leg)
            if key not in cache:
                cache[key] = self.board_cands(leg, t_now)
            cands = cache[key]
            if cands is None:
                return None
            i = bisect_left([c[0] for c in cands], t)
            if i >= len(cands):
                return None
            tb, vid = cands[i]
            if vid is None:     # 아직 화면에 안 보이는 차: 배차 간격 가정 대신 실제 기록의 대기 분포
                tb = t + timedelta(minutes=self.wait_q(leg, t, q))
            t = self.alight_pred(leg, vid, tb, t_now) + timedelta(minutes=leg["walk_after_min"])
        return ceil_min(t + timedelta(minutes=route["final_walk_min"]))

    def recommend(self, route, A, buffer_min, t_now):
        """도착 ≤ A − 여유 를 만족하는 가장 늦은 출발(분 내림). 앱 화면에 뜨는 값."""
        cache = {}
        target = A - timedelta(minutes=buffer_min)
        D = floor_min(target)
        for _ in range(180):
            if D < floor_min(t_now) - 30 * MIN:
                return None
            f = self.predict_arrival(route, D, t_now, cache, q=self.q_plan)
            if f is not None and f <= target:
                return D
            D -= MIN
        return None

    # ---- 실제 기록으로 재생 ----
    def simulate(self, route, leave):
        t = leave + timedelta(minutes=route["origin_walk_min"])
        trace = []
        for leg in route["legs"]:
            if leg["mode"] == "subway":
                sid = SUBWAY_ID[leg["line"]]
                lst = []
                for (st, s2, lab), v in self.d.truth_list.items():
                    if st == leg["board"] and s2 == sid and leg["board_dir"] in lab:
                        lst.extend(v)
                lst.sort()
                i = bisect_left([x[0] for x in lst], t)
                if i >= len(lst):
                    return None, "승차 기록 없음"
                tb, tid = lst[i]
                ta = Data.next_event(self.d.truth.get((leg["alight"], sid), {}).get(tid, []), tb)
                if not ta:
                    return None, "하차 추적 실패"
            else:
                lst = self.d.btruth_list.get(self.d.board_st, [])
                i = bisect_left([x[0] for x in lst], t)
                if i >= len(lst):
                    return None, "승차 기록 없음"
                tb, vid = lst[i]
                ta = Data.next_event(self.d.btruth.get(self.d.alight_st, {}).get(vid, []), tb)
                if not ta:
                    return None, "하차 추적 실패"
            if (tb - t).total_seconds() > 40 * 60:
                return None, "기록 공백"
            trace.append("%s %s탑승→%s하차" % (leg["line"], fmt(tb), fmt(ta)))
            t = ta + timedelta(minutes=leg["walk_after_min"])
        return t + timedelta(minutes=route["final_walk_min"]), " / ".join(trace)

    def static_total(self, route, day):
        return route["origin_walk_min"] + route["final_walk_min"] + sum(
            self.ride_min(l, day) + l["walk_after_min"] for l in route["legs"])


def blocks(times, gap_min=10):
    """기록이 이어진 구간들 [(시작, 끝)]"""
    out = []
    for t in sorted(times):
        if out and (t - out[-1][1]).total_seconds() <= gap_min * 60:
            out[-1][1] = t
        else:
            out.append([t, t])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--logs")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    cfg = load_config(a.config)
    E = cfg["evaluation"]
    log_dir = Path(a.logs) if a.logs else HERE / cfg["logger"]["log_dir"]
    data = Data(cfg, log_dir)
    eng = Engine(cfg, data)
    outd = Path(a.out)
    outd.mkdir(exist_ok=True)
    buf, lead, step = E["buffer_min"], E["lead_min"], E["appointment_step_min"]
    rows = []

    for rid, route in cfg["routes"].items():
        if route["legs"][0]["mode"] == "subway":
            times = data.sub_ts.get(route["legs"][0]["board"], [])
        else:
            times = data.bus_ts
        for b0, b1 in blocks(times):
            A = ceil_min(b0 + timedelta(minutes=lead + 5))
            A = A + timedelta(minutes=(-A.minute) % step)
            while A <= b1:
                rows.append(run_case(eng, data, rid, route, A, buf, lead, times, E))
                A += timedelta(minutes=step)

    keys = ["날짜", "경로", "약속", "시간대", "첫_권장", "최종_권장", "출발", "출발마감", "예상도착", "실제도착", "예측오차_분", "1시간전_예상도착", "1시간전_오차_분", "도착여유_분",
            "정시", "여유지킴", "너무일찍", "변경알림수", "기존_출발", "기존_도착", "기존_여유_분", "기존_정시", "기존_여유지킴",
            "마감출발_도착", "마감출발_정시", "상태", "이동기록"]
    with open(outd / "results.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})
    summ = summarize(rows, cfg)
    export_stats(rows, cfg, outd, summ["날짜"])
    (outd / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(summ, cfg, data)
    (outd / "summary.md").write_text(md, encoding="utf-8")
    print(md)


def run_case(eng, data, rid, route, A, buf, lead, times, E):
    r = {"날짜": svc(A), "경로": rid, "약속": A.strftime("%H:%M"), "시간대": band(A)}
    i0 = bisect_left(times, A - timedelta(minutes=lead))
    first = prev = None
    changes = 0
    leave = None
    for i in range(i0, len(times)):
        t = times[i]
        if t > A:
            break
        D = eng.recommend(route, A, buf, t)
        if D is None:
            continue
        if first is None:
            first, alerted, alert_t = D, D, t
            first_pred = eng.predict_arrival(route, D, t, {})
        # 프로토타입 규칙: 알린 값보다 2분 이상 달라지면 알림, 알림끼리 2분 간격 제한
        elif abs((D - alerted).total_seconds()) >= 120 and (t - alert_t).total_seconds() >= 120:
            changes += 1
            alerted, alert_t = D, t
        prev = D
        nxt = times[i + 1] if i + 1 < len(times) else t + timedelta(seconds=30)
        if D <= nxt:
            leave = max(t, D)
            leave_snap = t
            break
    if leave is None:
        r["상태"] = "권장값 계산 불가"
        return r
    dl = None
    D2 = eng.recommend(route, A, 0, leave if leave <= A else A)
    arr, trace = eng.simulate(route, leave)
    # 정확도: 앱이 화면에 보여 준 예상 도착 vs 실제 도착 (양수 = 실제가 더 늦음)
    pred = eng.predict_arrival(route, leave, leave_snap, {})
    if pred:
        r["예상도착"] = fmt(pred)
    farr, _ = eng.simulate(route, first)
    if first_pred and farr:
        r["1시간전_예상도착"] = fmt(first_pred)
        r["1시간전_오차_분"] = round((farr - first_pred).total_seconds() / 60, 1)
    base_leave = floor_min(A - timedelta(minutes=buf + eng.static_total(route, svc(A))))
    barr, _ = eng.simulate(route, base_leave)
    r.update({"첫_권장": first.strftime("%H:%M"), "최종_권장": prev.strftime("%H:%M"), "출발": fmt(leave),
              "변경알림수": changes, "기존_출발": base_leave.strftime("%H:%M")})
    if D2:
        r["출발마감"] = D2.strftime("%H:%M")
        darr, _ = eng.simulate(route, max(D2, leave))
        if darr:
            r["마감출발_도착"] = fmt(darr)
            r["마감출발_정시"] = int(darr <= A)
    if barr:
        r["기존_도착"] = fmt(barr)
        r["기존_정시"] = int(barr <= A)
        r["기존_여유_분"] = round((A - barr).total_seconds() / 60, 1)
        r["기존_여유지킴"] = int(r["기존_여유_분"] >= buf - 0.01)
    if not arr:
        r["상태"] = trace
        return r
    m = (A - arr).total_seconds() / 60
    if pred:
        r["예측오차_분"] = round((arr - pred).total_seconds() / 60, 1)
    r.update({"실제도착": fmt(arr), "도착여유_분": round(m, 1), "정시": int(arr <= A),
              "여유지킴": int(m >= buf - 0.01), "너무일찍": int(m > buf + E["too_early_extra_min"]),
              "상태": "OK", "이동기록": trace})
    return r


def export_stats(rows, cfg, outd, days):
    """프로토타입이 읽는 경로·시간대별 실적: 여유 지킴 횟수, 예측 오차 분포(실제−예상, 분)."""
    out = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "days": len(days), "routes": {}}
    for rid in cfg["routes"]:
        rs = [r for r in rows if r["경로"] == rid and r.get("상태") == "OK"]
        R = {}
        for name, grp in [("전체", rs)] + [(b, [r for r in rs if r["시간대"] == b]) for b in sorted({r["시간대"] for r in rs})]:
            if not grp:
                continue
            errs = sorted(r["예측오차_분"] for r in grp if "예측오차_분" in r)
            q = lambda f: errs[min(len(errs) - 1, int(f * (len(errs) - 1) + 0.5))] if errs else 0
            R[name] = {"n": len(grp), "ok": sum(r["여유지킴"] for r in grp), "err_p10": q(.1), "err_p50": q(.5), "err_p90": q(.9)}
        out["routes"][rid] = R
    (outd / "stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def _agg(rs):
    ok = [r for r in rs if r.get("상태") == "OK"]
    if not ok:
        return {"n": 0}
    ms = [r["도착여유_분"] for r in ok]
    late = [-m for m in ms if m < 0]
    base = [r for r in ok if r.get("기존_정시") != "" and "기존_정시" in r]
    dl = [r for r in ok if "마감출발_정시" in r]
    errs = [r["예측오차_분"] for r in ok if "예측오차_분" in r]
    ferrs = [r["1시간전_오차_분"] for r in rs if "1시간전_오차_분" in r]
    acc = {}
    if errs:
        ae = [abs(e) for e in errs]
        acc = {"예측_n": len(errs), "평균절대오차_분": round(stats.mean(ae), 2), "오차_중앙값_분": round(stats.median(errs), 2),
               "1분이내_비율": round(100 * sum(a <= 1 for a in ae) / len(ae), 1), "2분이내_비율": round(100 * sum(a <= 2 for a in ae) / len(ae), 1),
               "예상보다_2분넘게_늦음_비율": round(100 * sum(e > 2 for e in errs) / len(errs), 1),
               "최대_늦음_분": max(errs)}
    if ferrs:
        acc["1시간전_평균절대오차_분"] = round(stats.mean(abs(e) for e in ferrs), 2)
        acc["1시간전_2분이내_비율"] = round(100 * sum(abs(e) <= 2 for e in ferrs) / len(ferrs), 1)
    return {**acc,
        "n": len(ok),
        "정시율": round(100 * sum(r["정시"] for r in ok) / len(ok), 1),
        "여유지킴률": round(100 * sum(r["여유지킴"] for r in ok) / len(ok), 1),
        "평균여유_분": round(stats.mean(ms), 1),
        "여유_중앙값": round(stats.median(ms), 1),
        "지각_건": len(late),
        "평균지각_분": round(stats.mean(late), 1) if late else 0,
        "너무일찍_비율": round(100 * sum(r["너무일찍"] for r in ok) / len(ok), 1),
        "평균_변경알림": round(stats.mean(r["변경알림수"] for r in ok), 2),
        "기존방식_정시율": round(100 * sum(r["기존_정시"] for r in base) / len(base), 1) if base else None,
        "기존방식_여유지킴률": round(100 * sum(r["기존_여유지킴"] for r in base) / len(base), 1) if base else None,
        "기존방식_평균여유_분": round(stats.mean(r["기존_여유_분"] for r in base), 1) if base else None,
        "기존방식_여유_범위": [min(r["기존_여유_분"] for r in base), max(r["기존_여유_분"] for r in base)] if base else None,
        "여유_범위": [min(ms), max(ms)],
        "마감출발_정시율": round(100 * sum(r["마감출발_정시"] for r in dl) / len(dl), 1) if dl else None,
    }


def summarize(rows, cfg):
    s = {"전체": _agg(rows), "경로별": {}, "시간대별": {}, "제외": {}}
    for rid in cfg["routes"]:
        s["경로별"][rid] = _agg([r for r in rows if r["경로"] == rid])
    for b in sorted({r["시간대"] for r in rows}):
        s["시간대별"][b] = _agg([r for r in rows if r["시간대"] == b])
    for r in rows:
        if r.get("상태") != "OK":
            s["제외"][r.get("상태", "?")] = s["제외"].get(r.get("상태", "?"), 0) + 1
    s["날짜"] = sorted({r["날짜"] for r in rows})
    return s


def to_markdown(s, cfg, data):
    E = cfg["evaluation"]
    L = ["# 연구 1 · 실시간 정확도 검증 결과", "",
         "- 기록 날짜: %s (%d일)" % (", ".join(s["날짜"]), len(s["날짜"])),
         "- 조건: 일정 여유 %d분, 약속 %d분 간격, 약속 %d분 전부터 앱을 본다고 가정" % (E["buffer_min"], E["appointment_step_min"], E["lead_min"]),
         "- 정답: 같은 열차번호/차량번호가 실제로 역·정류소에 도착한 기록 (기록 간격만큼 오차 ±30~60초)", ""]
    hdr = "| 구분 | n | 정시율 | **성공률(여유 지킴)** | 평균 여유(분) | 지각(건/평균분) | 너무 일찍 | 변경 알림/회 | 기존 방식 정시율 | 마감 출발 정시율 |"
    sep = "|" + "---|" * 10

    def line(name, a):
        if not a.get("n"):
            return "| %s | 0 | - | - | - | - | - | - | - | - |" % name
        return "| %s | %d | %.1f%% | %.1f%% | %.1f | %d / %.1f | %.1f%% | %.2f | %s | %s |" % (
            name, a["n"], a["정시율"], a["여유지킴률"], a["평균여유_분"], a["지각_건"], a["평균지각_분"],
            a["너무일찍_비율"], a["평균_변경알림"],
            "-" if a["기존방식_정시율"] is None else "%.1f%%" % a["기존방식_정시율"],
            "-" if a["마감출발_정시율"] is None else "%.1f%%" % a["마감출발_정시율"])
    ah = "| 구분 | n | 평균 절대 오차(분) | ±1분 이내 | ±2분 이내 | 예상보다 2분 넘게 늦음 | 최대 늦음(분) | 1시간 전 예측 ±2분 이내 |"
    def aline(name, a):
        if not a.get("예측_n"):
            return "| %s | 0 | - | - | - | - | - | - |" % name
        return "| %s | %d | %.2f | %.1f%% | %.1f%% | %.1f%% | %+.1f | %s |" % (name, a["예측_n"], a["평균절대오차_분"], a["1분이내_비율"],
            a["2분이내_비율"], a["예상보다_2분넘게_늦음_비율"], a["최대_늦음_분"],
            "-" if "1시간전_2분이내_비율" not in a else "%.1f%%" % a["1시간전_2분이내_비율"])
    L += ["## 1. 정확도: 앱이 보여 준 예상 도착 vs 실제 도착", "출발하는 순간 앱 화면의 예상 도착과, 그대로 나갔을 때 실제 운행 기록으로 재생한 도착의 차이. (참고: 1시간 전 첫 예측의 오차)", "", ah, "|" + "---|" * 8, aline("전체", s["전체"])]
    for rid, a in s["경로별"].items():
        L.append(aline("%s %s" % (rid, cfg["routes"][rid]["name"]), a))
    for b, a in s["시간대별"].items():
        L.append(aline(b, a))
    L += ["", "## 2. 성공률: 권장대로 나가면 여유를 지켰나"]
    L += ["### 경로별", hdr, sep, line("전체", s["전체"])]
    for rid, a in s["경로별"].items():
        L.append(line("%s %s" % (rid, cfg["routes"][rid]["name"]), a))
    L += ["", "### 시간대별", hdr, sep]
    for b, a in s["시간대별"].items():
        L.append(line(b, a))
    tot = s["전체"]
    goal = E.get("success_target_pct", 95)
    L += ["", "### 판정 (성공 = 여유 %d분을 지켜 도착 · 목표 성공률 %d%% 이상)" % (E["buffer_min"], goal)]
    if tot.get("n"):
        L.append("- %s 성공률(여유 지킴률) %.1f%% (n=%d) · 참고: 약속 시각 안 도착 %.1f%%" % (
            "✅ 달성" if tot["여유지킴률"] >= goal else "❌ 미달", tot["여유지킴률"], tot["n"], tot["정시율"]))
        if tot["기존방식_정시율"] is not None:
            L.append("- 기존 방식(소요시간 역산 + 같은 여유 %d분): 정시율 %.1f%%, 여유 지킴률 %.1f%%, 평균 여유 %.1f분 (범위 %.1f~%.1f분)" % (
                E["buffer_min"], tot["기존방식_정시율"], tot["기존방식_여유지킴률"], tot["기존방식_평균여유_분"], *tot["기존방식_여유_범위"]))
            L.append("- 약속 탑승권: 정시율 %.1f%%, 여유 지킴률 %.1f%%, 평균 여유 %.1f분 (범위 %.1f~%.1f분)" % (
                tot["정시율"], tot["여유지킴률"], tot["평균여유_분"], *tot["여유_범위"]))
        if tot["마감출발_정시율"] is not None:
            L.append("- '늦어도(출발 마감)' 시각에 나갔을 때 정시율 %.1f%% → 낮으면 마감 계산을 더 보수적으로" % tot["마감출발_정시율"])
    if s["제외"]:
        L.append("- 분석 제외: " + ", ".join("%s %d건" % kv for kv in s["제외"].items()))
    L.append("- 버스 정류소: 승차 stId=%s, 하차 stId=%s" % (data.board_st, data.alight_st))
    return "\n".join(L)


if __name__ == "__main__":
    main()
