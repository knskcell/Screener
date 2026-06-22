# US Stock Pattern Scanner

A free, fully automated stock screener that runs daily and publishes results
to a public website. No server costs — runs entirely on GitHub's free tier.

## Live Website
`https://YOUR-GITHUB-USERNAME.github.io/stockscanner`

## What it does
- Scans NASDAQ + NYSE common stocks (~8,000) every weekday at 5:30 PM ET
- Detects: Cup & Handle, High Tight Flag, VCP (Volatility Contraction Pattern)
- Shows 50% run probability score + fundamentals for every match
- Publishes results to a filterable, sortable website automatically

---

## Setup (20 minutes, free forever)

### Step 1 — Create the GitHub repository

1. Go to **github.com** and sign in
2. Click the **+** button → **New repository**
3. Name it `stockscanner`
4. Set it to **Public** (required for free GitHub Pages)
5. Click **Create repository**

### Step 2 — Upload these files

Upload all files from this zip, keeping the folder structure:
```
stockscanner/
├── .github/
│   └── workflows/
│       └── daily-scan.yml     ← automation
├── scanner/
│   ├── scan.py                ← the scanner
│   └── requirements.txt
└── docs/
    ├── index.html             ← the website
    └── data/
        ├── results.json       ← populated by scanner
        └── summary.json       ← populated by scanner
```

**Easiest upload method:**
- On your new repo page, click **Add file → Upload files**
- Drag the entire unzipped folder in
- Click **Commit changes**

### Step 3 — Enable GitHub Pages

1. In your repo, click **Settings** (top menu)
2. Click **Pages** (left sidebar)
3. Under "Source", select **Deploy from a branch**
4. Branch: **main** | Folder: **/docs**
5. Click **Save**

Your site will be live at `https://YOUR-USERNAME.github.io/stockscanner` in ~2 minutes.

### Step 4 — Run the first scan

1. Click **Actions** tab in your repo
2. Click **Daily Stock Scan** in the left sidebar
3. Click **Run workflow** → **Run workflow**
4. Watch it run (takes ~2 hours for all US stocks, ~15 min for S&P 500 only)

After it finishes, refresh your website — it's live with real data.

### Step 5 — Automatic daily runs

The scanner runs automatically every weekday at 5:30 PM ET (10:30 PM UTC).
You don't need to do anything. GitHub Actions handles it.

---

## Customization

### Scan S&P 500 only (15 min instead of 2 hrs)
Edit `scanner/scan.py`, find the `fetch_universe()` function, and replace it with:
```python
def fetch_universe():
    tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    return tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
```

### Change scan time
Edit `.github/workflows/daily-scan.yml`, change the cron line:
```yaml
- cron: '0 22 * * 1-5'   # 22:00 UTC = 5:00 PM ET
```
Use https://crontab.guru to build the right time.

### Run locally
```bash
pip install yfinance pandas lxml
cd scanner
python scan.py
```
Results saved to `docs/data/`.

---

## GitHub Actions free tier limits
- 2,000 minutes/month on free accounts
- Each full scan (~8,000 stocks) takes ~120 minutes
- That's ~16 scans/month on the free tier (more than enough for daily weekday scans)

---

## Disclaimer
For educational and research purposes only. Not financial advice.
Past patterns do not guarantee future performance.
