# -*- coding: utf-8 -*-
"""PTSランキング掲載銘柄について「翌営業日の場中4本値」を取得し、
夜間PTS価格と翌日高値との差を検証できるデータ(docs/data/nextday.json)を作る。

背景(検証したい仮説):
  夜間PTSでは空売りができないため需給が買い方に偏り、翌日の場中より高くなりやすい。
  前営業日に両建て(現物買い+信用売り)を作っておき、夜間PTSで現物だけを売れば、
  実質的にPTS価格でのショートが成立する。
  「PTS価格 > 翌日高値」なら翌日どこで買い戻しても利益が確定する。

「翌営業日」の決め方が肝心なので、ファイル名の日付ではなく
スナップショットの as_of(実際のPTSデータ時点)から判定する。
スケジューラの遅延などで取得時刻が深夜0時からずれている日があるため。

  as_of 15:30以降   … その日の大引け後の夜間PTS。基準日 = as_of の日付
  as_of 09:00未満   … 前営業日夜の夜間PTSの最終値。基準日 = as_of 前日以前の直近立会日
  as_of 09:00-15:30 … 日中PTS。仮説の検証対象外なので除外する

  基準日(= PTSの直前の大引け)の次の立会日が「翌日」。

同じPTSの夜に対して複数のスナップショットがある場合(取引中と終了後など)は、
PTS終了後に取れた1件だけを残して重複集計を避ける。

出力:
  docs/data/nextday.json … サイトの「翌日最高値との差」タブ用データ(全日付ぶん)
  nextday_cache.json     … 取得済みの日足・信用区分のキャッシュ(再取得を避ける)

データ源:
  日足4本値  https://kabutan.jp/stock/read?c={code}&m=1&k=1
  信用区分   https://kabutan.jp/stock/?code={code} の kubun_btn(貸借/信用/現物)
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
DOCS_DATA = BASE / "docs" / "data"
CACHE_PATH = BASE / "nextday_cache.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.google.com/"}

CALENDAR_CODE = "0000"      # 日経平均。営業日カレンダーの生成に使う
SECTIONS = ["gainers", "losers"]
SLEEP = 0.8                 # 連続アクセスの間隔(秒)
KUBUN_MAX_AGE_DAYS = 30     # 信用区分の再取得間隔

SESSION_CLOSE_MIN = 15 * 60 + 30   # 15:30 大引け
SESSION_OPEN_MIN = 9 * 60          # 09:00 寄付
PTS_CLOSE_MIN = 23 * 60            # PTS夜間はおおむね23:59まで

KUBUN_RE = re.compile(r'<div class="kubun_btn"[^>]*>([^<]+)</div>')


def fetch(url: str, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"fetch failed: {url} ({last})")


def fetch_daily(code: str):
    """日足を {'YYYY-MM-DD': [始値, 高値, 安値, 終値]} で返す(単位は円)。"""
    text = fetch(f"https://kabutan.jp/stock/read?c={code}&m=1&k=1")
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return {}
    header = lines[0].split(",")
    # ヘッダ2番目 0=指数 / 1=個別株。価格の除数が異なる(個別株は÷10)
    divisor = 100.0 if len(header) > 1 and header[1] == "0" else 10.0

    bars = {}
    for line in lines[1:]:
        p = line.split(",")
        if len(p) < 5 or len(p[0]) != 8 or not p[0].isdigit():
            continue
        try:
            ohlc = [round(float(p[i]) / divisor, 2) for i in range(1, 5)]
        except ValueError:
            continue
        bars[f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:]}"] = ohlc
    return bars


def fetch_kubun(code: str) -> str:
    """信用区分。'貸借'(制度信用で空売り可) / '信用'(買いのみ) / '現物' / ''(不明)。"""
    try:
        html = fetch(f"https://kabutan.jp/stock/?code={code}")
    except RuntimeError:
        return ""
    m = KUBUN_RE.search(html)
    return m.group(1).strip() if m else ""


def kubun_stale(entry) -> bool:
    """信用区分キャッシュが未取得または古い場合にTrue。区分は頻繁には変わらない。"""
    if not entry or not entry.get("at"):
        return True
    try:
        age = (date.today() - date.fromisoformat(entry["at"])).days
    except ValueError:
        return True
    return age > KUBUN_MAX_AGE_DAYS


def load_cache():
    if CACHE_PATH.exists():
        try:
            c = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            c.setdefault("bars", {})
            c.setdefault("kubun", {})
            return c
        except json.JSONDecodeError:
            print("WARN: キャッシュが壊れているため作り直します", file=sys.stderr)
    return {"bars": {}, "kubun": {}}


def save_cache(cache):
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(CACHE_PATH)


def pnum(s):
    """'2,334.0' や '+19.5%' を float に。数値にできなければ None。"""
    if s is None:
        return None
    s = str(s).replace(",", "").replace("+", "").strip()
    s = re.sub(r"(千円|億円|兆円|倍|%)$", "", s)
    if s in ("", "-", "－"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def as_of_of(payload):
    """スナップショットの as_of(セクション別)から代表値を1つ取る。"""
    for sec in SECTIONS:
        v = (payload.get("as_of") or {}).get(sec)
        if v:
            return v
    return None


def classify(as_of: str):
    """as_of から (基準日ヒント, 品質) を返す。

    基準日ヒント: 'same:YYYY-MM-DD'(その日の大引け後) / 'prev:YYYY-MM-DD'(前営業日夜)
    品質: 'final'(PTS終了後に取れた) / 'mid'(PTS取引中) / None(日中PTS=対象外)
    """
    try:
        d, t = as_of.split(" ")
        hh, mm = (int(x) for x in t.split(":"))
    except (ValueError, AttributeError):
        return None, None
    minutes = hh * 60 + mm
    if minutes >= SESSION_CLOSE_MIN:
        return f"same:{d}", ("final" if minutes >= PTS_CLOSE_MIN else "mid")
    if minutes < SESSION_OPEN_MIN:
        return f"prev:{d}", "final"
    return None, None


def main():
    snapshots = sorted(DOCS_DATA.glob("????-??-??.json"))
    if not snapshots:
        print("ERROR: docs/data にスナップショットがありません", file=sys.stderr)
        return 1

    cache = load_cache()
    bars_cache, kubun_cache = cache["bars"], cache["kubun"]
    today = date.today().isoformat()

    # 営業日カレンダー(実際に日足が存在する日 = 立会日)
    calendar = sorted(fetch_daily(CALENDAR_CODE))
    if not calendar:
        print("ERROR: 営業日カレンダーを取得できませんでした", file=sys.stderr)
        return 1
    last_session = calendar[-1]
    cal_index = {d: i for i, d in enumerate(calendar)}

    def session_on_or_before(d: str):
        for x in reversed(calendar):
            if x <= d:
                return x
        return None

    def session_before(d: str):
        for x in reversed(calendar):
            if x < d:
                return x
        return None

    def session_after(d: str):
        i = cal_index.get(d)
        if i is None or i + 1 >= len(calendar):
            return None
        return calendar[i + 1]

    # ── PTSの夜(基準日)ごとに、使うスナップショットを1つに決める ──
    nights = {}   # base_date -> {snapshot, as_of, quality, payload}
    for path in snapshots:
        payload = json.loads(path.read_text(encoding="utf-8"))
        as_of = as_of_of(payload)
        if not as_of:
            print(f"{path.stem}: as_of が無いためスキップ", file=sys.stderr)
            continue
        hint, quality = classify(as_of)
        if hint is None:
            print(f"{path.stem}: as_of {as_of} は日中PTSのため対象外", file=sys.stderr)
            continue
        kind, d = hint.split(":")
        base = session_on_or_before(d) if kind == "same" else session_before(d)
        if base is None:
            continue
        cur = nights.get(base)
        # 同じ夜が複数あるときは final を優先し、次いで as_of が遅いものを使う
        qrank = lambda q: 1 if q == "final" else 0  # noqa: E731
        if cur is None or (qrank(quality), as_of) > (qrank(cur["quality"]), cur["as_of"]):
            nights[base] = {"snapshot": path.stem, "as_of": as_of,
                            "quality": quality, "payload": payload}

    # 日足キャッシュを必要な範囲に刈り込む(全期間ぶん返ってくるので放置すると肥大化する)
    cutoff = min(nights) if nights else calendar[-1]
    for code in list(bars_cache):
        bars_cache[code] = {d: v for d, v in bars_cache[code].items() if d >= cutoff}

    out_dates = []
    fetched = 0
    for base in sorted(nights):
        info = nights[base]
        nxt = session_after(base)
        if nxt is None or nxt > last_session:
            print(f"{base}の夜({info['snapshot']}): 翌営業日"
                  f"({nxt or '未確定'})はまだ取引が終わっていないためスキップ", file=sys.stderr)
            continue

        rows = []
        for sec in SECTIONS:
            for s in info["payload"]["sections"].get(sec, []):
                code = s["code"]

                cached = bars_cache.setdefault(code, {})
                if nxt not in cached:
                    bars = fetch_daily(code)
                    fetched += 1
                    time.sleep(SLEEP)
                    cached.update({d: v for d, v in bars.items() if d >= cutoff})

                if kubun_stale(kubun_cache.get(code)):
                    kubun_cache[code] = {"v": fetch_kubun(code), "at": today}
                    fetched += 1
                    time.sleep(SLEEP)

                ohlc = cached.get(nxt)
                base_ohlc = cached.get(base)
                rows.append({
                    "sec": sec,
                    "rank": int(s["rank"]),
                    "code": code,
                    "name": s["name"],
                    "market": s["market"],
                    "kubun": kubun_cache[code]["v"],
                    "pts": pnum(s["price"]),
                    "pts_pct": pnum(s["change_pct"]),
                    "o": ohlc[0] if ohlc else None,
                    "h": ohlc[1] if ohlc else None,
                    "l": ohlc[2] if ohlc else None,
                    "c": ohlc[3] if ohlc else None,
                    "base_c": base_ohlc[3] if base_ohlc else None,
                })

        out_dates.append({
            "base_date": base,          # PTSの直前の大引け(この日の夜のPTS)
            "next_date": nxt,           # 買い戻す側の立会日
            "snapshot": info["snapshot"],
            "as_of": info["as_of"],
            "quality": info["quality"],
            "rows": rows,
        })
        save_cache(cache)
        print(f"{base}の夜 → 翌営業日 {nxt} / {len(rows)}銘柄 "
              f"(as_of {info['as_of']}, {info['quality']})", file=sys.stderr)

    save_cache(cache)
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    payload = {"generated": datetime.now().isoformat(timespec="seconds"), "dates": out_dates}
    (DOCS_DATA / "nextday.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"nextday.json: {len(out_dates)}夜 / 新規取得 {fetched}件", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
