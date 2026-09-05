# StockPulse — Earnings Backtest (Alpaca/Polygon Edition)

Streamlit app for backtesting an earnings-day trading strategy with real-time market data from Alpaca (recommended) or Polygon.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys (never commit .env!)
streamlit run claud.py
```

## Configuration (environment variables)

Alpaca credentials and endpoints are read from environment variables. For local development, values in a `.env` file are loaded automatically via `python-dotenv`. See `.env.example` for the full template.

| Variable | Required | Description | Example |
|---|---|---|---|
| `ALPACA_API_KEY` | ✅ Yes | Alpaca API key ID | `PKXXXXXXXXXXXXXXXX` |
| `ALPACA_SECRET_KEY` | ✅ Yes | Alpaca secret key | `xxxxxxxxxxxxxxxxxxxxxxxx` |
| `ALPACA_BASE_URL` | ✅ Yes* | Trading base URL | Paper: `https://paper-api.alpaca.markets`<br>Live: `https://api.alpaca.markets` |
| `ALPACA_DATA_URL` | ❌ Optional | Market-data base URL | `https://data.alpaca.markets` (default) |
| `FINNHUB_API_KEY` | ❌ Optional | Upcoming earnings tickers | — |
| `POLYGON_API_KEY` | ❌ Optional | Auto-detect earnings dates | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | ❌ Optional | Telegram notifications | — |

\* If unset, `ALPACA_BASE_URL` defaults to the **paper** URL. Set it explicitly to avoid surprises.

### Paper vs. live

| Mode | `ALPACA_BASE_URL` | Credentials |
|---|---|---|
| Paper (default) | `https://paper-api.alpaca.markets` | Keys from the paper dashboard |
| Live | `https://api.alpaca.markets` | Keys from the live dashboard |

## Deploying on Render

1. Create a new **Web Service** in [Render](https://dashboard.render.com) connected to this repo.
2. Set the build/start commands:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `bash start.sh` (the repo already includes `start.sh`, which runs Streamlit on the `$PORT` Render provides). Alternatively, set the start command inline: `streamlit run claud.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true`
3. Set environment variables in the Render dashboard:
   - Go to your service → **Environment** tab → **Add Environment Variable**.
   - Add the **required** vars: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`.
   - Optionally add `ALPACA_DATA_URL`, `FINNHUB_API_KEY`, `POLYGON_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
4. Deploy. The app reads the variables at startup — no `.env` file needed on Render.

## 🔒 Security

**Never commit secrets.** Real API keys, tokens, and `.env` files are excluded via `.gitignore`. Use `.env.example` (placeholders only) as the template, and store production values exclusively in Render's environment settings. If a key is ever committed, rotate it immediately at [app.alpaca.markets](https://app.alpaca.markets).
