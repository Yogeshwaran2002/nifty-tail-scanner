# Phase 2 — Interactive Telegram bot (Cloudflare Workers, free)

An always-on Telegram bot that answers questions about the scanner's latest
output. It runs on **Cloudflare Workers** (free tier: 100k requests/day) as a
**webhook** — no polling, no server. Free-text questions are answered by a free
**open-source model (Llama) via Cloudflare Workers AI** — no external API key,
no Anthropic, nothing to pay.

It is **read-only**: it reads the JSON this scanner publishes to GitHub Pages and
never computes a signal or places an order (idea.md's rule for the conversational
layer).

## Commands
- `/today` — latest scan summary
- `/why` — which entry gates passed / failed, with values
- `/journal` — paper trades + loss budget
- `/backtest` — backtest stats (expectancy, payoff ratio, …)
- `/skew` — volatility smile + risk premium
- `/help`
- Any other text → the Llama explainer (strictly grounded on the scan JSON; it
  refuses to give buy/sell advice or forecasts)

## One-time deploy (all free)

1. **Install + log in to Cloudflare** (free account):
   ```bash
   npm install -g wrangler
   wrangler login
   ```
2. **Create the bot** — message **@BotFather** on Telegram → `/newbot` → copy the
   bot token. Find your numeric chat id: message your bot once, then open
   `https://api.telegram.org/bot<token>/getUpdates` and read `message.chat.id`.
3. **Set secrets** (run from this `worker/` folder):
   ```bash
   wrangler secret put TELEGRAM_BOT_TOKEN   # paste the BotFather token
   wrangler secret put WEBHOOK_SECRET       # any random string you invent
   wrangler secret put ALLOWED_CHAT_ID      # your numeric chat id (keeps the bot private)
   ```
4. **Deploy:**
   ```bash
   wrangler deploy
   ```
   Copy the deployed URL, e.g. `https://nifty-tail-bot.<you>.workers.dev`.
5. **Register the webhook** with Telegram (pass the same `WEBHOOK_SECRET`):
   ```bash
   curl "https://api.telegram.org/bot<token>/setWebhook?url=https://nifty-tail-bot.<you>.workers.dev&secret_token=<WEBHOOK_SECRET>"
   ```
6. Message your bot `/today`. Done.

## Notes
- **Workers AI free tier** is a daily neuron allowance (ample for personal use). If
  you exhaust it, fixed commands keep working; only free-text chat pauses.
- Swap the model by editing `LLM_MODEL` in `wrangler.toml` (any Workers AI text
  model, e.g. `@cf/meta/llama-3.3-70b-instruct-fp8-fast`) and `wrangler deploy`.
- The bot only answers `ALLOWED_CHAT_ID`, so strangers can't use your quota.
- It reads from `DATA_BASE_URL` (your GitHub Pages `data/` folder). The daily
  GitHub Action keeps that JSON fresh; the bot always reflects the newest scan.
