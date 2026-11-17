"""1단계 · 서울시 API 실응답 확인.

사용:  python api_check.py               (실제 호출, config.json 필요)
       python api_check.py --repeat 6    (30초 간격으로 6번, 열차번호 추적 확인)
결과:  check_out/ 에 원본 응답 JSON, check_out/check_report.md 에 점검표
"""
import argparse, json, time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from common import (load_config, fetch_subway, fetch_bus_arrivals, fetch_bus_positions,
                    subway_eta_seconds, bus_eta_seconds, pick_bus_stops, dir_match, parse_dt, HERE)

OK, NG, WARN = "✅", "❌", "⚠️"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--interval", type=int, default=30)
    a = ap.parse_args()
    cfg = load_config(a.config)
    out = HERE / "check_out"
    out.mkdir(exist_ok=True)
    lines = ["# 서울시 API 실응답 점검표", "", "실행 시각: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""]
    seen = defaultdict(set)          # (역, 노선) -> 열차번호
    zero, total = 0, 0
    labels = defaultdict(set)
    skew = []

    for k in range(a.repeat):
        stamp = datetime.now().strftime("%H%M%S")
        for st in cfg["subway_stations"]:
            try:
                res = fetch_subway(cfg, st)
            except Exception as e:
                lines.append("- %s 지하철 %s 호출 실패: %s" % (NG, st, e))
                continue
            (out / ("subway_%s_%s.json" % (st, stamp))).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            if k == 0:
                lines.append("- %s 지하철 %s: code=%s, %d행" % (OK if res["rows"] else NG, st, res["code"], len(res["rows"])))
            for r in res["rows"]:
                labels[(st, r.get("subwayId"))].add(r.get("trainLineNm", ""))
                seen[(st, r.get("subwayId"))].add(r.get("btrainNo"))
                total += 1
                if str(r.get("barvlDt", "0")) in ("0", ""):
                    zero += 1
                rt = parse_dt(r.get("recptnDt"))
                if rt:
                    skew.append((datetime.now() - rt).total_seconds())
        if k == 0:
            try:
                ba = fetch_bus_arrivals(cfg)
                (out / ("bus_arr_%s.json" % stamp)).write_text(json.dumps(ba, ensure_ascii=False, indent=1), encoding="utf-8")
                b, al, cands = pick_bus_stops(ba["rows"], cfg["bus"]["board_name"], cfg["bus"]["alight_name"], cfg["bus"]["alight_prev_hint"])
                lines.append("- %s 버스 도착정보(273): code=%s, 정류소 %d곳" % (OK if ba["rows"] else NG, ba["code"], len(ba["rows"])))
                lines.append("  - 후보 정류소: " + "; ".join("%s(순번 %s, stId %s, arsId %s)" % (c.get("stNm"), c.get("staOrd"), c.get("stId"), c.get("arsId")) for c in cands))
                lines.append("  - %s 자동 선택 승차: %s / 하차: %s" % (OK if b and al else NG,
                             b and "%s stId=%s" % (b["stNm"], b["stId"]), al and "%s stId=%s" % (al["stNm"], al["stId"])))
                if b:
                    lines.append("  - 승차 정류소 차량1 vehId=%s 남은 %s초 (메시지 '%s')" % (b.get("vehId1"), bus_eta_seconds(b, 1), b.get("arrmsg1")))
                    lines.append("  - 👉 config.json bus.board_stId=\"%s\", alight_stId=\"%s\" 로 고정하세요." % (b.get("stId"), al.get("stId") if al else ""))
            except Exception as e:
                lines.append("- %s 버스 도착정보 호출 실패: %s" % (NG, e))
            try:
                bp = fetch_bus_positions(cfg)
                (out / ("bus_pos_%s.json" % stamp)).write_text(json.dumps(bp, ensure_ascii=False, indent=1), encoding="utf-8")
                ex = bp["rows"][0] if bp["rows"] else {}
                need = ["vehId", "lastStnId", "stopFlag", "dataTm", "sectOrd"]
                miss = [n for n in need if n not in ex]
                lines.append("- %s 버스 위치정보: %d대, 필드 누락: %s" % (OK if bp["rows"] and not miss else NG, len(bp["rows"]), miss or "없음"))
            except Exception as e:
                lines.append("- %s 버스 위치정보 호출 실패: %s (실제 도착 기록용이라 꼭 필요)" % (NG, e))
        if k < a.repeat - 1:
            time.sleep(a.interval)

    lines += ["", "## 방면 표기 (config의 board_dir / alight_dir 와 대조)"]
    for (st, sid), labs in sorted(labels.items()):
        lines.append("- %s · %s: %s" % (st, sid, " | ".join(sorted(labs))))
    lines += ["", "## 경로별 필터 확인"]
    for rid, r in cfg["routes"].items():
        for leg in r["legs"]:
            if leg["mode"] != "subway":
                continue
            for side in ("board", "alight"):
                st, lab = leg[side], leg[side + "_dir"]
                ok = any(lab in l for (s, sid), labs in labels.items() if s == st for l in labs)
                lines.append("- %s %s %s %s '%s'" % (OK if ok else WARN, rid, leg["line"], st, lab))
    lines += ["", "## 품질"]
    if total:
        lines.append("- barvlDt=0 비율: %.0f%% (높으면 메시지 보완이 중요)" % (100 * zero / total))
    if skew:
        skew.sort()
        lines.append("- 수신시각(recptnDt) 지연 중앙값: %.0f초" % skew[len(skew) // 2])
    if a.repeat > 1:
        a2 = seen.get(("홍대입구", "1002"), set())
        for s2 in ("충정로", "을지로3가"):
            both = a2 & seen.get((s2, "1002"), set())
            lines.append("- %s 2호선 열차번호가 홍대입구·%s 양쪽에서 잡힘: %d개 (0이면 --repeat 늘려서 재시도)" % (OK if both else WARN, s2, len(both)))
    else:
        lines.append("- 열차번호 추적은 --repeat 6 이상으로 확인하세요.")
    rep = out / "check_report.md"
    rep.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("\n저장:", rep)


if __name__ == "__main__":
    main()
