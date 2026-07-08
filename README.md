# PTS株価ランキング (値上がり・値下がり 各上位30)

[バフェット・コード PTSランキング](https://www.buffett-code.com/pts) の値上がり・値下がりランキング上位30銘柄を毎営業日深夜に取得したデータです。

**📊 閲覧用サイト: https://al00000000.github.io/buffett-code-pts-ranking/**
(値上がり/値下がりタブ切替・日付切替・列ソート・銘柄検索・ライブ更新ができます)

## データ

- [docs/data/](docs/data/) … 閲覧用サイトが読み込む日次JSON
- [output/](output/) … 日次のランキングCSV (`ranking_YYYY-MM-DD.csv`, UTF-8)
  - 区分(値上がり/値下がり) / 順位 / 順位変動 / 前回順位 / コード / 銘柄名 / 市場 / 現在値 / 更新時刻 / 前日比 / 前日比% / 出来高 / 売買代金(推計) / 時価総額 / PER / PBR
- [history/](history/) … 比較計算用の生データ (JSON)

順位変動の表記: `↑n`(n位上昇) / `↓n`(n位下降) / `→`(変わらず) / `NEW`(前回圏外から登場)

## 取得スクリプト

[fetch_ranking.py](fetch_ranking.py) — Python標準ライブラリのみで動作します。

```
py fetch_ranking.py
```

## 注意

- データの取得元は buffett-code.com です。データの正確性は保証しません。投資判断は自己責任でお願いします。
- 市場休場日は更新されません。
