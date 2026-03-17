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

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

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


@st.cache_data(ttl=300, show_spinner=False)
def _safe_int(val):
    """Convert a value to int, treating NaN/None as 0."""
    if val is None:
        return 0
    try:
        if pd.isna(val):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _safe_float(val):
    """Convert a value to float, treating NaN/None as 0."""
    if val is None:
        return 0.0
    try:
        if pd.isna(val):
            return 0.0
    except (TypeError, ValueError):
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def get_options_bias_yfinance(ticker):
    """
    Fetch options chain data via yfinance (free, no API key needed).
    Scans multiple expirations to build an accurate picture of OI and volume.
    Uses delta-aware analysis: classifies calls/puts by moneyness to separate
    speculative bets from hedges/covered positions for more accurate sentiment.
    Returns dict with put/call ratio, sentiment, unusual volume, delta analysis, and details.
    """
    if not YFINANCE_AVAILABLE:
        return {"error": "yfinance not installed", "debug": []}

    debug_info = []
    try:
        stock = yf.Ticker(ticker)
        expirations = stock.options
        if not expirations:
            return {"error": "No options expirations found", "debug": ["No expirations returned by yfinance"]}

        # Get current price for moneyness calculations
        current_price = None
        try:
            hist = stock.history(period="1d")
            if not hist.empty:
                current_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass
        if current_price is None or current_price <= 0:
            try:
                info = stock.info
                current_price = float(info.get("regularMarketPrice") or info.get("previousClose", 0))
            except Exception:
                current_price = 0

        debug_info.append(f"Total expirations available: {len(expirations)}")
        debug_info.append(f"Current price for delta calc: ${current_price:.2f}" if current_price else "Price unavailable")

        # Skip same-day / expiring-today expirations (OI drains to 0)
        today_str = str(date.today())
        valid_exps = [e for e in expirations if e > today_str]
        if not valid_exps:
            valid_exps = expirations  # fallback if all are today or past

        # Use up to 6 nearest future expirations for a broader, more accurate view
        use_exps = valid_exps[:6]
        debug_info.append(f"Using expirations: {', '.join(use_exps)}")

        all_calls = []
        all_puts = []
        all_with_volume = []

        for exp in use_exps:
            try:
                chain = stock.option_chain(exp)
            except Exception:
                continue
            calls_df = chain.calls
            puts_df = chain.puts

            for _, row in calls_df.iterrows():
                vol = _safe_int(row.get("volume"))
                oi = _safe_int(row.get("openInterest"))
                strike = _safe_float(row.get("strike"))
                last = _safe_float(row.get("lastPrice"))
                bid = _safe_float(row.get("bid"))
                ask = _safe_float(row.get("ask"))
                iv = _safe_float(row.get("impliedVolatility"))
                itm = bool(row.get("inTheMoney", False))
                all_calls.append({"volume": vol, "oi": oi, "strike": strike,
                                  "last_price": last, "bid": bid, "ask": ask,
                                  "iv": iv, "itm": itm, "expiry": exp})
                if vol > 0 or oi > 0:
                    vol_oi_ratio = vol / oi if oi > 0 else 0
                    # Only flag unusual when OI > 0 (ratio is meaningful)
                    is_unusual = (oi > 0 and (
                        (vol > 1000) or
                        (vol_oi_ratio > 2 and vol > 100) or
                        (vol > oi and vol > 500)
                    ))
                    all_with_volume.append({
                        "strike": strike, "expiry": exp, "type": "CALL",
                        "volume": vol, "oi": oi,
                        "vol_oi_ratio": round(vol_oi_ratio, 1) if oi > 0 else 0,
                        "last_price": last, "is_unusual": is_unusual,
                        "iv": iv, "itm": itm,
                    })

            for _, row in puts_df.iterrows():
                vol = _safe_int(row.get("volume"))
                oi = _safe_int(row.get("openInterest"))
                strike = _safe_float(row.get("strike"))
                last = _safe_float(row.get("lastPrice"))
                bid = _safe_float(row.get("bid"))
                ask = _safe_float(row.get("ask"))
                iv = _safe_float(row.get("impliedVolatility"))
                itm = bool(row.get("inTheMoney", False))
                all_puts.append({"volume": vol, "oi": oi, "strike": strike,
                                 "last_price": last, "bid": bid, "ask": ask,
                                 "iv": iv, "itm": itm, "expiry": exp})
                if vol > 0 or oi > 0:
                    vol_oi_ratio = vol / oi if oi > 0 else 0
                    is_unusual = (oi > 0 and (
                        (vol > 1000) or
                        (vol_oi_ratio > 2 and vol > 100) or
                        (vol > oi and vol > 500)
                    ))
                    all_with_volume.append({
                        "strike": strike, "expiry": exp, "type": "PUT",
                        "volume": vol, "oi": oi,
                        "vol_oi_ratio": round(vol_oi_ratio, 1) if oi > 0 else 0,
                        "last_price": last, "is_unusual": is_unusual,
                        "iv": iv, "itm": itm,
                    })

        total_calls = len(all_calls)
        total_puts = len(all_puts)
        call_oi = sum(c["oi"] for c in all_calls)
        put_oi = sum(p["oi"] for p in all_puts)
        call_volume = sum(c["volume"] for c in all_calls)
        put_volume = sum(p["volume"] for p in all_puts)
        total_oi = call_oi + put_oi
        total_volume = call_volume + put_volume

        debug_info.append(f"Calls: {total_calls}, Puts: {total_puts}")
        debug_info.append(f"Call OI: {call_oi}, Put OI: {put_oi}")
        debug_info.append(f"Call Vol: {call_volume}, Put Vol: {put_volume}")

        # If total OI is 0 across all expirations, data is unreliable
        if total_oi == 0:
            debug_info.append("WARNING: Total OI is 0 — data may be stale or unavailable")

        pc_ratio = total_puts / total_calls if total_calls > 0 else 0
        oi_pc_ratio = put_oi / call_oi if call_oi > 0 else 0
        vol_pc_ratio = put_volume / call_volume if call_volume > 0 else 0

        # ── Delta-Aware Analysis ──
        # Classify options by moneyness to determine true directional intent.
        # Not all calls are bullish; not all puts are bearish:
        #   - Deep ITM calls (delta ~0.8-1.0) are often covered calls / hedges (neutral/bearish)
        #   - Far OTM puts (delta ~0.05-0.15) with high OI are often protective hedges (not bearish)
        #   - Near-ATM options (delta ~0.4-0.6) are the most directionally meaningful
        #   - OTM calls = speculative bullish; OTM puts = speculative bearish
        delta_analysis = {
            "spec_bull_oi": 0, "spec_bull_vol": 0,  # OTM calls (speculative bullish)
            "spec_bear_oi": 0, "spec_bear_vol": 0,  # Near-ATM & slightly OTM puts (speculative bearish)
            "hedge_call_oi": 0, "hedge_call_vol": 0,  # Deep ITM calls (likely covered calls)
            "hedge_put_oi": 0, "hedge_put_vol": 0,    # Far OTM puts (likely protective hedges)
            "atm_call_oi": 0, "atm_call_vol": 0,      # Near-ATM calls (directional bullish)
            "atm_put_oi": 0, "atm_put_vol": 0,        # Near-ATM puts (directional bearish)
        }

        if current_price and current_price > 0:
            for c in all_calls:
                strike = c["strike"]
                moneyness = (strike - current_price) / current_price  # +ve = OTM, -ve = ITM for calls
                if moneyness < -0.10:
                    # Deep ITM call (delta > ~0.85) — likely covered call / stock replacement
                    delta_analysis["hedge_call_oi"] += c["oi"]
                    delta_analysis["hedge_call_vol"] += c["volume"]
                elif -0.05 <= moneyness <= 0.05:
                    # Near ATM (delta ~0.4-0.6) — directional bullish
                    delta_analysis["atm_call_oi"] += c["oi"]
                    delta_analysis["atm_call_vol"] += c["volume"]
                elif moneyness > 0.05:
                    # OTM call (delta < ~0.4) — speculative bullish
                    delta_analysis["spec_bull_oi"] += c["oi"]
                    delta_analysis["spec_bull_vol"] += c["volume"]

            for p in all_puts:
                strike = p["strike"]
                moneyness = (current_price - strike) / current_price  # +ve = OTM, -ve = ITM for puts
                if moneyness > 0.15:
                    # Far OTM put (delta < ~0.15) — likely protective hedge
                    delta_analysis["hedge_put_oi"] += p["oi"]
                    delta_analysis["hedge_put_vol"] += p["volume"]
                elif -0.05 <= moneyness <= 0.10:
                    # Near ATM / slightly OTM put (delta ~0.3-0.6) — speculative bearish
                    delta_analysis["spec_bear_oi"] += p["oi"]
                    delta_analysis["spec_bear_vol"] += p["volume"]
                elif moneyness < -0.05:
                    # Deep ITM put — directional bearish or assignment risk
                    delta_analysis["atm_put_oi"] += p["oi"]
                    delta_analysis["atm_put_vol"] += p["volume"]

            # Delta-adjusted sentiment: weight speculative + ATM flow, discount hedges
            directional_bull = (delta_analysis["spec_bull_oi"] + delta_analysis["atm_call_oi"]) * 1.0
            directional_bear = (delta_analysis["spec_bear_oi"] + delta_analysis["atm_put_oi"]) * 1.0
            # Hedges get 25% weight — they indicate institutional positioning but not aggression
            hedge_adjustment = delta_analysis["hedge_call_oi"] * 0.25 + delta_analysis["hedge_put_oi"] * 0.25

            total_directional = directional_bull + directional_bear + hedge_adjustment
            if total_directional > 0:
                delta_bull_pct = directional_bull / total_directional
                delta_bear_pct = directional_bear / total_directional
            else:
                delta_bull_pct = 0.5
                delta_bear_pct = 0.5

            if delta_bull_pct > 0.60:
                delta_sentiment = "BULLISH"
                delta_color = "#00e5a0"
                delta_desc = f"Speculative + ATM call flow dominates ({delta_bull_pct*100:.0f}% bullish)"
            elif delta_bear_pct > 0.60:
                delta_sentiment = "BEARISH"
                delta_color = "#ff4d6a"
                delta_desc = f"Speculative + ATM put flow dominates ({delta_bear_pct*100:.0f}% bearish)"
            else:
                delta_sentiment = "NEUTRAL"
                delta_color = "#f5c842"
                delta_desc = f"Mixed directional flow ({delta_bull_pct*100:.0f}% bull / {delta_bear_pct*100:.0f}% bear)"

            debug_info.append(f"Delta analysis: Bull OI={directional_bull:.0f}, Bear OI={directional_bear:.0f}, "
                             f"Hedge Calls={delta_analysis['hedge_call_oi']}, Hedge Puts={delta_analysis['hedge_put_oi']}")
        else:
            delta_sentiment = "N/A"
            delta_color = "#6b7099"
            delta_desc = "Price unavailable for delta analysis"

        # ── Standard (raw) sentiment based on total OI ──
        if total_oi == 0:
            sentiment = "N/A"
            sentiment_color = "#6b7099"
            sentiment_desc = "No open interest data available"
        elif oi_pc_ratio < 0.7:
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

        # Only show contracts that have real OI for the activity table
        with_real_data = [x for x in all_with_volume if x["oi"] > 0 or x["volume"] > 5]
        with_real_data.sort(key=lambda x: (x.get("oi", 0), x.get("volume", 0)), reverse=True)
        top_volume = with_real_data[:15]
        unusual_activity = [x for x in top_volume if x.get("is_unusual")]
        debug_info.append(f"Contracts with OI or vol>5: {len(with_real_data)}, unusual: {len(unusual_activity)}")

        # Add moneyness label to top volume / unusual contracts
        if current_price and current_price > 0:
            for item in top_volume:
                strike = item["strike"]
                if item["type"] == "CALL":
                    m = (strike - current_price) / current_price
                    if m < -0.10:
                        item["moneyness"] = "Deep ITM"
                        item["intent"] = "Hedge/Cover"
                    elif -0.05 <= m <= 0.05:
                        item["moneyness"] = "ATM"
                        item["intent"] = "Directional"
                    else:
                        item["moneyness"] = "OTM"
                        item["intent"] = "Speculative"
                else:  # PUT
                    m = (current_price - strike) / current_price
                    if m > 0.15:
                        item["moneyness"] = "Far OTM"
                        item["intent"] = "Hedge/Protect"
                    elif -0.05 <= m <= 0.10:
                        item["moneyness"] = "ATM/Near"
                        item["intent"] = "Directional"
                    else:
                        item["moneyness"] = "Deep ITM"
                        item["intent"] = "Directional"

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
            "delta_sentiment": delta_sentiment,
            "delta_color": delta_color,
            "delta_desc": delta_desc,
            "delta_analysis": delta_analysis,
            "unusual_activity": unusual_activity,
            "top_volume": top_volume,
            "debug": debug_info,
        }
    except Exception as e:
        return {"error": str(e)[:100], "debug": debug_info}


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


def calc_support_resistance(daily_df, n_levels=5):
    """
    Calculate support and resistance levels from multiple methods:
    1. Pivot points (classic floor trader pivots)
    2. Recent swing highs/lows (fractals)
    3. Volume-weighted price clusters (VWAP-like)
    4. Round-number / psychological levels
    Returns dict with support_levels, resistance_levels (sorted, nearest first),
    and key_level (strongest confluence zone).
    """
    if daily_df is None or len(daily_df) < 20:
        return None

    current_price = float(daily_df["close"].iloc[-1])
    hi = float(daily_df["high"].iloc[-1])
    lo = float(daily_df["low"].iloc[-1])
    cl = current_price

    # ── 1. Classic Pivot Points ──
    pivot = (hi + lo + cl) / 3
    r1 = 2 * pivot - lo
    s1 = 2 * pivot - hi
    r2 = pivot + (hi - lo)
    s2 = pivot - (hi - lo)
    r3 = hi + 2 * (pivot - lo)
    s3 = lo - 2 * (hi - pivot)

    raw_supports = [s1, s2, s3]
    raw_resistances = [r1, r2, r3]

    # ── 2. Swing Highs/Lows (fractal pivots over last 60 days) ──
    lookback = min(60, len(daily_df))
    recent = daily_df.tail(lookback)
    swing_highs = []
    swing_lows = []
    highs = recent["high"].values
    lows = recent["low"].values
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(float(highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(float(lows[i]))

    for sh in swing_highs:
        if sh > current_price:
            raw_resistances.append(sh)
        else:
            raw_supports.append(sh)
    for sl in swing_lows:
        if sl < current_price:
            raw_supports.append(sl)
        else:
            raw_resistances.append(sl)

    # ── 3. Volume-Weighted Price Clusters ──
    if "volume" in daily_df.columns and len(daily_df) >= 20:
        recent_vol = daily_df.tail(20)
        # Find price level with highest volume (high-volume node)
        vwap_price = (recent_vol["close"] * recent_vol["volume"]).sum() / recent_vol["volume"].sum()
        if vwap_price < current_price:
            raw_supports.append(float(vwap_price))
        else:
            raw_resistances.append(float(vwap_price))
        # Highest volume day's price range midpoint
        max_vol_idx = recent_vol["volume"].idxmax()
        hvn = float((recent_vol.loc[max_vol_idx, "high"] + recent_vol.loc[max_vol_idx, "low"]) / 2)
        if hvn < current_price:
            raw_supports.append(hvn)
        else:
            raw_resistances.append(hvn)

    # ── 4. Round Numbers ──
    magnitude = 10 ** max(0, len(str(int(current_price))) - 2)
    round_below = int(current_price / magnitude) * magnitude
    round_above = round_below + magnitude
    if round_below > 0 and round_below < current_price:
        raw_supports.append(float(round_below))
    if round_above > current_price:
        raw_resistances.append(float(round_above))
    # Half-round
    half = magnitude / 2
    half_below = int(current_price / half) * half
    half_above = half_below + half
    if half_below > 0 and half_below < current_price:
        raw_supports.append(float(half_below))
    if half_above > current_price:
        raw_resistances.append(float(half_above))

    # ── Deduplicate & cluster nearby levels (within 0.3% of each other) ──
    def cluster_levels(levels, price, ascending=True):
        if not levels:
            return []
        levels = sorted(set(round(l, 2) for l in levels if l > 0))
        clustered = []
        for lv in levels:
            merged = False
            for i, (cv, cc) in enumerate(clustered):
                if abs(lv - cv) / cv < 0.003:  # within 0.3%
                    # Merge — keep the one closer to price, but count confluence
                    clustered[i] = ((cv * cc + lv) / (cc + 1), cc + 1)
                    merged = True
                    break
            if not merged:
                clustered.append((lv, 1))
        # Sort by distance from current price, break ties by confluence count
        clustered.sort(key=lambda x: (abs(x[0] - price), -x[1]))
        return [{"price": round(c[0], 2), "strength": c[1]} for c in clustered[:n_levels]]

    support_levels = cluster_levels([s for s in raw_supports if s < current_price], current_price)
    resistance_levels = cluster_levels([r for r in raw_resistances if r > current_price], current_price)

    # Sort supports descending (nearest first), resistances ascending (nearest first)
    support_levels.sort(key=lambda x: -x["price"])
    resistance_levels.sort(key=lambda x: x["price"])

    # Key level = strongest confluence
    all_levels = support_levels + resistance_levels
    key_level = max(all_levels, key=lambda x: x["strength"]) if all_levels else None

    return {
        "pivot": round(pivot, 2),
        "supports": support_levels[:n_levels],
        "resistances": resistance_levels[:n_levels],
        "key_level": key_level,
    }


# ──────────────────────────────────────────────
# STRATEGY ANALYSIS (Fib + FVG + Weinstein + Bias)
# ──────────────────────────────────────────────

def analyze_strategy_signals(daily_df, lookback=50, fib_vol_threshold=1.2):
    """
    Analyze price data using Fib + FVG + Weinstein + Bias strategy.
    Returns dict with signal analysis including SHORT conditions.
    """
    if daily_df is None or len(daily_df) < lookback + 10:
        return {"error": "Insufficient data", "short_signal": False}
    
    df = daily_df.copy()
    df = df.sort_index()
    
    # Ensure we have enough data
    if len(df) < lookback:
        return {"error": "Insufficient data", "short_signal": False}
    
    # ─── SWING HIGH/LOW ───
    hh = df["high"].rolling(lookback).max().iloc[-1]
    ll = df["low"].rolling(lookback).min().iloc[-1]
    swing_range = hh - ll
    
    # Find bar positions of swing high/low
    recent_window = df.tail(lookback)
    bar_hh = len(recent_window) - recent_window["high"].values[::-1].argmax() - 1
    bar_ll = len(recent_window) - recent_window["low"].values[::-1].argmin() - 1
    
    is_bearish_swing = bar_ll > bar_hh  # Recent low is more recent than recent high
    is_bullish_swing = bar_hh > bar_ll
    
    # ─── VOLUME ANALYSIS ───
    avg_vol = df["volume"].rolling(20).mean().iloc[-1]
    current_vol = df["volume"].iloc[-1]
    high_volume = current_vol > (avg_vol * fib_vol_threshold)
    
    # ─── BUYER/SELLER CONVICTION ───
    bar_range = df["high"].iloc[-1] - df["low"].iloc[-1]
    close_position = (df["close"].iloc[-1] - df["low"].iloc[-1]) / bar_range if bar_range > 0 else 0.5
    
    # Candle direction (open vs close)
    current_open = df["open"].iloc[-1]
    current_close = df["close"].iloc[-1]
    is_green_candle = current_close > current_open  # Bullish candle
    is_red_candle = current_close < current_open    # Bearish candle
    candle_body_pct = abs(current_close - current_open) / current_open * 100 if current_open > 0 else 0
    
    # Conviction requires: high volume + close position + candle direction alignment
    buyer_conviction = high_volume and close_position >= 0.5 and is_green_candle
    seller_conviction = high_volume and close_position < 0.5 and is_red_candle
    
    # Previous bar conviction
    prev_range = df["high"].iloc[-2] - df["low"].iloc[-2]
    prev_close_pos = (df["close"].iloc[-2] - df["low"].iloc[-2]) / prev_range if prev_range > 0 else 0.5
    prev_green = df["close"].iloc[-2] > df["open"].iloc[-2]
    prev_red = df["close"].iloc[-2] < df["open"].iloc[-2]
    strong_sellers = seller_conviction and prev_close_pos < 0.5 and prev_red
    strong_buyers = buyer_conviction and prev_close_pos >= 0.5 and prev_green
    
    # ─── TREND DIRECTION (simplified ZigZag) ───
    # Check if price is making lower highs and lower lows
    recent_highs = df["high"].tail(10).values
    recent_lows = df["low"].tail(10).values
    
    is_downtrend = (recent_highs[-1] < recent_highs[0] and 
                    recent_lows[-1] < recent_lows[0])
    is_uptrend = (recent_highs[-1] > recent_highs[0] and 
                  recent_lows[-1] > recent_lows[0])
    
    # ─── WEINSTEIN ANALYSIS ───
    ma30 = df["close"].rolling(30).mean()
    ma10 = df["close"].rolling(10).mean()
    
    current_price = df["close"].iloc[-1]
    current_ma30 = ma30.iloc[-1]
    current_ma10 = ma10.iloc[-1]
    
    # MA30 slope
    ma30_slope = (current_ma30 - ma30.iloc[-10]) / ma30.iloc[-10] if ma30.iloc[-10] > 0 else 0
    ma_is_flat = abs(ma30_slope) < 0.08
    
    # 52-period high/low
    high_52 = df["high"].tail(52).max()
    low_52 = df["low"].tail(52).min()
    range_52 = high_52 - low_52
    price_position = ((current_price - low_52) / range_52 * 100) if range_52 > 0 else 0
    dist_from_high = ((high_52 - current_price) / current_price * 100) if current_price > 0 else 0
    
    # Relative Strength vs SPY (simplified - just use price change)
    stock_change = current_price / df["close"].iloc[-50] if len(df) >= 50 else 1
    rs_improving = stock_change < 1  # For shorts, we want declining RS
    
    # Volume building
    avg_vol_10 = df["volume"].tail(10).mean()
    avg_vol_4 = df["volume"].tail(4).mean()
    volume_building = avg_vol_4 > avg_vol_10 * 1.1
    
    # MA relationships
    ma10_above_ma30 = current_ma10 > current_ma30
    ma10_below_ma30 = current_ma10 < current_ma30
    ma_turning_down = current_ma30 < ma30.iloc[-2] < ma30.iloc[-4]
    near_ma30 = current_price > current_ma30 * 0.90 and current_price < current_ma30 * 1.15
    
    # Weinstein breakout score (for shorts, we want LOW score)
    score_ma30_curling = 1 if ma_turning_down else 0
    score_ma10_cross = 0 if ma10_below_ma30 else 1
    score_rs_negative = 1 if stock_change < 1 else 0
    score_vol_building = 1 if volume_building else 0
    score_near_low = 1 if dist_from_high > 15 else 0
    
    breakout_score = score_ma10_cross + score_vol_building + (1 - score_near_low)
    breakdown_score = score_ma30_curling + (1 - score_ma10_cross) + score_rs_negative + score_vol_building + score_near_low
    
    # ─── BIAS ANALYSIS ───
    # Compare current close to recent swing point
    swing_low_price = df["low"].tail(20).min()
    swing_high_price = df["high"].tail(20).max()
    
    is_bullish_bias = current_price > (swing_low_price + swing_high_price) / 2
    is_bearish_bias = current_price < (swing_low_price + swing_high_price) / 2
    
    # ─── FVG (Fair Value Gap) DETECTION ───
    # Bearish FVG: gap down (high[1] < low[3])
    has_bearish_fvg = False
    fvg_details = None
    
    if len(df) >= 4:
        for i in range(1, min(5, len(df) - 3)):
            if df["high"].iloc[-(i+1)] < df["low"].iloc[-(i+3)]:
                fvg_top = df["low"].iloc[-(i+3)]
                fvg_bottom = df["high"].iloc[-(i+1)]
                fvg_size_pct = (fvg_top - fvg_bottom) / current_price * 100
                if fvg_size_pct >= 0.5:  # At least 0.5% gap
                    has_bearish_fvg = True
                    fvg_details = {
                        "type": "BEARISH",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "size_pct": round(fvg_size_pct, 2)
                    }
                    break
    
    # Check for bullish FVG
    has_bullish_fvg = False
    if len(df) >= 4 and not has_bearish_fvg:
        for i in range(1, min(5, len(df) - 3)):
            if df["low"].iloc[-(i+1)] > df["high"].iloc[-(i+3)]:
                fvg_top = df["low"].iloc[-(i+1)]
                fvg_bottom = df["high"].iloc[-(i+3)]
                fvg_size_pct = (fvg_top - fvg_bottom) / current_price * 100
                if fvg_size_pct >= 0.5:
                    has_bullish_fvg = True
                    fvg_details = {
                        "type": "BULLISH",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "size_pct": round(fvg_size_pct, 2)
                    }
                    break
    
    # ─── SHORT SIGNAL CONDITIONS ───
    # Tier 1: All 4 core conditions aligned bearish
    short_tier1 = (is_bearish_swing and 
                   seller_conviction and 
                   is_downtrend and 
                   is_bearish_bias)
    
    # Tier 2: Trend + bias + weak breakout score
    short_tier2 = (is_bearish_swing and 
                   is_bearish_bias and 
                   breakout_score <= 3 and 
                   not buyer_conviction)
    
    short_signal = short_tier1 or short_tier2
    short_tier = "T1" if short_tier1 else ("T2" if short_tier2 else None)
    
    # ─── LONG SIGNAL CONDITIONS ───
    # Tier 1: Core conditions aligned bullish
    long_tier1 = (is_bullish_swing and 
                  buyer_conviction and 
                  is_uptrend and 
                  is_bullish_bias)
    
    # Tier 2: Trend + bias + good score
    long_tier2 = (is_bullish_swing and 
                  is_bullish_bias and 
                  breakout_score >= 3 and 
                  not seller_conviction)
    
    long_signal = long_tier1 or long_tier2
    long_tier = "T1" if long_tier1 else ("T2" if long_tier2 else None)
    
    # ─── TAKE PROFIT CHECK (if already in position) ───
    # Look back to find potential entry points
    take_profit_pct = 0.10  # 10%
    short_tp_hit = False
    long_tp_hit = False
    
    # Check last 20 bars for potential entry and TP
    for i in range(5, min(20, len(df))):
        past_price = df["close"].iloc[-i]
        # Short take profit: price dropped 10% from entry
        if current_price < past_price * (1 - take_profit_pct):
            short_tp_hit = True
            break
    
    for i in range(5, min(20, len(df))):
        past_price = df["close"].iloc[-i]
        # Long take profit: price rose 10% from entry
        if current_price > past_price * (1 + take_profit_pct):
            long_tp_hit = True
            break
    
    return {
        # Swing analysis
        "swing_high": round(hh, 2),
        "swing_low": round(ll, 2),
        "is_bearish_swing": is_bearish_swing,
        "is_bullish_swing": is_bullish_swing,
        
        # Volume & Candle
        "high_volume": high_volume,
        "volume_ratio": round(current_vol / avg_vol, 2) if avg_vol > 0 else 0,
        "buyer_conviction": buyer_conviction,
        "seller_conviction": seller_conviction,
        "strong_sellers": strong_sellers,
        "strong_buyers": strong_buyers,
        "is_green_candle": is_green_candle,
        "is_red_candle": is_red_candle,
        "candle_body_pct": round(candle_body_pct, 2),
        
        # Trend
        "is_downtrend": is_downtrend,
        "is_uptrend": is_uptrend,
        
        # Weinstein
        "ma30": round(current_ma30, 2),
        "ma10": round(current_ma10, 2),
        "ma10_below_ma30": ma10_below_ma30,
        "ma_turning_down": ma_turning_down,
        "breakout_score": breakout_score,
        "breakdown_score": breakdown_score,
        "price_position": round(price_position, 1),
        "dist_from_high": round(dist_from_high, 1),
        
        # Bias
        "is_bullish_bias": is_bullish_bias,
        "is_bearish_bias": is_bearish_bias,
        
        # FVG
        "has_bearish_fvg": has_bearish_fvg,
        "has_bullish_fvg": has_bullish_fvg,
        "fvg_details": fvg_details,
        
        # Signals
        "short_signal": short_signal,
        "short_tier": short_tier,
        "long_signal": long_signal,
        "long_tier": long_tier,
        
        # Take profit zones
        "short_tp_hit": short_tp_hit,
        "long_tp_hit": long_tp_hit,
    }


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


# ──────────────────────────────────────────────
# FUNDAMENTALS (via yfinance)
# ──────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)  # Cache for 1 hour
def get_fundamentals(ticker):
    """
    Fetch fundamental data via yfinance.
    Returns dict with valuation, growth, profitability, risk, and analyst data.
    """
    if not YFINANCE_AVAILABLE:
        return None
    
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        if not info or "symbol" not in info:
            return None
        
        pe_ratio = info.get("trailingPE") or info.get("forwardPE")
        forward_pe = info.get("forwardPE")
        peg_ratio = info.get("pegRatio")
        market_cap = info.get("marketCap")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        target_price = info.get("targetMeanPrice")
        target_low = info.get("targetLowPrice")
        target_high = info.get("targetHighPrice")
        
        # Growth metrics
        revenue_growth = info.get("revenueGrowth")  # quarterly YoY
        earnings_growth = info.get("earningsGrowth")  # quarterly YoY
        revenue = info.get("totalRevenue")
        
        # Profitability
        profit_margin = info.get("profitMargins")
        gross_margin = info.get("grossMargins")
        operating_margin = info.get("operatingMargins")
        roe = info.get("returnOnEquity")
        roa = info.get("returnOnAssets")
        
        # Risk / Balance sheet
        debt_to_equity = info.get("debtToEquity")
        current_ratio = info.get("currentRatio")
        beta = info.get("beta")
        short_ratio = info.get("shortRatio")
        short_pct = info.get("shortPercentOfFloat")
        
        # Dividend
        dividend_yield = info.get("dividendYield")
        payout_ratio = info.get("payoutRatio")
        
        # Identity
        sector = info.get("sector", "N/A")
        industry = info.get("industry", "N/A")
        
        # 52-week
        week52_high = info.get("fiftyTwoWeekHigh")
        week52_low = info.get("fiftyTwoWeekLow")
        
        # Analyst recommendations
        rec_key = info.get("recommendationKey", "")  # buy, hold, sell, etc.
        rec_mean = info.get("recommendationMean")  # 1=strong buy, 5=sell
        num_analysts = info.get("numberOfAnalystOpinions")
        
        # EPS
        trailing_eps = info.get("trailingEps")
        forward_eps = info.get("forwardEps")
        
        # Calculate upside/downside to target
        if target_price and current_price and current_price > 0:
            target_upside = round(((target_price - current_price) / current_price) * 100, 1)
        else:
            target_upside = None
        
        # 52-week position
        if week52_high and week52_low and current_price:
            week52_range = week52_high - week52_low
            week52_position = ((current_price - week52_low) / week52_range * 100) if week52_range > 0 else 50
            pct_from_high = ((current_price - week52_high) / week52_high * 100) if week52_high else None
        else:
            week52_position = None
            pct_from_high = None
        
        # Format market cap
        if market_cap:
            if market_cap >= 1e12:
                market_cap_str = f"${market_cap / 1e12:.1f}T"
            elif market_cap >= 1e9:
                market_cap_str = f"${market_cap / 1e9:.1f}B"
            elif market_cap >= 1e6:
                market_cap_str = f"${market_cap / 1e6:.1f}M"
            else:
                market_cap_str = f"${market_cap:,.0f}"
        else:
            market_cap_str = "N/A"
        
        # Format revenue
        if revenue:
            if revenue >= 1e12:
                revenue_str = f"${revenue / 1e12:.1f}T"
            elif revenue >= 1e9:
                revenue_str = f"${revenue / 1e9:.1f}B"
            elif revenue >= 1e6:
                revenue_str = f"${revenue / 1e6:.0f}M"
            else:
                revenue_str = f"${revenue:,.0f}"
        else:
            revenue_str = "N/A"
        
        # Valuation assessment based on P/E
        if pe_ratio:
            if pe_ratio < 0:
                valuation = "Negative Earnings"
                valuation_color = "#6b7099"
            elif pe_ratio < 15:
                valuation = "Undervalued"
                valuation_color = "#00e5a0"
            elif pe_ratio <= 25:
                valuation = "Fair Value"
                valuation_color = "#f5c842"
            elif pe_ratio <= 40:
                valuation = "Overvalued"
                valuation_color = "#ff8c42"
            else:
                valuation = "Very Expensive"
                valuation_color = "#ff4d6a"
        else:
            valuation = "N/A"
            valuation_color = "#6b7099"
        
        # Fundamental flags (quick risk/opportunity signals)
        flags = []
        if revenue_growth and revenue_growth > 0.20:
            flags.append(("🚀 High Revenue Growth", "#00e5a0"))
        if revenue_growth and revenue_growth < -0.05:
            flags.append(("📉 Revenue Declining", "#ff4d6a"))
        if earnings_growth and earnings_growth > 0.25:
            flags.append(("💰 Strong Earnings Growth", "#00e5a0"))
        if earnings_growth and earnings_growth < -0.10:
            flags.append(("⚠️ Earnings Declining", "#ff4d6a"))
        if profit_margin and profit_margin > 0.20:
            flags.append(("✅ High Margins", "#00e5a0"))
        if profit_margin and profit_margin < 0:
            flags.append(("🔴 Unprofitable", "#ff4d6a"))
        if debt_to_equity and debt_to_equity > 200:
            flags.append(("⚠️ High Debt", "#ff4d6a"))
        if debt_to_equity is not None and debt_to_equity < 30:
            flags.append(("✅ Low Debt", "#00e5a0"))
        if short_pct and short_pct > 0.10:
            flags.append(("🔥 High Short Interest", "#ff8c42"))
        if dividend_yield and dividend_yield > 0.03:
            flags.append(("💵 Good Dividend", "#00e5a0"))
        if peg_ratio and 0 < peg_ratio < 1:
            flags.append(("🎯 PEG < 1 (Growth Bargain)", "#00e5a0"))
        if target_upside and target_upside > 20:
            flags.append(("📈 Analyst Upside >20%", "#00e5a0"))
        if target_upside and target_upside < -15:
            flags.append(("📉 Analyst Downside >15%", "#ff4d6a"))
        if week52_position and week52_position > 90:
            flags.append(("⚡ Near 52W High", "#f5c842"))
        if week52_position and week52_position < 15:
            flags.append(("📉 Near 52W Low", "#ff8c42"))
        
        return {
            "valuation": valuation,
            "valuation_color": valuation_color,
            "market_cap_str": market_cap_str,
            "target_price": round(target_price, 2) if target_price else None,
            "target_low": round(target_low, 2) if target_low else None,
            "target_high": round(target_high, 2) if target_high else None,
            "target_upside": target_upside,
            # Growth
            "revenue_growth": revenue_growth,
            "earnings_growth": earnings_growth,
            "revenue_str": revenue_str,
            # Valuation
            "pe_ratio": round(pe_ratio, 1) if pe_ratio else None,
            "forward_pe": round(forward_pe, 1) if forward_pe else None,
            "peg_ratio": round(peg_ratio, 2) if peg_ratio else None,
            # Profitability
            "profit_margin": profit_margin,
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "roe": roe,
            "roa": roa,
            # Risk
            "debt_to_equity": round(debt_to_equity, 1) if debt_to_equity else None,
            "current_ratio": round(current_ratio, 2) if current_ratio else None,
            "beta": round(beta, 2) if beta else None,
            "short_ratio": round(short_ratio, 1) if short_ratio else None,
            "short_pct": short_pct,
            # Dividend
            "dividend_yield": dividend_yield,
            "payout_ratio": payout_ratio,
            # Identity
            "sector": sector,
            "industry": industry,
            # 52-week
            "week52_high": round(week52_high, 2) if week52_high else None,
            "week52_low": round(week52_low, 2) if week52_low else None,
            "week52_position": round(week52_position, 1) if week52_position else None,
            "pct_from_high": round(pct_from_high, 1) if pct_from_high else None,
            # Analyst
            "rec_key": rec_key,
            "rec_mean": round(rec_mean, 1) if rec_mean else None,
            "num_analysts": num_analysts,
            # EPS
            "trailing_eps": round(trailing_eps, 2) if trailing_eps else None,
            "forward_eps": round(forward_eps, 2) if forward_eps else None,
            # Flags
            "flags": flags,
        }
    except Exception:
        return None


# ──────────────────────────────────────────────
# SECTOR ANALYSIS
# ──────────────────────────────────────────────

# Sector ETFs with their top holdings
SECTOR_ETFS = {
    "XLK": {"name": "Technology", "emoji": "💻", "stocks": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ADBE", "ORCL", "CSCO", "ACN"]},
    "XLF": {"name": "Financials", "emoji": "🏦", "stocks": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SPGI", "BLK", "AXP"]},
    "XLE": {"name": "Energy", "emoji": "⛽", "stocks": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "HAL"]},
    "XLV": {"name": "Healthcare", "emoji": "🏥", "stocks": ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY"]},
    "XLY": {"name": "Consumer Disc", "emoji": "🛒", "stocks": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG"]},
    "XLI": {"name": "Industrials", "emoji": "🏭", "stocks": ["CAT", "UNP", "HON", "UPS", "BA", "RTX", "DE", "LMT", "GE", "MMM"]},
    "XLP": {"name": "Cons Staples", "emoji": "🧴", "stocks": ["PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "CL", "MDLZ", "EL"]},
    "XLU": {"name": "Utilities", "emoji": "💡", "stocks": ["NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "PEG", "ED"]},
    "XLC": {"name": "Communication", "emoji": "📱", "stocks": ["META", "GOOGL", "GOOG", "NFLX", "DIS", "CMCSA", "VZ", "T", "TMUS", "CHTR"]},
    "XLB": {"name": "Materials", "emoji": "🧱", "stocks": ["LIN", "APD", "SHW", "ECL", "FCX", "NEM", "NUE", "DOW", "DD", "VMC"]},
    "XLRE": {"name": "Real Estate", "emoji": "🏠", "stocks": ["PLD", "AMT", "EQIX", "PSA", "CCI", "O", "WELL", "SPG", "DLR", "AVB"]},
}

@st.cache_data(ttl=300, show_spinner=False)  # 5 min cache
def get_sector_performance(api_key, api_secret, data_source):
    """
    Get performance data for all sector ETFs.
    Returns list of dicts with sector info and performance metrics.
    """
    results = []
    end_date = date.today()
    start_date = end_date - timedelta(days=30)  # Need ~20 trading days
    
    for etf, info in SECTOR_ETFS.items():
        try:
            # Fetch daily data for sector ETF
            if data_source == "Alpaca":
                df = get_daily_bars_alpaca(etf, str(start_date), str(end_date), api_key, api_secret)
            else:
                df = get_daily_bars(etf, str(start_date), str(end_date), api_key)
            
            if df.empty or len(df) < 5:
                continue
            
            current_price = df["close"].iloc[-1]
            
            # 1-day change
            if len(df) >= 2:
                prev_close = df["close"].iloc[-2]
                change_1d = ((current_price - prev_close) / prev_close) * 100
            else:
                change_1d = 0
            
            # 1-week change (5 trading days)
            if len(df) >= 6:
                week_ago = df["close"].iloc[-6]
                change_1w = ((current_price - week_ago) / week_ago) * 100
            else:
                change_1w = change_1d
            
            # 1-month change
            if len(df) >= 20:
                month_ago = df["close"].iloc[-20]
                change_1m = ((current_price - month_ago) / month_ago) * 100
            else:
                change_1m = change_1w
            
            # Momentum score (weighted average)
            momentum = (change_1d * 0.3) + (change_1w * 0.5) + (change_1m * 0.2)
            
            results.append({
                "etf": etf,
                "name": info["name"],
                "emoji": info["emoji"],
                "stocks": info["stocks"],
                "price": round(current_price, 2),
                "change_1d": round(change_1d, 2),
                "change_1w": round(change_1w, 2),
                "change_1m": round(change_1m, 2),
                "momentum": round(momentum, 2),
            })
        except Exception:
            continue
    
    # Sort by momentum (best first)
    results.sort(key=lambda x: x["momentum"], reverse=True)
    return results


# ──────────────────────────────────────────────
# STOCK SCANNER
# ──────────────────────────────────────────────

# Popular stocks to scan
SCAN_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "CRM",
    "ORCL", "ADBE", "INTC", "PYPL", "SQ", "SHOP", "COIN", "UBER", "ABNB", "SNOW",
    "BA", "CAT", "GS", "JPM", "V", "MA", "DIS", "NKE", "SBUX", "MCD",
    "XOM", "CVX", "PFE", "JNJ", "UNH", "MRNA", "LLY", "ABBV", "BMY", "MRK",
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "ARKK", "SOXX", "SMH"
]

def scan_single_stock(ticker, api_key, api_secret, data_source, use_fib=True, fib_tol=2.0, use_strategy=False):
    """
    Scan a single stock and return verdict/confidence.
    Returns dict with ticker, verdict, confidence, score, signals or None on error.
    """
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=365)  # 1 year for Fib levels
        
        # Fetch daily data
        if data_source == "Alpaca":
            daily_df = get_daily_bars_alpaca(ticker, str(start_date), str(end_date), api_key, api_secret)
        else:
            daily_df = get_daily_bars(ticker, str(start_date), str(end_date), api_key)
        
        if daily_df.empty or len(daily_df) < 20:
            return None
        
        current_price = daily_df["close"].iloc[-1]
        
        # 4H candle for today (if market is open) or last trading day
        hourly_df = None
        candle_4h_bias = "N/A"
        try:
            hourly_start = end_date - timedelta(days=7)
            if data_source == "Alpaca":
                hourly_df = get_hourly_bars_alpaca(ticker, str(hourly_start), str(end_date), api_key, api_secret)
            else:
                hourly_df = get_hourly_bars(ticker, str(hourly_start), str(end_date), api_key)
            
            if hourly_df is not None and not hourly_df.empty:
                # Try today, then yesterday
                for check_date in [end_date, end_date - timedelta(days=1), end_date - timedelta(days=2)]:
                    candle_4h = get_4h_noon_candle(ticker, check_date, hourly_df)
                    if candle_4h:
                        if candle_4h["close"] > candle_4h["open"]:
                            candle_4h_bias = "BULLISH"
                        elif candle_4h["close"] < candle_4h["open"]:
                            candle_4h_bias = "BEARISH"
                        break
        except:
            pass
        
        # Fibonacci bias
        fib_bias = "NEUTRAL"
        if use_fib:
            try:
                hi_52 = daily_df["high"].rolling(252).max().iloc[-1]
                lo_52 = daily_df["low"].rolling(252).min().iloc[-1]
                fib_result = nearest_fib(current_price, lo_52, hi_52, fib_tol)
                if fib_result:
                    fib_name = fib_result[0]
                    fib_pct = float(fib_name.split()[1].replace("%", ""))
                    if fib_pct >= 61.8:
                        fib_bias = "BEARISH"  # Near highs = resistance
                    elif fib_pct <= 38.2:
                        fib_bias = "BULLISH"  # Near lows = support
            except:
                pass
        
        # Options bias — try yfinance first (free), fall back to Alpaca/Polygon
        options_oi_bias = "N/A"
        options_vol_bias = "N/A"
        options_delta_bias = "N/A"
        try:
            options_data = None
            if YFINANCE_AVAILABLE:
                options_data = get_options_bias_yfinance(ticker)
                if options_data and "error" in options_data:
                    options_data = None
            if options_data is None:
                if data_source == "Alpaca":
                    options_data = get_options_bias_alpaca(ticker, api_key, api_secret)
                else:
                    options_data = get_options_bias(ticker, api_key)
            
            if options_data and "error" not in options_data:
                options_oi_bias = options_data.get("sentiment", "NEUTRAL")
                options_vol_bias = options_data.get("vol_sentiment", "N/A")
                options_delta_bias = options_data.get("delta_sentiment", "N/A")
        except:
            pass
        
        # Support / Resistance levels
        sr_data = None
        try:
            sr_data = calc_support_resistance(daily_df)
        except:
            pass
        
        # Calculate signals
        signals = []
        signal_names = []
        
        # 4H Candle (double weight)
        if candle_4h_bias == "BULLISH":
            signals.append(2)
            signal_names.append("4H:BULL")
        elif candle_4h_bias == "BEARISH":
            signals.append(-2)
            signal_names.append("4H:BEAR")
        
        # Fibonacci
        if fib_bias == "BULLISH":
            signals.append(1)
            signal_names.append("Fib:BULL")
        elif fib_bias == "BEARISH":
            signals.append(-1)
            signal_names.append("Fib:BEAR")
        else:
            signals.append(0)
        
        # Options OI
        if options_oi_bias == "BULLISH":
            signals.append(1)
            signal_names.append("OI:BULL")
        elif options_oi_bias == "BEARISH":
            signals.append(-1)
            signal_names.append("OI:BEAR")
        elif options_oi_bias == "NEUTRAL":
            signals.append(0)
        
        # Options Volume
        if options_vol_bias == "BULLISH":
            signals.append(1)
            signal_names.append("Vol:BULL")
        elif options_vol_bias == "BEARISH":
            signals.append(-1)
            signal_names.append("Vol:BEAR")
        elif options_vol_bias == "NEUTRAL":
            signals.append(0)
        
        # Options Delta-Adjusted
        if options_delta_bias == "BULLISH":
            signals.append(1)
            signal_names.append("Δ:BULL")
        elif options_delta_bias == "BEARISH":
            signals.append(-1)
            signal_names.append("Δ:BEAR")
        elif options_delta_bias == "NEUTRAL":
            signals.append(0)
        
        # Strategy (optional)
        if use_strategy:
            try:
                strategy_data = analyze_strategy_signals(daily_df)
                if strategy_data and "error" not in strategy_data:
                    if strategy_data.get("short_signal"):
                        signals.append(-2)
                        signal_names.append("Strat:SHORT")
                    elif strategy_data.get("long_signal"):
                        signals.append(2)
                        signal_names.append("Strat:LONG")
            except:
                pass
        
        if not signals:
            return None
        
        # Calculate verdict
        score = sum(signals)
        
        if score >= 2:
            verdict = "BULLISH"
        elif score <= -2:
            verdict = "BEARISH"
        elif score > 0:
            verdict = "LEAN BULLISH"
        elif score < 0:
            verdict = "LEAN BEARISH"
        else:
            verdict = "NEUTRAL"
        
        # Calculate confidence
        bullish_count = sum(1 for s in signals if s > 0)
        bearish_count = sum(1 for s in signals if s < 0)
        
        if candle_4h_bias == "BULLISH":
            divergent = bearish_count
        elif candle_4h_bias == "BEARISH":
            divergent = bullish_count
        else:
            divergent = 0
        
        if candle_4h_bias in ["BULLISH", "BEARISH"]:
            if divergent == 0:
                confidence = "HIGH"
            elif divergent == 1:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
        else:
            confidence = "N/A"
        
        # Calculate Entry/Exit levels
        recent_low = daily_df["low"].iloc[-10:].min()  # 10-day low for stop
        recent_high = daily_df["high"].iloc[-10:].max()  # 10-day high
        atr_14 = (daily_df["high"] - daily_df["low"]).rolling(14).mean().iloc[-1]  # ATR for sizing
        
        # Average daily move (for timeframe estimates)
        avg_daily_move = atr_14 * 0.6  # Conservative: ~60% of ATR as expected daily progress
        
        if verdict in ["BULLISH", "LEAN BULLISH"]:
            # Long setup
            entry = round(current_price, 2)
            stop_loss = round(recent_low - atr_14 * 0.5, 2)  # Below recent low
            risk = entry - stop_loss
            target1 = round(entry + risk * 2, 2)  # 2:1 R:R
            target2 = round(entry + risk * 3, 2)  # 3:1 R:R
            risk_pct = round((risk / entry) * 100, 1)
            
            # Timeframe estimates (in trading days)
            dist_to_t1 = target1 - entry
            dist_to_t2 = target2 - entry
            t1_days = max(1, round(dist_to_t1 / avg_daily_move)) if avg_daily_move > 0 else None
            t2_days = max(1, round(dist_to_t2 / avg_daily_move)) if avg_daily_move > 0 else None
            
        elif verdict in ["BEARISH", "LEAN BEARISH"]:
            # Short setup
            entry = round(current_price, 2)
            stop_loss = round(recent_high + atr_14 * 0.5, 2)  # Above recent high
            risk = stop_loss - entry
            target1 = round(entry - risk * 2, 2)  # 2:1 R:R
            target2 = round(entry - risk * 3, 2)  # 3:1 R:R
            risk_pct = round((risk / entry) * 100, 1)
            
            # Timeframe estimates (in trading days)
            dist_to_t1 = entry - target1
            dist_to_t2 = entry - target2
            t1_days = max(1, round(dist_to_t1 / avg_daily_move)) if avg_daily_move > 0 else None
            t2_days = max(1, round(dist_to_t2 / avg_daily_move)) if avg_daily_move > 0 else None
        else:
            entry = round(current_price, 2)
            stop_loss = None
            target1 = None
            target2 = None
            risk_pct = None
            t1_days = None
            t2_days = None
        
        # Get fundamentals (valuation + growth + profitability + risk)
        fundamentals = get_fundamentals(ticker)
        valuation = fundamentals.get("valuation", "N/A") if fundamentals else "N/A"
        valuation_color = fundamentals.get("valuation_color", "#6b7099") if fundamentals else "#6b7099"
        market_cap = fundamentals.get("market_cap_str", "N/A") if fundamentals else "N/A"
        target_price_1y = fundamentals.get("target_price") if fundamentals else None
        target_upside = fundamentals.get("target_upside") if fundamentals else None
        
        result = {
            "ticker": ticker,
            "price": round(current_price, 2),
            "verdict": verdict,
            "confidence": confidence,
            "score": score,
            "signals": ", ".join(signal_names),
            "4h": candle_4h_bias,
            "fib": fib_bias,
            "options_oi": options_oi_bias,
            "options_vol": options_vol_bias,
            "options_delta": options_delta_bias,
            "entry": entry,
            "stop_loss": stop_loss,
            "target1": target1,
            "target2": target2,
            "risk_pct": risk_pct,
            "t1_days": t1_days,
            "t2_days": t2_days,
            "valuation": valuation,
            "valuation_color": valuation_color,
            "market_cap": market_cap,
            "target_1y": target_price_1y,
            "target_upside": target_upside,
        }
        # Attach support/resistance levels
        if sr_data:
            result["supports"] = sr_data.get("supports", [])
            result["resistances"] = sr_data.get("resistances", [])
            result["pivot"] = sr_data.get("pivot")
            result["key_level"] = sr_data.get("key_level")
        # Attach extra fundamental fields when available
        if fundamentals:
            for fkey in ("sector", "pe_ratio", "forward_pe", "peg_ratio",
                         "revenue_growth", "earnings_growth", "profit_margin",
                         "roe", "debt_to_equity", "beta", "rec_key", "num_analysts",
                         "dividend_yield", "week52_position", "flags"):
                result[fkey] = fundamentals.get(fkey)
        return result
    except Exception as e:
        return None


def scan_stocks(api_key, api_secret, data_source, watchlist=None, use_fib=True, fib_tol=2.0, use_strategy=False):
    """
    Scan multiple stocks and return bullish + high confidence ones.
    Returns list of result dicts sorted by score descending.
    """
    if watchlist is None:
        watchlist = SCAN_WATCHLIST
    
    results = []
    for ticker in watchlist:
        result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
        if result:
            results.append(result)
    
    # Filter for BULLISH + HIGH confidence
    bullish_high = [r for r in results if r["verdict"] == "BULLISH" and r["confidence"] == "HIGH"]
    
    # Sort by score descending
    bullish_high.sort(key=lambda x: x["score"], reverse=True)
    
    return bullish_high[:5], results


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
    page_title="StockPulse — Earnings & Market Intelligence",
    page_icon="🎯",
    layout="wide",
)

# ──────────────────────────────────────────────
# INSPIRATIONAL QUOTES (rotates on each load)
# ──────────────────────────────────────────────
import random
QUOTES = [
    ("The stock market is a device for transferring money from the impatient to the patient.", "Warren Buffett"),
    ("In investing, what is comfortable is rarely profitable.", "Robert Arnott"),
    ("The goal of a successful trader is to make the best trades. Money is secondary.", "Alexander Elder"),
    ("Risk comes from not knowing what you're doing.", "Warren Buffett"),
    ("The trend is your friend until the end when it bends.", "Ed Seykota"),
    ("Markets can remain irrational longer than you can remain solvent.", "John Maynard Keynes"),
    ("It's not whether you're right or wrong, but how much money you make when you're right.", "George Soros"),
    ("The four most dangerous words in investing are: This time it's different.", "Sir John Templeton"),
    ("Buy when there's blood in the streets, even if the blood is your own.", "Baron Rothschild"),
    ("Know what you own, and know why you own it.", "Peter Lynch"),
    ("An investment in knowledge pays the best interest.", "Benjamin Franklin"),
    ("Wide diversification is only required when investors do not understand what they are doing.", "Warren Buffett"),
    ("The secret to investing is to figure out the value of something — and then pay a lot less.", "Joel Greenblatt"),
    ("Opportunities come infrequently. When it rains gold, put out the bucket, not the thimble.", "Warren Buffett"),
]
_quote_text, _quote_author = random.choice(QUOTES)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;900&family=Space+Mono:wght@400;700&display=swap');
  html, body, [class*="css"] { font-family: 'Inter', 'Space Mono', sans-serif !important; }
  .block-container { padding: 0.5rem 1.5rem 2rem; }
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
  /* Tab styling */
  .stTabs [data-baseweb="tab-list"] { gap: 2px; background: #0a0b14; border-radius: 8px; padding: 4px; }
  .stTabs [data-baseweb="tab"] { height: 44px; padding: 0 20px; font-weight: 600; font-size: 13px;
    border-radius: 6px; color: #6b7099; }
  .stTabs [aria-selected="true"] { background: linear-gradient(135deg, #131625, #1a1d2e) !important;
    color: #e8ecff !important; border-bottom: 2px solid #00e5a0 !important; }
  /* Scrolling quote ticker */
  @keyframes ticker { 0% { transform: translateX(100%); } 100% { transform: translateX(-100%); } }
  .quote-ticker { overflow: hidden; white-space: nowrap; background: linear-gradient(90deg, #0a0b14, #0d0f17, #0a0b14);
    border: 1px solid #1a1d2e20; padding: 6px 0; margin-bottom: 8px; border-radius: 4px; }
  .quote-ticker span { display: inline-block; animation: ticker 25s linear infinite; font-size: 11px;
    color: #6b7099; font-style: italic; letter-spacing: 0.5px; }
</style>
""", unsafe_allow_html=True)

# ── Branded Header ──────────────────────────────────
st.markdown(
    '<div style="display:flex;align-items:center;gap:14px;margin-bottom:2px">'
    '<div style="width:44px;height:44px;border-radius:10px;'
    'background:linear-gradient(135deg,rgba(0,229,160,.2),rgba(77,159,255,.15));'
    'border:1px solid rgba(0,229,160,.35);display:flex;align-items:center;justify-content:center;'
    'font-size:22px">🎯</div>'
    '<div>'
    '<h2 style="margin:0;letter-spacing:0.5px;color:#e8ecff;font-weight:900;font-size:26px">STOCKPULSE</h2>'
    '<p style="margin:0;font-size:9px;color:#3a3d5c;letter-spacing:2.5px;font-weight:600">'
    'EARNINGS · TECHNICAL · SECTOR INTELLIGENCE</p>'
    '</div>'
    '</div>',
    unsafe_allow_html=True,
)

# Scrolling inspirational quote
st.markdown(
    f'<div class="quote-ticker"><span>'
    f'"{_quote_text}" — {_quote_author}'
    f'&nbsp;&nbsp;&nbsp;•&nbsp;&nbsp;&nbsp;'
    f'"{_quote_text}" — {_quote_author}'
    f'</span></div>',
    unsafe_allow_html=True,
)

# ── Sidebar (slim — API keys & global settings only) ──
with st.sidebar:
    st.markdown(
        '<div style="text-align:center;margin-bottom:8px">'
        '<div style="font-size:22px">🎯</div>'
        '<div style="font-size:14px;font-weight:900;color:#e8ecff;letter-spacing:1px">STOCKPULSE</div>'
        '<div style="font-size:8px;color:#3a3d5c;letter-spacing:2px">MARKET INTELLIGENCE</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # Auto-populate keys from files if available
    import os
    finnhub_api_key_val = ""
    alpaca_api_key_val = ""
    alpaca_api_secret_val = ""
    polygon_key_val = ""
    try:
        env_path = os.path.expanduser("algorithmic-trading/.env")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    if "ALPACA_API_KEY" in line:
                        alpaca_api_key_val = line.split("=")[1].strip().strip('"')
                    if "ALPACA_API_SECRET" in line:
                        alpaca_api_secret_val = line.split("=")[1].strip().strip('"')
                    if "FINNHUB_API_KEY" in line:
                        finnhub_api_key_val = line.split("=")[1].strip().strip('"')
    except Exception:
        pass
    try:
        config_path = os.path.expanduser("algorithmic-trading/config.py")
        if os.path.exists(config_path):
            import ast
            with open(config_path, "r") as f:
                tree = ast.parse(f.read())
                for node in tree.body:
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if hasattr(target, 'id'):
                                if target.id == "API_KEY":
                                    alpaca_api_key_val = node.value.s
                                if target.id == "API_SECRET":
                                    alpaca_api_secret_val = node.value.s
                    if isinstance(node, ast.Assign) and hasattr(node.value, 'keys'):
                        for k, v in zip(node.value.keys, node.value.values):
                            if hasattr(k, 's') and hasattr(v, 's'):
                                if k.s == "API_KEY":
                                    alpaca_api_key_val = v.s
                                if k.s == "API_SECRET":
                                    alpaca_api_secret_val = v.s
    except Exception:
        pass
    def mask_key(key):
        if not key or len(key) < 6:
            return key
        return key[:2] + "*" * (len(key)-4) + key[-2:]

    st.markdown("### 🔑 API Keys")
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
            value=mask_key(alpaca_api_key_val) if alpaca_api_key_val else "",
            help="Get free keys at alpaca.markets",
            placeholder="your_alpaca_api_key",
        )
        api_secret = st.text_input(
            "Alpaca Secret Key", type="password",
            value=mask_key(alpaca_api_secret_val) if alpaca_api_secret_val else "",
            placeholder="your_alpaca_secret_key",
        )
        polygon_key = st.text_input(
            "Polygon Key (earnings)", type="password",
            value=mask_key(alpaca_api_key_val) if alpaca_api_key_val else "",
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

    finnhub_api_key = st.text_input(
        "Finnhub API Key (optional)",
        value=mask_key(finnhub_api_key_val) if finnhub_api_key_val else "",
        help="For fetching upcoming earnings tickers."
    )

    st.markdown("---")
    st.markdown("### ⚙️ Global Settings")
    use_fib = st.toggle("Fibonacci Zone Filter", value=True)
    if use_fib:
        fib_tol = st.slider("Fib Tolerance ±%", 0.5, 5.0, 2.0, 0.5)
        fib_tf  = st.radio("Swing timeframe", options=["Weekly", "Daily"], index=0, horizontal=True)
    else:
        fib_tol = 2.0
        fib_tf  = "Weekly"
    use_strategy = st.toggle("Strategy (Fib+Weinstein+Bias)", value=False,
                              help="Fib zones + FVG + Weinstein Stage + Volume Bias analysis")

    st.markdown("---")
    st.markdown(
        '<div style="font-size:9px;color:#3a3d5c;line-height:1.9;margin-top:4px">'
        '<b style="color:#6b7099">STRATEGY</b><br>'
        'Entry: AMC report day<br>'
        'Signal: 4H candle at ~1:30 PM ET<br>'
        'Green candle → LONG<br>'
        'Red candle → SHORT<br>'
        'Exit: Next trading day close'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Credential check ─────────────────────────────────
if data_source == "Alpaca":
    missing_creds = not api_key or not api_secret
else:
    missing_creds = not api_key

if missing_creds:
    st.markdown(
        '<div style="text-align:center;padding:80px 0">'
        '<div style="font-size:60px;margin-bottom:16px;opacity:.25">🎯</div>'
        '<div style="color:#6b7099;font-size:16px;margin-bottom:8px;font-weight:600">'
        'Welcome to StockPulse</div>'
        '<div style="color:#3a3d5c;font-size:11px;line-height:2">'
        'Enter your API credentials in the sidebar to get started.<br>'
        '<b>Alpaca</b>: Free real-time data at '
        '<a href="https://alpaca.markets" style="color:#00e5a0">alpaca.markets</a><br>'
        '<b>Polygon</b>: Free tier at '
        '<a href="https://polygon.io" style="color:#4d9fff">polygon.io</a>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    st.stop()

# Initialize session state
if "fetched_data" not in st.session_state:
    st.session_state.fetched_data = None

# Default shared values
default_watchlist = "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,AMD,NFLX,CRM,ORCL,ADBE,INTC,PYPL,SQ,SHOP,COIN,UBER,ABNB,SNOW,BA,CAT,GS,JPM,V,MA,DIS,NKE,SBUX,MCD,XOM,CVX,PFE,JNJ,UNH,MRNA,LLY,ABBV,BMY,MRK,SPY,QQQ,IWM,DIA,XLF,XLE,XLK,ARKK,SOXX,SMH"

# ══════════════════════════════════════════════════════════════════════════════
# MAIN TABS — 4 Pages
# ══════════════════════════════════════════════════════════════════════════════
tab_fetch, tab_estimator, tab_sector, tab_scanner = st.tabs([
    "📅 Earnings Analysis",
    "🔬 Earnings Estimator",
    "🔥 Sector Scan",
    "🔍 Technical Scanner",
])

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 1: EARNINGS ANALYSIS (Fetch Earnings + Run Backtest)                    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_fetch:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">📅</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Earnings Day Analysis</div>'
        '<div style="font-size:10px;color:#6b7099">Real-time technical + options flow for earnings plays. '
        'Pure technical & options analysis for the trading day.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    # Inputs inline
    fe_col1, fe_col2, fe_col3, fe_col4 = st.columns([2, 1, 1, 1])
    with fe_col1:
        symbols_raw = st.text_input("Ticker(s) — comma-separated", value="TSLA", key="fetch_ticker").upper().strip()
        symbols_list = [s.strip() for s in symbols_raw.split(",") if s.strip()]
        symbol = symbols_list[0] if symbols_list else ""
    with fe_col2:
        years = st.slider("History (years)", 1, 8, 4, key="fetch_years")
    with fe_col3:
        last_n_earnings = st.slider("Last N Earnings", 1, 20, 4, key="fetch_last_n")
    with fe_col4:
        use_4h = st.toggle("4H Candle", value=True, key="fetch_4h")

    fe_col5, fe_col6 = st.columns(2)
    with fe_col5:
        use_vol = st.toggle("Volume Filter", value=True, key="fetch_vol")
        vol_min = st.slider("Min Vol Ratio", 1.0, 3.0, 1.5, 0.1, key="fetch_vol_min") if use_vol else 1.5
    with fe_col6:
        next_earnings_input = st.date_input(
            "Next Earnings Date (AMC)", value=None,
            min_value=date.today(), max_value=date.today() + timedelta(days=180),
            key="fetch_next_earn",
        )

    with st.expander("📋 Past Earnings Dates (optional — paste YYYY-MM-DD, one per line)"):
        manual_dates_raw = st.text_area(
            "Dates", placeholder="2024-10-29\n2024-07-23\n2024-04-23",
            height=100, key="fetch_manual_dates", label_visibility="collapsed",
        )
    manual_dates_list = [l.strip() for l in manual_dates_raw.strip().splitlines() if l.strip()] if manual_dates_raw.strip() else []

    fe_btn1, fe_btn2 = st.columns(2)
    with fe_btn1:
        fetch_btn = st.button("📅 FETCH EARNINGS", use_container_width=True, type="primary", key="btn_fetch")
    with fe_btn2:
        run_btn = st.button("▶ RUN BACKTEST", use_container_width=True, key="btn_run")

    if not fetch_btn and not run_btn:
        if st.session_state.fetched_data is not None:
            data = st.session_state.fetched_data
            st.markdown(
                f'<div style="background:#0d0f1799;border:1px solid #1a1d2e;padding:12px;border-radius:4px;margin-bottom:12px">'
                f'<div style="font-size:11px;color:#6b7099">📅 <b style="color:#e8ecff">{data["symbol"]}</b> · '
                f'{len(data["earnings_events"])} earnings events loaded · Click <b style="color:#00e5a0">▶ RUN BACKTEST</b></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="text-align:center;padding:50px 0">'
                '<div style="font-size:48px;margin-bottom:12px;opacity:.2">📊</div>'
                '<div style="color:#6b7099;font-size:13px">Enter a ticker and click '
                '<b style="color:#4d9fff">📅 FETCH EARNINGS</b> to analyze</div>'
                '</div>',
                unsafe_allow_html=True,
            )

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 2: EARNINGS ESTIMATOR (Technical + Fundamental, CSV export)             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_estimator:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">🔬</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Earnings Estimator</div>'
        '<div style="font-size:10px;color:#6b7099">Weekend research tool — Technical + Fundamental analysis. '
        'Scan a watchlist for upcoming earnings, get verdicts, valuations, growth & risk flags, and export CSV.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    ee_col1, ee_col2 = st.columns([3, 1])
    with ee_col1:
        estimator_watchlist_raw = st.text_area(
            "Tickers to scan (comma-separated)", value=default_watchlist,
            height=80, key="est_watchlist", label_visibility="collapsed",
        )
    with ee_col2:
        earnings_days = st.number_input("Earnings in next N days", min_value=1, max_value=30, value=7, step=1, key="est_days")

    estimator_watchlist = [t.strip().upper() for t in estimator_watchlist_raw.strip().split(",") if t.strip()] if estimator_watchlist_raw.strip() else SCAN_WATCHLIST
    earnings_estimator_btn = st.button("🔬 SCAN EARNINGS", use_container_width=True, type="primary", key="btn_estimator")

    if not earnings_estimator_btn:
        st.markdown(
            '<div style="text-align:center;padding:50px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">🔬</div>'
            '<div style="color:#6b7099;font-size:13px">Paste tickers above and click '
            '<b style="color:#4d9fff">🔬 SCAN EARNINGS</b> for fundamental + technical verdicts</div>'
            '<div style="color:#3a3d5c;font-size:10px;margin-top:8px">'
            'Includes: P/E valuation · analyst targets · sector · growth flags · entry/stop/target levels</div>'
            '</div>',
            unsafe_allow_html=True,
        )

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 3: SECTOR SCAN                                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_sector:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">🔥</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Sector Scan</div>'
        '<div style="font-size:10px;color:#6b7099">Identify hot & cold sectors. '
        'Scans all 11 S&P sectors for momentum, then finds top stocks within each.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    sector_scan_btn = st.button("🔥 SCAN ALL SECTORS", use_container_width=True, type="primary", key="btn_sector")

    if not sector_scan_btn:
        # Show sector overview cards
        st.markdown(
            '<div style="text-align:center;padding:30px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">🔥</div>'
            '<div style="color:#6b7099;font-size:13px">Click '
            '<b style="color:#4d9fff">🔥 SCAN ALL SECTORS</b> to analyze sector momentum</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        # Mini sector grid
        sect_cols = st.columns(4)
        for i, (etf, info) in enumerate(list(SECTOR_ETFS.items())[:8]):
            with sect_cols[i % 4]:
                st.markdown(
                    f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;'
                    f'border-radius:6px;margin-bottom:6px;text-align:center">'
                    f'<div style="font-size:20px">{info["emoji"]}</div>'
                    f'<div style="font-size:10px;color:#e8ecff;font-weight:600">{info["name"]}</div>'
                    f'<div style="font-size:8px;color:#3a3d5c">{etf}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║ TAB 4: TECHNICAL SCANNER (Scan Bullish/Bearish — pure technical)            ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
with tab_scanner:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0a0b14,#131625);border:1px solid #1a1d2e;'
        'border-radius:8px;padding:18px 24px;margin-bottom:16px">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<div style="font-size:28px">🔍</div>'
        '<div>'
        '<div style="font-size:16px;font-weight:700;color:#e8ecff">Technical Scanner</div>'
        '<div style="font-size:10px;color:#6b7099">Pure technical scan — 4H candle, Fibonacci, '
        'options flow, Weinstein stage. Find high-confidence LONG and SHORT setups.</div>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )

    sc_col1, sc_col2 = st.columns([3, 1])
    with sc_col1:
        custom_watchlist_raw = st.text_area(
            "Watchlist (comma-separated)", value=default_watchlist,
            height=80, key="scan_watchlist", label_visibility="collapsed",
        )
    with sc_col2:
        scan_date = st.date_input("Scan Date", value=date.today(), key="scan_date")

    custom_watchlist = [t.strip().upper() for t in custom_watchlist_raw.split(",") if t.strip()] if custom_watchlist_raw.strip() else SCAN_WATCHLIST
    scan_btn = st.button("🔍 SCAN BULLISH & BEARISH", use_container_width=True, type="primary", key="btn_scan")

    if not scan_btn:
        st.markdown(
            '<div style="text-align:center;padding:50px 0">'
            '<div style="font-size:48px;margin-bottom:12px;opacity:.2">🔍</div>'
            '<div style="color:#6b7099;font-size:13px">Edit the watchlist above and click '
            '<b style="color:#4d9fff">🔍 SCAN</b> for bullish & bearish setups</div>'
            '</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# BUTTON HANDLERS — each runs inside its tab
# ══════════════════════════════════════════════════════════════════════════════

# ── Earnings Estimator ──────────────────────────
if earnings_estimator_btn:
  with tab_estimator:
    st.markdown(f"### � Earnings Estimator — Scanning {len(estimator_watchlist)} Tickers")
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(estimator_watchlist)

    scan_results = []
    for i, ticker in enumerate(estimator_watchlist):
        status_text.text(f"Analyzing {ticker}... ({i+1}/{total})")
        progress_bar.progress((i + 1) / total)
        result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
        if result:
            # Get fundamentals for extra context
            fundies = get_fundamentals(ticker)
            if fundies:
                result["sector"] = fundies.get("sector", "N/A")
                result["industry"] = fundies.get("industry", "N/A")
                result["pe_ratio"] = fundies.get("pe_ratio")
                result["forward_pe"] = fundies.get("forward_pe")
                result["peg_ratio"] = fundies.get("peg_ratio")
                result["analyst_target"] = fundies.get("target_price")
                result["revenue_growth"] = fundies.get("revenue_growth")
                result["earnings_growth"] = fundies.get("earnings_growth")
                result["profit_margin"] = fundies.get("profit_margin")
                result["roe"] = fundies.get("roe")
                result["debt_to_equity"] = fundies.get("debt_to_equity")
                result["beta"] = fundies.get("beta")
                result["dividend_yield"] = fundies.get("dividend_yield")
                result["short_pct"] = fundies.get("short_pct")
                result["week52_position"] = fundies.get("week52_position")
                result["pct_from_high"] = fundies.get("pct_from_high")
                result["rec_key"] = fundies.get("rec_key", "")
                result["num_analysts"] = fundies.get("num_analysts")
                result["revenue_str"] = fundies.get("revenue_str", "N/A")
                # Build a quick flags summary string
                flags = fundies.get("flags", [])
                result["flags"] = " · ".join(f[0] for f in flags) if flags else ""
            else:
                result["sector"] = "N/A"
                result["industry"] = "N/A"
                result["flags"] = ""
            scan_results.append(result)
    progress_bar.empty()
    status_text.empty()

    if scan_results:
        import pandas as pd
        df = pd.DataFrame(scan_results)

        # Format display columns — full fundamental + technical view
        display_cols = [
            "ticker", "price", "sector", "industry", "verdict", "confidence", "score",
            "entry", "stop_loss", "target1", "target2", "t1_days",
            "pe_ratio", "forward_pe", "peg_ratio", "valuation", "market_cap",
            "revenue_str", "revenue_growth", "earnings_growth",
            "profit_margin", "roe", "debt_to_equity", "beta",
            "dividend_yield", "short_pct",
            "analyst_target", "target_1y", "target_upside",
            "rec_key", "num_analysts",
            "week52_position", "pct_from_high",
            "flags",
        ]
        display_cols = [c for c in display_cols if c in df.columns]

        # Format columns for readability
        for col in ["target_1y", "analyst_target"]:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: f"${x}" if pd.notnull(x) and x else "N/A")
        if "target_upside" in df.columns:
            df["target_upside"] = df["target_upside"].apply(lambda x: f"{x:+.1f}%" if pd.notnull(x) and x != "" else "")
        if "pe_ratio" in df.columns:
            df["pe_ratio"] = df["pe_ratio"].apply(lambda x: f"{x:.1f}" if pd.notnull(x) and x else "N/A")
        if "forward_pe" in df.columns:
            df["forward_pe"] = df["forward_pe"].apply(lambda x: f"{x:.1f}" if pd.notnull(x) and x else "N/A")
        if "peg_ratio" in df.columns:
            df["peg_ratio"] = df["peg_ratio"].apply(lambda x: f"{x:.2f}" if pd.notnull(x) and x else "N/A")
        for pct_col in ["revenue_growth", "earnings_growth", "profit_margin", "roe", "dividend_yield", "short_pct"]:
            if pct_col in df.columns:
                df[pct_col] = df[pct_col].apply(lambda x: f"{x*100:.1f}%" if pd.notnull(x) and x != "" else "N/A")
        if "debt_to_equity" in df.columns:
            df["debt_to_equity"] = df["debt_to_equity"].apply(lambda x: f"{x:.0f}" if pd.notnull(x) and x != "" else "N/A")
        if "beta" in df.columns:
            df["beta"] = df["beta"].apply(lambda x: f"{x:.2f}" if pd.notnull(x) and x != "" else "N/A")
        if "week52_position" in df.columns:
            df["week52_position"] = df["week52_position"].apply(lambda x: f"{x:.0f}%" if pd.notnull(x) and x != "" else "N/A")
        if "pct_from_high" in df.columns:
            df["pct_from_high"] = df["pct_from_high"].apply(lambda x: f"{x:+.1f}%" if pd.notnull(x) and x != "" else "N/A")

        # Rename columns for cleaner headers
        col_rename = {
            "pe_ratio": "P/E", "forward_pe": "Fwd P/E", "peg_ratio": "PEG",
            "revenue_str": "Revenue", "revenue_growth": "Rev Growth",
            "earnings_growth": "EPS Growth", "profit_margin": "Margin",
            "roe": "ROE", "debt_to_equity": "D/E", "beta": "Beta",
            "dividend_yield": "Div Yield", "short_pct": "Short%",
            "analyst_target": "Analyst $", "target_1y": "1Y Target",
            "target_upside": "Upside", "rec_key": "Rating",
            "num_analysts": "# Analysts", "week52_position": "52W Pos",
            "pct_from_high": "vs 52W Hi", "market_cap": "Mkt Cap",
            "stop_loss": "Stop", "t1_days": "T1 Days", "flags": "Signals",
        }
        df_display = df[display_cols].rename(columns=col_rename)

        # Summary counts
        bullish_count = sum(1 for r in scan_results if r.get("verdict") == "BULLISH")
        bearish_count = sum(1 for r in scan_results if r.get("verdict") == "BEARISH")
        high_conf = sum(1 for r in scan_results if r.get("confidence") == "HIGH")

        # Sector breakdown
        sector_counts = {}
        for r in scan_results:
            s = r.get("sector", "N/A")
            sector_counts[s] = sector_counts.get(s, 0) + 1
        sector_summary = " · ".join(f"{s}: {c}" for s, c in sorted(sector_counts.items(), key=lambda x: -x[1])[:6])

        st.markdown(
            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px;margin-bottom:12px">'
            f'<span style="color:#6b7099;font-size:11px">Scanned <b style="color:#e8ecff">{total}</b> tickers · '
            f'<b style="color:#00e5a0">{bullish_count}</b> bullish · '
            f'<b style="color:#ff4d6a">{bearish_count}</b> bearish · '
            f'<b style="color:#4d9fff">{high_conf}</b> high confidence</span><br>'
            f'<span style="color:#3a3d5c;font-size:10px">Sectors: {sector_summary}</span></div>',
            unsafe_allow_html=True,
        )

        st.dataframe(df_display, use_container_width=True, height=min(40 * len(df_display) + 38, 600))

        # CSV download
        csv_data = df_display.to_csv(index=False)
        st.download_button(
            label="📥 Download CSV",
            data=csv_data,
            file_name=f"earnings_estimator_{date.today()}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.info("No scan results returned. Check that your API keys are valid and tickers are correct.")

# ── Scan for bullish stocks ──────────────────────────
if scan_btn:
  with tab_scanner:
    st.markdown(f"### 🔍 Scanning for Bullish Setups on {scan_date}...")
    progress_bar = st.progress(0)
    status_text = st.empty()
    top_bullish = []
    all_results = []
    total = len(custom_watchlist)
    for i, ticker in enumerate(custom_watchlist):
        status_text.text(f"Scanning {ticker}... ({i+1}/{total})")
        progress_bar.progress((i + 1) / total)
        # Pass scan_date to scan_single_stock if needed, or use in logic
        # For now, scan_single_stock uses current date, but you can extend it to use scan_date
        result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
        if result:
            all_results.append(result)
    progress_bar.empty()
    status_text.empty()
    top_bullish = [r for r in all_results if r["verdict"] == "BULLISH" and r["confidence"] == "HIGH"]
    top_bullish.sort(key=lambda x: x["score"], reverse=True)
    top_bullish = top_bullish[:5]
    top_bearish = [r for r in all_results if r["verdict"] == "BEARISH" and r["confidence"] == "HIGH"]
    top_bearish.sort(key=lambda x: x["score"])
    top_bearish = top_bearish[:5]
    st.markdown("---")
    col_bull, col_bear = st.columns(2)
    with col_bull:
        if top_bullish:
            st.markdown(
                '<div style="background:#00e5a015;border:1px solid #00e5a040;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#00e5a0;margin-bottom:4px">🚀 TOP BULLISH</div>'
                '<div style="font-size:10px;color:#6b7099">High confidence longs</div>'
                '</div>',
                unsafe_allow_html=True
            )
            for rank, r in enumerate(top_bullish, 1):
                stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}d</span>' if r.get("t1_days") else ""
                val_color = r.get("valuation_color", "#6b7099")
                if r.get("target_1y") and r.get("target_upside") is not None:
                    upside = r["target_upside"]
                    upside_color = "#00e5a0" if upside > 0 else "#ff4d6a"
                    target_1y_html = f'<span style="color:#4d9fff">${r["target_1y"]}</span> <span style="color:{upside_color}">({upside:+.1f}%)</span>'
                else:
                    target_1y_html = "N/A"
                st.markdown(
                    f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center">'
                    f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                    f'<span style="font-size:11px;color:#00e5a0">+{r["score"]}</span>'
                    f'</div>'
                    f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                    f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                    f'</div>'
                    f'<div style="font-size:9px;margin-top:3px">'
                    f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · '
                    f'{r.get("market_cap", "N/A")} · '
                    f'1Y: {target_1y_html}'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
        else:
            st.info("No high-confidence bullish setups found.")
    with col_bear:
        if top_bearish:
            st.markdown(
                '<div style="background:#ff4d6a15;border:1px solid #ff4d6a40;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#ff4d6a;margin-bottom:4px">📉 TOP BEARISH</div>'
                '<div style="font-size:10px;color:#6b7099">High confidence shorts</div>'
                '</div>',
                unsafe_allow_html=True
            )
            for rank, r in enumerate(top_bearish, 1):
                stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}d</span>' if r.get("t1_days") else ""
                val_color = r.get("valuation_color", "#6b7099")
                if r.get("target_1y") and r.get("target_upside") is not None:
                    upside = r["target_upside"]
                    upside_color = "#00e5a0" if upside > 0 else "#ff4d6a"
                    target_1y_html = f'<span style="color:#4d9fff">${r["target_1y"]}</span> <span style="color:{upside_color}">({upside:+.1f}%)</span>'
                else:
                    target_1y_html = "N/A"
                st.markdown(
                    f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center">'
                    f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                    f'<span style="font-size:11px;color:#ff4d6a">{r["score"]}</span>'
                    f'</div>'
                    f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                    f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                    f'</div>'
                    f'<div style="font-size:9px;margin-top:3px">'
                    f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · '
                    f'{r.get("market_cap", "N/A")} · '
                    f'1Y: {target_1y_html}'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
        else:
            st.info("No high-confidence bearish setups found.")
    bullish_count = sum(1 for r in all_results if "BULLISH" in r["verdict"])
    bearish_count = sum(1 for r in all_results if "BEARISH" in r["verdict"])
    st.markdown(
        f'<div style="font-size:10px;color:#6b7099;margin-top:12px;text-align:center">'
        f'Scanned {len(custom_watchlist)} stocks · {bullish_count} bullish · {bearish_count} bearish · '
        f'{len(top_bullish)} high-conf longs · {len(top_bearish)} high-conf shorts'
        f'</div>',
        unsafe_allow_html=True
    )

# ── Sector Scan ──────────────────────────────────────
if sector_scan_btn:
  with tab_sector:
    st.markdown("### 🔥 Sector Performance Analysis")
    
    with st.spinner("Analyzing sector performance..."):
        sector_perf = get_sector_performance(api_key, api_secret, data_source)
    
    if sector_perf:
        # sector_perf is a list sorted by momentum
        # Display hot sectors (positive momentum)
        hot_sectors = [s for s in sector_perf if s["momentum"] > 0]
        cold_sectors = [s for s in sector_perf if s["momentum"] <= 0]
        
        col_hot, col_cold = st.columns(2)
        
        with col_hot:
            st.markdown(
                '<div style="background:#00e5a015;border:1px solid #00e5a040;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#00e5a0;margin-bottom:4px">🔥 HOT SECTORS</div>'
                '<div style="font-size:10px;color:#6b7099">Positive momentum (buy strength)</div>'
                '</div>',
                unsafe_allow_html=True
            )
            
            if hot_sectors:
                for rank, data in enumerate(hot_sectors[:5], 1):
                    d1_color = "#00e5a0" if data["change_1d"] >= 0 else "#ff4d6a"
                    w1_color = "#00e5a0" if data["change_1w"] >= 0 else "#ff4d6a"
                    m1_color = "#00e5a0" if data["change_1m"] >= 0 else "#ff4d6a"
                    
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {data["emoji"]} {data["name"]}</span>'
                        f'<span style="font-size:11px;color:#00e5a0;font-weight:bold">+{data["momentum"]:.1f}</span>'
                        f'</div>'
                        f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                        f'{data["etf"]} · <span style="color:{d1_color}">1D: {data["change_1d"]:+.1f}%</span> · '
                        f'<span style="color:{w1_color}">1W: {data["change_1w"]:+.1f}%</span> · '
                        f'<span style="color:{m1_color}">1M: {data["change_1m"]:+.1f}%</span>'
                        f'</div>'
                        f'<div style="font-size:9px;color:#4d9fff;margin-top:3px">'
                        f'Top: {", ".join(data["stocks"][:5])}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.info("No hot sectors found.")
        
        with col_cold:
            st.markdown(
                '<div style="background:#ff4d6a15;border:1px solid #ff4d6a40;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#ff4d6a;margin-bottom:4px">❄️ COLD SECTORS</div>'
                '<div style="font-size:10px;color:#6b7099">Negative momentum (avoid or short)</div>'
                '</div>',
                unsafe_allow_html=True
            )
            
            if cold_sectors:
                for rank, data in enumerate(cold_sectors[:5], 1):
                    d1_color = "#00e5a0" if data["change_1d"] >= 0 else "#ff4d6a"
                    w1_color = "#00e5a0" if data["change_1w"] >= 0 else "#ff4d6a"
                    m1_color = "#00e5a0" if data["change_1m"] >= 0 else "#ff4d6a"
                    
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {data["emoji"]} {data["name"]}</span>'
                        f'<span style="font-size:11px;color:#ff4d6a;font-weight:bold">{data["momentum"]:.1f}</span>'
                        f'</div>'
                        f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                        f'{data["etf"]} · <span style="color:{d1_color}">1D: {data["change_1d"]:+.1f}%</span> · '
                        f'<span style="color:{w1_color}">1W: {data["change_1w"]:+.1f}%</span> · '
                        f'<span style="color:{m1_color}">1M: {data["change_1m"]:+.1f}%</span>'
                        f'</div>'
                        f'<div style="font-size:9px;color:#4d9fff;margin-top:3px">'
                        f'Top: {", ".join(data["stocks"][:5])}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.info("No cold sectors found.")
        
        # Now scan stocks in hot sectors
        if hot_sectors:
            st.markdown("---")
            st.markdown("### 🎯 Best Stocks in Hot Sectors")
            
            # Get stocks from top 3 hot sectors
            hot_stocks = []
            for sector in hot_sectors[:3]:
                hot_stocks.extend(sector["stocks"])
            hot_stocks = list(set(hot_stocks))  # Dedupe
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            hot_results = []
            total = len(hot_stocks)
            
            for i, ticker in enumerate(hot_stocks):
                status_text.text(f"Scanning {ticker}... ({i+1}/{total})")
                progress_bar.progress((i + 1) / total)
                
                result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
                if result:
                    hot_results.append(result)
            
            progress_bar.empty()
            status_text.empty()
            
            # Filter for good setups
            bullish_hot = [r for r in hot_results if r["verdict"] == "BULLISH" and r["confidence"] == "HIGH"]
            bullish_hot.sort(key=lambda x: x["score"], reverse=True)
            bullish_hot = bullish_hot[:5]
            
            bearish_hot = [r for r in hot_results if r["verdict"] == "BEARISH" and r["confidence"] == "HIGH"]
            bearish_hot.sort(key=lambda x: x["score"])
            bearish_hot = bearish_hot[:5]
            
            col_b, col_s = st.columns(2)
            
            with col_b:
                st.markdown(
                    '<div style="background:#00e5a015;border:1px solid #00e5a040;padding:8px;border-radius:8px;margin-bottom:8px">'
                    '<div style="font-size:12px;font-weight:bold;color:#00e5a0">🚀 BULLISH in Hot Sectors</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
                
                if bullish_hot:
                    for rank, r in enumerate(bullish_hot, 1):
                        stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                        t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                        t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}d</span>' if r.get("t1_days") else ""
                        val_color = r.get("valuation_color", "#6b7099")
                        
                        st.markdown(
                            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:8px;border-radius:4px;margin-bottom:4px">'
                            f'<div style="display:flex;justify-content:space-between;align-items:center">'
                            f'<span style="font-size:13px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                            f'<span style="font-size:10px;color:#00e5a0">+{r["score"]}</span>'
                            f'</div>'
                            f'<div style="font-size:9px;color:#6b7099;margin-top:3px">'
                            f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                            f'</div>'
                            f'<div style="font-size:9px;margin-top:2px">'
                            f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · {r.get("market_cap", "N/A")}'
                            f'</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )
                else:
                    st.info("No bullish setups in hot sectors.")
            
            with col_s:
                st.markdown(
                    '<div style="background:#ff4d6a15;border:1px solid #ff4d6a40;padding:8px;border-radius:8px;margin-bottom:8px">'
                    '<div style="font-size:12px;font-weight:bold;color:#ff4d6a">📉 BEARISH in Hot Sectors</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
                
                if bearish_hot:
                    for rank, r in enumerate(bearish_hot, 1):
                        stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                        t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                        t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}d</span>' if r.get("t1_days") else ""
                        val_color = r.get("valuation_color", "#6b7099")
                        
                        st.markdown(
                            f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:8px;border-radius:4px;margin-bottom:4px">'
                            f'<div style="display:flex;justify-content:space-between;align-items:center">'
                            f'<span style="font-size:13px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                            f'<span style="font-size:10px;color:#ff4d6a">{r["score"]}</span>'
                            f'</div>'
                            f'<div style="font-size:9px;color:#6b7099;margin-top:3px">'
                            f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                            f'</div>'
                            f'<div style="font-size:9px;margin-top:2px">'
                            f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · {r.get("market_cap", "N/A")}'
                            f'</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )
                else:
                    st.info("No bearish setups in hot sectors.")
            
            st.markdown(
                f'<div style="font-size:10px;color:#6b7099;margin-top:12px;text-align:center">'
                f'Scanned {len(hot_stocks)} stocks from top 3 hot sectors'
                f'</div>',
                unsafe_allow_html=True
            )
    else:
        # Fallback: scan all sector stocks without performance data
        st.warning("Could not fetch sector ETF data. Scanning all sector stocks instead...")
        
        # Get all unique stocks from all sectors
        all_sector_stocks = []
        for etf, info in SECTOR_ETFS.items():
            all_sector_stocks.extend(info["stocks"])
        all_sector_stocks = list(set(all_sector_stocks))
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        all_results = []
        total = len(all_sector_stocks)
        
        for i, ticker in enumerate(all_sector_stocks):
            status_text.text(f"Scanning {ticker}... ({i+1}/{total})")
            progress_bar.progress((i + 1) / total)
            
            result = scan_single_stock(ticker, api_key, api_secret, data_source, use_fib, fib_tol, use_strategy)
            if result:
                all_results.append(result)
        
        progress_bar.empty()
        status_text.empty()
        
        # Filter for good setups
        top_bullish = [r for r in all_results if r["verdict"] == "BULLISH" and r["confidence"] == "HIGH"]
        top_bullish.sort(key=lambda x: x["score"], reverse=True)
        top_bullish = top_bullish[:5]
        
        top_bearish = [r for r in all_results if r["verdict"] == "BEARISH" and r["confidence"] == "HIGH"]
        top_bearish.sort(key=lambda x: x["score"])
        top_bearish = top_bearish[:5]
        
        col_b, col_s = st.columns(2)
        
        with col_b:
            st.markdown(
                '<div style="background:#00e5a015;border:1px solid #00e5a040;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#00e5a0;margin-bottom:4px">🚀 TOP BULLISH (Sector Stocks)</div>'
                '<div style="font-size:10px;color:#6b7099">High confidence longs from all sectors</div>'
                '</div>',
                unsafe_allow_html=True
            )
            
            if top_bullish:
                for rank, r in enumerate(top_bullish, 1):
                    stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                    t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                    t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}d</span>' if r.get("t1_days") else ""
                    val_color = r.get("valuation_color", "#6b7099")
                    
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                        f'<span style="font-size:11px;color:#00e5a0">+{r["score"]}</span>'
                        f'</div>'
                        f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                        f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                        f'</div>'
                        f'<div style="font-size:9px;margin-top:3px">'
                        f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · {r.get("market_cap", "N/A")}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.info("No bullish setups found.")
        
        with col_s:
            st.markdown(
                '<div style="background:#ff4d6a15;border:1px solid #ff4d6a40;padding:12px;border-radius:8px;margin-bottom:12px">'
                '<div style="font-size:14px;font-weight:bold;color:#ff4d6a;margin-bottom:4px">📉 TOP BEARISH (Sector Stocks)</div>'
                '<div style="font-size:10px;color:#6b7099">High confidence shorts from all sectors</div>'
                '</div>',
                unsafe_allow_html=True
            )
            
            if top_bearish:
                for rank, r in enumerate(top_bearish, 1):
                    stop_html = f'<span style="color:#ff4d6a">${r["stop_loss"]}</span>' if r.get("stop_loss") else "N/A"
                    t1_html = f'<span style="color:#00e5a0">${r["target1"]}</span>' if r.get("target1") else "N/A"
                    t1_time = f'<span style="color:#4d9fff">~{r["t1_days"]}d</span>' if r.get("t1_days") else ""
                    val_color = r.get("valuation_color", "#6b7099")
                    
                    st.markdown(
                        f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:10px;border-radius:4px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:14px;font-weight:bold;color:#e8ecff">#{rank} {r["ticker"]}</span>'
                        f'<span style="font-size:11px;color:#ff4d6a">{r["score"]}</span>'
                        f'</div>'
                        f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
                        f'${r["price"]} · Stop: {stop_html} · T1: {t1_html} {t1_time}'
                        f'</div>'
                        f'<div style="font-size:9px;margin-top:3px">'
                        f'<span style="color:{val_color}">{r.get("valuation", "N/A")}</span> · {r.get("market_cap", "N/A")}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.info("No bearish setups found.")
        
        st.markdown(
            f'<div style="font-size:10px;color:#6b7099;margin-top:12px;text-align:center">'
            f'Scanned {len(all_sector_stocks)} stocks from all sectors'
            f'</div>',
            unsafe_allow_html=True
        )
    

# ── Fetch earnings data ──────────────────────────────
end_date   = date.today()
start_date = end_date - timedelta(days=365 * years + 60)

if fetch_btn:
  with tab_fetch:
    source_name = "Alpaca" if data_source == "Alpaca" else "Polygon"
    for _sym_idx, symbol in enumerate(symbols_list):
      st.markdown(f"---\n### 📊 {symbol}" if _sym_idx > 0 else "")
      with st.spinner(f"Fetching {symbol} data from {source_name}…"):
        status = st.empty()

        status.info(f"📈 Fetching daily price bars for {symbol}…")
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
            continue

        if daily_df.empty:
            st.error(f"No daily price data found for **{symbol}**. Check that the ticker is a valid US stock.")
            continue

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
            continue

        # Filter to requested date range
        cutoff = str(start_date)
        earnings_events = [(d, l, p) for d, l, p in earnings_events if d >= cutoff]

        if not earnings_events:
            status.empty()
            st.warning(f"No earnings events for **{symbol}** within the selected date range.")
            continue

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
          # Try yfinance first (free, no API key needed), fall back to Alpaca/Polygon
          options_data = None
          if YFINANCE_AVAILABLE:
              options_data = get_options_bias_yfinance(symbol)
              if options_data and "error" in options_data:
                  options_data = None  # yfinance failed, try API fallback
          if options_data is None:
              if data_source == "Alpaca":
                  options_data = get_options_bias_alpaca(symbol, api_key, api_secret)
              elif api_key:
                  options_data = get_options_bias(symbol, api_key)
    
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
    
      # Options Delta-Adjusted Bias
      options_delta_bias = "N/A"
      if options_data and "error" not in options_data:
          options_delta_bias = options_data.get("delta_sentiment", "N/A")
      if options_delta_bias == "BULLISH":
          signals.append(1)
          signal_details.append(("Options Δ", "BULLISH", "#00e5a0", "🟢"))
      elif options_delta_bias == "BEARISH":
          signals.append(-1)
          signal_details.append(("Options Δ", "BEARISH", "#ff4d6a", "🔴"))
      elif options_delta_bias == "NEUTRAL":
          signals.append(0)
          signal_details.append(("Options Δ", "NEUTRAL", "#f5c842", "🟡"))
      else:
          signal_details.append(("Options Δ", "N/A", "#6b7099", "⚪"))

      # Strategy Signal (Fib + Weinstein + Bias) - optional
      if use_strategy:
          strategy_data = analyze_strategy_signals(daily_df)
          if strategy_data and "error" not in strategy_data:
              if strategy_data.get("short_signal"):
                  signals.append(-2)  # Double weight for full strategy signal
                  signal_details.append(("Strategy", "SHORT", "#ff4d6a", "🔴"))
              elif strategy_data.get("long_signal"):
                  signals.append(2)
                  signal_details.append(("Strategy", "LONG", "#00e5a0", "🟢"))
              else:
                  signal_details.append(("Strategy", "NEUTRAL", "#f5c842", "🟡"))
          else:
              signal_details.append(("Strategy", "N/A", "#6b7099", "⚪"))
      else:
          strategy_data = None
    
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
    
      # ── Entry / Stop Loss / Targets ──
      recent_low = daily_df["low"].iloc[-10:].min()
      recent_high = daily_df["high"].iloc[-10:].max()
      atr_14 = (daily_df["high"] - daily_df["low"]).rolling(14).mean().iloc[-1]
      avg_daily_move = atr_14 * 0.6

      if final_verdict in ["BULLISH", "LEAN BULLISH"]:
          entry = round(latest_close, 2)
          stop_loss = round(recent_low - atr_14 * 0.5, 2)
          risk = entry - stop_loss
          target1 = round(entry + risk * 2, 2)
          target2 = round(entry + risk * 3, 2)
          risk_pct = round((risk / entry) * 100, 1)
          t1_days = max(1, round((target1 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
          t2_days = max(1, round((target2 - entry) / avg_daily_move)) if avg_daily_move > 0 else None
          setup_dir = "LONG"
          setup_color = "#00e5a0"
      elif final_verdict in ["BEARISH", "LEAN BEARISH"]:
          entry = round(latest_close, 2)
          stop_loss = round(recent_high + atr_14 * 0.5, 2)
          risk = stop_loss - entry
          target1 = round(entry - risk * 2, 2)
          target2 = round(entry - risk * 3, 2)
          risk_pct = round((risk / entry) * 100, 1)
          t1_days = max(1, round((entry - target1) / avg_daily_move)) if avg_daily_move > 0 else None
          t2_days = max(1, round((entry - target2) / avg_daily_move)) if avg_daily_move > 0 else None
          setup_dir = "SHORT"
          setup_color = "#ff4d6a"
      else:
          entry = round(latest_close, 2)
          stop_loss = None
          target1 = None
          target2 = None
          risk_pct = None
          t1_days = None
          t2_days = None
          setup_dir = None
          setup_color = "#6b7099"

      st.markdown("---")
      st.markdown("### 🎯 Entry / Stop Loss / Targets")

      if setup_dir:
          esl_col1, esl_col2, esl_col3, esl_col4 = st.columns(4)
          with esl_col1:
              st.metric("Entry", f"${entry:.2f}", f"{setup_dir}")
          with esl_col2:
              st.metric("Stop Loss", f"${stop_loss:.2f}", f"Risk: {risk_pct}%")
          with esl_col3:
              t1_time = f"~{t1_days}d" if t1_days else ""
              st.metric("Target 1 (2:1)", f"${target1:.2f}", t1_time)
          with esl_col4:
              t2_time = f"~{t2_days}d" if t2_days else ""
              st.metric("Target 2 (3:1)", f"${target2:.2f}", t2_time)

          st.markdown(
              f'<div style="background:#0d0f1799;border:1px solid {setup_color}40;'
              f'padding:12px;border-radius:4px;margin-top:8px">'
              f'<div style="font-size:10px;color:#6b7099">TRADE LEVELS</div>'
              f'<div style="font-size:12px;color:{setup_color};font-weight:bold;margin-top:4px">'
              f'{setup_dir} Setup · Entry ${entry:.2f} · Stop ${stop_loss:.2f} · '
              f'T1 ${target1:.2f} · T2 ${target2:.2f}</div>'
              f'<div style="font-size:10px;color:#6b7099;margin-top:4px">'
              f'Risk/Reward: 1:{2 if target1 else "?"} / 1:{3 if target2 else "?"} · '
              f'Risk: {risk_pct}% · ATR(14): ${atr_14:.2f}</div>'
              f'</div>',
              unsafe_allow_html=True
          )
      else:
          st.info("No directional signal — Entry/Stop/Target levels require a BULLISH or BEARISH verdict.")

      # ══════════════════════════════════════════════════════════════════════════════
      # SUPPORT / RESISTANCE LEVELS
      # ══════════════════════════════════════════════════════════════════════════════
      st.markdown("---")
      st.markdown("### 🏗️ Support & Resistance Levels")
      sr_levels = calc_support_resistance(daily_df)
      if sr_levels:
          sr_col1, sr_col2 = st.columns(2)

          with sr_col1:
              st.markdown('<div style="font-size:10px;color:#ff4d6a;font-weight:700;margin-bottom:6px">▲ RESISTANCE</div>',
                         unsafe_allow_html=True)
              for i, rlev in enumerate(sr_levels.get("resistances", [])[:5]):
                  rpx = rlev["price"]
                  rdist = abs(rpx - latest_close) / latest_close * 100
                  stars = "★" * rlev["strength"] + "☆" * max(0, 3 - rlev["strength"])
                  bar_color = "#ff4d6a" if i == 0 else "#ff4d6a80"
                  st.markdown(
                      f'<div style="background:#0d0f17;border-left:3px solid {bar_color};'
                      f'padding:8px 12px;border-radius:0 4px 4px 0;margin-bottom:4px">'
                      f'<div style="display:flex;justify-content:space-between;align-items:center">'
                      f'<span style="color:#ff4d6a;font-weight:700;font-size:13px">${rpx:.2f}</span>'
                      f'<span style="color:#6b7099;font-size:10px">+{rdist:.1f}% away · {stars}</span>'
                      f'</div></div>', unsafe_allow_html=True)

          with sr_col2:
              st.markdown('<div style="font-size:10px;color:#00e5a0;font-weight:700;margin-bottom:6px">▼ SUPPORT</div>',
                         unsafe_allow_html=True)
              for i, slev in enumerate(sr_levels.get("supports", [])[:5]):
                  spx = slev["price"]
                  sdist = abs(latest_close - spx) / latest_close * 100
                  stars = "★" * slev["strength"] + "☆" * max(0, 3 - slev["strength"])
                  bar_color = "#00e5a0" if i == 0 else "#00e5a080"
                  st.markdown(
                      f'<div style="background:#0d0f17;border-left:3px solid {bar_color};'
                      f'padding:8px 12px;border-radius:0 4px 4px 0;margin-bottom:4px">'
                      f'<div style="display:flex;justify-content:space-between;align-items:center">'
                      f'<span style="color:#00e5a0;font-weight:700;font-size:13px">${spx:.2f}</span>'
                      f'<span style="color:#6b7099;font-size:10px">-{sdist:.1f}% away · {stars}</span>'
                      f'</div></div>', unsafe_allow_html=True)

          # Pivot & key level summary
          pivot_px = sr_levels.get("pivot")
          key_lev = sr_levels.get("key_level")
          pivot_str = f"Pivot: ${pivot_px:.2f}" if pivot_px else ""
          key_str = ""
          if key_lev:
              kp = key_lev["price"]
              ktype = "Support" if kp < latest_close else "Resistance"
              key_str = f" · Key {ktype}: ${kp:.2f} (confluence ×{key_lev['strength']})"
          st.markdown(
              f'<div style="background:#0d0f1799;border:1px solid #1a1d2e;'
              f'padding:10px;border-radius:4px;margin-top:8px;font-size:11px;color:#6b7099">'
              f'📍 {pivot_str}{key_str} · '
              f'Sources: pivot points, swing fractals, volume clusters, round numbers</div>',
              unsafe_allow_html=True)
      else:
          st.info("Insufficient data to calculate support/resistance levels.")

      # ══════════════════════════════════════════════════════════════════════════════
      # FUNDAMENTAL ANALYSIS PANEL
      # ══════════════════════════════════════════════════════════════════════════════
      st.markdown("---")
      st.markdown("### 📊 Fundamental Analysis")
      with st.spinner("Fetching fundamentals..."):
          fund = get_fundamentals(symbol)
      if fund:
          # ── Flags / Quick Signals ──
          if fund.get("flags"):
              flags_html = " ".join(
                  f'<span style="background:{c}20;border:1px solid {c}40;color:{c};'
                  f'padding:3px 8px;border-radius:12px;font-size:10px;font-weight:600;margin-right:4px">{t}</span>'
                  for t, c in fund["flags"]
              )
              st.markdown(f'<div style="margin-bottom:12px">{flags_html}</div>', unsafe_allow_html=True)

          # ── Row 1: Identity & Valuation ──
          f_col1, f_col2, f_col3, f_col4 = st.columns(4)
          with f_col1:
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">SECTOR / INDUSTRY</div>'
                  f'<div style="font-size:12px;color:#e8ecff;font-weight:600;margin-top:4px">{fund.get("sector","N/A")}</div>'
                  f'<div style="font-size:10px;color:#6b7099">{fund.get("industry","N/A")}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col2:
              pe_str = f'{fund["pe_ratio"]}x' if fund.get("pe_ratio") else "N/A"
              fwd_pe_str = f'{fund["forward_pe"]}x' if fund.get("forward_pe") else "N/A"
              peg_str = f'{fund["peg_ratio"]}' if fund.get("peg_ratio") else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">VALUATION</div>'
                  f'<div style="font-size:12px;color:{fund["valuation_color"]};font-weight:600;margin-top:4px">{fund["valuation"]}</div>'
                  f'<div style="font-size:10px;color:#6b7099">P/E: {pe_str} · Fwd: {fwd_pe_str} · PEG: {peg_str}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col3:
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">MARKET CAP</div>'
                  f'<div style="font-size:12px;color:#e8ecff;font-weight:600;margin-top:4px">{fund["market_cap_str"]}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Revenue: {fund.get("revenue_str","N/A")}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col4:
              eps_t = f'${fund["trailing_eps"]}' if fund.get("trailing_eps") else "N/A"
              eps_f = f'${fund["forward_eps"]}' if fund.get("forward_eps") else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">EPS</div>'
                  f'<div style="font-size:12px;color:#e8ecff;font-weight:600;margin-top:4px">TTM: {eps_t}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Forward: {eps_f}</div>'
                  f'</div>', unsafe_allow_html=True)

          # ── Row 2: Growth & Profitability ──
          f_col5, f_col6, f_col7, f_col8 = st.columns(4)
          with f_col5:
              rg = fund.get("revenue_growth")
              rg_str = f'{rg*100:+.1f}%' if rg is not None else "N/A"
              rg_color = "#00e5a0" if rg and rg > 0 else ("#ff4d6a" if rg and rg < 0 else "#6b7099")
              eg = fund.get("earnings_growth")
              eg_str = f'{eg*100:+.1f}%' if eg is not None else "N/A"
              eg_color = "#00e5a0" if eg and eg > 0 else ("#ff4d6a" if eg and eg < 0 else "#6b7099")
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">GROWTH (QoQ YoY)</div>'
                  f'<div style="font-size:12px;margin-top:4px">'
                  f'<span style="color:{rg_color};font-weight:600">Rev: {rg_str}</span></div>'
                  f'<div style="font-size:10px"><span style="color:{eg_color}">Earnings: {eg_str}</span></div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col6:
              pm = fund.get("profit_margin")
              pm_str = f'{pm*100:.1f}%' if pm is not None else "N/A"
              pm_color = "#00e5a0" if pm and pm > 0 else ("#ff4d6a" if pm and pm < 0 else "#6b7099")
              gm = fund.get("gross_margin")
              gm_str = f'{gm*100:.1f}%' if gm is not None else "N/A"
              om = fund.get("operating_margin")
              om_str = f'{om*100:.1f}%' if om is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">MARGINS</div>'
                  f'<div style="font-size:12px;color:{pm_color};font-weight:600;margin-top:4px">Net: {pm_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Gross: {gm_str} · Op: {om_str}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col7:
              roe_val = fund.get("roe")
              roe_str = f'{roe_val*100:.1f}%' if roe_val is not None else "N/A"
              roe_color = "#00e5a0" if roe_val and roe_val > 0.15 else ("#f5c842" if roe_val and roe_val > 0 else "#ff4d6a" if roe_val else "#6b7099")
              roa_val = fund.get("roa")
              roa_str = f'{roa_val*100:.1f}%' if roa_val is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">EFFICIENCY</div>'
                  f'<div style="font-size:12px;color:{roe_color};font-weight:600;margin-top:4px">ROE: {roe_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">ROA: {roa_str}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col8:
              de = fund.get("debt_to_equity")
              de_str = f'{de}%' if de is not None else "N/A"
              de_color = "#00e5a0" if de is not None and de < 50 else ("#f5c842" if de is not None and de < 150 else "#ff4d6a" if de else "#6b7099")
              cr = fund.get("current_ratio")
              cr_str = f'{cr}' if cr is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">BALANCE SHEET</div>'
                  f'<div style="font-size:12px;color:{de_color};font-weight:600;margin-top:4px">D/E: {de_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Current Ratio: {cr_str}</div>'
                  f'</div>', unsafe_allow_html=True)

          # ── Row 3: Analyst & Risk ──
          f_col9, f_col10, f_col11, f_col12 = st.columns(4)
          with f_col9:
              rec = fund.get("rec_key", "N/A").upper()
              rec_colors = {"STRONG_BUY": "#00e5a0", "BUY": "#00e5a0", "HOLD": "#f5c842",
                            "SELL": "#ff4d6a", "STRONG_SELL": "#ff4d6a"}
              rec_color = rec_colors.get(rec, "#6b7099")
              n_analysts = fund.get("num_analysts", "N/A")
              tp = f'${fund["target_price"]}' if fund.get("target_price") else "N/A"
              tl = f'${fund["target_low"]}' if fund.get("target_low") else "?"
              th = f'${fund["target_high"]}' if fund.get("target_high") else "?"
              up = fund.get("target_upside")
              up_str = f'{up:+.1f}%' if up is not None else ""
              up_color = "#00e5a0" if up and up > 0 else "#ff4d6a"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">ANALYST CONSENSUS ({n_analysts})</div>'
                  f'<div style="font-size:12px;color:{rec_color};font-weight:600;margin-top:4px">{rec.replace("_"," ")}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Target: {tp} <span style="color:{up_color}">{up_str}</span> ({tl}–{th})</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col10:
              beta_val = fund.get("beta")
              beta_str = f'{beta_val}' if beta_val is not None else "N/A"
              beta_color = "#00e5a0" if beta_val and beta_val < 1 else ("#f5c842" if beta_val and beta_val < 1.5 else "#ff4d6a" if beta_val else "#6b7099")
              sr = fund.get("short_ratio")
              sr_str = f'{sr} days' if sr is not None else "N/A"
              sp = fund.get("short_pct")
              sp_str = f'{sp*100:.1f}%' if sp is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">RISK</div>'
                  f'<div style="font-size:12px;color:{beta_color};font-weight:600;margin-top:4px">Beta: {beta_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Short: {sp_str} ({sr_str})</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col11:
              dy = fund.get("dividend_yield")
              dy_str = f'{dy*100:.2f}%' if dy is not None else "None"
              dy_color = "#00e5a0" if dy and dy > 0.02 else "#6b7099"
              pr = fund.get("payout_ratio")
              pr_str = f'{pr*100:.0f}%' if pr is not None else "N/A"
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">DIVIDEND</div>'
                  f'<div style="font-size:12px;color:{dy_color};font-weight:600;margin-top:4px">Yield: {dy_str}</div>'
                  f'<div style="font-size:10px;color:#6b7099">Payout: {pr_str}</div>'
                  f'</div>', unsafe_allow_html=True)
          with f_col12:
              w52h = f'${fund["week52_high"]}' if fund.get("week52_high") else "N/A"
              w52l = f'${fund["week52_low"]}' if fund.get("week52_low") else "N/A"
              w52pos = fund.get("week52_position")
              w52pos_str = f'{w52pos:.0f}%' if w52pos is not None else "N/A"
              pfh = fund.get("pct_from_high")
              pfh_str = f'{pfh:+.1f}%' if pfh is not None else ""
              pfh_color = "#00e5a0" if pfh and pfh > -5 else ("#f5c842" if pfh and pfh > -20 else "#ff4d6a" if pfh else "#6b7099")
              st.markdown(
                  f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                  f'<div style="font-size:9px;color:#6b7099">52-WEEK RANGE</div>'
                  f'<div style="font-size:12px;color:{pfh_color};font-weight:600;margin-top:4px">{pfh_str} from high</div>'
                  f'<div style="font-size:10px;color:#6b7099">{w52l} — {w52h} (pos: {w52pos_str})</div>'
                  f'</div>', unsafe_allow_html=True)
      else:
          st.markdown(
              '<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:16px;border-radius:6px;text-align:center">'
              '<div style="color:#6b7099;font-size:11px">Fundamental data unavailable — yfinance may be rate-limited or ticker not found</div>'
              '</div>', unsafe_allow_html=True)

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
                  row = {
                      "Type": u['type'],
                      "Strike": f"${u['strike']:.2f}",
                      "Expiry": u['expiry'],
                      "Volume": f"{u['volume']:,}",
                      "OI": f"{u['oi']:,}",
                      "Vol/OI": f"{u['vol_oi_ratio']:.1f}x",
                      "🔥": "⚡" if u.get('is_unusual') else "",
                  }
                  if u.get("moneyness"):
                      row["Moneyness"] = u["moneyness"]
                      row["Intent"] = u.get("intent", "")
                  unusual_data.append(row)
            
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
                  row = {
                      "Type": u['type'],
                      "Strike": f"${u['strike']:.2f}",
                      "Expiry": u['expiry'],
                      "Volume": f"{u['volume']:,}",
                      "OI": f"{u['oi']:,}",
                      "Vol/OI": f"{u['vol_oi_ratio']:.1f}x",
                  }
                  if u.get("moneyness"):
                      row["Moneyness"] = u["moneyness"]
                      row["Intent"] = u.get("intent", "")
                  top_data.append(row)
            
              top_df = pd.DataFrame(top_data)
              st.dataframe(top_df, use_container_width=True, hide_index=True)
              st.info("No contracts met unusual activity thresholds (Vol>1000 or Vol/OI>2x)")
          else:
              st.warning("No volume data available. The options snapshot API may require a Polygon paid plan.")
              st.markdown('<div style="font-size:9px;color:#6b7099">'
                         'OI data is still available above from the contracts API.</div>',
                         unsafe_allow_html=True)
        
          # ── Delta-Aware Analysis Section ──
          delta_data = options_data.get("delta_analysis")
          delta_sent = options_data.get("delta_sentiment", "N/A")
          delta_clr = options_data.get("delta_color", "#6b7099")
          delta_dsc = options_data.get("delta_desc", "")
          if delta_data and delta_sent != "N/A":
              st.markdown("#### 🎯 Delta-Aware Sentiment")
              st.markdown(
                  '<div style="font-size:9px;color:#6b7099;margin-bottom:8px">'
                  'Classifies options by moneyness: deep ITM calls → hedges/covered calls, '
                  'far OTM puts → protective hedges, ATM/OTM → directional bets</div>',
                  unsafe_allow_html=True)
              da_col1, da_col2, da_col3 = st.columns(3)
              with da_col1:
                  st.markdown(
                      f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                      f'<div style="font-size:9px;color:#6b7099">SPECULATIVE BULLISH</div>'
                      f'<div style="font-size:9px;color:#6b7099;margin-top:2px">(OTM + ATM Calls)</div>'
                      f'<div style="font-size:14px;color:#00e5a0;font-weight:700;margin-top:6px">'
                      f'{delta_data["spec_bull_oi"] + delta_data["atm_call_oi"]:,} OI</div>'
                      f'<div style="font-size:10px;color:#6b7099">Vol: {delta_data["spec_bull_vol"] + delta_data["atm_call_vol"]:,}</div>'
                      f'</div>', unsafe_allow_html=True)
              with da_col2:
                  st.markdown(
                      f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                      f'<div style="font-size:9px;color:#6b7099">SPECULATIVE BEARISH</div>'
                      f'<div style="font-size:9px;color:#6b7099;margin-top:2px">(ATM + ITM Puts)</div>'
                      f'<div style="font-size:14px;color:#ff4d6a;font-weight:700;margin-top:6px">'
                      f'{delta_data["spec_bear_oi"] + delta_data["atm_put_oi"]:,} OI</div>'
                      f'<div style="font-size:10px;color:#6b7099">Vol: {delta_data["spec_bear_vol"] + delta_data["atm_put_vol"]:,}</div>'
                      f'</div>', unsafe_allow_html=True)
              with da_col3:
                  st.markdown(
                      f'<div style="background:#0d0f17;border:1px solid #1a1d2e;padding:12px;border-radius:6px">'
                      f'<div style="font-size:9px;color:#6b7099">HEDGES (DISCOUNTED)</div>'
                      f'<div style="font-size:9px;color:#6b7099;margin-top:2px">(Deep ITM Calls + Far OTM Puts)</div>'
                      f'<div style="font-size:14px;color:#f5c842;font-weight:700;margin-top:6px">'
                      f'{delta_data["hedge_call_oi"] + delta_data["hedge_put_oi"]:,} OI</div>'
                      f'<div style="font-size:10px;color:#6b7099">Vol: {delta_data["hedge_call_vol"] + delta_data["hedge_put_vol"]:,}</div>'
                      f'</div>', unsafe_allow_html=True)
              st.markdown(
                  f'<div style="background:#0d0f1799;border:1px solid {delta_clr}40;'
                  f'padding:12px;border-radius:4px;margin-top:8px">'
                  f'<div style="font-size:10px;color:#6b7099">DELTA-ADJUSTED SENTIMENT</div>'
                  f'<div style="font-size:14px;color:{delta_clr};font-weight:bold;margin-top:4px">'
                  f'{delta_sent}</div>'
                  f'<div style="font-size:10px;color:#6b7099;margin-top:4px">{delta_dsc}</div>'
                  f'</div>', unsafe_allow_html=True)

          # Summary box
          _delta_line = ""
          if options_data.get("delta_sentiment") and options_data["delta_sentiment"] != "N/A":
              _dc = options_data["delta_color"]
              _ds = options_data["delta_sentiment"]
              _delta_line = (f'<div style="font-size:12px;color:{_dc};font-weight:bold;margin-top:4px">'
                            f'{_ds} (Delta-Adjusted)</div>')
          st.markdown(
              f'<div style="background:#0d0f1799;border:1px solid {options_data["sentiment_color"]}40;'
              f'padding:12px;border-radius:4px;margin-top:12px">'
              f'<div style="font-size:10px;color:#6b7099">OPTIONS FLOW SUMMARY</div>'
              f'<div style="font-size:12px;color:{options_data["sentiment_color"]};font-weight:bold;margin-top:4px">'
              f'{options_data["sentiment"]} (Raw OI)</div>'
              f'{_delta_line}'
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
    
      # ── Strategy Analysis Section (Fib + FVG + Weinstein + Bias) ── (optional)
      if use_strategy:
          st.markdown("---")
          st.markdown("### 📊 Strategy Analysis (Fib + Weinstein + Bias)")
        
          strategy_data = analyze_strategy_signals(daily_df)
    
          if strategy_data and "error" not in strategy_data:
              # Main signal display
              if strategy_data["short_signal"]:
                  signal_color = "#ff4d6a"
                  signal_text = f"SHORT SIGNAL ({strategy_data['short_tier']})"
                  signal_emoji = "🔴"
              elif strategy_data["long_signal"]:
                  signal_color = "#00e5a0"
                  signal_text = f"LONG SIGNAL ({strategy_data['long_tier']})"
                  signal_emoji = "🟢"
              else:
                  signal_color = "#6b7099"
                  signal_text = "NO SIGNAL"
                  signal_emoji = "⚪"
            
              st.markdown(
                  f'<div style="background:linear-gradient(135deg, #0d0f17 0%, #131625 100%);'
                  f'border:2px solid {signal_color};border-radius:8px;padding:16px;margin:12px 0;text-align:center">'
                  f'<div style="font-size:24px">{signal_emoji}</div>'
                  f'<div style="font-size:18px;font-weight:bold;color:{signal_color};margin-top:8px">{signal_text}</div>'
                  f'</div>',
                  unsafe_allow_html=True
              )
            
              # Take profit alerts
              if strategy_data["short_tp_hit"]:
                  st.markdown(
                      '<div style="background:#00e5a020;border:1px solid #00e5a060;padding:12px;border-radius:4px;margin:8px 0">'
                      '<span style="font-size:14px">💰</span> '
                      '<span style="color:#00e5a0;font-weight:bold">SHORT TAKE PROFIT ZONE</span>'
                      '<span style="font-size:11px;color:#6b7099"> — Price dropped 10%+ from recent high</span>'
                      '</div>',
                      unsafe_allow_html=True
                  )
              if strategy_data["long_tp_hit"]:
                  st.markdown(
                      '<div style="background:#00e5a020;border:1px solid #00e5a060;padding:12px;border-radius:4px;margin:8px 0">'
                      '<span style="font-size:14px">💰</span> '
                      '<span style="color:#00e5a0;font-weight:bold">LONG TAKE PROFIT ZONE</span>'
                      '<span style="font-size:11px;color:#6b7099"> — Price rose 10%+ from recent low</span>'
                      '</div>',
                      unsafe_allow_html=True
                  )
            
              # Condition breakdown
              st.markdown("#### 📋 Signal Conditions")
            
              col1, col2 = st.columns(2)
            
              with col1:
                  st.markdown("**Structure**")
                  swing_icon = "🔴" if strategy_data["is_bearish_swing"] else ("🟢" if strategy_data["is_bullish_swing"] else "⚪")
                  trend_icon = "🔴" if strategy_data["is_downtrend"] else ("🟢" if strategy_data["is_uptrend"] else "⚪")
                  bias_icon = "🔴" if strategy_data["is_bearish_bias"] else ("🟢" if strategy_data["is_bullish_bias"] else "⚪")
                
                  st.markdown(f"{swing_icon} Swing: **{'Bearish' if strategy_data['is_bearish_swing'] else ('Bullish' if strategy_data['is_bullish_swing'] else 'Neutral')}**")
                  st.markdown(f"{trend_icon} Trend: **{'Down' if strategy_data['is_downtrend'] else ('Up' if strategy_data['is_uptrend'] else 'Ranging')}**")
                  st.markdown(f"{bias_icon} Bias: **{'Bearish' if strategy_data['is_bearish_bias'] else ('Bullish' if strategy_data['is_bullish_bias'] else 'Neutral')}**")
                
              with col2:
                  st.markdown("**Volume & Candle**")
                  vol_icon = "✅" if strategy_data["high_volume"] else "❌"
                  candle_icon = "🟢" if strategy_data.get("is_green_candle") else ("🔴" if strategy_data.get("is_red_candle") else "⚪")
                  seller_icon = "🔴" if strategy_data["seller_conviction"] else "⚪"
                  buyer_icon = "🟢" if strategy_data["buyer_conviction"] else "⚪"
                
                  candle_dir = "Green (Close > Open)" if strategy_data.get("is_green_candle") else ("Red (Close < Open)" if strategy_data.get("is_red_candle") else "Doji")
                  st.markdown(f"{candle_icon} Daily Candle: **{candle_dir}**")
                  st.markdown(f"{vol_icon} High Volume: **{strategy_data['volume_ratio']:.1f}x** avg")
                  st.markdown(f"{seller_icon} Seller Conviction: **{'Yes' if strategy_data['seller_conviction'] else 'No'}**")
                  st.markdown(f"{buyer_icon} Buyer Conviction: **{'Yes' if strategy_data['buyer_conviction'] else 'No'}**")
            
              # Weinstein indicators
              st.markdown("#### 📈 Weinstein Stage Analysis")
              wei_col1, wei_col2, wei_col3 = st.columns(3)
            
              with wei_col1:
                  st.metric("Price Position", f"{strategy_data['price_position']:.0f}%", 
                           f"of 52-week range")
              with wei_col2:
                  st.metric("From 52W High", f"-{strategy_data['dist_from_high']:.1f}%",
                           "MA10 < MA30" if strategy_data["ma10_below_ma30"] else "MA10 > MA30")
              with wei_col3:
                  st.metric("Breakdown Score", f"{strategy_data['breakdown_score']}/5",
                           "Higher = more bearish" if strategy_data['breakdown_score'] >= 3 else "Low score")
            
              # FVG info
              if strategy_data["fvg_details"]:
                  fvg = strategy_data["fvg_details"]
                  fvg_color = "#ff4d6a" if fvg["type"] == "BEARISH" else "#00e5a0"
                  st.markdown(
                      f'<div style="background:#0d0f1799;border:1px solid {fvg_color}40;'
                      f'padding:10px;border-radius:4px;margin-top:12px">'
                      f'<div style="font-size:10px;color:#6b7099">FAIR VALUE GAP</div>'
                      f'<div style="font-size:14px;color:{fvg_color};font-weight:bold">'
                      f'{fvg["type"]} FVG ({fvg["size_pct"]:.2f}%)</div>'
                      f'<div style="font-size:10px;color:#6b7099">'
                      f'Gap zone: ${fvg["bottom"]:.2f} - ${fvg["top"]:.2f}</div>'
                      f'</div>',
                      unsafe_allow_html=True
                  )
            
              # Short signal criteria summary
              with st.expander("📝 Short Signal Criteria", expanded=False):
                  st.markdown("""
                  **Tier 1 (Full Alignment):**
                  - ✅ Bearish Swing (recent low more recent than high)
                  - ✅ Seller Conviction (high volume + close in lower half)
                  - ✅ Downtrend (lower highs & lower lows)
                  - ✅ Bearish Bias (price below midpoint)
                
                  **Tier 2 (Trend + Bias):**
                  - ✅ Bearish Swing
                  - ✅ Bearish Bias
                  - ✅ Breakout Score ≤ 3
                  - ✅ No Buyer Conviction
                
                  **SHORT TAKE PROFIT:** Price drops 10% from entry
                  """)
          else:
              st.warning("Insufficient data for strategy analysis. Need at least 50 bars.")
    
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

# ── Run backtest ─────────────────────────────────────
if run_btn:
  with tab_fetch:
    if st.session_state.fetched_data is None:
        st.warning("Please click **📅 FETCH EARNINGS** first to load earnings data.")

if run_btn and st.session_state.fetched_data is not None:
  with tab_fetch:
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
