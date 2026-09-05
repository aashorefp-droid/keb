# StockPulse — Earnings Backtest (Yahoo Finance Edition)

Streamlit app for backtesting an earnings-day trading strategy with real-time market data from Yahoo Finance (`yfinance`). Optional integrations are available for Polygon and Finnhub.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # optional: fill in optional keys if needed
streamlit run claud.py
```

## Configuration (environment variables)

Primary market data is fetched from Yahoo Finance via `yfinance` without requiring API keys. Optional integrations (Polygon, Finnhub, Telegram) can be configured via environment variables. For local development, values in a `.env` file are loaded automatically via `python-dotenv`. See `.env.example` for the template.

| Variable | Required | Description | Example |
|---|---|---|---|
| `POLYGON_API_KEY` | ❌ Optional | Auto-detect earnings dates | — |
| `FINNHUB_API_KEY` | ❌ Optional | Upcoming earnings tickers | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | ❌ Optional | Telegram notifications | — |
| `ALPACA_API_KEY` | ❌ Optional | Optional Alpaca data fallback | — |
| `ALPACA_SECRET_KEY` | ❌ Optional | Optional Alpaca data fallback | — |
| `ALPACA_BASE_URL` | ❌ Optional | Optional Alpaca trading base URL | — |

## Deploying on Render

1. Create a new **Web Service** in [Render](https://dashboard.render.com) connected to this repo.
2. Set the build/start commands:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `bash start.sh` (the repo already includes `start.sh`, which runs Streamlit on the `$PORT` Render provides). Alternatively, set the start command inline: `streamlit run claud.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true`
3. Optionally set environment variables in the Render dashboard:
   - Go to your service → **Environment** tab → **Add Environment Variable**.
   - Optionally add `POLYGON_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
4. Deploy. The app runs out of the box using Yahoo Finance as the default data source — no API keys required for basic operation.

## 🔒 Security

**Never commit secrets.** Optional API keys, tokens, and `.env` files are excluded via `.gitignore`. Use `.env.example` (placeholders only) as the template, and store production values exclusively in Render's environment settings.
