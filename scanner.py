#!/usr/bin/env python3
"""🇹🇼 全台股（上市 TWSE + 上櫃 TPEX 約 1,970+ 檔）每日自動掃描並產生 GitHub Pages 網頁 (index.html).

100% 純 Python 3 標準函式庫，完全不需 pip install 任何外部套件。
"""
import json
import os
import re
import ssl
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "ui_template.html")
OUTPUT_HTML = os.path.join(BASE_DIR, "index.html")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_JSON = os.path.join(DATA_DIR, "latest_scan.json")

SSL_CTXS = [ssl.create_default_context(), ssl._create_unverified_context()]


def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TWStockRadar/1.0)"})
    last_err = None
    for ctx in SSL_CTXS:
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except ssl.SSLError as e:
            last_err = e
            continue
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError):
                last_err = e
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError("Failed to fetch " + url)


def parse_float(s):
    try:
        return float(str(s).replace(",", "").replace("+", "").replace("X", "").strip())
    except Exception:
        return None


def compute_metrics(code, name, sector, bars, bench_ret20=0.0):
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    vols = [b["v"] for b in bars]
    n = len(bars)

    for i in range(n):
        b = bars[i]
        b["ma5"] = round(sum(closes[max(0, i - 4) : i + 1]) / len(closes[max(0, i - 4) : i + 1]), 2)
        b["ma20"] = round(sum(closes[max(0, i - 19) : i + 1]) / len(closes[max(0, i - 19) : i + 1]), 2)
        b["ma60"] = round(sum(closes[max(0, i - 59) : i + 1]) / len(closes[max(0, i - 59) : i + 1]), 2)
        w20 = closes[max(0, i - 19) : i + 1]
        sd20 = statistics.pstdev(w20) if len(w20) > 1 else 0.0
        b["bb_up"] = round(b["ma20"] + 2.0 * sd20, 2)
        b["bb_dn"] = round(b["ma20"] - 2.0 * sd20, 2)
        b["bb_w"] = round((4.0 * sd20 / b["ma20"]) * 100.0, 2) if b["ma20"] > 0 else 0.0
        vw20 = vols[max(0, i - 19) : i + 1]
        b["vma20"] = int(sum(vw20) / len(vw20))

    last, prev = bars[-1], bars[-2]
    chg = round(last["c"] - prev["c"], 2)
    chg_pct = round((chg / prev["c"]) * 100.0, 2)
    vol_ratio = round(last["v"] / max(1, last["vma20"]), 2)
    vol_ratio_3d = round(max(bars[-k]["v"] / max(1, bars[-k]["vma20"]) for k in range(1, min(4, n))), 2)
    ma20_slope = round((last["ma20"] / bars[max(0, n - 6)]["ma20"] - 1.0) * 100.0, 2)
    ma60_slope = round((last["ma60"] / bars[max(0, n - 6)]["ma60"] - 1.0) * 100.0, 2)
    bias20 = round((last["c"] / last["ma20"] - 1.0) * 100.0, 2)
    pre_bb_w = round(min(b["bb_w"] for b in bars[max(0, n - 15) : max(1, n - 2)]), 1)
    curr_bb_w = round(last["bb_w"], 1)

    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(max(1, n - 14), n)
    ]
    atr14 = round(sum(trs) / len(trs), 2)
    stop_loss = round(min(last["ma20"] * 0.985, last["c"] - 1.8 * atr14), 2)
    stop_pct = round((stop_loss / last["c"] - 1.0) * 100.0, 2)
    risk_per_share = max(0.2, last["c"] - stop_loss)
    target_2r = round(last["c"] + 2.2 * risk_per_share, 2)
    target_pct = round((target_2r / last["c"] - 1.0) * 100.0, 2)

    gains = [max(0, closes[i] - closes[i - 1]) for i in range(max(1, n - 14), n)]
    losses = [max(0, closes[i - 1] - closes[i]) for i in range(max(1, n - 14), n)]
    avg_g, avg_l = sum(gains) / 14.0, sum(losses) / 14.0
    rsi14 = round(100.0 - (100.0 / (1.0 + avg_g / avg_l)), 1) if avg_l > 0 else 100.0
    ret20 = (last["c"] / closes[max(0, n - 21)] - 1.0) * 100.0
    rs_vs_0050 = round(ret20 - bench_ret20, 2)

    if code == "0050":
        cat, cat_label, score = "benchmark", "📊 台股大盤基準", 55
    elif bias20 > 12.0 and chg_pct > -3.0:
        cat, cat_label, score = "overextended", "⚠️ 強勢但乖離大・勿追高", min(85, int(68 + min(15, bias20 / 2)))
    elif chg_pct >= 0.0 and 0.0 <= bias20 <= 12.0 and (vol_ratio >= 1.2 or vol_ratio_3d >= 1.4) and rs_vs_0050 > 0:
        cat = "breakout"
        cat_label = "🔥 全市場篩出・帶量起漲"
        score = min(98, int(82 + min(8, vol_ratio_3d * 2.0) + (5 if pre_bb_w <= 16 else 0) + (3 if chg_pct >= 3.0 else 0)))
    elif chg_pct > -4.0 and -2.5 <= bias20 <= 8.0 and (pre_bb_w <= 18.0 or rs_vs_0050 >= 15.0):
        cat = "squeeze"
        cat_label = "⏳ 底部收斂・抗跌蓄勢"
        score = min(84, int(72 + (6 if pre_bb_w <= 12 else 2) + (4 if bias20 >= 0 else 0)))
    else:
        cat, cat_label, score = "downtrend", "❄️ 跌破生命線・空頭觀望", max(20, int(45 + bias20))

    b_list = [
        [b["d"][5:], b["o"], b["h"], b["l"], b["c"], b["v"], b["ma20"], b["ma60"], b["bb_up"], b["bb_dn"], b["vma20"]]
        for b in bars[-45:]
    ]
    return {
        "code": code, "name": name, "sector": sector,
        "cat": cat, "cat_label": cat_label, "score": score, "date": last["d"],
        "close": last["c"], "chg": chg, "chg_pct": chg_pct,
        "vol": last["v"], "vma20": last["vma20"], "vol_ratio": vol_ratio, "vol_ratio_3d": vol_ratio_3d,
        "ma5": last["ma5"], "ma20": last["ma20"], "ma60": last["ma60"],
        "ma20_slope": ma20_slope, "ma60_slope": ma60_slope, "bias20": bias20,
        "pre_bb_w": pre_bb_w, "curr_bb_w": curr_bb_w, "rsi14": rsi14, "rs_vs_0050": rs_vs_0050,
        "atr14": atr14, "stop_loss": stop_loss, "stop_pct": stop_pct,
        "target_2r": target_2r, "target_pct": target_pct, "b": b_list,
    }


def run_scan():
    print("Stage 1: Fetching ALL TWSE & TPEX stocks...")
    twse_day = fetch_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")
    twse_avg_raw = fetch_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL")
    tpex_raw = fetch_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")

    twse_avg = {}
    for r in twse_avg_raw:
        c = r.get("Code", "").strip()
        val = parse_float(r.get("MonthlyAveragePrice"))
        if val:
            twse_avg[c] = val

    twse_stocks = []
    for r in twse_day:
        code = r.get("Code", "").strip()
        if not (len(code) == 4 and code.isdigit() and not code.startswith("00")):
            continue
        close = parse_float(r.get("ClosingPrice"))
        chg = parse_float(r.get("Change"))
        vol_sh = parse_float(r.get("TradeVolume"))
        if not close or vol_sh is None:
            continue
        vol_lots = int(vol_sh // 1000)
        prev = close - chg if chg is not None else close
        chg_pct = (chg / prev * 100.0) if (chg is not None and prev > 0) else 0.0
        m_avg = twse_avg.get(code, 0.0)
        twse_stocks.append({
            "code": code, "name": r.get("Name", "").strip(), "market": "上市", "suffix": ".TW",
            "close": close, "chg_pct": round(chg_pct, 2), "vol_lots": vol_lots,
            "above_mavg": (close >= m_avg * 0.98) if m_avg > 0 else False,
        })

    tpex_map = {}
    for r in tpex_raw:
        code = r.get("SecuritiesCompanyCode", "").strip()
        if not (len(code) == 4 and code.isdigit() and not code.startswith("00")):
            continue
        close = parse_float(r.get("Close"))
        chg = parse_float(r.get("Change"))
        vol_sh = parse_float(r.get("TradingShares"))
        if not close or vol_sh is None:
            continue
        vol_lots = int(vol_sh // 1000)
        prev = close - chg if chg is not None else close
        chg_pct = (chg / prev * 100.0) if (chg is not None and prev > 0) else 0.0
        dt_str = r.get("Date", "")
        if code not in tpex_map or dt_str >= tpex_map[code]["dt"]:
            tpex_map[code] = {
                "dt": dt_str, "code": code, "name": r.get("CompanyName", "").strip(), "market": "上櫃", "suffix": ".TWO",
                "close": close, "chg_pct": round(chg_pct, 2), "vol_lots": vol_lots, "above_mavg": False,
            }

    tpex_stocks = list(tpex_map.values())
    all_stocks = twse_stocks + tpex_stocks
    total_scanned = len(all_stocks)
    rising_count = sum(1 for x in all_stocks if x["chg_pct"] > 0)
    falling_count = sum(1 for x in all_stocks if x["chg_pct"] < 0)
    liquid_twse = [x for x in twse_stocks if x["vol_lots"] >= 500 and x["close"] >= 10.0]
    liquid_tpex = [x for x in tpex_stocks if x["vol_lots"] >= 500 and x["close"] >= 10.0]
    liquid_count = len(liquid_twse) + len(liquid_tpex)

    cand_twse = sorted(liquid_twse, key=lambda x: (1 if x["chg_pct"] > 0 else 0, 1 if x["above_mavg"] else 0, x["chg_pct"], x["vol_lots"]), reverse=True)[:45]
    cand_tpex = sorted(liquid_tpex, key=lambda x: (1 if x["chg_pct"] > 0 else 0, x["chg_pct"], x["vol_lots"]), reverse=True)[:45]
    candidates = cand_twse + cand_tpex

    core_list = [
        ("0050", "元大台灣50", "大盤ETF", ".TW"),
        ("2330", "台積電", "上市・權值", ".TW"),
        ("2317", "鴻海", "上市・權值", ".TW"),
        ("2454", "聯發科", "上市・權值", ".TW"),
        ("2382", "廣達", "上市・權值", ".TW"),
        ("2603", "長榮", "上市・權值", ".TW"),
        ("2881", "富邦金", "上市・權值", ".TW"),
        ("2891", "中信金", "上市・權值", ".TW"),
    ]
    cand_codes = {c["code"] for c in candidates}
    for code, name, market, suffix in core_list:
        if code not in cand_codes:
            candidates.append({"code": code, "name": name, "market": market, "suffix": suffix})
            cand_codes.add(code)

    print(f"Stage 2: Deep 6-month daily K-bar fetch for {len(candidates)} shortlist stocks...")

    def fetch_yf(item):
        try:
            data = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{item['code']}{item['suffix']}?range=6mo&interval=1d", timeout=10)
            res = data["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            bars = []
            for i in range(len(ts)):
                o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
                if None in (o, h, l, c, v) or v == 0:
                    continue
                dt = datetime.fromtimestamp(ts[i]).strftime("%Y-%m-%d")
                bars.append({"d": dt, "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2), "v": int(v // 1000)})
            if len(bars) >= 45:
                return {**item, "bars": bars}
        except Exception:
            return None
        return None

    deep_results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(fetch_yf, c) for c in candidates]
        for f in as_completed(futs):
            r = f.result()
            if r:
                deep_results.append(r)

    if len(deep_results) < 10:
        raise RuntimeError(f"Only fetched {len(deep_results)} stocks; aborting overwrite to preserve existing index.html.")

    bench_ret20 = 0.0
    for r in deep_results:
        if r["code"] == "0050":
            cs = [b["c"] for b in r["bars"]]
            bench_ret20 = (cs[-1] / cs[-21] - 1.0) * 100.0

    processed = [compute_metrics(s["code"], s["name"], s["market"], s["bars"], bench_ret20) for s in deep_results]

    breakouts = sorted([x for x in processed if x["cat"] == "breakout"], key=lambda x: (-x["score"], -x["chg_pct"]))[:15]
    squeezes = sorted([x for x in processed if x["cat"] == "squeeze"], key=lambda x: (-x["score"], x["pre_bb_w"]))[:15]
    overext = sorted([x for x in processed if x["cat"] == "overextended"], key=lambda x: (-x["rs_vs_0050"], -x["bias20"]))[:10]
    benchmarks = [x for x in processed if x["code"] == "0050"]
    core_codes = {"2330", "2317", "2454", "2382", "2603", "2881", "2891"}
    chosen_codes = {x["code"] for x in breakouts + squeezes + overext + benchmarks}
    core_extras = [x for x in processed if x["code"] in core_codes and x["code"] not in chosen_codes]

    final_list = breakouts + squeezes + overext + benchmarks + core_extras
    tpe_now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    meta = {
        "scan_date": max(x["date"] for x in final_list),
        "updated_at_tpe": tpe_now,
        "total_scanned": total_scanned,
        "twse_count": len(twse_stocks),
        "tpex_count": len(tpex_stocks),
        "liquid_count": liquid_count,
        "rising_count": rising_count,
        "falling_count": falling_count,
        "deep_analyzed": len(deep_results),
        "breakout_found": len(breakouts),
        "squeeze_found": len(squeezes),
        "overext_found": len(overext),
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "stocks": final_list}, f, ensure_ascii=False, separators=(",", ":"))

    build_index_html(meta, final_list)
    print(f"SUCCESS: Scanned {total_scanned} stocks, generated index.html (updated {tpe_now} TPE).")


def build_index_html(meta, stocks):
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    js_str = json.dumps(stocks, ensure_ascii=False, separators=(",", ":"))
    html = html.replace("__STOCKS_JSON__", js_str)
    html = html.replace("__UPDATED_AT_TPE__", meta.get("updated_at_tpe", meta["scan_date"]))
    html = re.sub(
        r"全台股 [0-9,]+ 檔掃描 \(截至 [0-9-]+\)",
        f"全台股 {meta['total_scanned']:,} 檔掃描 (截至 {meta['scan_date']})",
        html,
    )

    market_mood = (
        "❄️ 空頭大回檔日（資金防禦優先，僅精選極少數逆勢抗跌起漲股）"
        if meta["falling_count"] > meta["rising_count"] * 2
        else (
            "🔥 多頭順風盤（資金充沛，適合積極捕捉帶量起漲第一波）"
            if meta["rising_count"] > meta["falling_count"] * 1.3
            else "⚖️ 震盪整理盤（選股不選市，鎖定 RS 相對強弱 > 0 的領頭羊）"
        )
    )

    banner = f"""<div id="fullMarketBanner" class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 shadow-sm space-y-2.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-red-500/15 text-red-500 border border-red-500/30">🌐 全市場掃描雷達</span>
            <span class="font-semibold text-sm">已掃描全台股 <strong>{meta['total_scanned']:,} 檔</strong>（上市 {meta['twse_count']:,} 檔 ＋ 上櫃 {meta['tpex_count']:,} 檔）</span>
          </div>
          <span class="text-xs font-medium text-[var(--muted-foreground)]">{market_mood}</span>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-5 gap-2.5 text-xs">
          <div class="p-2.5 rounded-lg bg-[var(--background)] border border-[var(--border)]">
            <div class="text-[var(--muted-foreground)]">① 全市場掃描總數</div>
            <div class="text-base font-bold mt-0.5">{meta['total_scanned']:,} 檔</div>
          </div>
          <div class="p-2.5 rounded-lg bg-[var(--background)] border border-[var(--border)]">
            <div class="text-[var(--muted-foreground)]">② 過濾殭屍股 (≥500張)</div>
            <div class="text-base font-bold mt-0.5">{meta['liquid_count']:,} 檔</div>
          </div>
          <div class="p-2.5 rounded-lg bg-[var(--background)] border border-[var(--border)]">
            <div class="text-[var(--muted-foreground)]">③ 今日全市場漲跌家數</div>
            <div class="text-base font-bold mt-0.5"><span class="text-red-500">漲 {meta['rising_count']}</span> / <span class="text-emerald-500">跌 {meta['falling_count']}</span></div>
          </div>
          <div class="p-2.5 rounded-lg bg-[var(--background)] border border-[var(--border)]">
            <div class="text-[var(--muted-foreground)]">④ 6個月日K深度精算</div>
            <div class="text-base font-bold mt-0.5">Top {meta['deep_analyzed']} 檔強勢候選</div>
          </div>
          <div class="p-2.5 rounded-lg bg-red-500/10 border border-red-500/30">
            <div class="text-red-500 font-semibold">⑤ 篩出起漲＋蓄勢黑馬</div>
            <div class="text-base font-bold text-red-500 mt-0.5">起漲 {meta['breakout_found']} 檔 / 蓄勢 {meta['squeeze_found']} 檔</div>
          </div>
        </div>
      </div>"""

    html = html.replace("__FULL_MARKET_BANNER__", banner)

    c_all = len(stocks)
    c_brk = sum(1 for s in stocks if s["cat"] == "breakout")
    c_sqz = sum(1 for s in stocks if s["cat"] == "squeeze")
    c_ovr = sum(1 for s in stocks if s["cat"] == "overextended")
    c_dwn = sum(1 for s in stocks if s["cat"] == "downtrend")
    html = re.sub(r"全市場精選 \(\d+\)", f"全市場精選 ({c_all})", html)
    html = re.sub(r"🔥 剛起漲・帶量突破 \(\d+\)", f"🔥 剛起漲・帶量突破 ({c_brk})", html)
    html = re.sub(r"⏳ 底部收斂・蓄勢待發 \(\d+\)", f"⏳ 底部收斂・蓄勢待發 ({c_sqz})", html)
    html = re.sub(r"⚠️ 強勢但乖離過大・勿追高 \(\d+\)", f"⚠️ 強勢但乖離過大・勿追高 ({c_ovr})", html)
    html = re.sub(r"❄️ 跌破生命線・空頭觀望 \(\d+\)", f"❄️ 跌破生命線・空頭觀望 ({c_dwn})", html)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    run_scan()
