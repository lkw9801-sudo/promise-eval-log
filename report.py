"""5단계 · 발표용 그래프 리포트 (out/report.html). evaluate.py 다음에 실행.

사용:  python report.py            → out/report.html (브라우저로 열어 캡처해 슬라이드에 사용)
       python report.py --mock     → 화면에 '모의 데이터' 경고 띠 표시
"""
import argparse, csv, html, json
from pathlib import Path
from common import load_config

CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--text-primary:#0b0b0b;--text-secondary:#52514e;--muted:#8a8984;
--grid:#e6e5e1;--series-1:#2a78d6;--series-2:#eb6834;--crit:#e34948;font-family:system-ui,-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
background:var(--surface-1);color:var(--text-primary);margin:0;padding:28px;max-width:1000px}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])) .viz-root{color-scheme:dark;--surface-1:#1a1a19;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--muted:#8f8e86;--grid:#33332f;--series-1:#3987e5;--series-2:#d95926}}
body{margin:0;background:var(--surface-1)}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 6px}.sub{color:var(--text-secondary);font-size:13px;margin:0 0 14px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}
.tile{border:1px solid var(--grid);border-radius:10px;padding:14px}.tile b{display:block;font-size:30px;font-variant-numeric:tabular-nums}
.tile span{font-size:12px;color:var(--text-secondary)}
.legend{display:flex;gap:16px;font-size:13px;color:var(--text-secondary);margin:6px 0}.sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
svg text{fill:var(--text-secondary);font-size:12px}svg .t1{fill:var(--text-primary)}
.warn{background:#fff3cd;color:#5c4400;padding:8px 12px;border-radius:8px;font-size:13px;margin-bottom:14px}
#tip{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);font-size:12px;padding:6px 8px;border-radius:6px;display:none;white-space:pre}
table{border-collapse:collapse;font-size:12px;width:100%;font-variant-numeric:tabular-nums}td,th{border-bottom:1px solid var(--grid);padding:4px 6px;text-align:right}
th:first-child,td:first-child{text-align:left}details{margin-top:24px}
"""


def strip_chart(rows, routes, buf):
    """경로별 도착 여유(약속 몇 분 전 도착) 점 분포: 기존 방식 vs 약속 탑승권"""
    W, rowh, left, right = 940, 70, 190, 20
    xs = [float(r["도착여유_분"]) for r in rows] + [float(r["기존_여유_분"]) for r in rows if r["기존_여유_분"]]
    lo, hi = min(-5, min(xs) - 1), max(buf + 15, max(xs) + 1)
    X = lambda v: left + (v - lo) / (hi - lo) * (W - left - right)
    H = 30 + rowh * len(routes) + 30
    o = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="경로별 도착 여유 분포">' % (W, H)]
    for v in range(int(lo) // 5 * 5, int(hi) + 1, 5):
        o.append('<line x1="%.1f" x2="%.1f" y1="24" y2="%d" stroke="var(--grid)"/>' % (X(v), X(v), H - 26))
        o.append('<text x="%.1f" y="%d" text-anchor="middle">%d</text>' % (X(v), H - 8, v))
    for v, lab in ((0, "약속 시각"), (buf, "여유 %d분" % buf)):
        o.append('<line x1="%.1f" x2="%.1f" y1="18" y2="%d" stroke="var(--text-secondary)" stroke-dasharray="4 3"/>' % (X(v), X(v), H - 26))
        o.append('<text x="%.1f" y="14" text-anchor="middle" class="t1">%s</text>' % (X(v), lab))
    for i, (rid, name) in enumerate(routes):
        y0 = 40 + i * rowh
        o.append('<text x="0" y="%d" class="t1">%s</text>' % (y0 + 22, html.escape(name)))
        rr = [r for r in rows if r["경로"] == rid]
        for j, r in enumerate(rr):
            jit = (j % 5 - 2) * 3
            if r["기존_여유_분"]:
                v = float(r["기존_여유_분"])
                tip = "%s %s 약속 %s\n기존 방식: %s 출발 → %s 도착 (%.1f분 전)" % (r["날짜"], rid, r["약속"], r["기존_출발"], r["기존_도착"], v)
                o.append('<circle cx="%.1f" cy="%d" r="4.5" fill="var(--series-2)" stroke="var(--surface-1)" stroke-width="2" data-tip="%s"/>' % (X(v), y0 + 8 + jit, html.escape(tip)))
            v = float(r["도착여유_분"])
            tip = "%s %s 약속 %s\n약속 탑승권: %s 출발 → %s 도착 (%.1f분 전)\n변경 알림 %s회" % (r["날짜"], rid, r["약속"], r["출발"], r["실제도착"], v, r["변경알림수"])
            o.append('<circle cx="%.1f" cy="%d" r="4.5" fill="var(--series-1)" stroke="var(--surface-1)" stroke-width="2" data-tip="%s"/>' % (X(v), y0 + 34 + jit, html.escape(tip)))
    o.append("</svg>")
    return "".join(o)


def bar_chart(summ, routes):
    """경로별 '여유 지킴률' 비교 막대 (기존 vs 약속 탑승권)"""
    W, left, bh, gap = 940, 190, 16, 2
    H = 20 + len(routes) * 56 + 24
    X = lambda p: left + p / 100 * (W - left - 60)
    o = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="경로별 여유 지킴률">' % (W, H)]
    for p in range(0, 101, 25):
        o.append('<line x1="%.1f" x2="%.1f" y1="10" y2="%d" stroke="var(--grid)"/><text x="%.1f" y="%d" text-anchor="middle">%d%%</text>' % (X(p), X(p), H - 20, X(p), H - 4, p))
    for i, (rid, name) in enumerate(routes):
        a = summ["경로별"].get(rid, {})
        if not a.get("n"):
            continue
        y = 18 + i * 56
        o.append('<text x="0" y="%d" class="t1">%s</text>' % (y + bh + 2, html.escape(name)))
        for k, (val, col, lab) in enumerate(((a["기존방식_여유지킴률"], "var(--series-2)", "기존"), (a["여유지킴률"], "var(--series-1)", "약속 탑승권"))):
            yy = y + k * (bh + gap)
            w = X(val) - X(0)
            if w >= 6:
                o.append('<path d="M%.1f %d h%.1f a4 4 0 0 1 4 4 v%d a4 4 0 0 1 -4 4 h-%.1f z" fill="%s" data-tip="%s %s: %.1f%% (n=%d)"/>' % (
                    X(0), yy, w - 4, bh - 8, w - 4, col, html.escape(name), lab, val, a["n"]))
            o.append('<text x="%.1f" y="%d" class="t1">%.0f%%</text>' % (X(0) + max(w, 0) + 6, yy + bh - 4, val))
    o.append("</svg>")
    return "".join(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    out = Path(a.out)
    rows = [r for r in csv.DictReader(open(out / "results.csv", encoding="utf-8-sig")) if r["상태"] == "OK"]
    summ = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    buf = cfg["evaluation"]["buffer_min"]
    routes = [(rid, "%s %s" % (rid, r["name"])) for rid, r in cfg["routes"].items()]
    t = summ["전체"]
    goal = cfg["evaluation"].get("success_target_pct", 95)
    tiles = [("%.1f분" % t.get("평균절대오차_분", 0), "정확도: 예상 도착과 실제 도착의 평균 차이 (n=%d)" % t.get("예측_n", 0)),
             ("%.1f%%" % t.get("2분이내_비율", 0), "예상 도착 ±2분 안에 실제 도착"),
             ("%.1f%%" % t["여유지킴률"], "성공률: 여유 %d분 지켜 도착 (목표 %d%% · 기존 방식 %.1f%%)" % (buf, goal, t["기존방식_여유지킴률"])),
             ("%.1f%%" % t["정시율"], "약속 시각 안에 도착 (n=%d)" % t["n"])]
    tr = "".join("<tr><td>%s</td><td>%d</td><td>%.1f%%</td><td>%.1f%%</td><td>%.1f%%</td><td>%.1f</td><td>%.1f</td><td>%.2f</td><td>%.1f%%</td></tr>" % (
        n, a["n"], a["정시율"], a["여유지킴률"], a["기존방식_여유지킴률"], a["평균여유_분"], a["기존방식_평균여유_분"], a["평균_변경알림"], a["마감출발_정시율"])
        for n, a in [("전체", t)] + [(nm, summ["경로별"][rid]) for rid, nm in routes] + list(summ["시간대별"].items()) if a.get("n"))
    legend = '<div class="legend"><span><i class="sw" style="background:var(--series-2)"></i>기존 방식 (소요시간 역산 + 여유)</span><span><i class="sw" style="background:var(--series-1)"></i>약속 탑승권 (실시간 권장 출발)</span></div>'
    doc = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>6조 연구 1 · 실시간 정확도 검증</title><style>%s</style></head><body><div class="viz-root">
%s<h1>앱이 말한 도착 시각, 실제로 맞았나</h1><p class="sub">서울시 실시간 도착 기록 %d일 · 경로 3개 · 일정 여유 %d분 · 같은 열차·차량의 실제 도착으로 재생</p>
<div class="tiles">%s</div>
<h2>약속보다 몇 분 일찍 도착했나 (점 1개 = 약속 1건)</h2><p class="sub">점선 사이(0~%d분)가 '여유를 일부 쓴 도착', 0 왼쪽이 지각 · 가로축: 약속보다 몇 분 일찍 도착했나</p>%s%s
<h2>성공률: 여유 %d분을 지킨 비율</h2>%s%s
<details><summary>표로 보기</summary><table><tr><th>구분</th><th>n</th><th>정시율</th><th>여유 지킴</th><th>기존 여유 지킴</th><th>평균 여유</th><th>기존 평균 여유</th><th>변경 알림/회</th><th>마감 출발 정시</th></tr>%s</table></details>
</div><div id="tip"></div><script>
const tip=document.getElementById('tip');document.querySelectorAll('[data-tip]').forEach(e=>{
e.addEventListener('mousemove',ev=>{tip.textContent=e.dataset.tip;tip.style.display='block';tip.style.left=(ev.clientX+12)+'px';tip.style.top=(ev.clientY+12)+'px'});
e.addEventListener('mouseleave',()=>tip.style.display='none')});</script></body></html>""" % (
        CSS, '<div class="warn">⚠️ 모의 데이터로 만든 시험 화면입니다. 발표에 쓰지 마세요.</div>' if a.mock else "",
        len(summ["날짜"]), buf, "".join('<div class="tile"><b>%s</b><span>%s</span></div>' % x for x in tiles),
        buf, legend, strip_chart(rows, routes, buf), buf, legend, bar_chart(summ, routes), tr)
    (out / "report.html").write_text(doc, encoding="utf-8")
    print("저장:", out / "report.html")


if __name__ == "__main__":
    main()
