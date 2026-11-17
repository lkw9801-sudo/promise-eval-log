"""약속 탑승권 프로토타입 실행기 (실시간 서울시 API 연동). 파이썬 표준 라이브러리만 사용.

사용:  python server.py          실제 서울시 API (config.json의 인증키)
       python server.py --mock   인터넷 없이 모의 데이터로 연습
브라우저에서 http://localhost:8787 → 콘솔 「데이터」에서 「실시간 · 서울시 API」 클릭
"""
import json, random, sys, threading, time, urllib.parse, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from common import load_config, fetch_subway, fetch_bus_arrivals, HERE

CFG = load_config()
MOCK = "--mock" in sys.argv
PORT = 8787
STATIONS = ["홍대입구", "충정로", "을지로3가"]
CACHE = {"at": 0.0, "data": None}
LOCK = threading.Lock()


def mock_payload():
    import mock_logs as M
    from datetime import timedelta
    now = datetime.now()
    rng = random.Random(int(now.timestamp() // 600))      # 10분 동안 같은 운행표
    t0 = now.replace(second=0, microsecond=0) - timedelta(minutes=5)
    trains = {sid: M.gen_trains(random.Random(int(t0.timestamp() // 600) + int(sid)), sid, t0, t0 + timedelta(minutes=60)) for sid in M.LINES}
    buses = M.gen_buses(random.Random(int(t0.timestamp() // 600)), t0, t0 + timedelta(minutes=60))
    sub = {st: {"ok": True, "msg": "모의", "list": M.subway_rows(rng, st, now, trains)} for st in STATIONS}
    return {"mode": "mock", "at": now.strftime("%H:%M:%S"), "subway": sub,
            "bus": {"ok": True, "msg": "모의", "items": M.bus_arr_rows(rng, now, buses)}}


def live_payload():
    sub = {}
    for st in STATIONS:
        try:
            r = fetch_subway(CFG, st)
            sub[st] = {"ok": bool(r["rows"]), "msg": "%s %s" % (r["code"] or "", r["msg"] or ""), "list": r["rows"]}
        except Exception as e:
            sub[st] = {"ok": False, "msg": "연결 실패: %r" % e, "list": []}
    try:
        b = fetch_bus_arrivals(CFG)
        bus = {"ok": bool(b["rows"]), "msg": "%s %s" % (b["code"] or "", b["msg"] or ""), "items": b["rows"]}
    except Exception as e:
        bus = {"ok": False, "msg": "연결 실패: %r" % e, "items": []}
    return {"mode": "live", "at": datetime.now().strftime("%H:%M:%S"), "subway": sub, "bus": bus}


def get_live(force=False):
    with LOCK:
        age = time.time() - CACHE["at"]
        if CACHE["data"] is None or age > 50 or (force and age > 15):
            CACHE["data"] = mock_payload() if MOCK else live_payload()
            CACHE["at"] = time.time()
            d = CACHE["data"]
            bad = [k for k, v in d["subway"].items() if not v["ok"]] + ([] if d["bus"]["ok"] else ["버스 273"])
            print(datetime.now().strftime("%H:%M:%S"), "갱신", "· 문제: " + ", ".join(bad) if bad else "· 정상")
        return CACHE["data"]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/ping":
            return self.send(200, json.dumps({"app": "promise-pass", "mock": MOCK}), "application/json")
        if u.path == "/api/stats":
            f = HERE / "out" / "stats.json"
            if f.exists():
                return self.send(200, f.read_bytes(), "application/json; charset=utf-8")
            return self.send(404, "no stats", "text/plain; charset=utf-8")
        if u.path == "/api/live":
            d = get_live("force=1" in (u.query or ""))
            return self.send(200, json.dumps(d, ensure_ascii=False, indent=1), "application/json; charset=utf-8")
        return self.send(404, "not found", "text/plain; charset=utf-8")


def main():
    url = "http://localhost:%d" % PORT
    print("=" * 50)
    print(" 약속 탑승권 실행기%s" % ("  [모의 모드]" if MOCK else "  [서울시 실시간]"))
    print(" 브라우저: %s   (끄기: Ctrl+C)" % url)
    print("=" * 50)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        print("포트 %d를 이미 쓰고 있어요. 열려 있는 실행기를 먼저 끄세요." % PORT)
        sys.exit(1)
    threading.Thread(target=get_live, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
