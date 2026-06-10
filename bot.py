"""
Discord Prediction Market Bot
==============================
Supports fake-money prediction markets using the LMSR (Logarithmic Market
Scoring Rule) – the same mechanism used by Kalshi and Polymarket.

Environment variables required:
  DISCORD_TOKEN  – your bot token from the Discord Developer Portal

Optional:
  DB_PATH        – path to the SQLite database file (default: predictions.db)
"""

import io
import os
import math
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
import matplotlib
matplotlib.use("Agg")  # non-interactive backend; must be set before pyplot import
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from db import Database
from market import (
    lmsr_prob_yes,
    shares_for_dollars_yes,
    shares_for_dollars_no,
    dollars_for_selling_yes,
    dollars_for_selling_no,
)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_B = 100.0       # LMSR liquidity parameter default
MIN_BET   = 0.01        # minimum dollars per trade
COLOR_OK  = 0x57F287    # discord green
COLOR_ERR = 0xED4245    # discord red
COLOR_INFO = 0x5865F2   # discord blurple
COLOR_GOLD = 0xFEE75C   # discord yellow

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()

class PredictionBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.db = Database()

    async def setup_hook(self) -> None:
        await self.db.init()
        await self.tree.sync()
        print("Slash commands synced globally.")

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id={self.user.id})")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        msg = str(error)
        if isinstance(error, app_commands.CheckFailure):
            msg = "You don't have permission to use this command."
        embed = discord.Embed(description=f"❌ {msg}", color=COLOR_ERR)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

bot = PredictionBot()

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt(amount: float) -> str:
    """Format a dollar amount nicely."""
    return f"${amount:,.2f}"

def pct(prob: float) -> str:
    """Format a probability as a percentage string."""
    return f"{prob * 100:.1f}%"

def require_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None

def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        return False
    return member.guild_permissions.administrator or member.guild_permissions.manage_guild

def admin_check(interaction: discord.Interaction) -> bool:
    if not is_admin(interaction):
        raise app_commands.CheckFailure("Administrator or Manage Guild permission required.")
    return True

def guild_id(interaction: discord.Interaction) -> str:
    return str(interaction.guild_id)

def uid(user: discord.User | discord.Member) -> str:
    return str(user.id)


def build_market_chart(history, question: str, market_id: int, resolution: str | None) -> io.BytesIO:
    """Return a PNG chart of YES probability over time as a BytesIO object."""
    probs = [row["probability"] * 100 for row in history]
    n = len(probs)

    fig, ax = plt.subplots(figsize=(10, 4), dpi=100)
    ax.plot(range(n), probs, color="#5865F2", linewidth=2.5, zorder=3)
    ax.fill_between(range(n), probs, alpha=0.15, color="#5865F2", zorder=2)

    ax.axhline(50, color="#888888", linewidth=0.8, linestyle="--", zorder=1)
    ax.set_ylim(0, 100)
    ax.set_xlim(0, max(n - 1, 1))
    ax.set_xlabel("Trade #", fontsize=10)
    ax.set_ylabel("YES Probability (%)", fontsize=10)

    title = question if len(question) <= 60 else question[:57] + "…"
    ax.set_title(f"Market #{market_id}: {title}", fontsize=11, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax.grid(True, alpha=0.25)

    if resolution:
        label_y = 95 if resolution == "yes" else 5
        color = "#57F287" if resolution == "yes" else "#ED4245"
        ax.axhline(
            100 if resolution == "yes" else 0,
            color=color, linewidth=2, linestyle="-",
            label=f"Resolved {resolution.upper()}",
        )
        ax.legend(fontsize=9)

    fig.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close(fig)
    return buf


# ── /balance ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="balance", description="Check your (or another user's) balance.")
@app_commands.describe(user="The user to check (defaults to you).")
async def cmd_balance(interaction: discord.Interaction, user: discord.Member | None = None):
    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return
    target = user or interaction.user
    bal = await bot.db.get_balance(uid(target), guild_id(interaction))
    embed = discord.Embed(
        title="💰 Balance",
        description=f"{target.mention} has **{fmt(bal)}**",
        color=COLOR_OK,
    )
    await interaction.response.send_message(embed=embed)


# ── /give_money ───────────────────────────────────────────────────────────────

@bot.tree.command(name="give_money", description="[Admin] Give fake money to a user.")
@app_commands.describe(user="Recipient.", amount="Amount to give (must be > 0).")
@app_commands.check(admin_check)
async def cmd_give_money(interaction: discord.Interaction, user: discord.Member, amount: float):
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        return
    new_bal = await bot.db.adjust_balance(uid(user), guild_id(interaction), amount)
    embed = discord.Embed(
        description=f"✅ Gave **{fmt(amount)}** to {user.mention}. New balance: **{fmt(new_bal)}**",
        color=COLOR_OK,
    )
    await interaction.response.send_message(embed=embed)


# ── /take_money ───────────────────────────────────────────────────────────────

@bot.tree.command(name="take_money", description="[Admin] Take fake money from a user.")
@app_commands.describe(user="Target user.", amount="Amount to remove (must be > 0).")
@app_commands.check(admin_check)
async def cmd_take_money(interaction: discord.Interaction, user: discord.Member, amount: float):
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        return
    bal = await bot.db.get_balance(uid(user), guild_id(interaction))
    new_bal = max(0.0, bal - amount)
    await bot.db.set_balance(uid(user), guild_id(interaction), new_bal)
    embed = discord.Embed(
        description=f"✅ Removed **{fmt(amount)}** from {user.mention}. New balance: **{fmt(new_bal)}**",
        color=COLOR_OK,
    )
    await interaction.response.send_message(embed=embed)


# ── /leaderboard ──────────────────────────────────────────────────────────────

@bot.tree.command(name="leaderboard", description="Show the top 10 balances in this server.")
async def cmd_leaderboard(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    rows = await bot.db.get_leaderboard(guild_id(interaction))
    if not rows:
        await interaction.response.send_message("No users yet.", ephemeral=True)
        return
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        member = interaction.guild.get_member(int(row["user_id"]))
        name = member.display_name if member else f"User {row['user_id']}"
        prefix = medals[i] if i < 3 else f"**{i+1}.**"
        lines.append(f"{prefix} {name} — {fmt(row['balance'])}")
    embed = discord.Embed(
        title="🏆 Leaderboard",
        description="\n".join(lines),
        color=COLOR_GOLD,
    )
    await interaction.response.send_message(embed=embed)


# ── /create_market ────────────────────────────────────────────────────────────

@bot.tree.command(name="create_market", description="[Admin] Create a new prediction market.")
@app_commands.describe(
    question="The yes/no question for this market.",
    liquidity="LMSR liquidity parameter b (default 100). Higher = less price impact.",
)
@app_commands.check(admin_check)
async def cmd_create_market(
    interaction: discord.Interaction,
    question: str,
    liquidity: float = DEFAULT_B,
):
    if liquidity <= 0:
        await interaction.response.send_message("Liquidity must be positive.", ephemeral=True)
        return
    if len(question) > 300:
        await interaction.response.send_message("Question too long (max 300 chars).", ephemeral=True)
        return
    market_id = await bot.db.create_market(
        guild_id(interaction), question, liquidity, uid(interaction.user)
    )
    embed = discord.Embed(
        title="📈 New Market Created",
        color=COLOR_INFO,
    )
    embed.add_field(name="ID", value=str(market_id), inline=True)
    embed.add_field(name="Starting probability", value="50.0% YES / 50.0% NO", inline=True)
    embed.add_field(name="Liquidity (b)", value=str(liquidity), inline=True)
    embed.add_field(name="Question", value=question, inline=False)
    embed.set_footer(text=f"Use /bet {market_id} yes <amount> to trade.")
    await interaction.response.send_message(embed=embed)


# ── /markets ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="markets", description="List all open prediction markets.")
async def cmd_markets(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    markets = await bot.db.list_markets(guild_id(interaction), status="open")
    if not markets:
        await interaction.response.send_message("No open markets right now.", ephemeral=True)
        return

    embed = discord.Embed(title="📊 Open Markets", color=COLOR_INFO)
    for m in markets[:20]:  # cap at 20 to stay within embed limits
        prob = lmsr_prob_yes(m["q_yes"], m["q_no"], m["b"])
        q = m["question"] if len(m["question"]) <= 80 else m["question"][:77] + "…"
        embed.add_field(
            name=f"#{m['id']}  {pct(prob)} YES",
            value=q,
            inline=False,
        )
    embed.set_footer(text="Use /market <id> to see details and chart.")
    await interaction.response.send_message(embed=embed)


# ── /market ───────────────────────────────────────────────────────────────────

@bot.tree.command(name="market", description="View a specific market with its price chart.")
@app_commands.describe(market_id="The market ID (from /markets).")
async def cmd_market(interaction: discord.Interaction, market_id: int):
    if interaction.guild is None:
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    await interaction.response.defer()

    m = await bot.db.get_market(market_id, guild_id(interaction))
    if m is None:
        await interaction.followup.send("Market not found.", ephemeral=True)
        return

    prob = lmsr_prob_yes(m["q_yes"], m["q_no"], m["b"])
    history = await bot.db.get_price_history(market_id)

    status_icon = "🟢" if m["status"] == "open" else "🔴"
    embed = discord.Embed(
        title=f"{status_icon} Market #{market_id}",
        description=f"**{m['question']}**",
        color=COLOR_INFO if m["status"] == "open" else COLOR_ERR,
    )
    embed.add_field(name="YES", value=f"**{pct(prob)}**", inline=True)
    embed.add_field(name="NO", value=f"**{pct(1 - prob)}**", inline=True)
    embed.add_field(name="Status", value=m["status"].capitalize(), inline=True)
    embed.add_field(name="YES shares outstanding", value=f"{m['q_yes']:.2f}", inline=True)
    embed.add_field(name="NO shares outstanding", value=f"{m['q_no']:.2f}", inline=True)
    embed.add_field(name="Trades", value=str(max(len(history) - 1, 0)), inline=True)

    if m["resolution"]:
        embed.add_field(name="Resolution", value=m["resolution"].upper(), inline=False)

    # Your position
    pos = await bot.db.get_position(uid(interaction.user), guild_id(interaction), market_id)
    if pos and (pos["yes_shares"] > 0.0001 or pos["no_shares"] > 0.0001):
        val_yes = pos["yes_shares"] * (1.0 if m["resolution"] == "yes" else (prob if m["status"] == "open" else 0.0))
        val_no  = pos["no_shares"]  * (1.0 if m["resolution"] == "no"  else ((1-prob) if m["status"] == "open" else 0.0))
        embed.add_field(
            name="Your position",
            value=(
                f"YES: {pos['yes_shares']:.4f} shares (~{fmt(pos['yes_shares'] * prob)})\n"
                f"NO:  {pos['no_shares']:.4f} shares (~{fmt(pos['no_shares'] * (1-prob))})"
            ),
            inline=False,
        )

    # Chart
    chart_buf = build_market_chart(history, m["question"], market_id, m["resolution"])
    file = discord.File(chart_buf, filename="chart.png")
    embed.set_image(url="attachment://chart.png")
    await interaction.followup.send(embed=embed, file=file)


# ── /bet ──────────────────────────────────────────────────────────────────────

@bot.tree.command(name="bet", description="Buy YES or NO shares in a market.")
@app_commands.describe(
    market_id="The market ID.",
    direction="YES or NO.",
    amount="Dollar amount to spend.",
)
@app_commands.choices(direction=[
    app_commands.Choice(name="YES", value="yes"),
    app_commands.Choice(name="NO",  value="no"),
])
async def cmd_bet(
    interaction: discord.Interaction,
    market_id: int,
    direction: str,
    amount: float,
):
    if interaction.guild is None:
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    if amount < MIN_BET:
        await interaction.response.send_message(f"Minimum bet is {fmt(MIN_BET)}.", ephemeral=True)
        return

    m = await bot.db.get_market(market_id, guild_id(interaction))
    if m is None:
        await interaction.response.send_message("Market not found.", ephemeral=True)
        return
    if m["status"] != "open":
        await interaction.response.send_message("This market is closed.", ephemeral=True)
        return

    bal = await bot.db.get_balance(uid(interaction.user), guild_id(interaction))
    if bal < amount:
        await interaction.response.send_message(
            f"Insufficient balance. You have {fmt(bal)}.", ephemeral=True
        )
        return

    q_yes, q_no, b = m["q_yes"], m["q_no"], m["b"]

    try:
        if direction == "yes":
            shares = shares_for_dollars_yes(amount, q_yes, q_no, b)
            new_q_yes, new_q_no = q_yes + shares, q_no
        else:
            shares = shares_for_dollars_no(amount, q_yes, q_no, b)
            new_q_yes, new_q_no = q_yes, q_no + shares
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return

    new_prob = lmsr_prob_yes(new_q_yes, new_q_no, b)

    # Commit changes
    await bot.db.adjust_balance(uid(interaction.user), guild_id(interaction), -amount)
    await bot.db.update_market_shares(market_id, new_q_yes, new_q_no)
    if direction == "yes":
        await bot.db.upsert_position(uid(interaction.user), guild_id(interaction), market_id, yes_delta=shares)
    else:
        await bot.db.upsert_position(uid(interaction.user), guild_id(interaction), market_id, no_delta=shares)
    await bot.db.record_price(market_id, new_prob, f"buy_{direction}")

    old_prob = lmsr_prob_yes(q_yes, q_no, b)
    embed = discord.Embed(title="✅ Bet Placed", color=COLOR_OK)
    embed.add_field(name="Market", value=f"#{market_id}", inline=True)
    embed.add_field(name="Direction", value=direction.upper(), inline=True)
    embed.add_field(name="Spent", value=fmt(amount), inline=True)
    embed.add_field(name="Shares received", value=f"{shares:.4f}", inline=True)
    embed.add_field(
        name="Price moved",
        value=f"{pct(old_prob)} → {pct(new_prob)} YES",
        inline=True,
    )
    new_bal = await bot.db.get_balance(uid(interaction.user), guild_id(interaction))
    embed.add_field(name="Remaining balance", value=fmt(new_bal), inline=True)
    await interaction.response.send_message(embed=embed)


# ── /sell ─────────────────────────────────────────────────────────────────────

@bot.tree.command(name="sell", description="Sell YES or NO shares back to the market.")
@app_commands.describe(
    market_id="The market ID.",
    direction="YES or NO.",
    shares="Number of shares to sell.",
)
@app_commands.choices(direction=[
    app_commands.Choice(name="YES", value="yes"),
    app_commands.Choice(name="NO",  value="no"),
])
async def cmd_sell(
    interaction: discord.Interaction,
    market_id: int,
    direction: str,
    shares: float,
):
    if interaction.guild is None:
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    if shares <= 0:
        await interaction.response.send_message("Shares must be positive.", ephemeral=True)
        return

    m = await bot.db.get_market(market_id, guild_id(interaction))
    if m is None:
        await interaction.response.send_message("Market not found.", ephemeral=True)
        return
    if m["status"] != "open":
        await interaction.response.send_message("This market is closed.", ephemeral=True)
        return

    pos = await bot.db.get_position(uid(interaction.user), guild_id(interaction), market_id)
    if pos is None:
        await interaction.response.send_message("You have no position in this market.", ephemeral=True)
        return

    owned = pos["yes_shares"] if direction == "yes" else pos["no_shares"]
    if shares > owned + 1e-9:
        await interaction.response.send_message(
            f"You only own {owned:.4f} {direction.upper()} shares.", ephemeral=True
        )
        return

    shares = min(shares, owned)  # clamp for floating-point safety
    q_yes, q_no, b = m["q_yes"], m["q_no"], m["b"]

    try:
        if direction == "yes":
            payout = dollars_for_selling_yes(shares, q_yes, q_no, b)
            new_q_yes, new_q_no = q_yes - shares, q_no
        else:
            payout = dollars_for_selling_no(shares, q_yes, q_no, b)
            new_q_yes, new_q_no = q_yes, q_no - shares
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return

    new_prob = lmsr_prob_yes(new_q_yes, new_q_no, b)
    old_prob = lmsr_prob_yes(q_yes, q_no, b)

    # Commit changes
    await bot.db.adjust_balance(uid(interaction.user), guild_id(interaction), payout)
    await bot.db.update_market_shares(market_id, new_q_yes, new_q_no)
    if direction == "yes":
        await bot.db.upsert_position(uid(interaction.user), guild_id(interaction), market_id, yes_delta=-shares)
    else:
        await bot.db.upsert_position(uid(interaction.user), guild_id(interaction), market_id, no_delta=-shares)
    await bot.db.record_price(market_id, new_prob, f"sell_{direction}")

    embed = discord.Embed(title="✅ Shares Sold", color=COLOR_OK)
    embed.add_field(name="Market", value=f"#{market_id}", inline=True)
    embed.add_field(name="Direction", value=direction.upper(), inline=True)
    embed.add_field(name="Shares sold", value=f"{shares:.4f}", inline=True)
    embed.add_field(name="Payout received", value=fmt(payout), inline=True)
    embed.add_field(
        name="Price moved",
        value=f"{pct(old_prob)} → {pct(new_prob)} YES",
        inline=True,
    )
    new_bal = await bot.db.get_balance(uid(interaction.user), guild_id(interaction))
    embed.add_field(name="New balance", value=fmt(new_bal), inline=True)
    await interaction.response.send_message(embed=embed)


# ── /my_positions ─────────────────────────────────────────────────────────────

@bot.tree.command(name="my_positions", description="Show your current market positions.")
async def cmd_my_positions(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    rows = await bot.db.get_user_positions(uid(interaction.user), guild_id(interaction))
    if not rows:
        await interaction.response.send_message("You have no open positions.", ephemeral=True)
        return

    embed = discord.Embed(title="📋 Your Positions", color=COLOR_INFO)
    for row in rows[:20]:
        prob = lmsr_prob_yes(row["q_yes"], row["q_no"], row["b"])
        status_icon = "🟢" if row["status"] == "open" else "🔴"
        q = row["question"] if len(row["question"]) <= 60 else row["question"][:57] + "…"

        lines = []
        if row["yes_shares"] > 0.0001:
            val = row["yes_shares"] * prob
            lines.append(f"YES: {row['yes_shares']:.4f} shares (~{fmt(val)})")
        if row["no_shares"] > 0.0001:
            val = row["no_shares"] * (1 - prob)
            lines.append(f"NO:  {row['no_shares']:.4f} shares (~{fmt(val)})")

        embed.add_field(
            name=f"{status_icon} #{row['market_id']}  {pct(prob)} YES — {q}",
            value="\n".join(lines),
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── /resolve ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="resolve", description="[Admin] Resolve a market and pay out winners.")
@app_commands.describe(
    market_id="The market to resolve.",
    outcome="The correct outcome.",
)
@app_commands.choices(outcome=[
    app_commands.Choice(name="YES", value="yes"),
    app_commands.Choice(name="NO",  value="no"),
])
@app_commands.check(admin_check)
async def cmd_resolve(interaction: discord.Interaction, market_id: int, outcome: str):
    await interaction.response.defer()

    m = await bot.db.get_market(market_id, guild_id(interaction))
    if m is None:
        await interaction.followup.send("Market not found.", ephemeral=True)
        return
    if m["status"] != "open":
        await interaction.followup.send("Market is already resolved.", ephemeral=True)
        return

    positions = await bot.db.get_all_positions_for_market(market_id, guild_id(interaction))

    # Mark market resolved before paying out (so concurrent trades are blocked)
    await bot.db.resolve_market(market_id, outcome)
    await bot.db.record_price(
        market_id, 1.0 if outcome == "yes" else 0.0, f"resolved_{outcome}"
    )

    total_paid = 0.0
    winners = 0
    for pos in positions:
        winning_shares = pos["yes_shares"] if outcome == "yes" else pos["no_shares"]
        if winning_shares > 0.0001:
            payout = winning_shares  # $1 per share
            await bot.db.adjust_balance(pos["user_id"], guild_id(interaction), payout)
            total_paid += payout
            winners += 1

    embed = discord.Embed(
        title=f"🏁 Market #{market_id} Resolved: **{outcome.upper()}**",
        description=f"**{m['question']}**",
        color=COLOR_OK if outcome == "yes" else COLOR_ERR,
    )
    embed.add_field(name="Winners paid out", value=str(winners), inline=True)
    embed.add_field(name="Total paid", value=fmt(total_paid), inline=True)
    embed.add_field(name="Payout", value="$1.00 per winning share", inline=True)
    await interaction.followup.send(embed=embed)


# ── Entry point ───────────────────────────────────────────────────────────────

token = os.environ.get("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")

bot.run(token)
