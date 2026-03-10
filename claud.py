"""
Earnings Backtest — Alpaca/Polygon Edition
Strategy: Enter on earnings report day (AMC) at ~1:30 PM ET (12:30 PM CST)
          using the 9:30–1:30 ET 4H candle direction.
          Exit: next trading day close.
Data Sources:
  - Alpaca: Free real-time OHLCV bars (recommended)
  - Polygon: Free tier has ~7 day delay on hourly bars
"""

import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta, date
import time

# ──────────────────────────────────────────────
# POLYGON API HELPERS
# ──────────────────────────────────────────────

BASE = "https://api.polygon.io"

def poly_get(endpoint, params, api_key):
    """Generic Polygon GET with clear error messages."""
    p = dict(params)
    p["apiKey"] = api_key
    url = BASE + endpoint
    for attempt in range(3):
        try:
            r = requests.get(url, params=p, timeout=15)

            if r.status_code == 429:
                time.sleep(12)
                continue

            if r.status_code in (401, 403):
                try:
                    body = r.json().get("message", r.text[:200])
                except Exception:
                    body = r.text[:200]
                code = r.status_code
                hint = ("Invalid API key" if code == 401 else
                        "Access denied — this endpoint may require a paid Polygon plan")
                raise Exception(
                    f"Polygon {code}: {hint}\n"
                    f"API response: {body}\n"
                    f"Get/check your key at https://polygon.io/dashboard"
                )

            if r.status_code == 404:
                raise Exception(f"Polygon 404: Ticker not found. URL: {url}")

            if not r.ok:
                try:
                    body = r.json().get("message", r.text[:200])
                except Exception:
                    body = r.text[:200]
                raise Exception(f"Polygon {r.status_code}: {body}")

            return r.json()

        except Exception as e:
            if attempt == 2:
                raise
            err = str(e)
            if any(x in err for x in ("401", "403", "404", "API key")):
                raise  # no retry for auth/permission errors
            time.sleep(3)
    return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_daily_bars(ticker, start_date, end_date, api_key):
    """Daily adjusted OHLCV from Polygon."""
    endpoint = f"/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
    data = poly_get(endpoint, {"adjusted": "true", "sort": "asc", "limit": 5000}, api_key)
    results = data.get("results", [])
    if not results:
        return pd.DataFrame()
    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["t"], unit="ms").dt.date
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def get_hourly_bars(ticker, start_date, end_date, api_key):
    """Hourly adjusted bars from Polygon."""
    endpoint = f"/v2/aggs/ticker/{ticker}/range/1/hour/{start_date}/{end_date}"
    data = poly_get(endpoint, {"adjusted": "true", "sort": "asc", "limit": 5000}, api_key)
    results = data.get("results", [])
    if not results:
        return pd.DataFrame()
    df = pd.DataFrame(results)
    # Convert ms timestamp → ET datetime
    df["dt_utc"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df["dt_et"] = df["dt_utc"].dt.tz_convert("America/New_York")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("dt_et")[["open", "high", "low", "close", "volume"]]
    return df


# ──────────────────────────────────────────────
# ALPACA API HELPERS
# ──────────────────────────────────────────────

ALPACA_DATA_BASE = "https://data.alpaca.markets"

def alpaca_get(endpoint, params, api_key, api_secret):
    """Generic Alpaca GET with authentication headers."""
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }
    url = ALPACA_DATA_BASE + endpoint
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)

            if r.status_code == 429:
                time.sleep(5)
                continue

            if r.status_code in (401, 403):
                try:
                    body = r.json().get("message", r.text[:200])
                except Exception:
                    body = r.text[:200]
                raise Exception(
                    f"Alpaca {r.status_code}: Invalid API credentials\n"
                    f"API response: {body}\n"
                    f"Get/check your key at https://app.alpaca.markets/paper/dashboard/overview"
                )

            if r.status_code == 404:
                raise Exception(f"Alpaca 404: Ticker not found. URL: {url}")

            if not r.ok:
                try:
                    body = r.json().get("message", r.text[:200])
                except Exception:
                    body = r.text[:200]
                raise Exception(f"Alpaca {r.status_code}: {body}")

            return r.json()

        except Exception as e:
            if attempt == 2:
                raise
            err = str(e)
            if any(x in err for x in ("401", "403", "404", "API")):
                raise  # no retry for auth/permission errors
            time.sleep(2)
    return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_daily_bars_alpaca(ticker, start_date, end_date, api_key, api_secret):
    """Daily adjusted OHLCV from Alpaca (IEX feed - free)."""
    endpoint = f"/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Day",
        "start": f"{start_date}T00:00:00Z",
        "end": f"{end_date}T23:59:59Z",
        "adjustment": "all",
        "limit": 10000,
        "sort": "asc",
        "feed": "iex",  # Free IEX feed (SIP requires paid subscription)
    }
    data = alpaca_get(endpoint, params, api_key, api_secret)
    bars = data.get("bars", [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"]).dt.date
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def get_hourly_bars_alpaca(ticker, start_date, end_date, api_key, api_secret):
    """Hourly adjusted bars from Alpaca IEX feed (real-time, no delay!)."""
    endpoint = f"/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Hour",
        "start": f"{start_date}T00:00:00Z",
        "end": f"{end_date}T23:59:59Z",
        "adjustment": "all",
        "limit": 10000,
        "sort": "asc",
        "feed": "iex",  # Free IEX feed (SIP requires paid subscription)
    }
    data = alpaca_get(endpoint, params, api_key, api_secret)
    bars = data.get("bars", [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    # Convert timestamp → ET datetime
    df["dt_utc"] = pd.to_datetime(df["t"], utc=True)
    df["dt_et"] = df["dt_utc"].dt.tz_convert("America/New_York")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("dt_et")[["open", "high", "low", "close", "volume"]]
    return df


@st.cache_data(ttl=300, show_spinner=False)  # 5 min cache for fresher options data
def get_options_bias_alpaca(ticker, api_key, api_secret):
    """
    Fetch options chain data from Alpaca and calculate bias metrics.
    Returns dict with put/call ratio, sentiment, unusual volume, and details.
    """
    debug_info = []
    
    try:
        # Alpaca options snapshot endpoint
        endpoint = f"/v1beta1/options/snapshots/{ticker}"
        params = {"limit": 250, "feed": "indicative"}  # indicative feed is free
        
        try:
            data = alpaca_get(endpoint, params, api_key, api_secret)
            snapshots = data.get("snapshots", {})
            debug_info.append(f"Options API: {len(snapshots)} contracts")
        except Exception as e:
            debug_info.append(f"Options API error: {str(e)[:80]}")
            return {"error": f"Options API error: {str(e)[:80]}", "debug": debug_info}
        
        if not snapshots:
            return {"error": "No options data returned", "debug": debug_info}
        
        calls = []
        puts = []
        all_with_volume = []
        
        for symbol, snap in snapshots.items():
            # Parse contract type from symbol (e.g., TSLA250321C00250000)
            # Format: UNDERLYING + YYMMDD + C/P + STRIKE (8 digits, strike * 1000)
            is_call = "C" in symbol.split(ticker)[-1][:7] if ticker in symbol else None
            
            greeks = snap.get("greeks", {})
            quote = snap.get("latestQuote", {})
            trade = snap.get("latestTrade", {})
            
            vol = trade.get("s", 0) or 0  # trade size as proxy for volume
            oi = snap.get("openInterest", 0) or 0
            
            # Try to extract strike from symbol
            try:
                after_ticker = symbol.replace(ticker, "")
                if len(after_ticker) >= 15:
                    strike = int(after_ticker[7:15]) / 1000
                    exp_str = after_ticker[:6]
                    exp = f"20{exp_str[:2]}-{exp_str[2:4]}-{exp_str[4:6]}"
                else:
                    strike = 0
                    exp = ""
            except:
                strike = 0
                exp = ""
            
            contract_data = {
                "symbol": symbol,
                "oi": oi,
                "volume": vol,
                "strike": strike,
                "expiry": exp,
                "bid": quote.get("bp", 0),
                "ask": quote.get("ap", 0),
                "last_price": trade.get("p", 0),
            }
            
            if is_call:
                calls.append(contract_data)
                contract_data["type"] = "CALL"
            elif is_call is False:
                puts.append(contract_data)
                contract_data["type"] = "PUT"
            
            if oi > 0 or vol > 0:
                vol_oi_ratio = vol / oi if oi > 0 else vol
                is_unusual = (vol > 1000) or (vol_oi_ratio > 2 and vol > 100) or (vol > oi and vol > 500)
                contract_data["vol_oi_ratio"] = round(vol_oi_ratio, 1)
                contract_data["is_unusual"] = is_unusual
                all_with_volume.append(contract_data)
        
        total_calls = len(calls)
        total_puts = len(puts)
        
        call_oi = sum(c.get("oi", 0) for c in calls)
        put_oi = sum(p.get("oi", 0) for p in puts)
        call_volume = sum(c.get("volume", 0) for c in calls)
        put_volume = sum(p.get("volume", 0) for p in puts)
        
        debug_info.append(f"Calls: {total_calls}, Puts: {total_puts}")
        debug_info.append(f"Call OI: {call_oi}, Put OI: {put_oi}")
        
        # Sort by OI descending (volume often sparse in options)
        all_with_volume.sort(key=lambda x: x.get("oi", 0), reverse=True)
        top_volume = all_with_volume[:15]
        unusual_activity = [x for x in top_volume if x.get("is_unusual")]
        
        total_oi = call_oi + put_oi
        total_volume = call_volume + put_volume
        
        # Ratios
        pc_ratio = total_puts / total_calls if total_calls > 0 else 0
        oi_pc_ratio = put_oi / call_oi if call_oi > 0 else 0
        vol_pc_ratio = put_volume / call_volume if call_volume > 0 else 0
        
        # Sentiment
        if oi_pc_ratio < 0.7:
            sentiment = "BULLISH"
            sentiment_color = "#00e5a0"
            sentiment_desc = "Call-heavy OI indicates bullish sentiment"
        elif oi_pc_ratio <= 1.0:
            sentiment = "NEUTRAL"
            sentiment_color = "#f5c842"
            sentiment_desc = "Balanced put/call ratio"
        else:
            sentiment = "BEARISH"
            sentiment_color = "#ff4d6a"
            sentiment_desc = "Put-heavy OI indicates bearish sentiment"
        
        if total_volume > 0:
            if vol_pc_ratio < 0.7:
                vol_sentiment = "BULLISH"
                vol_color = "#00e5a0"
            elif vol_pc_ratio <= 1.0:
                vol_sentiment = "NEUTRAL"
                vol_color = "#f5c842"
            else:
                vol_sentiment = "BEARISH"
                vol_color = "#ff4d6a"
        else:
            vol_sentiment = "N/A"
            vol_color = "#6b7099"
        
        return {
            "total_calls": total_calls,
            "total_puts": total_puts,
            "pc_ratio": round(pc_ratio, 2),
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_oi": total_oi,
            "oi_pc_ratio": round(oi_pc_ratio, 2),
            "call_volume": call_volume,
            "put_volume": put_volume,
            "total_volume": total_volume,
            "vol_pc_ratio": round(vol_pc_ratio, 2),
            "sentiment": sentiment,
            "sentiment_color": sentiment_color,
            "sentiment_desc": sentiment_desc,
            "vol_sentiment": vol_sentiment,
            "vol_color": vol_color,
            "unusual_activity": unusual_activity,
            "top_volume": top_volume,
            "debug": debug_info,
        }
    except Exception as e:
        return {"error": str(e)[:100], "debug": debug_info if 'debug_info' in dir() else []}


@st.cache_data(ttl=300, show_spinner=False)  # 5 min cache for fresher options data
def get_options_bias(ticker, api_key):
    """
    Fetch options chain data from Polygon and calculate bias metrics.
    Returns dict with put/call ratio, sentiment, unusual volume, and details.
    """
    debug_info = []  # Track API responses for diagnostics
    
    try:
        # Get options snapshot for real-time volume data
        snapshot_endpoint = f"/v3/snapshot/options/{ticker}"
        snapshot_params = {"limit": 250}
        snapshot_results = []
        snapshot_error = None
        
        try:
            snapshot_data = poly_get(snapshot_endpoint, snapshot_params, api_key)
            snapshot_results = snapshot_data.get("results", [])
            debug_info.append(f"Snapshot API: {len(snapshot_results)} contracts")
        except Exception as e:
            snapshot_error = str(e)[:80]
            debug_info.append(f"Snapshot API error: {snapshot_error}")
        
        # Also get contracts list as fallback
        endpoint = f"/v3/reference/options/contracts"
        params = {
            "underlying_ticker": ticker,
            "expired": "false",
            "limit": 1000,
        }
        data = poly_get(endpoint, params, api_key)
        results = data.get("results", [])
        debug_info.append(f"Contracts API: {len(results)} contracts")
        
        if not results and not snapshot_results:
            return {"error": "No options data from either API", "debug": debug_info}
        
        # Use snapshot data if available (has volume), otherwise use contracts
        if snapshot_results:
            calls = [r for r in snapshot_results if r.get("details", {}).get("contract_type") == "call"]
            puts = [r for r in snapshot_results if r.get("details", {}).get("contract_type") == "put"]
            
            # Extract volume and OI from snapshot
            call_volume = sum(r.get("day", {}).get("volume", 0) for r in calls)
            put_volume = sum(r.get("day", {}).get("volume", 0) for r in puts)
            call_oi = sum(r.get("open_interest", 0) for r in calls)
            put_oi = sum(r.get("open_interest", 0) for r in puts)
            
            total_calls = len(calls)
            total_puts = len(puts)
            
            debug_info.append(f"Call vol: {call_volume}, Put vol: {put_volume}")
            
            # Find ALL contracts with volume, sorted by volume
            # Then flag the top ones as "unusual" or "high volume"
            all_with_volume = []
            for r in snapshot_results:
                details = r.get("details", {})
                day = r.get("day", {})
                vol = day.get("volume", 0)
                oi = r.get("open_interest", 0) or 1
                strike = details.get("strike_price", 0)
                exp = details.get("expiration_date", "")
                ctype = details.get("contract_type", "")
                
                if vol > 0:  # Any volume
                    vol_oi_ratio = vol / oi if oi > 0 else vol
                    
                    # Flag as unusual if high vol/OI ratio or high absolute volume
                    is_unusual = (vol > 1000) or (vol_oi_ratio > 2 and vol > 100) or (vol > oi and vol > 500)
                    
                    all_with_volume.append({
                        "strike": strike,
                        "expiry": exp,
                        "type": ctype.upper() if ctype else "?",
                        "volume": vol,
                        "oi": oi,
                        "vol_oi_ratio": round(vol_oi_ratio, 1),
                        "last_price": day.get("close", day.get("last", {}).get("price", 0)),
                        "is_unusual": is_unusual,
                    })
            
            # Sort by volume descending
            all_with_volume.sort(key=lambda x: x["volume"], reverse=True)
            
            # Top 15 by volume (mark unusual ones)
            top_volume = all_with_volume[:15]
            unusual_activity = [x for x in top_volume if x.get("is_unusual")]
            
            debug_info.append(f"Contracts with volume: {len(all_with_volume)}, unusual: {len(unusual_activity)}")
            
        else:
            # Fallback to contracts list (no volume data)
            calls = [r for r in results if r.get("contract_type") == "call"]
            puts = [r for r in results if r.get("contract_type") == "put"]
            
            total_calls = len(calls)
            total_puts = len(puts)
            
            call_oi = sum(r.get("open_interest", 0) for r in calls)
            put_oi = sum(r.get("open_interest", 0) for r in puts)
            call_volume = 0
            put_volume = 0
            unusual_activity = []
            top_volume = []
            debug_info.append("Using contracts API (no volume data available)")
        
        total_oi = call_oi + put_oi
        total_volume = call_volume + put_volume
        
        # Put/Call ratio by contracts
        pc_ratio = total_puts / total_calls if total_calls > 0 else 0
        
        # Put/Call ratio by OI
        oi_pc_ratio = put_oi / call_oi if call_oi > 0 else 0
        
        # Put/Call ratio by volume
        vol_pc_ratio = put_volume / call_volume if call_volume > 0 else 0
        
        # Determine sentiment based on put/call ratio
        if oi_pc_ratio < 0.7:
            sentiment = "BULLISH"
            sentiment_color = "#00e5a0"
            sentiment_desc = "Call-heavy flow indicates bullish sentiment"
        elif oi_pc_ratio <= 1.0:
            sentiment = "NEUTRAL"
            sentiment_color = "#f5c842"
            sentiment_desc = "Balanced put/call ratio"
        else:
            sentiment = "BEARISH"
            sentiment_color = "#ff4d6a"
            sentiment_desc = "Put-heavy flow indicates bearish sentiment"
        
        # Volume sentiment (today's flow)
        if total_volume > 0:
            if vol_pc_ratio < 0.7:
                vol_sentiment = "BULLISH"
                vol_color = "#00e5a0"
            elif vol_pc_ratio <= 1.0:
                vol_sentiment = "NEUTRAL"
                vol_color = "#f5c842"
            else:
                vol_sentiment = "BEARISH"
                vol_color = "#ff4d6a"
        else:
            vol_sentiment = "N/A"
            vol_color = "#6b7099"
        
        return {
            "total_calls": total_calls,
            "total_puts": total_puts,
            "pc_ratio": round(pc_ratio, 2),
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_oi": total_oi,
            "oi_pc_ratio": round(oi_pc_ratio, 2),
            "call_volume": call_volume,
            "put_volume": put_volume,
            "total_volume": total_volume,
            "vol_pc_ratio": round(vol_pc_ratio, 2),
            "sentiment": sentiment,
            "sentiment_color": sentiment_color,
            "sentiment_desc": sentiment_desc,
            "vol_sentiment": vol_sentiment,
            "vol_color": vol_color,
            "unusual_activity": unusual_activity,
            "top_volume": top_volume if 'top_volume' in dir() else [],
            "debug": debug_info,
        }
    except Exception as e:
        return {"error": str(e)[:100], "debug": debug_info if 'debug_info' in dir() else []}


@st.cache_data(ttl=3600, show_spinner=False)
def get_earnings_dates_polygon(ticker, api_key, limit=20):
    """
    Try Polygon vX financials endpoint (requires paid plan).
    Returns sorted list of (report_date, quarter_label, period) or [].
    """
    # Try both the given ticker and common aliases
    aliases = [ticker]
    if ticker == "GOOG":   aliases.append("GOOGL")
    if ticker == "GOOGL":  aliases.append("GOOG")
    if ticker == "BRK.B":  aliases.append("BRK/B")

    for t in aliases:
        try:
            endpoint = "/vX/reference/financials"
            params = {
                "ticker": t,
                "timeframe": "quarterly",
                "sort": "period_of_report_date",
                "order": "desc",
                "limit": limit,
            }
            data = poly_get(endpoint, params, api_key)
            results = data.get("results", [])
            if not results:
                continue
            events = []
            for r in results:
                filing = r.get("filing_date")
                period = r.get("period_of_report_date")
                fy     = r.get("fiscal_year", "")
                fq     = r.get("fiscal_period", "")
                label  = f"{fq} {fy}".strip() if fy else (period or "")
                if filing:
                    events.append((filing, label, period or filing))
            if events:
                events.sort(key=lambda x: x[0])
                return events
        except Exception:
            continue
    return []


@st.cache_data(ttl=3600, show_spinner=False)
def detect_earnings_from_prices(daily_df, min_gap_pct=3.0, min_vol_ratio=1.5):
    """
    Auto-detect likely earnings dates from daily price data.
    Looks for overnight gaps ≥ min_gap_pct% AND volume ≥ min_vol_ratio × 20d avg.
    Returns sorted list of (date_str, quarter_label, period).
    """
    if daily_df.empty or len(daily_df) < 25:
        return []

    df = daily_df.copy().reset_index()
    df = df.sort_values("date").reset_index(drop=True)

    events = []
    for i in range(1, len(df)):
        prev_close = df.loc[i-1, "close"]
        cur_open   = df.loc[i, "open"]
        cur_vol    = df.loc[i, "volume"]
        cur_date   = df.loc[i, "date"]

        # Overnight gap
        gap_pct = abs(cur_open - prev_close) / prev_close * 100
        if gap_pct < min_gap_pct:
            continue

        # Volume spike vs 20d avg
        start_idx = max(0, i - 21)
        avg_vol   = df.loc[start_idx:i-1, "volume"].mean()
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0
        if vol_ratio < min_vol_ratio:
            continue

        # Space events at least 45 days apart (quarterly)
        if events and (cur_date - datetime.strptime(events[-1][0], "%Y-%m-%d").date()).days < 45:
            # Keep the larger gap
            prev_gap = abs(
                df[df["date"] == datetime.strptime(events[-1][0], "%Y-%m-%d").date()]["open"].values[0]
                - df[df["date"] == datetime.strptime(events[-1][0], "%Y-%m-%d").date()].index[0]
            ) if events else 0
            continue

        # Label by approximate quarter
        yr  = cur_date.year
        mo  = cur_date.month
        if mo <= 3:   qtr = f"Q4 {yr-1}"
        elif mo <= 6: qtr = f"Q1 {yr}"
        elif mo <= 9: qtr = f"Q2 {yr}"
        else:         qtr = f"Q3 {yr}"

        # The ENTRY is cur_date (reaction day in old strategy).
        # For our strategy (enter on report day = day BEFORE the gap),
        # the report date is the previous trading day.
        report_date = str(df.loc[i-1, "date"])
        events.append((report_date, qtr, report_date))

    return events


def get_earnings_dates(ticker, api_key, limit=20, daily_df=None, manual_dates=None):
    """
    Multi-source earnings date resolver — priority order:
    1. Manual dates pasted by user
    2. Polygon vX financials (paid)
    3. Auto-detect from price/volume gaps (free)
    """
    # 1. Manual override
    if manual_dates:
        events = []
        for i, d in enumerate(manual_dates):
            try:
                dt  = datetime.strptime(d.strip(), "%Y-%m-%d").date()
                yr  = dt.year
                mo  = dt.month
                if mo <= 3:   qtr = f"Q4 {yr-1}"
                elif mo <= 6: qtr = f"Q1 {yr}"
                elif mo <= 9: qtr = f"Q2 {yr}"
                else:         qtr = f"Q3 {yr}"
                events.append((str(dt), qtr, str(dt)))
            except Exception:
                continue
        if events:
            events.sort(key=lambda x: x[0])
            return events, "manual"

    # 2. Polygon financials
    poly_events = get_earnings_dates_polygon(ticker, api_key, limit)
    if poly_events:
        return poly_events, "polygon"

    # 3. Auto-detect from price gaps
    if daily_df is not None and not daily_df.empty:
        auto_events = detect_earnings_from_prices(daily_df)
        if auto_events:
            return auto_events, "auto-detected"

    return [], "none"


def estimate_next_earnings(events):
    """Estimate next upcoming earnings date from cadence of past events."""
    if len(events) < 2:
        return None
    try:
        dates   = [datetime.strptime(e[0], "%Y-%m-%d").date() for e in events]
        gaps    = [(dates[i+1]-dates[i]).days for i in range(len(dates)-1)]
        avg_gap = int(np.mean(gaps[-4:]))  # use last 4 gaps
        nxt     = dates[-1] + timedelta(days=avg_gap)
        if nxt > date.today():
            return str(nxt)
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# 4H CANDLE
# ──────────────────────────────────────────────

def get_4h_noon_candle(ticker, report_date, hourly_df):
    """
    Build the 9:30 AM – 1:30 PM ET 4H candle for a given date.
    Returns dict with open, close, high, low or None.
    """
    if hourly_df is None or hourly_df.empty:
        return None
    try:
        # Convert report_date to date object if it's not already
        if isinstance(report_date, str):
            report_date = datetime.strptime(report_date, "%Y-%m-%d").date()
        elif hasattr(report_date, 'date'):
            report_date = report_date.date() if callable(getattr(report_date, 'date')) else report_date
        
        # Filter for the specific date
        day_bars = hourly_df[hourly_df.index.date == report_date]
        
        if day_bars.empty:
            return None
            
        # Keep hours 9-13 ET (9:30–1:30 window)
        window = day_bars[
            (day_bars.index.hour >= 9) & (day_bars.index.hour < 14)
        ]
        # More precisely: exclude before 9:30 and after 13:30
        window = window[
            ~((window.index.hour == 9) & (window.index.minute < 30)) &
            ~((window.index.hour == 13) & (window.index.minute >= 30))
        ]
        if window.empty:
            return None
        return {
            "open":  float(window.iloc[0]["open"]),
            "close": float(window.iloc[-1]["close"]),
            "high":  float(window["high"].max()),
            "low":   float(window["low"].min()),
            "bars":  len(window),
        }
    except Exception as e:
        return None


# ──────────────────────────────────────────────
# FIBONACCI
# ──────────────────────────────────────────────

FIB_RET = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_EXT = [1.272, 1.414, 1.618, 2.0, 2.618]

def calc_fib_levels(lo, hi):
    rng = hi - lo
    lvls = {}
    for f in FIB_RET:
        lvls[f"R {f*100:.1f}%"] = hi - rng * f
    for f in FIB_EXT:
        lvls[f"E {f*100:.1f}%"] = lo + rng * f
    return lvls

def nearest_fib(price, lo, hi, tol_pct):
    if not lo or not hi or hi <= lo or price <= 0:
        return None
    lvls = calc_fib_levels(lo, hi)
    best_name, best_price, best_dist = None, None, float("inf")
    for name, lvl in lvls.items():
        dist = abs(price - lvl) / lvl * 100
        if dist < best_dist:
            best_dist = dist
            best_name = name
            best_price = lvl
    return (best_name, best_price, round(best_dist, 2)) if best_dist <= tol_pct else None


# ──────────────────────────────────────────────
# BACKTEST CORE
# ──────────────────────────────────────────────

def next_trading_day(d, daily_index):
    """Return next date in daily_index after d."""
    d = pd.Timestamp(d).date() if not isinstance(d, date) else d
    for idx_date in sorted(daily_index):
        if idx_date > d:
            return idx_date
    return None

def run_backtest(ticker, daily_df, hourly_df, earnings_events,
                 vol_threshold, use_vol, fib_tol, use_fib, use_4h, fib_tf="Weekly"):
    trades = []
    skipped_reasons = []  # Track why events were skipped
    daily_dates = list(daily_df.index)

    # Build weekly OHLCV once for weekly fib swing option
    # daily_df index is plain date objects — must convert to DatetimeIndex for resample
    try:
        _dfw = daily_df.copy()
        _dfw.index = pd.to_datetime(_dfw.index)
        weekly_df = _dfw.resample("W").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna()
        weekly_df.index = weekly_df.index.date  # back to date for consistent comparisons
    except Exception:
        weekly_df = pd.DataFrame()

    for idx, (report_date_str, label, period) in enumerate(earnings_events):
        try:
            report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()

            # ── Entry day = report date ──
            entry_date = report_date
            if entry_date not in daily_df.index:
                skipped_reasons.append((report_date_str, "entry_date not in price data"))
                continue

            # ── Exit day = next trading day ──
            exit_date = next_trading_day(entry_date, daily_dates)
            if exit_date is None or exit_date not in daily_df.index:
                skipped_reasons.append((report_date_str, "no valid exit date (next trading day)"))
                continue

            # ── 4H candle signal ──
            if use_4h and not hourly_df.empty:
                candle = get_4h_noon_candle(ticker, entry_date, hourly_df)
            else:
                candle = None

            if candle:
                signal_open  = candle["open"]
                signal_close = candle["close"]
                candle_type  = "4H"
            else:
                # fallback: daily open vs close
                row          = daily_df.loc[entry_date]
                signal_open  = row["open"]
                signal_close = row["close"]
                candle_type  = "daily"

            direction  = "LONG" if signal_close > signal_open else "SHORT"
            entry_px   = signal_close
            exit_px    = float(daily_df.loc[exit_date, "close"])
            day_move   = (float(daily_df.loc[entry_date, "close"]) - signal_open) / signal_open * 100

            pnl_pct = (
                (exit_px - entry_px) / entry_px * 100
                if direction == "LONG"
                else (entry_px - exit_px) / entry_px * 100
            )

            # ── Volume filter ──
            exit_vol     = float(daily_df.loc[exit_date, "volume"])
            recent_dates = [d for d in daily_dates if d < entry_date][-20:]
            avg_vol_20   = float(daily_df.loc[recent_dates, "volume"].mean()) if len(recent_dates) >= 5 else None
            vol_ratio    = exit_vol / avg_vol_20 if avg_vol_20 else None
            passes_vol   = (vol_ratio >= vol_threshold) if (use_vol and vol_ratio is not None) else (not use_vol)

            # ── Fibonacci ──
            if idx > 0:
                prev_exit_str = earnings_events[idx - 1][0]
                prev_exit_d   = next_trading_day(
                    datetime.strptime(prev_exit_str, "%Y-%m-%d").date(), daily_dates
                )
                swing_dates = [d for d in daily_dates if prev_exit_d and prev_exit_d <= d < report_date]
            else:
                # first event: use 52-week window
                one_yr_ago  = report_date - timedelta(days=365)
                swing_dates = [d for d in daily_dates if one_yr_ago <= d < report_date]

            if swing_dates and use_fib:
                if fib_tf == "Weekly" and not weekly_df.empty:
                    # Filter weekly bars whose week-end date falls in swing window
                    # Note: weekly_df.index is already date objects after resample
                    swing_start = swing_dates[0]
                    swing_end   = swing_dates[-1]
                    w_idx = list(weekly_df.index)
                    w_mask = [(d >= swing_start and d <= swing_end) for d in w_idx]
                    w_slice = weekly_df[w_mask]
                    if not w_slice.empty:
                        swing_hi = float(w_slice["high"].max())
                        swing_lo = float(w_slice["low"].min())
                    else:
                        # fallback to daily if no weekly bars in window
                        swing_hi = float(daily_df.loc[swing_dates, "high"].max())
                        swing_lo = float(daily_df.loc[swing_dates, "low"].min())
                else:
                    swing_hi = float(daily_df.loc[swing_dates, "high"].max())
                    swing_lo = float(daily_df.loc[swing_dates, "low"].min())
                fib_hit = nearest_fib(entry_px, swing_lo, swing_hi, fib_tol)
            else:
                swing_hi, swing_lo, fib_hit = None, None, None

            passes_fib = fib_hit is not None if use_fib else True
            passes_all = passes_vol and passes_fib

            trades.append({
                "q":           label,
                "report_date": str(report_date),
                "entry_date":  str(entry_date),
                "exit_date":   str(exit_date),
                "direction":   direction,
                "candle_type": candle_type,
                "signal_open": round(signal_open, 2),
                "entry":       round(entry_px, 2),
                "exit":        round(exit_px, 2),
                "pnl_pct":     round(pnl_pct, 2),
                "win":         pnl_pct > 0,
                "day_move":    round(day_move, 2),
                "vol_ratio":   round(vol_ratio, 2) if vol_ratio else None,
                "passes_vol":  passes_vol,
                "fib_hit":     fib_hit,
                "passes_fib":  passes_fib,
                "passes_all":  passes_all,
                "swing_lo":    round(swing_lo, 2) if swing_lo else None,
                "swing_hi":    round(swing_hi, 2) if swing_hi else None,
            })

        except Exception as e:
            skipped_reasons.append((report_date_str, f"error: {str(e)[:50]}"))
            continue

    result_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    result_df.attrs["skipped_reasons"] = skipped_reasons
    return result_df


# ──────────────────────────────────────────────
# STATS
# ──────────────────────────────────────────────

def calc_stats(active_df):
    if active_df.empty:
        return {
            "total_return": 0.0,
            "win_rate": 0.0,
            "wins": 0,
            "losses": 0,
            "n": 0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": None,
            "max_dd": 0.0,
            "avg_trade": 0.0,
            "equity": [100.0],
            "final_eq": 100.0,
        }
    pnls  = active_df["pnl_pct"].values
    mult  = np.prod(1 + pnls / 100)
    eq    = [100.0]
    peak  = 100.0
    maxdd = 0.0
    cur   = 100.0
    for p in pnls:
        cur *= 1 + p / 100
        if cur > peak: peak = cur
        dd = (peak - cur) / peak * 100
        if dd > maxdd: maxdd = dd
        eq.append(round(cur, 2))

    wins   = active_df[active_df["win"]]
    losses = active_df[~active_df["win"]]
    avg_w  = wins["pnl_pct"].mean() if len(wins) else 0
    avg_l  = losses["pnl_pct"].mean() if len(losses) else 0
    pf     = abs(len(wins) * avg_w / (len(losses) * avg_l)) if len(losses) and avg_l else float("inf")

    return {
        "total_return": round((mult - 1) * 100, 2),
        "win_rate":     round(len(wins) / len(active_df) * 100, 1),
        "wins":         len(wins),
        "losses":       len(losses),
        "n":            len(active_df),
        "avg_win":      round(avg_w, 2),
        "avg_loss":     round(avg_l, 2),
        "profit_factor":round(pf, 2) if pf != float("inf") else None,
        "max_dd":       round(maxdd, 2),
        "avg_trade":    round(pnls.mean(), 2),
        "equity":       eq,
        "final_eq":     round(cur, 2),
    }


# ──────────────────────────────────────────────
# CHARTS
# ──────────────────────────────────────────────

DARK = dict(
    paper_bgcolor="#07080d", plot_bgcolor="#07080d",
    font_color="#c8cce8", font_family="Courier New",
)
GREEN, RED, BLUE, PURPLE, YELLOW, CYAN = "#00e5a0","#ff4d6a","#4d9fff","#a78bfa","#f5c842","#22d3ee"


def equity_chart(active_df, stats):
    labels = ["START"] + list(active_df["q"])
    equity = stats["equity"]
    colors = ["#484f58"] + [GREEN if w else RED for w in active_df["win"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=equity, mode="lines+markers",
        line=dict(color=GREEN if stats["total_return"] >= 0 else RED, width=2.5),
        marker=dict(color=colors, size=8, line=dict(color="#07080d", width=2)),
        fill="tozeroy", fillcolor="rgba(0,229,160,0.06)",
        hovertemplate="<b>%{x}</b><br>$%{y:.2f}<extra></extra>",
    ))
    fig.add_hline(y=100, line_dash="dot", line_color="#252840")
    fig.update_layout(
        **DARK, height=300, margin=dict(l=50, r=10, t=10, b=50),
        xaxis=dict(tickangle=-40, gridcolor="#1a1d2e", showline=False),
        yaxis=dict(tickprefix="$", gridcolor="#1a1d2e", showline=False),
        showlegend=False,
    )
    return fig


def pnl_bar_chart(all_df):
    colors = []
    for _, row in all_df.iterrows():
        if not row.get("passes_all", True):
            colors.append("rgba(58,61,92,0.4)")
        elif row["win"]:
            colors.append(f"rgba(0,229,160,0.8)")
        else:
            colors.append(f"rgba(255,77,106,0.8)")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=all_df["q"], y=all_df["pnl_pct"],
        marker_color=colors,
        hovertemplate="<b>%{x}</b><br>P&L: %{y:.2f}%<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="#252840")
    fig.update_layout(
        **DARK, height=300, margin=dict(l=50, r=10, t=10, b=50),
        xaxis=dict(tickangle=-40, gridcolor="#1a1d2e"),
        yaxis=dict(ticksuffix="%", gridcolor="#1a1d2e"),
        showlegend=False,
    )
    return fig


def fib_freq_chart(all_df):
    fib_hits = all_df[all_df["fib_hit"].notna()].copy()
    if fib_hits.empty:
        return None
    counts = {}
    for _, row in fib_hits.iterrows():
        name = row["fib_hit"][0]
        counts[name] = counts.get(name, {"count": 0, "wins": 0})
        counts[name]["count"] += 1
        if row["win"]: counts[name]["wins"] += 1
    rows       = sorted(counts.items(), key=lambda x: -x[1]["count"])
    # Display name: "Ret 61.8%" or "Ext 127.2%"
    disp_names = [("Ext " if r[0][0]=="E" else "Ret ") + r[0][1:] for r in rows]
    cnts       = [r[1]["count"] for r in rows]
    bar_colors = [YELLOW if r[0][0]=="E" else BLUE for r in rows]
    hover_text = [
        f'{"Extension" if r[0][0]=="E" else "Retracement"} {r[0][1:]}<br>'
        f'Hits: {r[1]["count"]}<br>Win: {r[1]["wins"]} / Loss: {r[1]["count"]-r[1]["wins"]}'
        for r in rows
    ]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=cnts, y=disp_names, orientation="h",
        marker_color=bar_colors,
        text=[f'{"EXT" if r[0][0]=="E" else "RET"}' for r in rows],
        textposition="inside",
        textfont=dict(size=9, color="#07080d"),
        hovertext=hover_text,
        hoverinfo="text",
    ))
    fig.update_layout(
        **DARK, height=max(200, len(rows)*28+60),
        margin=dict(l=10, r=10, t=10, b=30),
        xaxis=dict(title="Hits", gridcolor="#1a1d2e"),
        yaxis=dict(gridcolor="#1a1d2e"),
        showlegend=False,
    )
    return fig


# ──────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────

st.set_page_config(
    page_title="Earnings Backtest — Polygon",
    page_icon="▲",
    layout="wide",
)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap');
  html, body, [class*="css"] { font-family: 'Space Mono', monospace !important; }
  .block-container { padding: 1rem 1.5rem 2rem; }
  div[data-testid="metric-container"] {
    background: #0d0f17; border: 1px solid #1a1d2e;
    padding: 12px 16px; border-radius: 4px;
  }
  div[data-testid="metric-container"] label { font-size: 9px !important; letter-spacing: 1.5px; color: #3a3d5c !important; }
  div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 22px !important; font-weight: 900 !important; }
  .stDataFrame { border: 1px solid #1a1d2e; }
  .pill { display:inline-block; padding:2px 10px; border-radius:3px; font-size:10px; font-weight:700; letter-spacing:1px; }
  .pill-green { background:rgba(0,229,160,.12); color:#00e5a0; border:1px solid rgba(0,229,160,.3); }
  .pill-red   { background:rgba(255,77,106,.12); color:#ff4d6a; border:1px solid rgba(255,77,106,.3); }
  .pill-yellow{ background:rgba(245,200,66,.12); color:#f5c842; border:1px solid rgba(245,200,66,.3); }
  .pill-blue  { background:rgba(77,159,255,.12); color:#4d9fff; border:1px solid rgba(77,159,255,.3); }
</style>
""", unsafe_allow_html=True)

# ── Header ──────────────────────────────────────────
col_logo, col_title = st.columns([1, 10])
with col_logo:
    st.markdown(
        '<div style="width:36px;height:36px;border-radius:6px;background:rgba(0,229,160,.15);'
        'border:1px solid rgba(0,229,160,.3);display:flex;align-items:center;justify-content:center;'
        'font-size:18px;margin-top:8px">▲</div>',
        unsafe_allow_html=True,
    )
with col_title:
    st.markdown(
        '<h2 style="margin:0;letter-spacing:1px;color:#e8ecff">PRE-EARNINGS 4H BACKTEST</h2>'
        '<p style="margin:0;font-size:9px;color:#3a3d5c;letter-spacing:2px">'
        'REAL MARKET DATA · ALPACA OR POLYGON</p>',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ── Sidebar ─────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    
    # Data source selection
    data_source = st.radio(
        "Data Source",
        options=["Alpaca", "Polygon"],
        index=0,
        horizontal=True,
        help="Alpaca: Free real-time data · Polygon: Free tier has ~7 day delay on hourly bars",
    )
    
    if data_source == "Alpaca":
        api_key = st.text_input(
            "Alpaca API Key", type="password",
            help="Get free keys at alpaca.markets",
            placeholder="your_alpaca_api_key",
        )
        api_secret = st.text_input(
            "Alpaca Secret Key", type="password",
            placeholder="your_alpaca_secret_key",
        )
        # Polygon key for earnings dates lookup (optional)
        polygon_key = st.text_input(
            "Polygon Key (earnings)", type="password",
            help="Optional: for auto-detecting earnings dates via Polygon financials API",
            placeholder="optional_polygon_key",
        )
    else:
        api_key = st.text_input(
            "Polygon API Key", type="password",
            help="Get a free key at polygon.io",
            placeholder="your_polygon_api_key",
        )
        api_secret = None
        polygon_key = api_key

    symbol = st.text_input("Ticker Symbol", value="TSLA").upper().strip()

    years = st.slider("History (years)", 1, 8, 4)
    
    last_n_earnings = st.slider("Last N Earnings", 1, 20, 4,
                                 help="Only backtest the most recent N earnings events")

    st.markdown("---")
    st.markdown("**Filters**")

    use_4h = st.toggle("4H Noon Candle (12PM CST)", value=True,
                       help="Use 9:30–1:30 ET 4H bar for signal. Falls back to daily if hourly unavailable.")

    use_fib = st.toggle("Fibonacci Zone Filter", value=True)
    if use_fib:
        fib_tol = st.slider("Fib Tolerance ±%", 0.5, 5.0, 2.0, 0.5)
        fib_tf  = st.radio(
            "Swing timeframe",
            options=["Weekly", "Daily"],
            index=0,
            horizontal=True,
            help="Weekly: swing high/low from weekly candles (smoother, institutional levels). "
                 "Daily: swing high/low from daily candles (more levels, shorter-term).",
        )
    else:
        fib_tol = 2.0
        fib_tf  = "Weekly"

    use_vol = st.toggle("Volume Filter", value=True)
    if use_vol:
        vol_min = st.slider("Min Vol Ratio (vs 20d avg)", 1.0, 3.0, 1.5, 0.1)
    else:
        vol_min = 1.5

    st.markdown("---")
    st.markdown("**📅 Next Earnings (manual)**")
    next_earnings_input = st.date_input(
        "Report date (AMC)",
        value=None,
        min_value=date.today(),
        max_value=date.today() + timedelta(days=180),
        help="Enter the known upcoming earnings date. Earnings must be AMC (after market close).",
        label_visibility="collapsed",
    )

    st.markdown("**📋 Past Earnings Dates (optional)**")
    manual_dates_raw = st.text_area(
        "One date per line (YYYY-MM-DD)",
        placeholder="2024-10-29\n2024-07-23\n2024-04-23\n...",
        height=120,
        help="Paste known AMC earnings report dates. If provided, skips Polygon financials lookup.",
        label_visibility="collapsed",
    )
    if manual_dates_raw.strip():
        manual_dates_list = [l.strip() for l in manual_dates_raw.strip().splitlines() if l.strip()]
    else:
        manual_dates_list = []

    st.markdown("---")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        fetch_btn = st.button("📅 FETCH EARNINGS", use_container_width=True)
    with col_btn2:
        run_btn = st.button("▶ RUN BACKTEST", type="primary", use_container_width=True)

    st.markdown(f"""
    <div style="font-size:9px;color:#3a3d5c;line-height:1.9;margin-top:12px">
    <b style="color:#6b7099">STRATEGY</b><br>
    Entry: AMC report day<br>
    Signal: 4H candle at ~1:30 PM ET<br>
    Green candle → LONG<br>
    Red candle → SHORT<br>
    Exit: Next trading day close<br>
    Data: {data_source} real OHLCV
    </div>
    """, unsafe_allow_html=True)


# ── Main ─────────────────────────────────────────────
# Check API credentials based on data source
if data_source == "Alpaca":
    missing_creds = not api_key or not api_secret
else:
    missing_creds = not api_key

if missing_creds:
    if data_source == "Alpaca":
        st.markdown("""
        <div style="text-align:center;padding:80px 0">
          <div style="font-size:40px;margin-bottom:16px;opacity:.3">🦙</div>
          <div style="color:#6b7099;font-size:14px;margin-bottom:8px">Enter your Alpaca API Key and Secret in the sidebar</div>
          <div style="color:#3a3d5c;font-size:10px;line-height:2">
            Free account at <a href="https://alpaca.markets" style="color:#f0c000">alpaca.markets</a><br>
            Real-time OHLCV · hourly bars with NO delay · paper trading
          </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="text-align:center;padding:80px 0">
          <div style="font-size:40px;margin-bottom:16px;opacity:.3">▲</div>
          <div style="color:#6b7099;font-size:14px;margin-bottom:8px">Enter your Polygon API key in the sidebar to get started</div>
          <div style="color:#3a3d5c;font-size:10px;line-height:2">
            Free key available at <a href="https://polygon.io" style="color:#4d9fff">polygon.io</a><br>
            Real historical OHLCV · hourly bars · earnings filing dates
          </div>
        </div>
        """, unsafe_allow_html=True)
    st.stop()

# Initialize session state for storing fetched data
if "fetched_data" not in st.session_state:
    st.session_state.fetched_data = None

if not fetch_btn and not run_btn:
    # Show existing earnings if already fetched
    if st.session_state.fetched_data is not None:
        data = st.session_state.fetched_data
        st.markdown(
            f'<div style="background:#0d0f1799;border:1px solid #1a1d2e;padding:12px;border-radius:4px;margin-bottom:12px">'
            f'<div style="font-size:10px;color:#6b7099">📅 <b style="color:#e8ecff">{data["symbol"]}</b> · {len(data["earnings_events"])} earnings events loaded</div>'
            f'<div style="font-size:9px;color:#3a3d5c;margin-top:4px">Click <b style="color:#00e5a0">▶ RUN BACKTEST</b> to analyze</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("""
        <div style="text-align:center;padding:60px 0;color:#3a3d5c;font-size:11px">
          Click <b style="color:#4d9fff">📅 FETCH EARNINGS</b> to load earnings dates, then <b style="color:#00e5a0">▶ RUN BACKTEST</b>
        </div>
        """, unsafe_allow_html=True)
    st.stop()

# ── Fetch earnings data ──────────────────────────────
end_date   = date.today()
start_date = end_date - timedelta(days=365 * years + 60)

if fetch_btn:
    source_name = "Alpaca" if data_source == "Alpaca" else "Polygon"
    with st.spinner(f"Fetching {symbol} data from {source_name}…"):
        status = st.empty()

        status.info("📈 Fetching daily price bars…")
        try:
            if data_source == "Alpaca":
                daily_df = get_daily_bars_alpaca(symbol, str(start_date - timedelta(days=90)), str(end_date), api_key, api_secret)
            else:
                daily_df = get_daily_bars(symbol, str(start_date - timedelta(days=90)), str(end_date), api_key)
        except Exception as e:
            status.empty()
            st.error(f"**{source_name} API Error**\n\n{e}")
            if data_source == "Alpaca":
                st.markdown(
                    '<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:14px;border-radius:4px;font-size:10px;color:#6b7099;line-height:2">'
                    '<b style="color:#e8ecff">Troubleshooting:</b><br>'
                    '• Check your Alpaca API Key and Secret at app.alpaca.markets<br>'
                    '• Make sure you\'re using keys from your paper trading account<br>'
                    '• Ticker must be a valid US stock ticker (e.g. TSLA, AAPL, HPE, GOOG)'
                    '</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:14px;border-radius:4px;font-size:10px;color:#6b7099;line-height:2">'
                    '<b style="color:#e8ecff">Troubleshooting:</b><br>'
                    '• Make sure your Polygon API key is correct (check polygon.io/dashboard)<br>'
                    '• Free tier supports: <code>daily bars</code>, <code>hourly bars</code> — it does NOT support <code>/vX/reference/financials</code><br>'
                    '• Ticker must be a valid US stock ticker (e.g. TSLA, AAPL, HPE, GOOG)<br>'
                    '• If you see 403: the key is wrong or expired'
                    '</div>',
                    unsafe_allow_html=True,
                )
            st.stop()

        if daily_df.empty:
            st.error(f"No daily price data found for **{symbol}**. Check that the ticker is a valid US stock.")
            st.stop()

        status.info("📅 Resolving earnings dates…")
        # Use Polygon key for earnings dates (even when using Alpaca for price data)
        earnings_key = polygon_key if data_source == "Alpaca" else api_key
        earnings_events, earn_source = get_earnings_dates(
            symbol, earnings_key,
            limit=years * 5 + 4,
            daily_df=daily_df,
            manual_dates=manual_dates_list if manual_dates_list else None,
        )
        next_earn = estimate_next_earnings(earnings_events) if earnings_events else None

        if not earnings_events:
            status.empty()
            st.error(
                f"Could not find earnings dates for **{symbol}**. "
                f"Try pasting known dates manually in the sidebar (YYYY-MM-DD, one per line)."
            )
            st.stop()

        # Filter to requested date range
        cutoff = str(start_date)
        earnings_events = [(d, l, p) for d, l, p in earnings_events if d >= cutoff]

        if not earnings_events:
            status.empty()
            st.warning("No earnings events found within the selected date range. Try increasing history years.")
            st.stop()

        # Fetch hourly bars for 4H candle analysis
        status.info("⏱ Fetching hourly bars for 4H candle analysis…")
        hourly_start = end_date - timedelta(days=14)  # Last 14 days for display
        hourly_data_delay = None
        try:
            if data_source == "Alpaca":
                hourly_df = get_hourly_bars_alpaca(symbol, str(hourly_start), str(end_date), api_key, api_secret)
            else:
                hourly_df = get_hourly_bars(symbol, str(hourly_start), str(end_date), api_key)
            if hourly_df.empty:
                st.sidebar.warning(f"⚠️ No hourly data returned from {source_name}")
            else:
                # Show what data we actually got
                actual_start = hourly_df.index.min()
                actual_end = hourly_df.index.max()
                hourly_data_delay = (date.today() - actual_end.date()).days
                st.sidebar.info(f"📊 Hourly data: {actual_start.date()} to {actual_end.date()}")
                if hourly_data_delay > 1 and data_source == "Polygon":
                    st.sidebar.warning(f"⚠️ Polygon free tier: hourly data is {hourly_data_delay} days behind")
        except Exception as e:
            st.sidebar.warning(f"⚠️ Hourly fetch error: {str(e)[:60]}")
            hourly_df = pd.DataFrame()
        
        status.empty()

        # Store in session state
        st.session_state.fetched_data = {
            "symbol": symbol,
            "daily_df": daily_df,
            "hourly_df": hourly_df,
            "earnings_events": earnings_events,
            "earn_source": earn_source,
            "next_earn": next_earn,
            "start_date": start_date,
            "end_date": end_date,
        }

    # Display earnings and Fibonacci analysis
    st.success(f"✅ Found {len(earnings_events)} earnings events for **{symbol}**")
    
    # Source badge
    source_colors = {"manual": "#00e5a0", "polygon": "#4d9fff", "auto-detected": "#f5c842", "none": "#ff4d6a"}
    source_labels = {
        "manual":       "✏️ manual input",
        "polygon":      "🔷 Polygon financials",
        "auto-detected":"⚡ auto-detected from price gaps",
        "none":         "❌ none",
    }
    st.markdown(
        f'<div style="font-size:10px;color:{source_colors.get(earn_source,"#6b7099")};margin-bottom:12px">'
        f'Source: {source_labels.get(earn_source, earn_source)}</div>',
        unsafe_allow_html=True,
    )
    
    # ══════════════════════════════════════════════════════════════════════════════
    # COMPUTE ALL SIGNALS FIRST FOR VERDICT
    # ══════════════════════════════════════════════════════════════════════════════
    
    # ── Current Price & Fibonacci Analysis ──
    latest_close = float(daily_df["close"].iloc[-1])
    latest_date = daily_df.index[-1]
    
    # Calculate swing high/low from last earnings to now (or 90 days if no recent earnings)
    if len(earnings_events) >= 1:
        last_earn_date = datetime.strptime(earnings_events[-1][0], "%Y-%m-%d").date()
        swing_start = last_earn_date
    else:
        swing_start = latest_date - timedelta(days=90)
    
    swing_data = daily_df[daily_df.index >= swing_start]
    if len(swing_data) < 5:
        swing_data = daily_df.tail(90)  # fallback
    
    swing_hi = float(swing_data["high"].max())
    swing_lo = float(swing_data["low"].min())
    swing_range = swing_hi - swing_lo
    
    # Calculate Fibonacci levels
    fib_levels = calc_fib_levels(swing_lo, swing_hi)
    
    # Find nearest Fib level and bias
    nearest = nearest_fib(latest_close, swing_lo, swing_hi, 100)  # 100% tolerance to always find nearest
    if nearest:
        nearest_name, nearest_price, nearest_dist = nearest
    else:
        nearest_name, nearest_price, nearest_dist = "N/A", 0, 0
    
    # Determine bias based on price position
    price_position = (latest_close - swing_lo) / swing_range if swing_range > 0 else 0.5
    if price_position >= 0.618:
        fib_bias = "BULLISH"
        bias_color = "#00e5a0"
        bias_desc = "Price above 61.8% retracement - bullish structure"
    elif price_position >= 0.382:
        fib_bias = "NEUTRAL"
        bias_color = "#f5c842"
        bias_desc = "Price in consolidation zone (38.2%-61.8%)"
    else:
        fib_bias = "BEARISH"
        bias_color = "#ff4d6a"
        bias_desc = "Price below 38.2% retracement - bearish structure"
    
    # Display current price panel
    st.markdown("---")
    st.markdown("### 📊 Current Analysis")
    
    # ── 4H Candle Bias ──
    hourly_df = st.session_state.fetched_data.get("hourly_df", pd.DataFrame())
    
    # Check current time in ET to determine if 4H candle is complete
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    today = date.today()
    
    # 4H candle completes at 1:30 PM ET (13:30)
    candle_complete_time = now_et.replace(hour=13, minute=30, second=0, microsecond=0)
    is_after_candle_close = now_et >= candle_complete_time
    is_market_day = now_et.weekday() < 5  # Mon-Fri
    
    # Try today's candle first
    today_4h = get_4h_noon_candle(symbol, today, hourly_df)
    
    # If today's candle exists and market time is after 1:30 PM ET, use it
    # Otherwise, try to get the last trading day's candle
    if today_4h and is_after_candle_close:
        candle_4h = today_4h
        candle_date = today
        candle_status = "COMPLETE"
    elif today_4h and not is_after_candle_close:
        # Today's candle is still forming - show partial but mark as in-progress
        candle_4h = today_4h
        candle_date = today
        candle_status = "IN PROGRESS"
    else:
        # Try yesterday or last trading day
        candle_4h = None
        candle_date = None
        checked_dates = []
        # Look back up to 10 days for last trading day (handles holidays/weekends)
        for days_back in range(1, 11):
            check_date = today - timedelta(days=days_back)
            past_candle = get_4h_noon_candle(symbol, check_date, hourly_df)
            checked_dates.append((check_date, "found" if past_candle else "no data"))
            if past_candle:
                candle_4h = past_candle
                candle_date = check_date
                candle_status = "LAST SESSION"
                break
        
        # Store checked dates for debug
        if not candle_4h:
            st.session_state["_4h_debug_dates"] = checked_dates
    
    if candle_4h:
        candle_change = (candle_4h["close"] - candle_4h["open"]) / candle_4h["open"] * 100
        if candle_4h["close"] > candle_4h["open"]:
            candle_4h_bias = "BULLISH"
            candle_4h_color = "#00e5a0"
            candle_4h_desc = f"Green candle (+{candle_change:.2f}%)"
            candle_4h_signal = "LONG"
        else:
            candle_4h_bias = "BEARISH"
            candle_4h_color = "#ff4d6a"
            candle_4h_desc = f"Red candle ({candle_change:.2f}%)"
            candle_4h_signal = "SHORT"
        
        # Add status to description
        if candle_status == "IN PROGRESS":
            candle_4h_desc = f"⏳ {candle_4h_desc} (forming)"
            candle_4h_signal = f"{candle_4h_signal} (tentative)"
        elif candle_status == "LAST SESSION":
            candle_4h_desc = f"📅 {candle_4h_desc} ({candle_date})"
    else:
        candle_4h_bias = "NO DATA"
        candle_4h_color = "#6b7099"
        candle_4h_desc = "No 4H candle data available"
        candle_4h_signal = "N/A"
        candle_status = "UNAVAILABLE"
        candle_date = None
    
    # ══════════════════════════════════════════════════════════════════════════════
    # FETCH OPTIONS DATA FOR VERDICT
    # ══════════════════════════════════════════════════════════════════════════════
    with st.spinner("Analyzing options flow..."):
        if data_source == "Alpaca":
            options_data = get_options_bias_alpaca(symbol, api_key, api_secret)
        elif api_key:
            options_data = get_options_bias(symbol, api_key)
        else:
            options_data = None
    
    # Extract options sentiment
    if options_data and "error" not in options_data:
        options_oi_bias = options_data.get("sentiment", "NEUTRAL")
        options_vol_bias = options_data.get("vol_sentiment", "N/A")
    else:
        options_oi_bias = "N/A"
        options_vol_bias = "N/A"
    
    # ══════════════════════════════════════════════════════════════════════════════
    # CALCULATE FINAL VERDICT
    # ══════════════════════════════════════════════════════════════════════════════
    
    # Score each signal: BULLISH = +1, NEUTRAL = 0, BEARISH = -1, N/A = skip
    signals = []
    signal_details = []
    
    # 4H Candle (highest weight - this is the primary entry signal)
    if candle_4h_bias == "BULLISH":
        signals.append(2)  # Double weight for 4H
        signal_details.append(("4H Candle", "BULLISH", "#00e5a0", "🟢"))
    elif candle_4h_bias == "BEARISH":
        signals.append(-2)
        signal_details.append(("4H Candle", "BEARISH", "#ff4d6a", "🔴"))
    else:
        signal_details.append(("4H Candle", "N/A", "#6b7099", "⚪"))
    
    # Fibonacci Bias
    if fib_bias == "BULLISH":
        signals.append(1)
        signal_details.append(("Fibonacci", "BULLISH", "#00e5a0", "🟢"))
    elif fib_bias == "BEARISH":
        signals.append(-1)
        signal_details.append(("Fibonacci", "BEARISH", "#ff4d6a", "🔴"))
    else:
        signals.append(0)
        signal_details.append(("Fibonacci", "NEUTRAL", "#f5c842", "🟡"))
    
    # Options OI Bias
    if options_oi_bias == "BULLISH":
        signals.append(1)
        signal_details.append(("Options OI", "BULLISH", "#00e5a0", "🟢"))
    elif options_oi_bias == "BEARISH":
        signals.append(-1)
        signal_details.append(("Options OI", "BEARISH", "#ff4d6a", "🔴"))
    elif options_oi_bias == "NEUTRAL":
        signals.append(0)
        signal_details.append(("Options OI", "NEUTRAL", "#f5c842", "🟡"))
    else:
        signal_details.append(("Options OI", "N/A", "#6b7099", "⚪"))
    
    # Options Volume Bias
    if options_vol_bias == "BULLISH":
        signals.append(1)
        signal_details.append(("Options Vol", "BULLISH", "#00e5a0", "🟢"))
    elif options_vol_bias == "BEARISH":
        signals.append(-1)
        signal_details.append(("Options Vol", "BEARISH", "#ff4d6a", "🔴"))
    elif options_vol_bias == "NEUTRAL":
        signals.append(0)
        signal_details.append(("Options Vol", "NEUTRAL", "#f5c842", "🟡"))
    else:
        signal_details.append(("Options Vol", "N/A", "#6b7099", "⚪"))
    
    # Calculate final verdict
    if signals:
        score = sum(signals)
        max_score = len(signals) + 1  # +1 for extra weight on 4H
        
        if score >= 2:
            final_verdict = "BULLISH"
            verdict_color = "#00e5a0"
            verdict_emoji = "🚀"
            verdict_action = "LONG"
        elif score <= -2:
            final_verdict = "BEARISH"
            verdict_color = "#ff4d6a"
            verdict_emoji = "📉"
            verdict_action = "SHORT"
        elif score > 0:
            final_verdict = "LEAN BULLISH"
            verdict_color = "#7ed4a0"
            verdict_emoji = "📈"
            verdict_action = "LONG (cautious)"
        elif score < 0:
            final_verdict = "LEAN BEARISH"
            verdict_color = "#ff8a9f"
            verdict_emoji = "📉"
            verdict_action = "SHORT (cautious)"
        else:
            final_verdict = "NEUTRAL"
            verdict_color = "#f5c842"
            verdict_emoji = "⚖️"
            verdict_action = "WAIT / NO TRADE"
        
        # ── Calculate Confidence Level ──
        # Check signal alignment
        bullish_count = sum(1 for s in signals if s > 0)
        bearish_count = sum(1 for s in signals if s < 0)
        neutral_count = sum(1 for s in signals if s == 0)
        total_signals = len(signals)
        
        # Determine direction from 4H candle (primary signal)
        candle_direction = "BULL" if candle_4h_bias == "BULLISH" else ("BEAR" if candle_4h_bias == "BEARISH" else "NONE")
        
        # Check if other signals align with 4H candle direction
        if candle_direction == "BULL":
            # Count signals that align with bullish (bullish or neutral counts as aligned)
            aligned = bullish_count + neutral_count
            divergent = bearish_count
        elif candle_direction == "BEAR":
            aligned = bearish_count + neutral_count
            divergent = bullish_count
        else:
            aligned = neutral_count
            divergent = 0
        
        # Check specific divergences
        fib_diverges = (candle_direction == "BULL" and fib_bias == "BEARISH") or \
                       (candle_direction == "BEAR" and fib_bias == "BULLISH")
        options_oi_diverges = (candle_direction == "BULL" and options_oi_bias == "BEARISH") or \
                              (candle_direction == "BEAR" and options_oi_bias == "BULLISH")
        
        # Calculate confidence
        if candle_4h_bias in ["BULLISH", "BEARISH"]:
            if divergent == 0:
                confidence = "HIGH"
                confidence_color = "#00e5a0"
                confidence_emoji = "🎯"
                confidence_desc = "All signals aligned"
            elif divergent == 1:
                confidence = "MEDIUM"
                confidence_color = "#f5c842"
                confidence_emoji = "⚡"
                if fib_diverges:
                    confidence_desc = "Fib diverges - potential reversal play"
                elif options_oi_diverges:
                    confidence_desc = "Options OI diverges - watch positioning"
                else:
                    confidence_desc = "Minor divergence in signals"
            else:
                confidence = "LOW"
                confidence_color = "#ff4d6a"
                confidence_emoji = "⚠️"
                confidence_desc = f"{divergent} signals diverge - high risk"
        else:
            confidence = "N/A"
            confidence_color = "#6b7099"
            confidence_emoji = "❓"
            confidence_desc = "No 4H candle signal"
        
        # Special case: reversal setup
        is_reversal = fib_diverges and candle_4h_bias in ["BULLISH", "BEARISH"]
        if is_reversal:
            reversal_note = "⚡ REVERSAL SETUP" if candle_direction == "BULL" else "⚡ REVERSAL SHORT"
        else:
            reversal_note = None
            
    else:
        final_verdict = "INSUFFICIENT DATA"
        verdict_color = "#6b7099"
        verdict_emoji = "❓"
        verdict_action = "NEED MORE DATA"
        score = 0
        confidence = "N/A"
        confidence_color = "#6b7099"
        confidence_emoji = "❓"
        confidence_desc = "Insufficient signals"
        reversal_note = None
    
    # ══════════════════════════════════════════════════════════════════════════════
    # DISPLAY FINAL VERDICT (TOP OF PAGE)
    # ══════════════════════════════════════════════════════════════════════════════
    
    # Build reversal badge if applicable
    reversal_html = ""
    if reversal_note:
        reversal_html = (
            f'<div style="background:#f5c84220;border:1px solid #f5c84260;'
            f'padding:6px 16px;border-radius:20px;display:inline-block;margin-bottom:12px">'
            f'<span style="font-size:12px;color:#f5c842;font-weight:bold">{reversal_note}</span>'
            f'</div><br>'
        )
    
    st.markdown(
        f'<div style="background:linear-gradient(135deg, #0d0f17 0%, #131625 100%);'
        f'border:2px solid {verdict_color};border-radius:12px;padding:24px;margin:20px 0;text-align:center">'
        f'<div style="font-size:48px;margin-bottom:8px">{verdict_emoji}</div>'
        f'<div style="font-size:14px;color:#6b7099;letter-spacing:2px;margin-bottom:4px">FINAL VERDICT</div>'
        f'<div style="font-size:42px;font-weight:bold;color:{verdict_color};margin-bottom:8px">{symbol}</div>'
        f'{reversal_html}'
        f'<div style="font-size:28px;font-weight:bold;color:{verdict_color};margin-bottom:12px">{final_verdict}</div>'
        f'<div style="font-size:16px;color:#e8ecff;margin-bottom:8px">Signal: <b style="color:{verdict_color}">{verdict_action}</b></div>'
        # Confidence badge
        f'<div style="display:inline-block;background:{confidence_color}20;border:1px solid {confidence_color}60;'
        f'padding:8px 20px;border-radius:24px;margin-bottom:16px">'
        f'<span style="font-size:14px">{confidence_emoji}</span> '
        f'<span style="font-size:13px;color:{confidence_color};font-weight:bold">CONFIDENCE: {confidence}</span>'
        f'<div style="font-size:10px;color:#6b7099;margin-top:2px">{confidence_desc}</div>'
        f'</div>'
        f'<div style="display:flex;justify-content:center;gap:16px;flex-wrap:wrap;margin-top:16px">'
        + ''.join([
            f'<div style="background:#0d0f1799;border:1px solid {color}40;padding:8px 16px;border-radius:20px">'
            f'<span style="font-size:12px">{emoji}</span> '
            f'<span style="font-size:11px;color:#6b7099">{name}:</span> '
            f'<span style="font-size:11px;color:{color};font-weight:bold">{bias}</span>'
            f'</div>'
            for name, bias, color, emoji in signal_details
        ])
        + f'</div>'
        f'<div style="font-size:10px;color:#3a3d5c;margin-top:16px">Score: {score} · Higher = more bullish</div>'
        f'</div>',
        unsafe_allow_html=True
    )
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Current Price", f"${latest_close:.2f}", f"as of {latest_date}")
    with col2:
        st.metric("Fibonacci Bias", fib_bias, bias_desc[:30])
    with col3:
        st.metric("4H Candle Bias", candle_4h_bias, candle_4h_desc[:35])
    with col4:
        st.metric("Nearest Fib Level", nearest_name, f"${nearest_price:.2f} ({nearest_dist:.1f}% away)")
    
    # 4H Candle details
    if candle_4h:
        date_label = f"{candle_date}" if candle_date else "N/A"
        status_emoji = "✅" if candle_status == "COMPLETE" else ("⏳" if candle_status == "IN PROGRESS" else "📅")
        st.markdown(f"#### ⏱ 4H Candle (9:30 AM - 1:30 PM ET) · {status_emoji} {candle_status} · {date_label}")
        c4h_col1, c4h_col2, c4h_col3, c4h_col4 = st.columns(4)
        with c4h_col1:
            st.metric("Open", f"${candle_4h['open']:.2f}")
        with c4h_col2:
            st.metric("Close", f"${candle_4h['close']:.2f}")
        with c4h_col3:
            st.metric("High", f"${candle_4h['high']:.2f}")
        with c4h_col4:
            st.metric("Low", f"${candle_4h['low']:.2f}")
        
        # Status-specific message
        if candle_status == "IN PROGRESS":
            time_remaining = candle_complete_time - now_et
            mins_remaining = int(time_remaining.total_seconds() // 60)
            status_msg = f"⏳ Candle forming · ~{mins_remaining} min until 1:30 PM ET"
        elif candle_status == "COMPLETE":
            status_msg = f"✅ Today's candle complete"
        else:
            days_old = (today - candle_date).days
            if days_old > 1:
                status_msg = f"⚠️ Data is {days_old} days old · Polygon free tier delay"
            else:
                status_msg = f"📅 Last trading session ({candle_date})"
        
        st.markdown(
            f'<div style="background:#0d0f1799;border:1px solid {candle_4h_color}40;'
            f'padding:12px;border-radius:4px;margin:12px 0">'
            f'<div style="font-size:10px;color:#6b7099">4H CANDLE SIGNAL</div>'
            f'<div style="font-size:18px;color:{candle_4h_color};font-weight:bold;margin-top:4px">'
            f'{candle_4h_signal}</div>'
            f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
            f'{status_msg} · Based on {candle_4h["bars"]} hourly bars · '
            f'Range: ${candle_4h["low"]:.2f} - ${candle_4h["high"]:.2f}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
        
        # Show warning if data is significantly delayed
        if candle_status == "LAST SESSION" and candle_date:
            days_old = (today - candle_date).days
            if days_old > 1:
                st.markdown(
                    f'<div style="background:#f5c84210;border:1px solid #f5c84240;'
                    f'padding:10px;border-radius:4px;margin-top:8px;font-size:10px;color:#f5c842">'
                    f'⚠️ <b>Polygon Free Tier Limitation:</b> Hourly bar data is delayed by ~{days_old} days. '
                    f'For real-time intraday data, upgrade to a paid Polygon plan at polygon.io/pricing'
                    f'</div>',
                    unsafe_allow_html=True
                )
    else:
        st.markdown("#### ⏱ 4H Candle (9:30 AM - 1:30 PM ET)")
        
        # Debug info
        hourly_info = []
        if hourly_df is None or hourly_df.empty:
            hourly_info.append("❌ No hourly data loaded")
        else:
            hourly_info.append(f"✓ Hourly data: {len(hourly_df)} bars")
            if len(hourly_df) > 0:
                hourly_info.append(f"✓ Date range: {hourly_df.index.min()} to {hourly_df.index.max()}")
                # Check available dates
                available_dates = sorted(set(hourly_df.index.date))[-10:]
                hourly_info.append(f"✓ Recent dates with data: {available_dates}")
        hourly_info.append(f"✓ Today: {today} ({today.strftime('%A')})")
        hourly_info.append(f"✓ Current ET time: {now_et.strftime('%Y-%m-%d %H:%M %Z')}")
        
        # Show dates that were checked
        checked = st.session_state.get("_4h_debug_dates", [])
        if checked:
            hourly_info.append(f"✓ Dates checked for 4H candle:")
            for d, status in checked:
                hourly_info.append(f"   - {d} ({d.strftime('%A')}): {status}")
        
        with st.expander("🔍 Debug: Hourly Data Info", expanded=True):
            for info in hourly_info:
                st.text(info)
        
        st.warning("No 4H candle data available. See debug info above.")
    
    # Fibonacci levels table
    st.markdown("#### 📐 Fibonacci Levels")
    st.markdown(f'<div style="font-size:10px;color:#6b7099;margin-bottom:8px">'
                f'Swing: ${swing_lo:.2f} (low) → ${swing_hi:.2f} (high) · Range: ${swing_range:.2f}</div>',
                unsafe_allow_html=True)
    
    fib_data = []
    for name, level in sorted(fib_levels.items(), key=lambda x: x[1], reverse=True):
        dist_pct = abs(latest_close - level) / level * 100
        position = "▶" if abs(latest_close - level) < swing_range * 0.02 else ""
        fib_data.append({
            "Level": name,
            "Price": f"${level:.2f}",
            "Distance": f"{dist_pct:.1f}%",
            "": position
        })
    
    fib_df = pd.DataFrame(fib_data)
    st.dataframe(fib_df, use_container_width=True, hide_index=True)
    
    # Visual bias indicator
    st.markdown(
        f'<div style="background:linear-gradient(90deg, #ff4d6a 0%, #f5c842 50%, #00e5a0 100%);'
        f'height:8px;border-radius:4px;margin:12px 0;position:relative">'
        f'<div style="position:absolute;left:{price_position*100:.1f}%;top:-4px;'
        f'width:16px;height:16px;background:{bias_color};border-radius:50%;'
        f'border:2px solid #07080d;transform:translateX(-50%)"></div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:9px;color:#6b7099">'
        f'<span>Bearish (0%)</span><span>Neutral (50%)</span><span>Bullish (100%)</span>'
        f'</div>',
        unsafe_allow_html=True
    )
    
    # ── Options Bias Section ──
    st.markdown("---")
    st.markdown("### 📈 Options Bias")
    
    # options_data was already fetched above for verdict calculation
    
    if options_data and "error" not in options_data:
        # Row 1: Main metrics
        opt_col1, opt_col2, opt_col3, opt_col4 = st.columns(4)
        with opt_col1:
            st.metric("OI Sentiment", options_data["sentiment"], options_data["sentiment_desc"][:30])
        with opt_col2:
            st.metric("P/C Ratio (OI)", f"{options_data['oi_pc_ratio']:.2f}", 
                     f"{options_data['put_oi']:,} P / {options_data['call_oi']:,} C")
        with opt_col3:
            st.metric("Total OI", f"{options_data['total_oi']:,}",
                     f"{options_data['total_puts']} P · {options_data['total_calls']} C")
        with opt_col4:
            if options_data.get('total_volume', 0) > 0:
                st.metric("Today's Volume", f"{options_data['total_volume']:,}",
                         f"P/C: {options_data['vol_pc_ratio']:.2f}")
            else:
                st.metric("Today's Volume", "N/A", "Snapshot unavailable")
        
        # Row 2: Volume flow
        if options_data.get('total_volume', 0) > 0:
            vol_col1, vol_col2, vol_col3 = st.columns(3)
            with vol_col1:
                st.metric("Call Volume", f"{options_data['call_volume']:,}")
            with vol_col2:
                st.metric("Put Volume", f"{options_data['put_volume']:,}")
            with vol_col3:
                st.metric("Volume Sentiment", options_data['vol_sentiment'])
        
        # Visual options bias indicator
        pc = options_data['oi_pc_ratio']
        if pc <= 0.3:
            opt_position = 1.0
        elif pc >= 1.4:
            opt_position = 0.0
        else:
            opt_position = 1.0 - (pc - 0.3) / 1.1
        
        opt_position = max(0, min(1, opt_position))
        
        st.markdown(
            f'<div style="background:linear-gradient(90deg, #ff4d6a 0%, #f5c842 50%, #00e5a0 100%);'
            f'height:8px;border-radius:4px;margin:12px 0;position:relative">'
            f'<div style="position:absolute;left:{opt_position*100:.1f}%;top:-4px;'
            f'width:16px;height:16px;background:{options_data["sentiment_color"]};border-radius:50%;'
            f'border:2px solid #07080d;transform:translateX(-50%)"></div>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:9px;color:#6b7099">'
            f'<span>Put Heavy (Bearish)</span><span>Balanced</span><span>Call Heavy (Bullish)</span>'
            f'</div>',
            unsafe_allow_html=True
        )
        
        # ── Unusual Options Activity ──
        unusual = options_data.get('unusual_activity', [])
        top_vol = options_data.get('top_volume', [])
        
        st.markdown("#### 🔥 Options Volume Activity")
        
        # Show debug info
        debug = options_data.get('debug', [])
        if debug:
            with st.expander("🔍 API Debug Info", expanded=False):
                for d in debug:
                    st.text(d)
        
        if unusual:
            st.markdown('<div style="font-size:9px;color:#6b7099;margin-bottom:8px">'
                       'Contracts with high volume or unusual Vol/OI ratio (potential large bets)</div>',
                       unsafe_allow_html=True)
            
            unusual_data = []
            for u in unusual:
                unusual_data.append({
                    "Type": u['type'],
                    "Strike": f"${u['strike']:.2f}",
                    "Expiry": u['expiry'],
                    "Volume": f"{u['volume']:,}",
                    "OI": f"{u['oi']:,}",
                    "Vol/OI": f"{u['vol_oi_ratio']:.1f}x",
                    "🔥": "⚡" if u.get('is_unusual') else "",
                })
            
            unusual_df = pd.DataFrame(unusual_data)
            st.dataframe(unusual_df, use_container_width=True, hide_index=True)
            
            # Summary of unusual activity
            call_unusual = [u for u in unusual if u['type'] == 'CALL']
            put_unusual = [u for u in unusual if u['type'] == 'PUT']
            call_vol = sum(u['volume'] for u in call_unusual)
            put_vol = sum(u['volume'] for u in put_unusual)
            
            if call_vol > put_vol * 1.5:
                unusual_bias = "BULLISH"
                unusual_color = "#00e5a0"
                unusual_desc = f"Call volume dominates ({call_vol:,} vs {put_vol:,} puts)"
            elif put_vol > call_vol * 1.5:
                unusual_bias = "BEARISH"
                unusual_color = "#ff4d6a"
                unusual_desc = f"Put volume dominates ({put_vol:,} vs {call_vol:,} calls)"
            else:
                unusual_bias = "MIXED"
                unusual_color = "#f5c842"
                unusual_desc = f"Mixed flow ({call_vol:,} calls / {put_vol:,} puts)"
            
            st.markdown(
                f'<div style="background:#0d0f1799;border:1px solid {unusual_color}40;'
                f'padding:12px;border-radius:4px;margin-top:8px">'
                f'<div style="font-size:10px;color:#6b7099">TOP VOLUME BIAS</div>'
                f'<div style="font-size:14px;color:{unusual_color};font-weight:bold;margin-top:4px">'
                f'🔥 {unusual_bias}</div>'
                f'<div style="font-size:10px;color:#6b7099;margin-top:4px">{unusual_desc}</div>'
                f'</div>',
                unsafe_allow_html=True
            )
        elif top_vol:
            # Show top volume even if not flagged as unusual
            st.markdown('<div style="font-size:9px;color:#6b7099;margin-bottom:8px">'
                       'Top contracts by today\'s volume</div>',
                       unsafe_allow_html=True)
            
            top_data = []
            for u in top_vol[:10]:
                top_data.append({
                    "Type": u['type'],
                    "Strike": f"${u['strike']:.2f}",
                    "Expiry": u['expiry'],
                    "Volume": f"{u['volume']:,}",
                    "OI": f"{u['oi']:,}",
                    "Vol/OI": f"{u['vol_oi_ratio']:.1f}x",
                })
            
            top_df = pd.DataFrame(top_data)
            st.dataframe(top_df, use_container_width=True, hide_index=True)
            st.info("No contracts met unusual activity thresholds (Vol>1000 or Vol/OI>2x)")
        else:
            st.warning("No volume data available. The options snapshot API may require a Polygon paid plan.")
            st.markdown('<div style="font-size:9px;color:#6b7099">'
                       'OI data is still available above from the contracts API.</div>',
                       unsafe_allow_html=True)
        
        # Summary box
        st.markdown(
            f'<div style="background:#0d0f1799;border:1px solid {options_data["sentiment_color"]}40;'
            f'padding:12px;border-radius:4px;margin-top:12px">'
            f'<div style="font-size:10px;color:#6b7099">OPTIONS FLOW SUMMARY</div>'
            f'<div style="font-size:12px;color:{options_data["sentiment_color"]};font-weight:bold;margin-top:4px">'
            f'{options_data["sentiment"]} (OI-based)</div>'
            f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
            f'P/C Ratio (OI): {options_data["oi_pc_ratio"]:.2f} · '
            f'{"<0.7 = Bullish" if options_data["oi_pc_ratio"] < 0.7 else ("0.7-1.0 = Neutral" if options_data["oi_pc_ratio"] <= 1.0 else ">1.0 = Bearish")}'
            f'</div></div>',
            unsafe_allow_html=True
        )
    elif options_data and "error" in options_data:
        st.warning(f"⚠️ Options data unavailable: {options_data['error'][:60]}...")
        debug = options_data.get('debug', [])
        if debug:
            with st.expander("🔍 API Debug Info", expanded=True):
                for d in debug:
                    st.text(d)
        st.markdown('<div style="font-size:9px;color:#6b7099">'
                   'Options chain data may require a Polygon paid plan.</div>',
                   unsafe_allow_html=True)
    else:
        st.info("No options data available for this ticker.")
    
    # Next earnings info
    if next_earn:
        days_to_earn = (datetime.strptime(next_earn, "%Y-%m-%d").date() - date.today()).days
        st.markdown(
            f'<div style="background:#0d0f1799;border:1px solid #4d9fff40;padding:12px;border-radius:4px;margin-top:16px">'
            f'<div style="font-size:10px;color:#6b7099">📅 NEXT EARNINGS (estimated)</div>'
            f'<div style="font-size:16px;color:#4d9fff;font-weight:bold">{next_earn}</div>'
            f'<div style="font-size:10px;color:#6b7099">{days_to_earn} days away</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    
    # Show earnings dates table
    st.markdown("---")
    st.markdown("### 📅 Earnings History")
    earn_df = pd.DataFrame(earnings_events, columns=["Report Date", "Quarter", "Period"])
    earn_df = earn_df.sort_values("Report Date", ascending=False).reset_index(drop=True)
    st.dataframe(earn_df, use_container_width=True, height=250)
    
    st.info("👆 Review the analysis above, then click **▶ RUN BACKTEST** to run the strategy.")
    st.stop()

# ── Run backtest ─────────────────────────────────────
if run_btn:
    if st.session_state.fetched_data is None:
        st.warning("Please click **📅 FETCH EARNINGS** first to load earnings data.")
        st.stop()
    
    # Load from session state
    data = st.session_state.fetched_data
    symbol = data["symbol"]
    daily_df = data["daily_df"]
    earnings_events = data["earnings_events"]
    earn_source = data["earn_source"]
    next_earn = data["next_earn"]
    start_date = data["start_date"]
    end_date = data["end_date"]
    
    status = st.empty()
    
    # Source badge in sidebar
    source_colors = {"manual": "#00e5a0", "polygon": "#4d9fff", "auto-detected": "#f5c842", "none": "#ff4d6a"}
    source_labels = {
        "manual":       "✏️ manual input",
        "polygon":      "🔷 Polygon financials",
        "auto-detected":"⚡ auto-detected from price gaps",
        "none":         "❌ none",
    }
    st.sidebar.markdown(
        f'<div style="font-size:9px;color:{source_colors.get(earn_source,"#6b7099")};'
        f'margin-top:4px">Earnings source: {source_labels.get(earn_source, earn_source)} '
        f'({len(earnings_events)} events)</div>',
        unsafe_allow_html=True,
    )

    # Hourly bars for 4H candle analysis
    hourly_start = max(start_date, end_date - timedelta(days=730))
    if use_4h:
        status.info("⏱ Fetching hourly bars for 4H candle analysis…")
        try:
            if data_source == "Alpaca":
                hourly_df = get_hourly_bars_alpaca(symbol, str(hourly_start), str(end_date), api_key, api_secret)
            else:
                hourly_df = get_hourly_bars(symbol, str(hourly_start), str(end_date), api_key)
            if hourly_df.empty:
                st.sidebar.warning("⚠️ No hourly data returned — 4H candle will fall back to daily open/close.")
        except Exception as e:
            st.sidebar.warning(f"⚠️ Hourly bars unavailable ({str(e)[:80]}). 4H candle will fall back to daily.")
            hourly_df = pd.DataFrame()
    else:
        hourly_df = pd.DataFrame()

    status.info("⚙️ Running backtest…")
    
    # Filter to last N earnings (sorted by date, take last N)
    earnings_sorted = sorted(earnings_events, key=lambda x: x[0])
    earnings_filtered = earnings_sorted[-last_n_earnings:] if len(earnings_sorted) > last_n_earnings else earnings_sorted
    
    all_trades = run_backtest(
        symbol, daily_df, hourly_df, earnings_filtered,
        vol_min, use_vol, fib_tol, use_fib, use_4h, fib_tf,
    )
    status.empty()
    
    # Store filtered count for diagnostics
    earnings_used = earnings_filtered

if all_trades.empty:
    skipped_reasons = all_trades.attrs.get("skipped_reasons", [])
    
    # Build diagnostic message
    diag_lines = []
    diag_lines.append(f"**Earnings events used:** {len(earnings_used)} (filtered from {len(earnings_events)} total)")
    diag_lines.append(f"**Daily price data range:** {min(daily_df.index)} to {max(daily_df.index)}")
    
    if skipped_reasons:
        diag_lines.append("\n**Skipped events:**")
        for dt, reason in skipped_reasons[:10]:  # Show first 10
            diag_lines.append(f"- {dt}: {reason}")
        if len(skipped_reasons) > 10:
            diag_lines.append(f"- ... and {len(skipped_reasons) - 10} more")
    
    st.warning("No trades could be computed. Try expanding the date range or relaxing filters.")
    st.info("\n".join(diag_lines))
    
    # Suggestions based on diagnostics
    suggestions = []
    if skipped_reasons:
        entry_issues = sum(1 for _, r in skipped_reasons if "entry_date not in" in r)
        exit_issues = sum(1 for _, r in skipped_reasons if "exit date" in r)
        if entry_issues > 0:
            suggestions.append(f"• {entry_issues} earnings dates are outside your price data range")
        if exit_issues > 0:
            suggestions.append(f"• {exit_issues} events are missing next-day exit data (possibly at end of data range)")
    
    if suggestions:
        st.markdown("**Possible causes:**\n" + "\n".join(suggestions))
    
    st.stop()

active_trades = all_trades[all_trades["passes_all"]].reset_index(drop=True)
skipped       = len(all_trades) - len(active_trades)
stats         = calc_stats(active_trades)

# ── Next earnings banner ─────────────────────────────
display_next = str(next_earnings_input) if next_earnings_input else next_earn
if display_next:
    days_away = (datetime.strptime(display_next, "%Y-%m-%d").date() - date.today()).days
    urgency_color = "#ff4d6a" if days_away <= 1 else ("#f5c842" if days_away <= 7 else "#4d9fff")
    days_label = "TODAY" if days_away == 0 else ("TOMORROW" if days_away == 1 else f"in {days_away} days")
    src_label = "manually set" if next_earnings_input else "estimated from filing cadence"
    st.markdown(
        f'<div style="background:rgba(245,200,66,.06);border:1px solid {urgency_color}40;'
        f'padding:10px 18px;border-radius:4px;margin-bottom:12px;display:flex;align-items:center;gap:20px">'
        f'<span style="font-size:20px">📅</span>'
        f'<div>'
        f'<div style="font-size:9px;color:#6b7099;letter-spacing:1.5px;margin-bottom:2px">NEXT EARNINGS · {src_label.upper()}</div>'
        f'<div><b style="color:{urgency_color};font-size:16px">{display_next}</b>'
        f' &nbsp;<span style="font-size:11px;color:{urgency_color};font-weight:700">{days_label}</span>'
        f' &nbsp;<span style="font-size:9px;color:#6b7099">· AMC · enter at 1:30 PM ET on this date</span></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

# ── Filter summary ───────────────────────────────────
tags = []
if use_4h:  tags.append('<span class="pill pill-blue">4H CANDLE</span>')
if use_fib: tags.append(f'<span class="pill pill-blue">FIB {fib_tf.upper()} ±{fib_tol}%</span>')
if use_vol: tags.append(f'<span class="pill pill-green">VOL ≥{vol_min:.1f}x</span>')
st.markdown(
    f'<div style="margin-bottom:12px;font-size:10px;color:#6b7099">'
    f'<b style="color:#e8ecff">{len(all_trades)}</b> events · '
    f'<b style="color:#00e5a0">{len(active_trades)}</b> active · '
    f'<b style="color:#f5c842">{skipped}</b> filtered &nbsp;&nbsp;'
    + " ".join(tags) + "</div>",
    unsafe_allow_html=True,
)

# ── Stats row ────────────────────────────────────────
profit = stats["total_return"] >= 0
c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
c1.metric("Total Return",   f'{stats["total_return"]:+.2f}%', f'$100 → ${stats["final_eq"]:.0f}')
c2.metric("Win Rate",       f'{stats["win_rate"]:.1f}%',      f'{stats["wins"]}W / {stats["losses"]}L')
c3.metric("Profit Factor",  str(stats["profit_factor"]) if stats["profit_factor"] else "∞", f'avg W {stats["avg_win"]:+.2f}%')
c4.metric("Max Drawdown",   f'-{stats["max_dd"]:.2f}%')
c5.metric("Avg Trade",      f'{stats["avg_trade"]:+.2f}%')
c6.metric("Active Trades",  str(stats["n"]))
c7.metric("Fib Hits",       f'{all_trades["fib_hit"].notna().sum()}/{len(all_trades)}')

st.markdown("---")

# ── LIVE SETUP PANEL ────────────────────────────────────────────
if display_next:
    next_date  = datetime.strptime(display_next, "%Y-%m-%d").date()
    days_away  = (next_date - date.today()).days

    last_trade    = all_trades.iloc[-1] if not all_trades.empty else None
    last_swing_lo = last_trade["swing_lo"] if last_trade is not None else None
    last_swing_hi = last_trade["swing_hi"] if last_trade is not None else None
    latest_close  = float(daily_df["close"].iloc[-1]) if not daily_df.empty else None

    long_trades  = active_trades[active_trades["direction"] == "LONG"]
    short_trades = active_trades[active_trades["direction"] == "SHORT"]
    long_wr   = long_trades["win"].mean() * 100  if len(long_trades)  else 0
    short_wr  = short_trades["win"].mean() * 100 if len(short_trades) else 0
    long_avg  = long_trades["pnl_pct"].mean()    if len(long_trades)  else 0
    short_avg = short_trades["pnl_pct"].mean()   if len(short_trades) else 0
    best_dir  = "LONG" if long_wr >= short_wr else "SHORT"
    best_wr   = max(long_wr, short_wr)

    recent_20   = daily_df["volume"].tail(20).mean() if not daily_df.empty else None
    vol_trigger = recent_20 * vol_min if (recent_20 and use_vol) else None

    fib_table_html = ""
    if last_swing_lo and last_swing_hi:
        fib_lvls    = calc_fib_levels(last_swing_lo, last_swing_hi)
        sorted_lvls = sorted(fib_lvls.items(), key=lambda x: x[1])
        fib_rows = []
        for name, lvl in sorted_lvls:
            is_ext    = name.startswith("E")
            type_label = "EXT" if is_ext else "RET"
            type_full  = "Extension" if is_ext else "Retracement"
            if latest_close:
                dist      = (lvl - latest_close) / latest_close * 100
                dist_str  = f"{dist:+.1f}%"
                highlight = "#00e5a0" if abs(dist) <= fib_tol else ("#f5c842" if abs(dist) <= fib_tol * 2 else "#3a3d5c")
            else:
                dist_str, highlight = "—", "#3a3d5c"
            color = "#f5c842" if is_ext else "#4d9fff"
            fib_rows.append(
                f'<tr style="border-bottom:1px solid #0d0f17">'
                f'<td style="padding:4px 10px;white-space:nowrap">'
                f'  <span style="font-size:8px;padding:1px 5px;border-radius:2px;font-weight:700;'
                f'  background:{color}15;color:{color};border:1px solid {color}30">{type_label}</span>'
                f'  <span style="color:#6b7099;font-size:8px;margin-left:3px">{type_full}</span>'
                f'</td>'
                f'<td style="padding:4px 10px;color:{color};font-weight:700">{name[1:]}</td>'
                f'<td style="padding:4px 10px;color:#e8ecff;font-family:monospace">${lvl:.2f}</td>'
                f'<td style="padding:4px 10px;color:{highlight};font-family:monospace">{dist_str}</td></tr>'
            )
        fib_table_html = (
            '<table style="width:100%;border-collapse:collapse;font-size:10px">'
            '<tr style="border-bottom:1px solid #1a1d2e">'
            '<th style="padding:4px 10px;color:#3a3d5c;text-align:left;font-size:8px;letter-spacing:1px">TYPE</th>'
            '<th style="padding:4px 10px;color:#3a3d5c;text-align:left;font-size:8px;letter-spacing:1px">LEVEL</th>'
            '<th style="padding:4px 10px;color:#3a3d5c;text-align:left;font-size:8px;letter-spacing:1px">PRICE</th>'
            '<th style="padding:4px 10px;color:#3a3d5c;text-align:left;font-size:8px;letter-spacing:1px">FROM NOW</th>'
            '</tr>' + "".join(fib_rows) + '</table>'
        )

    days_label = "TODAY" if days_away == 0 else ("TOMORROW" if days_away == 1 else f"in {days_away} days")
    urgency_color = "#ff4d6a" if days_away <= 1 else ("#f5c842" if days_away <= 7 else "#4d9fff")
    lc_str  = f"${latest_close:.2f}" if latest_close else "—"
    vol_str = f"≈{vol_trigger/1e6:.1f}M shares" if vol_trigger else "vol filter off"

    st.markdown(
        f'<div style="background:#0a0b14;border:1px solid {urgency_color}40;border-radius:6px;'
        f'padding:14px 18px 6px;margin-bottom:16px">'
        f'<div style="font-size:9px;color:#6b7099;letter-spacing:2px;margin-bottom:14px">'
        f'🎯 LIVE TRADE SETUP — <b style="color:#e8ecff">{symbol}</b>'
        f' · EARNINGS <b style="color:{urgency_color}">{display_next}</b>'
        f' &nbsp;<span style="color:{urgency_color};font-weight:700">{days_label}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    col_s1, col_s2, col_s3 = st.columns(3)

    with col_s1:
        st.markdown(
            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:4px;padding:14px">'
            f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:10px">📋 TRADE CHECKLIST</div>'
            f'<div style="font-size:10px;line-height:2.4;color:#c8cce8">'
            f'<div>☐ &nbsp;Confirm earnings is <b style="color:#f5c842">AMC</b> on {display_next}</div>'
            f'<div>☐ &nbsp;At <b style="color:#e8ecff">9:30 AM ET</b> — note the open price</div>'
            f'<div>☐ &nbsp;At <b style="color:#00e5a0">1:30 PM ET (12:30 CST)</b> — read 4H candle</div>'
            f'<div>☐ &nbsp;Green → <b style="color:#00e5a0">LONG</b> &nbsp;&nbsp; Red → <b style="color:#ff4d6a">SHORT</b></div>'
            f'<div>☐ &nbsp;Exit day vol ≥ <b style="color:#22d3ee">{vol_min:.1f}x</b> ({vol_str})</div>'
            f'<div>☐ &nbsp;Entry near fib level <b style="color:#a78bfa">±{fib_tol}%</b></div>'
            f'<div>☐ &nbsp;Enter at 1:30 PM ET · exit = <b>next day MOC</b></div>'
            f'</div>'
            f'<div style="margin-top:10px;padding:8px;background:#090b13;border-radius:3px;font-size:9px;color:#6b7099">'
            f'Last close: <b style="color:#e8ecff">{lc_str}</b>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    with col_s2:
        st.markdown(
            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:4px;padding:14px">'
            f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:12px">📊 HISTORICAL EDGE ({symbol} · {stats["n"]} active trades)</div>'
            f'<div style="margin-bottom:12px">'
            f'  <div style="font-size:9px;color:#6b7099;margin-bottom:2px">LONG ({len(long_trades)} trades)</div>'
            f'  <div style="font-size:20px;font-weight:900;color:#00e5a0;font-family:monospace">{long_wr:.0f}%</div>'
            f'  <div style="font-size:9px;color:#6b7099">win rate · avg {long_avg:+.2f}% per trade</div>'
            f'  <div style="height:4px;background:#1a1d2e;border-radius:2px;margin-top:5px">'
            f'    <div style="width:{min(long_wr,100):.0f}%;height:100%;background:#00e5a0;border-radius:2px"></div></div>'
            f'</div>'
            f'<div style="margin-bottom:12px">'
            f'  <div style="font-size:9px;color:#6b7099;margin-bottom:2px">SHORT ({len(short_trades)} trades)</div>'
            f'  <div style="font-size:20px;font-weight:900;color:#ff4d6a;font-family:monospace">{short_wr:.0f}%</div>'
            f'  <div style="font-size:9px;color:#6b7099">win rate · avg {short_avg:+.2f}% per trade</div>'
            f'  <div style="height:4px;background:#1a1d2e;border-radius:2px;margin-top:5px">'
            f'    <div style="width:{min(short_wr,100):.0f}%;height:100%;background:#ff4d6a;border-radius:2px"></div></div>'
            f'</div>'
            f'<div style="padding:8px;background:#090b13;border-radius:3px;font-size:9px">'
            f'  Strongest historical direction: '
            f'  <b style="color:{"#00e5a0" if best_dir=="LONG" else "#ff4d6a"}">{best_dir} ({best_wr:.0f}% WR)</b><br>'
            f'  <span style="color:#3a3d5c">Signal still follows 4H candle on the day.</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    with col_s3:
        lc_label = f" · last ${latest_close:.2f}" if latest_close else ""

        # ── Compute current fib state ──────────────────
        fib_state_html = ""
        if last_swing_lo and last_swing_hi and latest_close:
            rng        = last_swing_hi - last_swing_lo
            # Where is price as % of the swing range?
            pos_pct    = (latest_close - last_swing_lo) / rng * 100 if rng > 0 else 50

            # Which fib zone is price currently sitting in?
            fib_lvls_sorted = sorted(calc_fib_levels(last_swing_lo, last_swing_hi).items(), key=lambda x: x[1])
            zone_below = None  # closest fib level below price
            zone_above = None  # closest fib level above price
            for fname, flvl in fib_lvls_sorted:
                if flvl <= latest_close:
                    zone_below = (fname, flvl)
                else:
                    zone_above = (fname, flvl)
                    break

            # Bull / Bear determination:
            # BULLISH: price is in a RET zone (below swing high = pulling back, support likely)
            #          and above the 50% retracement (R50.0)
            # BEARISH: price below R50.0 retracement (deep pullback, lost momentum)
            # EXTENDED: price above swing high (in extension territory = EXT zone)
            # BREAKDOWN: price below swing low

            r50  = last_swing_hi - rng * 0.5
            r618 = last_swing_hi - rng * 0.618
            r786 = last_swing_hi - rng * 0.786

            if latest_close > last_swing_hi:
                fib_state       = "EXTENDED BULLISH"
                state_color     = "#00e5a0"
                state_bg        = "rgba(0,229,160,0.06)"
                state_border    = "rgba(0,229,160,0.3)"
                state_icon      = "🚀"
                state_desc      = (f"Price is <b>above the prior swing high</b> (${last_swing_hi:.2f}). "
                                   f"In <b style='color:#f5c842'>Extension territory</b> — momentum is strong but price is stretched. "
                                   f"Watch EXT 127.2% (${last_swing_lo + rng*1.272:.2f}) as next resistance.")
            elif latest_close >= r50:
                fib_state       = "BULLISH"
                state_color     = "#00e5a0"
                state_bg        = "rgba(0,229,160,0.06)"
                state_border    = "rgba(0,229,160,0.3)"
                state_icon      = "📈"
                state_desc      = (f"Price is holding <b>above the 50% retracement</b> (${r50:.2f}). "
                                   f"In healthy pullback zone. Buyers are in control of the prior swing. "
                                   f"Key support: R 61.8% at ${r618:.2f}.")
            elif latest_close >= r618:
                fib_state       = "NEUTRAL / DECISION ZONE"
                state_color     = "#f5c842"
                state_bg        = "rgba(245,200,66,0.06)"
                state_border    = "rgba(245,200,66,0.3)"
                state_icon      = "⚖️"
                state_desc      = (f"Price is between the <b>50% and 61.8% retracement</b>. "
                                   f"This is the golden pocket — a make-or-break zone. "
                                   f"Hold above ${r618:.2f} = bullish. Break below = bearish shift.")
            elif latest_close >= r786:
                fib_state       = "BEARISH"
                state_color     = "#ff4d6a"
                state_bg        = "rgba(255,77,106,0.06)"
                state_border    = "rgba(255,77,106,0.3)"
                state_icon      = "📉"
                state_desc      = (f"Price has retraced <b>below the 61.8%</b>. "
                                   f"Sellers are dominant. Last support at R 78.6% (${r786:.2f}). "
                                   f"A break below here signals full retracement back to swing low.")
            elif latest_close >= last_swing_lo:
                fib_state       = "STRONG BEARISH"
                state_color     = "#ff4d6a"
                state_bg        = "rgba(255,77,106,0.08)"
                state_border    = "rgba(255,77,106,0.4)"
                state_icon      = "🔻"
                state_desc      = (f"Price is below the <b>78.6% retracement</b> — near the swing low (${last_swing_lo:.2f}). "
                                   f"Momentum has fully reversed. Watch for breakdown below ${last_swing_lo:.2f}.")
            else:
                fib_state       = "BREAKDOWN"
                state_color     = "#ff4d6a"
                state_bg        = "rgba(255,77,106,0.1)"
                state_border    = "rgba(255,77,106,0.5)"
                state_icon      = "⚠️"
                state_desc      = (f"Price has broken <b>below the swing low</b> (${last_swing_lo:.2f}). "
                                   f"Prior fib levels are invalidated. Bears in full control.")

            # Position bar
            bar_pct = max(0, min(100, pos_pct))
            # Nearest fib hit
            nearest = None
            min_dist = float("inf")
            for fname, flvl in fib_lvls_sorted:
                d = abs(latest_close - flvl) / flvl * 100
                if d < min_dist:
                    min_dist = d
                    nearest  = (fname, flvl, d)

            nearest_html = ""
            if nearest:
                nc    = "#f5c842" if nearest[0].startswith("E") else "#4d9fff"
                ntype = "Extension" if nearest[0].startswith("E") else "Retracement"
                nearest_html = (
                    f'<div style="font-size:9px;color:#6b7099;margin-top:8px">'
                    f'Nearest level: <b style="color:{nc}">{ntype} {nearest[0][1:]}</b>'
                    f' at ${nearest[1]:.2f}'
                    f' <span style="color:#3a3d5c">({nearest[2]:.1f}% away)</span>'
                    f'</div>'
                )

            fib_state_html = (
                f'<div style="background:{state_bg};border:1px solid {state_border};'
                f'border-radius:4px;padding:12px;margin-bottom:10px">'
                f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:6px">CURRENT FIB STATE</div>'
                f'<div style="font-size:16px;font-weight:900;color:{state_color};margin-bottom:4px">'
                f'{state_icon} {fib_state}</div>'
                f'<div style="font-size:9px;color:#c8cce8;line-height:1.7;margin-bottom:8px">{state_desc}</div>'
                f'<!-- swing position bar -->'
                f'<div style="font-size:8px;color:#3a3d5c;margin-bottom:3px">'
                f'POSITION IN SWING: {pos_pct:.0f}% &nbsp;(low ${last_swing_lo:.2f} → high ${last_swing_hi:.2f})</div>'
                f'<div style="position:relative;height:8px;background:#1a1d2e;border-radius:4px">'
                f'  <div style="position:absolute;left:{bar_pct:.0f}%;top:-2px;width:12px;height:12px;'
                f'  border-radius:50%;background:{state_color};transform:translateX(-50%);'
                f'  border:2px solid #07080d"></div>'
                f'  <!-- fib ticks on bar -->'
                f'  <div style="position:absolute;left:23.6%;top:0;width:1px;height:100%;background:#4d9fff40"></div>'
                f'  <div style="position:absolute;left:38.2%;top:0;width:1px;height:100%;background:#4d9fff40"></div>'
                f'  <div style="position:absolute;left:50%;top:0;width:1px;height:100%;background:#4d9fff60"></div>'
                f'  <div style="position:absolute;left:61.8%;top:0;width:1px;height:100%;background:#4d9fff80"></div>'
                f'  <div style="position:absolute;left:78.6%;top:0;width:1px;height:100%;background:#4d9fff40"></div>'
                f'</div>'
                f'{nearest_html}'
                f'</div>'
            )

        st.markdown(
            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;border-radius:4px;padding:14px">'
            f'<div style="font-size:8px;color:#3a3d5c;letter-spacing:1.5px;margin-bottom:8px">'
            f'📐 FIB STATE &amp; LEVELS{lc_label}</div>'
            + fib_state_html
            + (fib_table_html if fib_table_html else '<div style="color:#3a3d5c;font-size:10px;padding:8px">No swing data available</div>')
            + f'<div style="font-size:8px;margin-top:10px;line-height:2;border-top:1px solid #1a1d2e;padding-top:8px">'
            f'<span style="background:#4d9fff15;color:#4d9fff;border:1px solid #4d9fff30;padding:1px 6px;border-radius:2px;font-weight:700;font-size:8px">RET</span>'
            f' <span style="color:#6b7099">Retracement</span> — price pulling back <i>into</i> prior range<br>'
            f'<span style="background:#f5c84215;color:#f5c842;border:1px solid #f5c84230;padding:1px 6px;border-radius:2px;font-weight:700;font-size:8px">EXT</span>'
            f' <span style="color:#6b7099">Extension</span> — price extended <i>beyond</i> prior range<br>'
            f'<span style="color:#00e5a0">■</span> <span style="color:#6b7099">green = within ±{fib_tol}% of entry price</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

# ── Tabs ──────────────────────────────────────────────
tab_curve, tab_bars, tab_fib, tab_log = st.tabs([
    "📈 Equity Curve", "📊 P&L per Trade", "📐 Fib Analysis", "📋 Trade Log"
])

with tab_curve:
    if not active_trades.empty:
        st.plotly_chart(equity_chart(active_trades, stats), use_container_width=True)
        st.caption(f"$100 compounded · {len(active_trades)} trades · entry = 4H candle close on report day")

with tab_bars:
    st.plotly_chart(pnl_bar_chart(all_trades), use_container_width=True)
    st.caption("Colored = active trades · Grey = filtered out")

with tab_fib:
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Entry position within prior earnings swing**")
        for _, row in all_trades.iterrows():
            if not row["swing_lo"] or not row["swing_hi"]:
                continue
            rng = row["swing_hi"] - row["swing_lo"]
            ep  = (row["entry"] - row["swing_lo"]) / rng * 100 if rng > 0 else 50

            dir_color = GREEN if row["direction"] == "LONG" else RED
            fib_badge = ""
            if row["fib_hit"]:
                fname     = row["fib_hit"][0]
                is_ext    = fname.startswith("E")
                fc        = YELLOW if is_ext else BLUE
                type_word = "Ext" if is_ext else "Ret"
                fib_badge = (
                    f' <span style="font-size:8px;padding:1px 5px;border-radius:2px;font-weight:700;'
                    f'background:{fc}15;color:{fc};border:1px solid {fc}30">{type_word}</span>'
                    f' <span style="color:{fc};font-size:9px">{fname[1:]}</span>'
                )

            opacity = 1.0 if row["passes_all"] else 0.35
            st.markdown(
                f'<div style="opacity:{opacity};margin-bottom:6px;padding:8px 12px;'
                f'background:#0d0f17;border-left:2px solid {"#1a1d2e" if not row["passes_all"] else dir_color};'
                f'border:1px solid #1a1d2e;border-radius:3px">'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:5px">'
                f'<span style="color:#e8ecff;font-size:10px;font-weight:700">{row["q"]}</span>'
                f'<span style="color:{dir_color};font-size:9px;font-weight:700">{row["direction"]}{fib_badge}</span>'
                f'</div>'
                f'<div style="height:5px;background:#1a1d2e;border-radius:2px;position:relative">'
                f'<div style="position:absolute;left:{min(100,max(0,ep)):.0f}%;top:-3px;width:11px;height:11px;'
                f'border-radius:50%;background:{"#00e5a0" if row["fib_hit"] else "#ff4d6a"};'
                f'transform:translateX(-50%);border:2px solid #07080d"></div>'
                f'</div>'
                f'<div style="display:flex;justify-content:space-between;font-size:8px;color:#3a3d5c;margin-top:4px">'
                f'<span>${row["swing_lo"]:.2f}</span>'
                f'<span style="color:#6b7099">${row["entry"]:.2f}</span>'
                f'<span>${row["swing_hi"]:.2f}</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    with col_b:
        st.markdown("**Fib level hit frequency**")
        fig_fib = fib_freq_chart(all_trades)
        if fig_fib:
            st.plotly_chart(fig_fib, use_container_width=True)
        else:
            st.info(f"No fib hits within ±{fib_tol}% tolerance.")

        st.markdown("**4H candle direction accuracy (active trades)**")
        for d, dc in [("LONG", GREEN), ("SHORT", RED)]:
            dt = active_trades[active_trades["direction"] == d]
            if dt.empty: continue
            dw = dt["win"].sum()
            wr = dw / len(dt) * 100
            st.markdown(
                f'<div style="margin-bottom:8px">'
                f'<div style="display:flex;justify-content:space-between;font-size:9px;margin-bottom:3px">'
                f'<span style="color:{dc};font-weight:700">{d}</span>'
                f'<span style="color:#6b7099">{dw}W/{len(dt)-dw}L · {wr:.0f}%</span></div>'
                f'<div style="height:4px;background:#1a1d2e;border-radius:2px">'
                f'<div style="width:{wr:.0f}%;height:100%;background:{dc};border-radius:2px"></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

with tab_log:
    display_df = all_trades[[
        "q","report_date","entry_date","exit_date","direction","candle_type",
        "entry","exit","pnl_pct","vol_ratio","passes_all"
    ]].copy()

    def style_row(row):
        base = "color: #c8cce8; font-family: monospace; font-size: 11px;"
        if not row["passes_all"]:
            return [base + "opacity: 0.35;"] * len(row)
        if row["pnl_pct"] > 0:
            return [base] * len(row)
        return [base] * len(row)

    st.dataframe(
        display_df.style
            .format({
                "entry":    "${:.2f}",
                "exit":     "${:.2f}",
                "pnl_pct":  "{:+.2f}%",
                "vol_ratio":"{:.2f}x",
            })
            .apply(style_row, axis=1)
            .applymap(lambda v: f"color: {GREEN}; font-weight: bold" if isinstance(v, str) and "+" in v and "%" in v and float(v.replace("+","").replace("%","")) > 0 else (f"color: {RED}; font-weight: bold" if isinstance(v, str) and "%" in v and v.startswith("-") else ""), subset=["pnl_pct"])
            .applymap(lambda v: f"color: {GREEN}; font-weight: bold" if v == "LONG" else (f"color: {RED}; font-weight: bold" if v == "SHORT" else ""), subset=["direction"]),
        use_container_width=True,
        height=450,
    )

    # Footer stats
    st.markdown(
        f'<div style="margin-top:8px;padding:10px;background:#0d0f17;border:1px solid #1a1d2e;'
        f'border-radius:4px;font-size:10px;color:#6b7099;display:flex;gap:20px">'
        f'<span>Compounded ({len(active_trades)} trades):</span>'
        f'<b style="color:{"#00e5a0" if profit else "#ff4d6a"}">{stats["total_return"]:+.2f}%</b>'
        f'</div>',
        unsafe_allow_html=True,
    )

st.markdown("---")
st.markdown(
    '<div style="font-size:8px;color:#3a3d5c;line-height:2">'
    '⚠ Data from Polygon.io (real adjusted OHLCV). Earnings dates from SEC filing dates via Polygon vX financials — '
    'may differ slightly from actual announcement date. 4H candle = 9:30–1:30 ET on report day. '
    'Fib swing = high/low between prior earnings exit and current report date. '
    'Exit = next trading day close. No slippage/commission. Not financial advice.'
    '</div>',
    unsafe_allow_html=True,
)