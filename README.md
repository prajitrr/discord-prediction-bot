# Discord Prediction Market Bot

A fully-featured prediction market bot for Discord, powered by the **LMSR
(Logarithmic Market Scoring Rule)** — the same mechanism used by Kalshi and
Polymarket.  Users receive fake money and can bet on yes/no questions just
like a real prediction market.

---

## Features

| Command | Who | Description |
|---|---|---|
| `/balance [user]` | Everyone | Check your (or another user's) balance |
| `/give_money @user amount` | Admin | Give fake money to a user |
| `/take_money @user amount` | Admin | Take fake money from a user |
| `/leaderboard` | Everyone | Top 10 balances on the server |
| `/create_market question [liquidity]` | Admin | Open a new yes/no market |
| `/markets` | Everyone | List all open markets |
| `/market id` | Everyone | View details + price chart for a market |
| `/bet id yes\|no amount` | Everyone | Buy YES or NO shares |
| `/sell id yes\|no shares` | Everyone | Sell shares back to the market |
| `/my_positions` | Everyone | Show your current open positions |
| `/resolve id yes\|no` | Admin | Resolve a market and pay out winners |

### Market mechanics

* Markets start at exactly **50 % YES / 50 % NO**.
* Prices move automatically as users buy shares — more YES buyers → higher YES
  price, just like Polymarket.
* Each winning share pays out **$1.00** on resolution.
* Shares can be bought or sold at any time while the market is open.
* The `liquidity` parameter `b` controls price sensitivity:
  * Lower `b` → bigger price moves per dollar (thinner market).
  * Higher `b` → smaller price moves (deeper market).
  * Default is **100** (suitable for a server with a few active traders).

---

## Hosting on Pella.app (free)

1. **Create a Discord bot** at <https://discord.com/developers/applications>
   * Enable the `applications.commands` scope.
   * Under *Bot → Privileged Gateway Intents*, no extra intents are needed.
   * Copy your bot **token**.

2. **Sign up** at <https://pella.app> (free, no credit card required).

3. **Create a new project** → *Upload Files* or link this GitHub repo.

4. **Upload all four files**:
   ```
   bot.py
   db.py
   market.py
   requirements.txt
   ```

5. **Set runtime** → Python.  **Start command** → `python bot.py`.

6. **Add environment variable**:
   | Key | Value |
   |---|---|
   | `DISCORD_TOKEN` | your bot token |

7. Click **Deploy / Start**.

The bot will automatically sync slash commands on startup.  Allow up to a
minute for Discord to propagate the new commands to your server.

---

## Local development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DISCORD_TOKEN=your_token_here
python bot.py
```

---

## How the LMSR works

The LMSR cost function is:

```
C(q_yes, q_no) = b · ln( exp(q_yes/b) + exp(q_no/b) )
```

* `q_yes`, `q_no` — total outstanding shares for each side.
* Probability of YES = `exp(q_yes/b) / (exp(q_yes/b) + exp(q_no/b))`.
* Cost to buy `Δ` YES shares = `C(q_yes+Δ, q_no) − C(q_yes, q_no)`.
* Selling is the reverse operation.

The house subsidises initial liquidity (guaranteed maximum loss ≈ `b·ln(2)`
per market).  Because this is fake money that is fine — and it ensures there
is always a price to trade at.
