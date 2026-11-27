"""NSE end-of-day data access.

Everything here is best-effort and DEFENSIVE. NSE actively blocks bare HTTP
clients and changes file formats, so each function:
  - primes cookies by hitting the NSE homepage with a browser-like session,
  - retries, and
  - raises DataUnavailable with a clear message (never a silent wrong number).

idea.md flags two realities we honour here:
  * NSE has been revising bhavcopy distribution — the parser keys off column
    NAMES, not positions, and tells you when a file is missing/malformed.
  * Datacenter IPs (like GitHub Actions runners) get blocked more than home IPs.
    If the Action can't reach NSE, run `scripts/run_scan.py` locally and commit.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import date, datetime, timedelta

import requests

NSE_HOME = "https://www.nseindia.com"
FO_BHAV_URL = (
    "https://nsearchives.nseindia.com/content/fo/"
    "BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
VIX_HIST_API = NSE_HOME + "/api/historical/vixhistory"
INDEX_HIST_API = NSE_HOME + "/api/historical/indicesHistory"

# Yahoo Finance chart API — keyless, datacenter-friendly (works from GitHub
# Actions runners, unlike NSE's /api/historical/* which anti-bots datacenter IPs).
# Primary source for VIX + NIFTY history; NSE is the fallback.
YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YF_VIX = "%5EINDIAVIX"   # ^INDIAVIX
YF_NIFTY = "%5ENSEI"     # ^NSEI

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


class DataUnavailable(RuntimeError):
    """Raised when NSE data cannot be fetched/parsed. Message is user-facing."""


def nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:
        s.get(NSE_HOME, timeout=20)  # prime cookies
        time.sleep(1.0)
    except requests.RequestException as e:
        raise DataUnavailable(f"Could not reach NSE homepage to establish a session: {e}")
    return s


def _get(session: requests.Session, url: str, *, params=None, retries: int = 3, timeout: int = 30):
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(1.5 * (attempt + 1))
    raise DataUnavailable(f"GET failed after {retries} tries ({url}): {last}")


def download_fo_bhavcopy(session: requests.Session, on: date) -> list[dict]:
    """Download + parse the F&O UDiFF bhavcopy for a trading date.

    Returns a list of row dicts (raw column names preserved). Raises
    DataUnavailable on a holiday/weekend/not-yet-published file.
    """
    url = FO_BHAV_URL.format(yyyymmdd=on.strftime("%Y%m%d"))
    r = _get(session, url)
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = zf.read(name).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, StopIteration) as e:
        raise DataUnavailable(
            f"Bhavcopy for {on} was not a valid zip/csv (holiday, not yet published, "
            f"or NSE changed the format): {e}")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise DataUnavailable(f"Bhavcopy for {on} parsed to zero rows.")
    return rows


def extract_nifty_puts(rows: list[dict], underlying: str = "NIFTY") -> dict:
    """Pull index-option PUT rows (back-compat wrapper over extract_options)."""
    o = extract_options(rows, underlying)
    return {"spot": o["spot"], "puts": o["puts"]}


def extract_options(rows: list[dict], underlying: str = "NIFTY") -> dict:
    """Pull index-option PUT and CALL rows for `underlying` (NIFTY/BANKNIFTY) out
    of a UDiFF bhavcopy. Returns {"spot", "puts":[...], "calls":[...]} where each
    leg is {expiry,strike,close,open_interest,volume}."""
    def col(row, *names):
        for n in names:
            if n in row and row[n] not in ("", None):
                return row[n]
        return None

    puts, calls, spot = [], [], None
    for row in rows:
        sym = (col(row, "TckrSymb", "SYMBOL") or "").strip().upper()
        if sym != underlying.upper():
            continue
        instr = (col(row, "FinInstrmTp", "INSTRUMENT") or "").upper()
        optt = (col(row, "OptnTp", "OPTION_TYP") or "").upper()
        is_option = ("OPT" in instr) or optt in ("PE", "CE")
        if not is_option or optt not in ("PE", "CE"):
            continue
        try:
            strike = float(col(row, "StrkPric", "STRIKE_PR"))
            close = float(col(row, "ClsPric", "CLOSE") or 0)
            oi = float(col(row, "OpnIntrst", "OPEN_INT") or 0)
            vol = float(col(row, "TtlTradgVol", "CONTRACTS") or 0)
        except (TypeError, ValueError):
            continue
        expiry = _parse_date(col(row, "XpryDt", "EXPIRY_DT"))
        if expiry is None:
            continue
        up = col(row, "UndrlygPric")
        if up:
            try:
                spot = float(up)
            except ValueError:
                pass
        leg = {"expiry": expiry, "strike": strike, "close": close,
               "open_interest": oi, "volume": vol}
        (puts if optt == "PE" else calls).append(leg)
    return {"spot": spot, "puts": puts, "calls": calls}


def _parse_date(s) -> date | None:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d-%b-%y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _range_for_days(days: int) -> str:
    if days > 200:
        return "1y"
    if days > 120:
        return "6mo"
    return "3mo"


def yahoo_history(session: requests.Session, symbol: str, rng: str) -> list[tuple[date, float]]:
    """Daily closes from Yahoo Finance's chart API. Keyless; works from
    datacenter IPs. Returns [(date, close), ...] sorted ascending."""
    url = YF_CHART.format(symbol=symbol)
    r = _get(session, url, params={"range": rng, "interval": "1d"})
    try:
        chart = r.json().get("chart", {})
        if chart.get("error"):
            raise DataUnavailable(f"Yahoo error for {symbol}: {chart['error']}")
        result = chart.get("result") or []
        if not result:
            raise DataUnavailable(f"Yahoo returned no result for {symbol}.")
        node = result[0]
        stamps = node.get("timestamp") or []
        closes = (node.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    except (ValueError, AttributeError, KeyError, IndexError) as e:
        raise DataUnavailable(f"Yahoo JSON parse failed for {symbol}: {e}")
    out = []
    for ts, c in zip(stamps, closes):
        if c is None:
            continue
        out.append((datetime.utcfromtimestamp(ts).date(), float(c)))
    out.sort(key=lambda t: t[0])
    if not out:
        raise DataUnavailable(f"Yahoo returned zero usable closes for {symbol}.")
    return out


def get_vix_history(session: requests.Session, days: int = 260) -> list[tuple[date, float]]:
    """India VIX daily closes. Yahoo first (datacenter-friendly), NSE fallback."""
    try:
        return yahoo_history(session, YF_VIX, _range_for_days(days))
    except DataUnavailable as yf_err:
        try:
            return _vix_history_nse(session, days)
        except DataUnavailable as nse_err:
            raise DataUnavailable(f"VIX unavailable — Yahoo: {yf_err} | NSE: {nse_err}")


def yahoo_ohlc(session: requests.Session, symbol: str, rng: str) -> list[dict]:
    """Daily OHLC bars from Yahoo's chart API (for Yang–Zhang vol). Keyless.
    Returns [{date, open, high, low, close}, ...] sorted ascending."""
    url = YF_CHART.format(symbol=symbol)
    r = _get(session, url, params={"range": rng, "interval": "1d"})
    try:
        chart = r.json().get("chart", {})
        if chart.get("error"):
            raise DataUnavailable(f"Yahoo error for {symbol}: {chart['error']}")
        node = (chart.get("result") or [{}])[0]
        stamps = node.get("timestamp") or []
        q = (node.get("indicators", {}).get("quote") or [{}])[0]
        o, h, l, c = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or []
    except (ValueError, AttributeError, KeyError, IndexError) as e:
        raise DataUnavailable(f"Yahoo OHLC parse failed for {symbol}: {e}")
    out = []
    for i, ts in enumerate(stamps):
        if i < len(c) and None not in (o[i] if i < len(o) else None, h[i] if i < len(h) else None,
                                       l[i] if i < len(l) else None, c[i]):
            out.append({"date": datetime.utcfromtimestamp(ts).date(),
                        "open": float(o[i]), "high": float(h[i]), "low": float(l[i]), "close": float(c[i])})
    out.sort(key=lambda b: b["date"])
    if not out:
        raise DataUnavailable(f"Yahoo returned zero OHLC bars for {symbol}.")
    return out


def get_nifty_history(session: requests.Session, days: int = 40) -> list[tuple[date, float]]:
    """NIFTY 50 daily closes. Yahoo first (datacenter-friendly), NSE fallback."""
    try:
        return yahoo_history(session, YF_NIFTY, _range_for_days(days))
    except DataUnavailable as yf_err:
        try:
            return _nifty_history_nse(session, days)
        except DataUnavailable as nse_err:
            raise DataUnavailable(f"NIFTY index unavailable — Yahoo: {yf_err} | NSE: {nse_err}")


def _vix_history_nse(session: requests.Session, days: int = 260) -> list[tuple[date, float]]:
    """India VIX closes for roughly `days` calendar days back. Best-effort."""
    to = date.today()
    frm = to - timedelta(days=int(days * 1.6))  # pad for weekends/holidays
    params = {"from": frm.strftime("%d-%m-%Y"), "to": to.strftime("%d-%m-%Y")}
    try:
        session.get(NSE_HOME + "/market-data/india-vix", timeout=15)
    except requests.RequestException:
        pass
    r = _get(session, VIX_HIST_API, params=params)
    try:
        data = r.json().get("data", [])
    except ValueError:
        raise DataUnavailable("India VIX history did not return JSON.")
    out = []
    for d in data:
        dt = _parse_date(d.get("EOD_TIMESTAMP") or d.get("TIMESTAMP") or d.get("timestamp"))
        close = d.get("EOD_CLOSE_INDEX_VAL") or d.get("CLOSE") or d.get("close")
        if dt is not None and close not in (None, ""):
            try:
                out.append((dt, float(close)))
            except ValueError:
                continue
    out.sort(key=lambda t: t[0])
    if not out:
        raise DataUnavailable("India VIX history parsed to zero rows.")
    return out


def _nifty_history_nse(session: requests.Session, days: int = 40) -> list[tuple[date, float]]:
    """NIFTY 50 index closes for realized-vol estimation. Best-effort."""
    to = date.today()
    frm = to - timedelta(days=int(days * 1.8))
    params = {"indexType": "NIFTY 50",
              "from": frm.strftime("%d-%m-%Y"), "to": to.strftime("%d-%m-%Y")}
    r = _get(session, INDEX_HIST_API, params=params)
    try:
        payload = r.json()
        data = payload.get("data", {}).get("indexCloseOnlineRecords", []) or payload.get("data", [])
    except (ValueError, AttributeError):
        raise DataUnavailable("NIFTY index history did not return expected JSON.")
    out = []
    for d in data:
        dt = _parse_date(d.get("EOD_TIMESTAMP") or d.get("TIMESTAMP") or d.get("mTIMESTAMP"))
        close = d.get("EOD_CLOSE_INDEX_VAL") or d.get("CLOSE") or d.get("close")
        if dt is not None and close not in (None, ""):
            try:
                out.append((dt, float(close)))
            except ValueError:
                continue
    out.sort(key=lambda t: t[0])
    if not out:
        raise DataUnavailable("NIFTY index history parsed to zero rows.")
    return out
