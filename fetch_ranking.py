# -*- coding: utf-8 -*-
"""バフェット・コード PTSランキング(値上がり/値下がり 上位30)を取得し、
前回保存分と比較した順位変動付きで CSV と公開サイト用 JSON に出力する。

出力:
  history/YYYY-MM-DD.json       … 当日の生データ(翌日以降の比較用)
  output/ranking_YYYY-MM-DD.csv … 当日のランキングCSV(UTF-8)
  docs/data/YYYY-MM-DD.json     … GitHub Pages 用データ
  docs/data/index.json          … 日付一覧(新しい順)
  標準出力に CSV のフルパスを表示する。
"""
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
HISTORY = BASE / "history"
OUTPUT = BASE / "output"
DOCS_DATA = BASE / "docs" / "data"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# バフェット・コードはRefererヘッダが無いと403を返す(User-Agentのみでは不十分)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.google.com/",
}

SECTIONS = [
    ("gainers", "https://www.buffett-code.com/pts"),
    ("losers", "https://www.buffett-code.com/pts/decrease"),
]
TOP_N = 30

# ページ上部の「YYYY/MM/DD HH:MM時点(N分ディレイ)」表記(データ時点)
AS_OF_RE = re.compile(r'l-mt30">(\d{4})/(\d{2})/(\d{2}) (\d{2}:\d{2})[^<]*時点[^<]*</div>')

ROW_RE = re.compile(
    r'<tr><td class="u-text-center pts-ranking-table__rank[^"]*">(?P<rank>\d+)</td>'
    r'<td class="u-text-left[^"]*"><a href="/company/(?P<code>[0-9A-Za-z]+)/">(?P<name>[^<]*)</a>'
    r'<span[^>]*>(?P<code2>[0-9A-Za-z]+),(?P<market>[^<]*)</span></td>'
    r'<td class="u-text-right[^"]*">(?P<price>[^<]*)<span[^>]*>(?P<price_time>[^<]*)</span></td>'
    r'<td class="u-text-right[^"]*">(?P<change>[^<]*)<span[^>]*>(?P<change_pct>[^<]*)</span></td>'
    r'<td class="u-text-right[^"]*">(?P<volume>[^<]*)</td>'
    r'<td class="u-text-right[^"]*">(?P<value>[^<]*)</td>'
    r'<td class="u-text-right[^"]*">(?P<market_cap>[^<]*)</td>'
    r'<td class="u-text-right[^"]*">(?P<per>[^<]*)</td>'
    r'<td class="u-text-right[^"]*">(?P<pbr>[^<]*)</td></tr>')


def parse_number(s: str) -> float:
    """カンマ区切り・単位付きの数値文字列をfloatに変換(－は0扱い)"""
    if not s or s.strip() in ("－", "-", ""):
        return 0.0
    s = s.replace(",", "").replace("+", "")
    s = re.sub(r"(千円|億円|兆円|倍|%)$", "", s.strip())
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def parse(html: str):
    rows = []
    for m in ROW_RE.finditer(html):
        d = {k: v.strip() for k, v in m.groupdict().items()}
        del d["code2"]
        rows.append(d)
    return rows


def parse_as_of(html: str):
    m = AS_OF_RE.search(html)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}"


def load_prev(section: str, today: str):
    files = sorted(p for p in HISTORY.glob("*.json") if p.stem < today)
    if not files:
        return None, {}
    prev_date = files[-1].stem
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    stocks = data.get(section, [])
    return prev_date, {s["code"]: s["rank"] for s in stocks}


def apply_rank_change(stocks, prev_map, has_prev: bool):
    for s in stocks:
        rank = int(s["rank"])
        if s["code"] in prev_map:
            prev_rank = prev_map[s["code"]]
            diff = prev_rank - rank
            if diff > 0:
                s["move"] = f"↑{diff}"
            elif diff < 0:
                s["move"] = f"↓{-diff}"
            else:
                s["move"] = "→"
            s["prev_rank"] = prev_rank
            s["move_num"] = diff
        else:
            s["move"] = "NEW" if has_prev else ""
            s["prev_rank"] = ""
            s["move_num"] = None


def main():
    today = date.today().isoformat()
    HISTORY.mkdir(exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)
    DOCS_DATA.mkdir(parents=True, exist_ok=True)

    result = {}
    as_of_map = {}
    for section, url in SECTIONS:
        html = fetch(url)
        as_of = parse_as_of(html)
        rows = parse(html)
        if len(rows) < TOP_N:
            print(f"ERROR: {section} から{TOP_N}件取得できませんでした"
                  f"(取得件数: {len(rows)}, ページ構造の変更の可能性)", file=sys.stderr)
            sys.exit(1)
        rows = rows[:TOP_N]
        prev_date, prev_map = load_prev(section, today)
        apply_rank_change(rows, prev_map, bool(prev_map) or prev_date is not None)
        result[section] = rows
        as_of_map[section] = as_of
        time.sleep(1.5)

    prev_date, _ = load_prev("gainers", today)

    (HISTORY / f"{today}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    site_payload = {
        "date": today,
        "as_of": as_of_map,
        "prev_date": prev_date,
        "sections": result,
    }
    (DOCS_DATA / f"{today}.json").write_text(
        json.dumps(site_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    dates = sorted((p.stem for p in DOCS_DATA.glob("????-??-??.json")), reverse=True)
    (DOCS_DATA / "index.json").write_text(
        json.dumps({"dates": dates}, ensure_ascii=False), encoding="utf-8")

    header = ["区分", "順位", "順位変動", "前回順位", "コード", "銘柄名", "市場",
              "現在値", "更新時刻", "前日比", "前日比%", "出来高",
              "売買代金(推計)", "時価総額", "PER", "PBR"]
    lines = [",".join(header)]
    section_label = {"gainers": "値上がり", "losers": "値下がり"}
    for section, _ in SECTIONS:
        for s in result[section]:
            cells = [section_label[section], str(s["rank"]), s["move"], str(s["prev_rank"]),
                     s["code"], s["name"], s["market"], s["price"], s["price_time"],
                     s["change"], s["change_pct"], s["volume"], s["value"],
                     s["market_cap"], s["per"], s["pbr"]]
            lines.append(",".join('"' + c.replace('"', '""') + '"' for c in cells))
    csv_path = OUTPUT / f"ranking_{today}.csv"
    csv_path.write_text("\n".join(lines), encoding="utf-8")

    print(str(csv_path))
    print(f"値上がり: {len(result['gainers'])}件, 値下がり: {len(result['losers'])}件, "
          f"比較対象: {prev_date or 'なし(初回)'}", file=sys.stderr)


if __name__ == "__main__":
    main()
