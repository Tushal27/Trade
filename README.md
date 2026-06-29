# Trade Signal Bot 📈

A bot that watches **Bitcoin (BTCUSDT)**, decides when a trade looks good
(**LONG / SHORT / EXIT**), and **emails you on Gmail** the moment the
decision changes. You take the trade yourself — the bot never touches your
money and needs **no exchange account or API keys**.

Every signal email includes the entry price, a suggested **stop-loss** and
**target**, and the reason for the call. See [ARCHITECTURE.md](ARCHITECTURE.md)
for the full system design.

> ⚠️ **Honest note:** no bot can promise a high win rate. This system aims for
> profitable *risk/reward discipline* (≥1.5R targets, volatility-scaled stops,
> staying out of dangerous chop) — not for being right every time. Never risk
> money you can't afford to lose.

---

## Setup (one time, ~10 minutes)

### Step 1 — Create a Gmail App Password

The bot sends email through your own Gmail account using an **App Password**
(a special 16-character password just for apps — your real password is never
used or stored in code).

1. Go to your Google Account → **Security**.
2. Turn ON **2-Step Verification** (required for app passwords).
3. Go to <https://myaccount.google.com/apppasswords>.
4. Enter a name like `trade-bot` and click **Create**.
5. Copy the 16-character password it shows you (e.g. `abcd efgh ijkl mnop`).

### Step 2 — Add the secrets to GitHub

1. Open this repository on GitHub → **Settings** → **Secrets and variables**
   → **Actions** → **New repository secret**.
2. Add these two secrets:

| Secret name | Value |
|---|---|
| `GMAIL_ADDRESS` | your Gmail address (e.g. `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | the 16-character app password from Step 1 (spaces don't matter) |

Optional: add `RECIPIENT_EMAIL` if you want signals sent to a *different*
address. By default the bot emails your own Gmail.

### Step 3 — Test it

1. Go to the **Actions** tab → **Trade Signal Bot** → **Run workflow**.
2. Tick **"Send a status email even if no signal changed"** → **Run workflow**.
3. Within a couple of minutes you should get a 📊 status email with the
   current market read for BTC. If the run fails, open it — the log
   will say exactly what's wrong (usually a mistyped app password).

### Optional — Instant Telegram alerts on your phone (free)

Email is the reliable record; Telegram adds an instant push notification.
Both channels get every signal once this is set up.

1. Open Telegram and search for **@BotFather** (the official one, blue
   checkmark). Send it `/newbot`, pick any name (e.g. `My Trade Alerts`) and
   any username ending in `bot` (e.g. `tushal_trade_alerts_bot`).
2. BotFather replies with a **token** like `123456789:AAH9x...` — copy it.
3. **Press Start on your new bot** (open the link BotFather gives you and tap
   START). This step is required — bots can't message you first.
4. Get your **chat id**: search for **@userinfobot**, press Start, and it
   replies with your numeric id (e.g. `1234567890`).
5. Add two more repository secrets (Settings → Secrets and variables →
   Actions):

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | your numeric id from @userinfobot |

6. Test again via Actions → Trade Signal Bot → Run workflow (tick the status
   email box) — you should now get both the email **and** a Telegram message.

If the Telegram secrets aren't set, the bot simply skips Telegram — email
keeps working as before.

### Optional — The Brain 🧠 (AI analyst commentary on every alert)

Add Claude (the AI by Anthropic) as an analyst inside your bot: every signal
alert gains a short plain-language market read — what the regime means, the
key risks around this exact setup, and a discipline reminder.

**Important design rule:** the Brain only *explains* — it can never change,
veto, or create a trade decision. The backtested rules keep full authority
(see ARCHITECTURE.md for why).

Three providers — the bot uses whichever it finds (priority: your own
engine → Claude → Gemini):

**Option C — Your own OpenAI-compatible engine (e.g. a multi-model gateway):**
1. Add two repository secrets: `OPENAI_BASE_URL` (e.g. `https://your-domain/v1`)
   and `OPENAI_API_KEY`.
2. Optional: a repository *variable* `OPENAI_MODEL` (defaults to `auto`).
That's it — your engine writes the 🧠 Brain's read on every alert.

Or pick one of the hosted options below:

**Option A — Google Gemini (free, recommended to start):**
1. Go to <https://aistudio.google.com> → sign in with your Google account →
   **Get API key** → **Create API key**. No card needed; the free tier is
   far more than this bot will ever use.
2. Add it as a repository secret named `GEMINI_API_KEY`.

**Option B — Claude by Anthropic (premium quality, ~$0.01 per alert):**
1. Create an account at <https://platform.claude.com>, add a small credit
   (even $5 lasts a very long time), and create an API key (`sk-ant-...`).
2. Add it as a repository secret named `ANTHROPIC_API_KEY`.

Done — the next alert will include a "🧠 Brain's read" section. If both keys
are set, Claude is used. No key = no Brain = bot works exactly as before.
(Note: on Gemini's free tier, Google may use the prompts to improve their
products — your prompts contain only public market data, nothing personal.)

### Step 4 — Turn on the automatic schedule

GitHub only runs scheduled jobs from the **main** branch. Once you're happy,
merge this branch into `main` — after that the bot checks the market
**every 15 minutes, 24/7**, and emails you only when a signal changes.
No server, no PC left running, completely free.

---

## What the emails look like

```
Subject: 🚨 Trade Signal: LONG BTCUSDT @ 67,250.00

BTCUSDT  —  FLAT -> LONG
  Price:   67,250.00
  Regime:  TREND_UP / NORMAL (confidence 84%)
  Stop:    66,100.00
  Target:  68,975.00  (~1.5R)
  Why:     Trend entry: 4h uptrend + 1h EMA20>EMA50, price above EMA20,
           RSI 56 not overbought.
```

**Suggested money rule:** risk no more than **1% of your capital** per
signal. The distance between entry and stop tells you your position size —
never the other way around.

## Beyond entry signals

**Trade tracking.** When a signal opens, the bot remembers its stop and
target and watches every run. You get a 🎯 **TARGET HIT** or 🛑 **STOP HIT**
alert with the result in R (e.g. `+1.50R`), and the outcome is recorded in
`state/ledger.json`. After a stop-out the symbol gets a 6-hour cooldown — no
revenge re-entries.

**Weekly scorecard.** Every Monday you get a 📈 email/Telegram summary of the
week and all-time: closed trades, real win rate, total R. After a month you
will *know* your actual success rate — no guessing.

**Entry filters (quality control).** Two vetoes block statistically weak
entries (they never block exits):
- *Funding veto* — when perp funding is extreme, the trade is crowded and
  fragile; the bot refuses to join the crowd.
- *BTC veto* — ETH longs are blocked while BTC is in a 4h downtrend (and
  ETH shorts while BTC trends up). Alts follow BTC.

**Backtesting.** Actions tab → **Backtest** → Run workflow (choose how many
days). It replays history through the *exact same live signal code* —
conservative fills, 0.16% fees per trade — and emails you the report: win
rate, average R, total R, max drawdown. Run it before trusting the bot with
real risk sizes.

## How it decides (short version)

1. **Regime first (4h chart):** is the market trending up, trending down, or
   ranging? Is volatility compressed or exploding?
2. **Signal second (1h chart):**
   - In a trend → join the trend on a healthy pullback (never chases
     overbought/oversold extremes).
   - In a range → fade extremes at the Bollinger bands, but **only when
     volatility is calm** — in violent chop it stands aside on purpose.
3. **Email only on change:** HOLD → LONG, LONG → EXIT, etc. No spam.

Full details, including known weaknesses: [ARCHITECTURE.md](ARCHITECTURE.md).

## Run it locally (optional)

Pure Python 3.11+, zero dependencies:

```bash
python -m bot.main --dry-run        # print decisions, send nothing
python -m unittest discover -s tests   # run the offline test suite
```

To send real email locally, export `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`
first, then run `python -m bot.main`.

---

*Signals are generated automatically from public market data and are for
information only — not financial advice.*
