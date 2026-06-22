"""
US Stock Pattern Scanner — GitHub Actions daily runner
Scans NASDAQ + NYSE common stocks for technical patterns and saves
results as JSON files consumed by the GitHub Pages website.

Patterns detected:
  - Cup & Handle (O'Neil criteria)
  - High Tight Flag (Livermore / Minervini)
  - VCP — Volatility Contraction Pattern (Minervini)
  - Stage 2 uptrend with 50% probability score

Outputs:
  docs/data/results.json       — full scan results
  docs/data/summary.json       — scan metadata (date, counts, timing)
  docs/data/universe.json      — all tickers that were scanned
"""

import json, math, time, warnings, urllib.request, csv, io, os, sys
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("pip install yfinance pandas lxml")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
MAX_WORKERS   = int(os.environ.get("SCAN_WORKERS", "12"))
OUT_DIR       = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
PERIOD        = "2y"

# ── Ticker universe ───────────────────────────────────────────────────────────

EXCLUDE_TERMS = [
    "Warrant","- Unit"," Right","ETF","Fund","Preferred","Note",
    "Debenture","Acquisition Corp","Blank Check","American Depositary",
    "Depositary Share","Index","Trust","SPAC","Commercial Paper","Royalty",
]

def _is_common(sym: str, name: str) -> bool:
    sym = sym.strip()
    if not sym or len(sym) > 5: return False
    if sym.endswith("W") or (sym.endswith("R") and len(sym) > 3): return False
    return not any(x in name for x in EXCLUDE_TERMS)

def fetch_universe() -> list:
    """Fetch NASDAQ + NYSE common stocks from GitHub datasets."""
    tickers = []
    sources = [
        "https://raw.githubusercontent.com/datasets/nasdaq-listings/master/data/nasdaq-listed.csv",
        "https://raw.githubusercontent.com/datasets/nyse-listings/master/data/nyse-listed.csv",
    ]
    for url in sources:
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                text = r.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            sym_col  = None
            name_col = None
            for row in reader:
                if sym_col is None:
                    sym_col  = next((k for k in row if "symbol" in k.lower()), None)
                    name_col = next((k for k in row if "name"   in k.lower()), None)
                if not sym_col: continue
                sym  = row.get(sym_col, "").strip()
                name = row.get(name_col, "") if name_col else ""
                if _is_common(sym, name):
                    tickers.append(sym)
        except Exception as e:
            print(f"  [warn] Could not fetch {url}: {e}")
    tickers = list(dict.fromkeys(tickers))
    print(f"  Universe: {len(tickers)} common stocks (NASDAQ + NYSE)")
    return tickers

# ── Technical helpers ─────────────────────────────────────────────────────────

def _sma(arr, n):
    out = [None] * len(arr)
    for i in range(n - 1, len(arr)):
        out[i] = sum(arr[i-n+1:i+1]) / n
    return out

def _ema(arr, n):
    k = 2 / (n + 1)
    out = [None] * len(arr)
    for i in range(len(arr)):
        if i < n - 1:
            continue
        if i == n - 1:
            out[i] = sum(arr[:n]) / n
        else:
            out[i] = arr[i] * k + out[i-1] * (1 - k)
    return out

# ── Pattern detectors ─────────────────────────────────────────────────────────

def detect_cup_handle(closes, volumes):
    n = len(closes)
    if n < 60: return None
    best = None
    for s in range(max(0, n-325-40-15), n-60):
        for e in range(s+35, min(s+325, n-5)):
            seg  = closes[s:e+1]
            lp, rp = seg[0], seg[-1]
            if lp <= 0 or rp <= 0: continue
            rim_diff = abs(lp-rp) / max(lp,rp)
            if rim_diff > 0.05: continue
            inner = seg[int(len(seg)*.15):int(len(seg)*.85)]
            if not inner: continue
            cup_low = min(inner)
            pavg = (lp+rp)/2
            depth = (pavg-cup_low)/pavg
            if depth < 0.10 or depth > 0.50: continue
            low_idx = seg.index(min(seg))
            if low_idx < len(seg)*.20 or low_idx > len(seg)*.80: continue
            w52 = closes[max(0,e-252):e+1]
            if rp < max(w52)*0.90: continue
            for he in range(e+5, min(e+40, n)):
                bars_ago = n-1-he
                if bars_ago > 15: continue
                hs  = closes[e:he+1]
                hvs = volumes[e:he+1]
                if len(hs) < 5: continue
                hh, hl = max(hs), min(hs)
                hd = (rp-hl)/rp if rp else 1
                if hd < 0.05 or hd > 0.15: continue
                cup_mid = cup_low + (pavg-cup_low)*0.5
                if hl < cup_mid: continue
                if hh > rp*1.03: continue
                cv = volumes[s:e+1]
                avg_cv = sum(cv)/len(cv) if cv else 1
                avg_hv = sum(hvs)/len(hvs) if hvs else 1
                vol_dry = avg_hv < avg_cv*0.75
                pivot   = hh*1.005
                broke   = closes[-1] >= pivot
                status  = "BREAKOUT" if broke and bars_ago<=15 else ("HANDLE FORMING" if bars_ago<=5 else "RECENT")
                pen = abs(depth-0.28)*100 + rim_diff*200 + abs(hd-0.08)*100
                quality = max(0, min(100, 85-pen + (10 if vol_dry else 0) + (15 if broke else 0)))
                if best is None or quality > best["quality"]:
                    best = {
                        "pattern": "Cup & Handle",
                        "status": status, "quality": round(quality,1),
                        "cup_len_wks": round((e-s)/5,1),
                        "handle_len_wks": round(len(hs)/5,1),
                        "cup_depth_pct": round(depth*100,1),
                        "handle_depth_pct": round(hd*100,1),
                        "left_rim": round(lp,2), "right_rim": round(rp,2),
                        "cup_low": round(cup_low,2), "pivot": round(pivot,2),
                        "vol_dry_up": vol_dry, "broke_out": broke,
                    }
    return best


def detect_htf(closes, volumes):
    """High Tight Flag: 25%+ pole in ≤20 days, flag ≤15% deep."""
    n = len(closes)
    if n < 20: return None
    for i in range(max(0,n-80), n-10):
        pe, gain = i, 0.0
        for j in range(i+1, min(i+22,n)):
            if closes[i] == 0: break
            gain = (closes[j]-closes[i])/closes[i]
            if gain >= 0.25: pe = j; break
        if gain < 0.25: continue
        fs  = closes[pe:min(pe+18,n)]
        fvs = volumes[pe:min(pe+18,n)]
        if len(fs) < 4: continue
        fh, fl = max(fs), min(fs)
        fd = (fh-fl)/fh if fh else 1
        if fd > 0.15: continue
        bars_in_flag = n-1-pe
        if bars_in_flag > 25: continue
        pv = sum(volumes[i:pe+1])/max(1,pe-i+1)
        fv = sum(fvs)/max(1,len(fvs))
        vol_c = fv < pv*0.65
        pivot = fh*1.01
        broke = closes[-1] >= pivot
        status = "BREAKOUT" if broke else ("FLAG FORMING" if bars_in_flag<=18 else "RECENT")
        quality = min(100, 60 + gain*80 + (15 if vol_c else 0) + (15 if broke else 0) - fd*100)
        return {
            "pattern": "High Tight Flag",
            "status": status, "quality": round(max(0,quality),1),
            "pole_gain_pct": round(gain*100,1),
            "flag_depth_pct": round(fd*100,1),
            "pole_days": pe-i,
            "pivot": round(pivot,2),
            "vol_contraction": vol_c, "broke_out": broke,
            "cup_len_wks": None, "handle_len_wks": None,
            "cup_depth_pct": None, "handle_depth_pct": None,
            "left_rim": None, "right_rim": None, "cup_low": None,
            "vol_dry_up": vol_c,
        }
    return None


def detect_vcp(closes, volumes):
    """
    Volatility Contraction Pattern (Minervini):
    3+ contractions where each pullback is shallower and on lower volume.
    Price in Stage 2, near 52W high.
    """
    n = len(closes)
    if n < 60: return None

    # Rolling 10-day high-low range as volatility proxy
    ranges = []
    for i in range(10, n):
        seg = closes[i-10:i]
        r   = (max(seg)-min(seg))/max(seg) if max(seg) else 0
        ranges.append(r)

    if len(ranges) < 30: return None

    # Find contractions: each local range lower than previous
    contractions = 0
    depths = []
    for i in range(5, len(ranges)-5):
        local_max_before = max(ranges[max(0,i-10):i])
        local_max_after  = max(ranges[i:min(len(ranges),i+10)])
        current          = ranges[i]
        if current < local_max_before * 0.75:
            contractions += 1
            depths.append(round(current*100,1))

    if contractions < 3: return None

    # Must be near 52W high
    w52  = closes[max(0,n-252):]
    h52  = max(w52)
    dist = (closes[-1]-h52)/h52
    if dist < -0.20: return None  # must be within 20% of 52W high

    # Volume should be declining in recent 20 days
    recent_vol = sum(volumes[-10:])/10
    prior_vol  = sum(volumes[-30:-10])/20 if n > 30 else recent_vol
    vol_c = recent_vol < prior_vol*0.80

    # Pivot = recent 5-day high + 0.5%
    pivot = max(closes[-5:])*1.005
    broke = closes[-1] >= pivot

    quality = min(100, 45 + contractions*8 + (15 if vol_c else 0) + (10 if broke else 0))
    status  = "BREAKOUT" if broke else "CONTRACTING"

    return {
        "pattern": "VCP",
        "status": status, "quality": round(quality,1),
        "contractions": contractions,
        "vol_contraction": vol_c, "broke_out": broke,
        "pivot": round(pivot,2),
        "cup_len_wks": None, "handle_len_wks": None,
        "cup_depth_pct": None, "handle_depth_pct": None,
        "left_rim": None, "right_rim": None, "cup_low": None,
        "vol_dry_up": vol_c,
    }


# ── 50% probability score ─────────────────────────────────────────────────────

def probability_score(closes, volumes, cup=None, htf=None, vcp=None):
    n   = len(closes)
    cur = closes[-1]
    ma50  = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    m50   = ma50[n-1]
    m200  = ma200[n-1]
    w52   = closes[max(0,n-252):]
    h52   = max(w52)
    d52   = (cur-h52)/h52
    idx3m = max(0, n-63)
    m3    = (cur-closes[idx3m])/closes[idx3m] if closes[idx3m] else 0
    rv    = sum(volumes[-10:])/10
    av    = sum(volumes[-60:])/60 if n >= 60 else rv
    vr    = rv/av if av else 1
    rets  = [math.log(closes[i]/closes[i-1]) for i in range(max(1,n-60),n) if closes[i-1]>0]
    vol   = math.sqrt(sum((r-sum(rets)/len(rets))**2 for r in rets)/len(rets)*252) if rets else 0
    ma_sp = (m50-m200)/m200 if m50 and m200 else -0.05

    def factor(raw, lo, hi, w):
        return max(lo,min(hi,(raw-lo)/(hi-lo)))*w, w

    pts, mx = 0.0, 0.0
    for raw,lo,hi,w in [
        (d52,  -0.5, 0.0, 20),(ma_sp,-0.08,0.25,18),
        (m3,   -0.25,1.0, 18),(vr,   0.5,  3.0, 15),
        (vol,   0.3, 1.5, 10),
    ]:
        p,wt = factor(raw,lo,hi,w); pts+=p; mx+=wt

    bonus = 0
    if cup: bonus += 12
    if htf: bonus += 14
    if vcp: bonus += 10

    return min(96, max(3, round(pts/mx*75 + bonus)))


# ── Main per-stock analysis ───────────────────────────────────────────────────

def analyze(ticker):
    try:
        tk = yf.Ticker(ticker)
        df = tk.history(period=PERIOD, auto_adjust=True, actions=False)
        if df is None or df.empty or len(df) < 80: return None
        closes  = df["Close"].tolist()
        volumes = df["Volume"].tolist()
        n       = len(closes)
        cur     = closes[-1]
        if cur <= 0: return None

        ma50  = _sma(closes, 50)
        ma200 = _sma(closes, 200)
        m50   = ma50[n-1]
        m200  = ma200[n-1]
        if not m50 or not m200: return None

        w52  = closes[max(0,n-252):]
        h52  = max(w52)
        l52  = min(w52)
        idx3m= max(0,n-63)
        m3   = round((cur-closes[idx3m])/closes[idx3m]*100,1) if closes[idx3m] else 0
        rv   = sum(volumes[-10:])/10
        av   = sum(volumes[-60:])/60 if n>=60 else rv
        vr   = round(rv/av,2) if av else 1
        dist = round((cur-h52)/h52*100,1)
        ma_sp= round((m50-m200)/m200*100,1)

        cup  = detect_cup_handle(closes, volumes)
        htf  = detect_htf(closes, volumes)
        vcp  = detect_vcp(closes, volumes)
        prob = probability_score(closes, volumes, cup, htf, vcp)

        # Only include if at least one pattern found OR probability >= 55
        has_pattern = cup or htf or vcp
        if not has_pattern and prob < 55: return None

        # Stage 2 check (required for pattern validity)
        stage2 = cur > m50 and cur > m200 and m50 > m200

        # Fundamentals (best-effort, silently skip if unavailable)
        name=ticker; sector=""; mktcap=None; eps_g=None; pe=None; rev_g=None
        try:
            inf    = tk.info or {}
            name   = inf.get("longName") or inf.get("shortName") or ticker
            sector = inf.get("sector","")
            mktcap = inf.get("marketCap")
            eps_g  = inf.get("earningsQuarterlyGrowth")
            rev_g  = inf.get("revenueGrowth")
            pe     = inf.get("trailingPE")
        except Exception:
            pass

        return {
            "ticker":        ticker,
            "name":          name,
            "sector":        sector or "—",
            "price":         round(cur,2),
            "target50":      round(cur*1.5,2),
            "prob50":        prob,
            "dist52h":       dist,
            "ret3m":         m3,
            "volRatio":      vr,
            "stage2":        stage2,
            "above50ma":     cur > m50,
            "above200ma":    cur > m200,
            "maSpread":      ma_sp,
            "ma50":          round(m50,2),
            "ma200":         round(m200,2),
            "h52":           round(h52,2),
            "l52":           round(l52,2),
            "mktcap":        round(mktcap/1e9,1) if mktcap else None,
            "epsGrowth":     round(eps_g*100,1) if eps_g is not None else None,
            "revGrowth":     round(rev_g*100,1) if rev_g is not None else None,
            "pe":            round(pe,1) if pe else None,
            "cup":           cup,
            "htf":           htf,
            "vcp":           vcp,
        }
    except Exception:
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()

    print("=" * 56)
    print("  US Stock Pattern Scanner")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 56)

    tickers = fetch_universe()

    print(f"\n  Scanning {len(tickers)} stocks with {MAX_WORKERS} workers…\n")

    results = []
    done = 0
    errors = 0
    BAR = 44

    def redraw():
        pct  = done/len(tickers)
        fill = int(BAR*pct)
        bar  = "█"*fill + "░"*(BAR-fill)
        elapsed = time.time()-t0
        eta = elapsed/done*(len(tickers)-done) if done else 0
        m,s = divmod(int(eta),60)
        print(f"\r  [{bar}] {done}/{len(tickers)}  hits={len(results)}  ETA {m}m{s:02d}s",
              end="", flush=True)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(analyze, t): t for t in tickers}
        for fut in as_completed(futs):
            done += 1
            res = fut.result()
            if res is None: errors += 1
            else: results.append(res)
            if done % 10 == 0 or done == len(tickers): redraw()

    elapsed = time.time()-t0
    m,s = divmod(int(elapsed),60)
    print(f"\n\n  Done in {m}m{s:02d}s — {len(results)} stocks matched\n")

    # Sort by probability score
    results.sort(key=lambda x: (-x["prob50"], -(x["cup"]["quality"] if x["cup"] else 0)))

    # Write results
    results_path = os.path.join(OUT_DIR, "results.json")
    with open(results_path, "w") as f:
        json.dump(results, f, separators=(",",":"))
    print(f"  Saved {len(results)} results → {results_path}")

    # Write summary
    now = datetime.now(timezone.utc)
    summary = {
        "date":        now.strftime("%Y-%m-%d"),
        "time":        now.strftime("%H:%M UTC"),
        "timestamp":   now.isoformat(),
        "scanned":     len(tickers),
        "matched":     len(results),
        "errors":      errors,
        "elapsed_min": round(elapsed/60,1),
        "cup_handle":  sum(1 for r in results if r["cup"]),
        "htf":         sum(1 for r in results if r["htf"]),
        "vcp":         sum(1 for r in results if r["vcp"]),
        "prob55plus":  sum(1 for r in results if r["prob50"] >= 55),
        "prob70plus":  sum(1 for r in results if r["prob50"] >= 70),
    }
    with open(os.path.join(OUT_DIR,"summary.json"),"w") as f:
        json.dump(summary, f, indent=2)

    print(f"  Cup & Handle: {summary['cup_handle']}")
    print(f"  High Tight Flag: {summary['htf']}")
    print(f"  VCP: {summary['vcp']}")
    print(f"  Prob ≥55%: {summary['prob55plus']}")
    print(f"  Prob ≥70%: {summary['prob70plus']}\n")


if __name__ == "__main__":
    main()
