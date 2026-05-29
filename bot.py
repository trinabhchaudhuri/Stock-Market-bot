import os, json, random, math, asyncio, io
from datetime import datetime, timezone
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ui import View, Button
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

load_dotenv()

TOKEN     = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
DB_FILE   = "economy.json"
ST_FILE   = "stocks.json"

# ══════════════════════════════════════════════════════
#  STOCK CONFIG
# ══════════════════════════════════════════════════════
STOCK_BASE_PRICE    = 10.0   # starting price per share
STOCK_PT_MULTIPLIER = 1.0    # +$1 per activity point
MSG_POINTS          = 1      # pts per message
VOICE_POINTS_PM     = 2      # pts per minute in voice
SHORT_DECAY_PTS     = 1      # points drained per hour per shorted share
SNAPSHOT_INTERVAL   = 900    # 15 minutes in seconds
MAX_SNAPSHOTS       = 96     # keep 24h of 15-min snapshots

# ══════════════════════════════════════════════════════
#  DATABASE — economy
# ══════════════════════════════════════════════════════
def load_db():
    if not os.path.exists(DB_FILE):
        open(DB_FILE, "w").write("{}")
    with open(DB_FILE) as f: return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=2)

def get_user(db, uid):
    uid = str(uid)
    if uid not in db:
        db[uid] = {"balance": 500, "last_daily": None, "last_weekly": None}
    u = db[uid]
    for k in ("last_daily", "last_weekly"):
        if k not in u: u[k] = None
    return u

# ══════════════════════════════════════════════════════
#  DATABASE — stocks
# ══════════════════════════════════════════════════════
def load_st():
    if not os.path.exists(ST_FILE):
        open(ST_FILE, "w").write('{"stocks":{},"portfolios":{},"voice_sessions":{}}')
    with open(ST_FILE) as f: return json.load(f)

def save_st(data):
    with open(ST_FILE, "w") as f: json.dump(data, f, indent=2)

def get_stock(st, uid):
    uid = str(uid)
    if uid not in st["stocks"]:
        st["stocks"][uid] = {
            "points": 0,
            "price": STOCK_BASE_PRICE,
            "history": [],
            "name": None,
        }
    s = st["stocks"][uid]
    if "history" not in s: s["history"] = []
    if "name" not in s:    s["name"] = None
    return s

def get_portfolio(st, uid):
    uid = str(uid)
    if uid not in st["portfolios"]:
        st["portfolios"][uid] = {"longs": {}, "shorts": {}}
    p = st["portfolios"][uid]
    if "longs"  not in p: p["longs"]  = {}
    if "shorts" not in p: p["shorts"] = {}
    return p

def stock_price(stock):
    return round(STOCK_BASE_PRICE + max(0, stock["points"]) * STOCK_PT_MULTIPLIER, 2)

def push_snapshot(stock):
    now = now_ts()
    price = stock_price(stock)
    stock["price"] = price
    hist = stock["history"]
    if not hist or now - hist[-1]["ts"] >= SNAPSHOT_INTERVAL:
        hist.append({"ts": now, "price": price})
        if len(hist) > MAX_SNAPSHOTS:
            hist.pop(0)

def now_ts():
    return datetime.now(timezone.utc).timestamp()

def fmt_time(secs):
    h, m, s = int(secs // 3600), int((secs % 3600) // 60), int(secs % 60)
    return f"{h}h {m}m {s}s"

def ts_to_dt(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc)

# ══════════════════════════════════════════════════════
#  CHART GENERATOR — with time-window filtering
# ══════════════════════════════════════════════════════
def make_chart(stock, display_name: str, window_hours: float = 24) -> discord.File:
    hist = stock["history"]
    cutoff = now_ts() - window_hours * 3600

    filtered = [h for h in hist if h["ts"] >= cutoff]
    if len(filtered) < 2:
        filtered = [
            {"ts": now_ts() - 900, "price": stock_price(stock)},
            {"ts": now_ts(),        "price": stock_price(stock)},
        ]

    times  = [ts_to_dt(h["ts"]) for h in filtered]
    prices = [h["price"] for h in filtered]
    start  = prices[0]
    end    = prices[-1]
    up     = end >= start

    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    color = "#00e676" if up else "#ff5252"
    ax.plot(times, prices, color=color, linewidth=2.5, zorder=3)
    ax.fill_between(times, prices, min(prices) * 0.98,
                    alpha=0.25, color=color, zorder=2)
    ax.grid(color="#ffffff18", linestyle="--", linewidth=0.5, zorder=1)
    ax.set_axisbelow(True)
    ax.tick_params(colors="#aaaacc", labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333355")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate(rotation=30)

    chg     = end - start
    chg_pct = (chg / start * 100) if start else 0
    arrow   = "▲" if up else "▼"
    color2  = "#00e676" if up else "#ff5252"
    window_label = {0.25: "15m", 1: "1h", 2: "2h", 8: "8h", 24: "24h"}.get(window_hours, f"{window_hours}h")
    ax.set_title(
        f"{display_name}'s Stock  [{window_label}]   ${end:.2f}  {arrow} {chg_pct:+.1f}%",
        color="#e0e0ff", fontsize=13, fontweight="bold", pad=12
    )
    ax.set_ylabel("Price ($)", color="#aaaacc", fontsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:.2f}"))
    ax.annotate(
        f"${end:.2f}",
        xy=(times[-1], end),
        xytext=(8, 0), textcoords="offset points",
        color=color2, fontsize=9, fontweight="bold",
        va="center",
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return discord.File(buf, filename="chart.png")

# ══════════════════════════════════════════════════════
#  STOCK INFO VIEW — time window buttons
# ══════════════════════════════════════════════════════
class StockInfoView(View):
    WINDOWS = [
        ("15m",  0.25),
        ("1h",   1.0),
        ("2h",   2.0),
        ("8h",   8.0),
        ("24h",  24.0),
    ]

    def __init__(self, member: discord.Member, stock: dict, current_window: float = 24.0):
        super().__init__(timeout=120)
        self.member  = member
        self.stock   = stock
        self.window  = current_window
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for label, hours in self.WINDOWS:
            btn = Button(
                label=label,
                style=discord.ButtonStyle.primary if hours == self.window else discord.ButtonStyle.secondary,
            )
            btn.callback = self._make_cb(hours)
            self.add_item(btn)

    def _make_cb(self, hours: float):
        async def cb(interaction: discord.Interaction):
            self.window = hours
            self._build_buttons()
            chart_file = make_chart(self.stock, self.member.display_name, hours)
            e = self._make_embed()
            await interaction.response.edit_message(embed=e, attachments=[chart_file], view=self)
        return cb

    def _make_embed(self):
        s     = self.stock
        price = stock_price(s)
        hist  = s["history"]
        if len(hist) >= 2:
            prev    = hist[-2]["price"]
            chg     = price - prev
            chg_pct = chg / prev * 100 if prev else 0
            chg_str = f"{'▲' if chg>=0 else '▼'} {chg:+.2f} ({chg_pct:+.1f}%)"
        else:
            chg_str = "➖ No change data yet"

        st = load_st()
        holders = []
        for uid, port in st["portfolios"].items():
            longs  = port.get("longs",  {}).get(str(self.member.id), 0)
            shorts = port.get("shorts", {}).get(str(self.member.id), 0)
            if longs or shorts:
                holders.append(f"`{uid[-6:]}` — 🟢 {longs} long  🔴 {shorts} short")

        up = len(hist) >= 2 and hist[-1]["price"] >= hist[-2]["price"]
        e = (discord.Embed(
            title=f"📈 {self.member.display_name}'s Stock",
            color=0x00e676 if up else 0xff5252,
            timestamp=datetime.now(timezone.utc),
        )
        .add_field(name="Current Price",   value=f"**${price:.2f}**",          inline=True)
        .add_field(name="Activity Points", value=f"**{s['points']:,} pts**",   inline=True)
        .add_field(name="24h Change",      value=chg_str,                       inline=True)
        .add_field(name="Data Points",     value=f"{len(hist)} snapshots",      inline=True)
        .add_field(name="Holders",         value="\n".join(holders) or "None yet", inline=False)
        .set_image(url="attachment://chart.png")
        .set_footer(text="Use $buy or $short to trade • Select a time window above"))
        return e

# ══════════════════════════════════════════════════════
#  BLACKJACK
# ══════════════════════════════════════════════════════
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

def new_deck():
    d = [{"s": s, "r": r} for s in SUITS for r in RANKS]
    random.shuffle(d)
    return d

def card_val(c):
    if c["r"] in ("J", "Q", "K"): return 10
    if c["r"] == "A": return 11
    return int(c["r"])

def hand_total(hand):
    t = sum(card_val(c) for c in hand)
    aces = sum(1 for c in hand if c["r"] == "A")
    while t > 21 and aces: t -= 10; aces -= 1
    return t

def fmt_hand(hand, hide2=False):
    return " ".join("`??`" if hide2 and i == 1 else f"`{c['r']}{c['s']}`" for i, c in enumerate(hand))

BJ_COLORS = {"playing": 0x3498DB, "win": 0x2ECC71, "blackjack": 0x2ECC71,
             "lose": 0xE74C3C, "bust": 0xE74C3C, "push": 0xF1C40F, "dbust": 0x2ECC71}
BJ_TITLES = {"playing": "🃏 Blackjack", "win": "🎉 You Win!", "blackjack": "🃏 BLACKJACK! 21!",
             "lose": "💀 Dealer Wins", "bust": "💥 You Bust!", "push": "🤝 Push — Tie Game", "dbust": "🎉 Dealer Busts — You Win!"}

def bj_embed(player, dealer, bet, balance, status, hide_dealer=False):
    pt = hand_total(player)
    dt = "?" if hide_dealer else hand_total(dealer)
    return (discord.Embed(title=BJ_TITLES.get(status, "🃏 Blackjack"), color=BJ_COLORS.get(status, 0x3498DB), timestamp=datetime.now(timezone.utc))
        .add_field(name=f"Your Hand ({pt})", value=fmt_hand(player), inline=True)
        .add_field(name=f"Dealer Hand ({dt})", value=fmt_hand(dealer, hide_dealer), inline=True)
        .add_field(name="Bet", value=f"${bet:,}", inline=True)
        .add_field(name="Balance", value=f"${balance:,}", inline=True))

class BlackjackView(View):
    def __init__(self, deck, player, dealer, bet, uid, db):
        super().__init__(timeout=60)
        self.deck = deck; self.player = player; self.dealer = dealer
        self.bet = bet; self.uid = str(uid); self.db = db; self.doubled = False

    def uobj(self): return get_user(self.db, self.uid)

    async def end_game(self, interaction, status, delta):
        u = self.uobj(); u["balance"] += delta; save_db(self.db)
        e = bj_embed(self.player, self.dealer, self.bet, u["balance"], status)
        lbl = "Won" if delta > 0 else ("Lost" if delta < 0 else "Returned")
        e.add_field(name=lbl, value=f"**{'+' if delta >= 0 else ''}${delta:,}**", inline=True)
        await interaction.response.edit_message(embed=e, view=None); self.stop()

    def dealer_play(self):
        while hand_total(self.dealer) < 17: self.dealer.append(self.deck.pop())

    def resolve(self):
        pt, dt = hand_total(self.player), hand_total(self.dealer)
        if dt > 21: return "dbust", self.bet
        if pt > dt: return "win", self.bet
        if pt < dt: return "lose", -self.bet
        return "push", 0

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="👊")
    async def hit(self, interaction, button):
        if interaction.user.id != int(self.uid): return await interaction.response.send_message("Not your game!", ephemeral=True)
        self.player.append(self.deck.pop())
        if hand_total(self.player) > 21: await self.end_game(interaction, "bust", -self.bet)
        elif hand_total(self.player) == 21:
            self.dealer_play(); s, d = self.resolve(); await self.end_game(interaction, s, d)
        else:
            u = self.uobj()
            await interaction.response.edit_message(embed=bj_embed(self.player, self.dealer, self.bet, u["balance"], "playing", True), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand(self, interaction, button):
        if interaction.user.id != int(self.uid): return await interaction.response.send_message("Not your game!", ephemeral=True)
        self.dealer_play(); s, d = self.resolve(); await self.end_game(interaction, s, d)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.danger, emoji="💰")
    async def double_down(self, interaction, button):
        if interaction.user.id != int(self.uid): return await interaction.response.send_message("Not your game!", ephemeral=True)
        if self.doubled: return await interaction.response.send_message("Already doubled!", ephemeral=True)
        u = self.uobj()
        if self.bet > u["balance"]: return await interaction.response.send_message("Not enough to double!", ephemeral=True)
        self.bet *= 2; self.doubled = True; self.player.append(self.deck.pop())
        if hand_total(self.player) > 21: await self.end_game(interaction, "bust", -self.bet)
        else:
            self.dealer_play(); s, d = self.resolve(); await self.end_game(interaction, s, d)

    async def on_timeout(self):
        for i in self.children: i.disabled = True

# ══════════════════════════════════════════════════════
#  SLOTS
# ══════════════════════════════════════════════════════
SLOT_SYM = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣", "🃏"]
SLOT_W   = [30, 25, 20, 15, 6, 3, 1]

def w_sym(): return random.choices(SLOT_SYM, weights=SLOT_W, k=1)[0]

def slots_mult(r):
    a, b, c = r
    if a == b == c: return {"🃏": 50, "7️⃣": 20, "💎": 15, "🍇": 8, "🍊": 5, "🍋": 3, "🍒": 2}.get(a, 1)
    if a == b or b == c or a == c: return 0.5
    return 0.0

# ══════════════════════════════════════════════════════
#  PLINKO
# ══════════════════════════════════════════════════════
PLINKO_M = [10, 3, 1.5, 0.5, 0.3, 0.5, 1.5, 3, 10]

def sim_plinko():
    pos = 0; path = []
    for _ in range(8):
        d = random.choice(["R", "L"]); path.append(d)
        if d == "R": pos += 1
    return pos, PLINKO_M[pos], path

# ══════════════════════════════════════════════════════
#  ROULETTE
# ══════════════════════════════════════════════════════
# European roulette: 0 is green, 1-36 alternating red/black
ROULETTE_RED = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
ROULETTE_BLACK = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}

ROULETTE_SPIN_FRAMES = [
    "🟥🟥🟥 ⬛🟥⬛ 🟥🟥🟥",
    "⬛🟥⬛ 🟥⬛🟥 ⬛🟥⬛",
    "🟥⬛🟥 ⬛🟥⬛ 🟥⬛🟥",
    "⬛🟥⬛ 🟥⬛🟥 ⬛🟥⬛",
    "🟥🟥🟥 ⬛🟥⬛ 🟥🟥🟥",
]

def roulette_spin():
    number = random.randint(0, 36)
    if number == 0:
        color = "green"
        emoji = "🟩"
    elif number in ROULETTE_RED:
        color = "red"
        emoji = "🟥"
    else:
        color = "black"
        emoji = "⬛"
    return number, color, emoji

class RouletteView(View):
    def __init__(self, bet: int, uid: int, db: dict):
        super().__init__(timeout=60)
        self.bet = bet
        self.uid = str(uid)
        self.db  = db

    def uobj(self): return get_user(self.db, self.uid)

    async def _resolve(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("Not your game!", ephemeral=True)

        # Disable buttons immediately
        for item in self.children: item.disabled = True

        # Show spinning animation
        spin_embed = discord.Embed(
            title="🎡 Roulette — Spinning...",
            description=(
                "```\n"
                "  ╔══════════════════╗\n"
                "  ║  🎡 SPINNING...  ║\n"
                "  ║                  ║\n"
                "  ║  🟥 ⬛ 🟥 ⬛ 🟥  ║\n"
                "  ║  ⬛ 🟥 ⬛ 🟥 ⬛  ║\n"
                "  ║  🟥 ⬛ 🟥 ⬛ 🟥  ║\n"
                "  ║                  ║\n"
                "  ╚══════════════════╝\n"
                "```"
            ),
            color=0xF1C40F,
            timestamp=datetime.now(timezone.utc),
        )
        spin_embed.add_field(name="Your Bet", value=f"**{choice.upper()}** — **${self.bet:,}**", inline=True)
        await interaction.response.edit_message(embed=spin_embed, view=self)

        # Animate the spin
        frames = [
            ("🟥 ⬛ 🟥 ⬛ 🟥\n⬛ 🟥 ⬛ 🟥 ⬛\n🟥 ⬛ 🟥 ⬛ 🟥", 0.4),
            ("⬛ 🟥 ⬛ 🟥 ⬛\n🟥 ⬛ 🟥 ⬛ 🟥\n⬛ 🟥 ⬛ 🟥 ⬛", 0.4),
            ("🟥 ⬛ 🟥 ⬛ 🟥\n⬛ 🟥 ⬛ 🟥 ⬛\n🟥 ⬛ 🟥 ⬛ 🟥", 0.35),
            ("⬛ 🟥 ⬛ 🟥 ⬛\n🟥 ⬛ 🟥 ⬛ 🟥\n⬛ 🟥 ⬛ 🟥 ⬛", 0.3),
            ("🟥 ⬛ 🟥 ⬛ 🟥\n⬛ 🟥 ⬛ 🟥 ⬛\n🟥 ⬛ 🟥 ⬛ 🟥", 0.25),
            ("⬛ 🟥 ⬛ 🟥 ⬛\n🟥 ⬛ 🟥 ⬛ 🟥\n⬛ 🟥 ⬛ 🟥 ⬛", 0.2),
            ("🟥 ⬛ 🟥 ⬛ 🟥\n⬛ 🟥 ⬛ 🟥 ⬛\n🟥 ⬛ 🟥 ⬛ 🟥", 0.15),
            ("⬛ 🟥 ⬛ 🟥 ⬛\n🟥 ⬛ 🟥 ⬛ 🟥\n⬛ 🟥 ⬛ 🟥 ⬛", 0.1),
        ]
        for frame_art, delay in frames:
            await asyncio.sleep(delay)
            frame_embed = discord.Embed(
                title="🎡 Roulette — Spinning...",
                description=f"```\n  ╔══════════════════╗\n  ║                  ║\n{chr(10).join('  ║  ' + line + '  ║' for line in frame_art.split(chr(10)))}\n  ║                  ║\n  ╚══════════════════╝\n```",
                color=0xF1C40F,
                timestamp=datetime.now(timezone.utc),
            )
            frame_embed.add_field(name="Your Bet", value=f"**{choice.upper()}** — **${self.bet:,}**", inline=True)
            await interaction.edit_original_response(embed=frame_embed)

        await asyncio.sleep(0.3)

        # Final result
        number, color, emoji = roulette_spin()
        u = self.uobj()

        if number == 0:
            # Green — house wins
            won = False
            net = -self.bet
            result_title = "🟩 Green (0) — House Wins!"
            result_color = 0x2ECC40
        elif color == choice:
            won = True
            net = self.bet
            result_title = f"{emoji} {color.upper()} **#{number}** — You Win! 🎉"
            result_color = 0x2ECC71
        else:
            won = False
            net = -self.bet
            result_title = f"{emoji} {color.upper()} **#{number}** — You Lose!"
            result_color = 0xE74C3C

        u["balance"] += net
        save_db(self.db)

        # Build the roulette wheel display
        wheel_display = self._build_wheel(number, color, emoji)

        result_embed = discord.Embed(
            title=f"🎡 Roulette Result",
            description=(
                f"```\n{wheel_display}\n```\n"
                f"**Result: {emoji} {color.upper()} #{number}**\n"
                f"Your pick: **{choice.upper()}**"
            ),
            color=result_color,
            timestamp=datetime.now(timezone.utc),
        )
        result_embed.add_field(name="Bet",     value=f"**${self.bet:,}**",                                inline=True)
        result_embed.add_field(name="Won" if won else "Lost", value=f"**{'+' if won else ''}${net:,}**", inline=True)
        result_embed.add_field(name="Balance", value=f"**${u['balance']:,}**",                            inline=True)
        result_embed.set_footer(text="Red/Black pays 1:1 • Green (0) always wins for the house")

        await interaction.edit_original_response(embed=result_embed, view=None)
        self.stop()

    def _build_wheel(self, number: int, color: str, emoji: str) -> str:
        # A simple ASCII roulette wheel display
        lines = [
            "  ╔══════════════════════╗",
            "  ║    🎡  ROULETTE      ║",
            "  ╠══════════════════════╣",
           f"  ║  🟥 ⬛ {emoji} ⬛ 🟥  ║",
           f"  ║  ⬛ {emoji} [{number:02d}] {emoji} ⬛  ║",
           f"  ║  🟥 ⬛ {emoji} ⬛ 🟥  ║",
            "  ╠══════════════════════╣",
           f"  ║  Result: {color.upper():<12}║",
            "  ╚══════════════════════╝",
        ]
        return "\n".join(lines)

    @discord.ui.button(label="🟥 Red", style=discord.ButtonStyle.danger)
    async def bet_red(self, interaction: discord.Interaction, button: Button):
        await self._resolve(interaction, "red")

    @discord.ui.button(label="⬛ Black", style=discord.ButtonStyle.secondary)
    async def bet_black(self, interaction: discord.Interaction, button: Button):
        await self._resolve(interaction, "black")

    async def on_timeout(self):
        for item in self.children: item.disabled = True

# ══════════════════════════════════════════════════════
#  STOCKS BROWSE VIEW  (paginated)
# ══════════════════════════════════════════════════════
PER_PAGE = 5

class StockBrowseView(View):
    def __init__(self, entries, guild):
        super().__init__(timeout=120)
        self.entries = entries
        self.guild   = guild
        self.page    = 0
        self.pages   = math.ceil(len(entries) / PER_PAGE) or 1

    def make_embed(self):
        start = self.page * PER_PAGE
        chunk = self.entries[start:start + PER_PAGE]
        lines = []
        for rank, (uid, s) in enumerate(chunk, start=start + 1):
            name  = s.get("name") or f"User#{uid[-4:]}"
            price = stock_price(s)
            hist  = s["history"]
            if len(hist) >= 2:
                chg = price - hist[-2]["price"]
                arrow = "📈" if chg >= 0 else "📉"
                chg_str = f"{'+' if chg>=0 else ''}{chg:.2f}"
            else:
                arrow, chg_str = "➖", "0.00"
            pts = s["points"]
            lines.append(
                f"**#{rank}** {arrow} **{name}**\n"
                f"  Price: **${price:.2f}** ({chg_str})  •  Points: **{pts:,}**  •  ID: `{uid}`"
            )
        embed = discord.Embed(
            title="📊 Stock Market — All Stocks",
            description="\n\n".join(lines) or "No stocks yet.",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Page {self.page+1}/{self.pages}  •  Use $stockinfo @user for charts  •  $buy or $short to trade")
        return embed

    def update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.pages - 1

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction, button):
        self.page = max(0, self.page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction, button):
        self.page = min(self.pages - 1, self.page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

# ══════════════════════════════════════════════════════
#  BOT + TREE
# ══════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
intents.members         = True
intents.voice_states    = True
client = discord.Client(intents=intents)
tree   = app_commands.CommandTree(client)

def insuf(bal):
    return discord.Embed(title="❌ Insufficient Funds", description=f"You only have **${bal:,}**!", color=0xE74C3C, timestamp=datetime.now(timezone.utc))

# ══════════════════════════════════════════════════════
#  PREFIX COMMAND HANDLER  ($commands)
# ══════════════════════════════════════════════════════
PREFIX = "$"

PREFIX_ALIASES = {
    "balance":   "balance",
    "bal":       "balance",
    "daily":     "daily",
    "weekly":    "weekly",
    "cf":        "cf",
    "bj":        "bj",
    "slots":     "slots",
    "plinko":    "plinko",
    "roulette":  "roulette",
    "rl":        "roulette",
    "stocks":    "stocks",
    "stockinfo": "stockinfo",
    "si":        "stockinfo",
    "buy":       "buy",
    "sell":      "sell",
    "short":     "short",
    "covershort":"covershort",
    "cs":        "covershort",
    "portfolio": "portfolio",
    "port":      "portfolio",
    "help":      "help",
}

# ══════════════════════════════════════════════════════
#  ACTIVITY TRACKING
# ══════════════════════════════════════════════════════
@client.event
async def on_message(message: discord.Message):
    if message.author.bot: return

    # Stock points for messaging
    st  = load_st()
    s   = get_stock(st, message.author.id)
    s["name"]   = message.author.display_name
    s["points"] += MSG_POINTS
    push_snapshot(s)
    save_st(st)

    # Handle $ prefix commands
    if not message.content.startswith(PREFIX): return
    content = message.content[len(PREFIX):].strip()
    if not content: return
    parts = content.split()
    cmd   = parts[0].lower()
    args  = parts[1:]

    resolved = PREFIX_ALIASES.get(cmd)
    if not resolved: return

    ctx = message  # we use message as context

    # ── balance ──
    if resolved == "balance":
        db = load_db(); u = get_user(db, str(message.author.id))
        e = (discord.Embed(title="💰 Wallet Balance", description=f"**{message.author.display_name}**, here's your balance:", color=0xF1C40F, timestamp=datetime.now(timezone.utc))
             .add_field(name="Balance", value=f"**${u['balance']:,}**", inline=True)
             .set_footer(text="$cf $bj $slots $plinko $roulette • $daily $weekly • $stocks to trade"))
        await message.channel.send(embed=e)

    # ── daily ──
    elif resolved == "daily":
        db = load_db(); u = get_user(db, str(message.author.id)); now = now_ts(); cd = 86400
        if u["last_daily"] and now - u["last_daily"] < cd:
            e = discord.Embed(title="⏰ Already Claimed!", description=f"Come back in **{fmt_time(cd-(now-u['last_daily']))}**!", color=0xE74C3C)
            return await message.channel.send(embed=e)
        u["balance"] += 5000; u["last_daily"] = now; save_db(db)
        e = (discord.Embed(title="📅 Daily Reward Claimed!", description=f"Here's your payout, **{message.author.display_name}**! 🎉", color=0x2ECC71, timestamp=datetime.now(timezone.utc))
             .add_field(name="Reward", value="**+$5,000**", inline=True)
             .add_field(name="New Balance", value=f"**${u['balance']:,}**", inline=True)
             .set_footer(text="Come back in 24 hours!"))
        await message.channel.send(embed=e)

    # ── weekly ──
    elif resolved == "weekly":
        db = load_db(); u = get_user(db, str(message.author.id)); now = now_ts(); cd = 604800
        if u["last_weekly"] and now - u["last_weekly"] < cd:
            e = discord.Embed(title="⏰ Already Claimed!", description=f"Come back in **{fmt_time(cd-(now-u['last_weekly']))}**!", color=0xE74C3C)
            return await message.channel.send(embed=e)
        u["balance"] += 1000; u["last_weekly"] = now; save_db(db)
        e = (discord.Embed(title="📆 Weekly Reward Claimed!", description=f"Here's your weekly payout, **{message.author.display_name}**! 🎉", color=0x9B59B6, timestamp=datetime.now(timezone.utc))
             .add_field(name="Reward", value="**+$1,000**", inline=True)
             .add_field(name="New Balance", value=f"**${u['balance']:,}**", inline=True)
             .set_footer(text="Come back in 7 days!"))
        await message.channel.send(embed=e)

    # ── cf ──
    elif resolved == "cf":
        if len(args) < 2:
            return await message.channel.send("Usage: `$cf <heads/tails> <amount>`")
        choice_raw = args[0].lower()
        if choice_raw not in ("heads", "tails", "h", "t"):
            return await message.channel.send("Choose `heads` or `tails`!")
        choice = "heads" if choice_raw in ("heads", "h") else "tails"
        try: amount = int(args[1])
        except ValueError: return await message.channel.send("Amount must be a number!")
        if amount < 1: return await message.channel.send("Min bet $1!")
        db = load_db(); u = get_user(db, str(message.author.id))
        if amount > u["balance"]: return await message.channel.send(embed=insuf(u["balance"]))
        result = random.choice(["heads", "tails"]); won = choice == result
        emoji = "🪙" if result == "heads" else "🌑"
        u["balance"] += amount if won else -amount; save_db(db)
        e = (discord.Embed(title="🎉 You Won!" if won else "💀 You Lost!", description=f"Coin landed **{result}** {emoji}\nYou picked **{choice}** — {'correct! 🎊' if won else 'wrong! 😬'}", color=0x2ECC71 if won else 0xE74C3C, timestamp=datetime.now(timezone.utc))
             .add_field(name="Won" if won else "Lost", value=f"**{'+' if won else '-'}${amount:,}**", inline=True)
             .add_field(name="New Balance", value=f"**${u['balance']:,}**", inline=True))
        await message.channel.send(embed=e)

    # ── bj ──
    elif resolved == "bj":
        if len(args) < 1: return await message.channel.send("Usage: `$bj <amount>`")
        try: amount = int(args[0])
        except ValueError: return await message.channel.send("Amount must be a number!")
        if amount < 1: return await message.channel.send("Min bet $1!")
        db = load_db(); u = get_user(db, str(message.author.id))
        if amount > u["balance"]: return await message.channel.send(embed=insuf(u["balance"]))
        deck = new_deck(); player = [deck.pop(), deck.pop()]; dealer = [deck.pop(), deck.pop()]
        if hand_total(player) == 21:
            w = math.floor(amount * 1.5); u["balance"] += w; save_db(db)
            e = bj_embed(player, dealer, amount, u["balance"], "blackjack")
            e.add_field(name="Payout", value=f"**+${w:,}** (1.5x)", inline=True)
            return await message.channel.send(embed=e)
        view = BlackjackView(deck, player, dealer, amount, message.author.id, db)
        await message.channel.send(embed=bj_embed(player, dealer, amount, u["balance"], "playing", True), view=view)

    # ── slots ──
    elif resolved == "slots":
        if len(args) < 1: return await message.channel.send("Usage: `$slots <amount>`")
        try: amount = int(args[0])
        except ValueError: return await message.channel.send("Amount must be a number!")
        if amount < 1: return await message.channel.send("Min bet $1!")
        db = load_db(); u = get_user(db, str(message.author.id))
        if amount > u["balance"]: return await message.channel.send(embed=insuf(u["balance"]))
        reels = [w_sym(), w_sym(), w_sym()]; mult = slots_mult(reels); net = math.floor(amount * mult) - amount
        u["balance"] += net; save_db(db)
        is_win = net > 0; is_push = mult == 0.5
        if mult >= 50: title, desc = "🎰 ⭐ JACKPOT ⭐", "**TRIPLE JOKER!**\n"
        elif mult >= 20: title, desc = "🎰 🔥 MEGA WIN!", "**Triple 7s!**\n"
        elif mult >= 10: title, desc = "🎰 🎉 BIG WIN!", "**Triple Diamonds!**\n"
        elif is_win: title, desc = "🎰 You Win!", ""
        elif is_push: title, desc = "🎰 Partial Win", "Pair — partial return.\n"
        else: title, desc = "🎰 No Match", ""
        board = f"{desc}┌─────────────────┐\n│  {reels[0]}  {reels[1]}  {reels[2]}  │\n└─────────────────┘"
        e = (discord.Embed(title=title, description=board, color=0x2ECC71 if is_win else (0xF1C40F if is_push else 0xE74C3C), timestamp=datetime.now(timezone.utc))
             .add_field(name="Multiplier", value=f"**{mult}x**", inline=True)
             .add_field(name="Won" if net >= 0 else "Lost", value=f"**{'+' if net >= 0 else ''}${net:,}**", inline=True)
             .add_field(name="Balance", value=f"**${u['balance']:,}**", inline=True)
             .set_footer(text="🃏=50x • 7️⃣=20x • 💎=15x • 🍇=8x • 🍊=5x • 🍋=3x • 🍒=2x • Pair=0.5x"))
        await message.channel.send(embed=e)

    # ── plinko ──
    elif resolved == "plinko":
        if len(args) < 1: return await message.channel.send("Usage: `$plinko <amount>`")
        try: amount = int(args[0])
        except ValueError: return await message.channel.send("Amount must be a number!")
        if amount < 1: return await message.channel.send("Min bet $1!")
        db = load_db(); u = get_user(db, str(message.author.id))
        if amount > u["balance"]: return await message.channel.send(embed=insuf(u["balance"]))
        pos, mult, path = sim_plinko(); net = math.floor(amount * mult) - amount; u["balance"] += net; save_db(db)
        md = "  ".join(f"❱**{m}x**❰" if i == pos else f"{m}x" for i, m in enumerate(PLINKO_M))
        ps = "".join("→" if d == "R" else "←" for d in path)
        title = "🎯 JACKPOT LANE!" if mult >= 10 else ("🎯 Great Drop!" if mult >= 3 else ("🎯 Plinko Result" if mult >= 1 else "🎯 Unlucky Drop"))
        e = (discord.Embed(title=title, description=f"**Path:** {ps}\n\n**Multipliers:**\n{md}", color=0x2ECC71 if mult >= 3 else (0xF1C40F if mult >= 1 else 0xE74C3C), timestamp=datetime.now(timezone.utc))
             .add_field(name="Multiplier", value=f"**{mult}x**", inline=True)
             .add_field(name="Won" if net >= 0 else "Lost", value=f"**{'+' if net >= 0 else ''}${net:,}**", inline=True)
             .add_field(name="Balance", value=f"**${u['balance']:,}**", inline=True)
             .set_footer(text="Multipliers: 10x 3x 1.5x 0.5x 0.3x 0.5x 1.5x 3x 10x"))
        await message.channel.send(embed=e)

    # ── roulette ──
    elif resolved == "roulette":
        if len(args) < 1: return await message.channel.send("Usage: `$roulette <amount>` then pick Red or Black")
        try: amount = int(args[0])
        except ValueError: return await message.channel.send("Amount must be a number!")
        if amount < 1: return await message.channel.send("Min bet $1!")
        db = load_db(); u = get_user(db, str(message.author.id))
        if amount > u["balance"]: return await message.channel.send(embed=insuf(u["balance"]))
        u["balance"] -= amount; save_db(db)
        view = RouletteView(amount, message.author.id, db)
        e = (discord.Embed(
            title="🎡 Roulette — Place Your Bet!",
            description=(
                "```\n"
                "  ╔══════════════════════╗\n"
                "  ║    🎡  ROULETTE      ║\n"
                "  ╠══════════════════════╣\n"
                "  ║  🟥 ⬛ 🟥 ⬛ 🟥  ║\n"
                "  ║  ⬛ 🟥  0  🟥 ⬛  ║\n"
                "  ║  🟥 ⬛ 🟥 ⬛ 🟥  ║\n"
                "  ╠══════════════════════╣\n"
                "  ║  Pick Red or Black   ║\n"
                "  ╚══════════════════════╝\n"
                "```\n"
                "The wheel has **37 pockets**: 18 🟥 Red, 18 ⬛ Black, 1 🟩 Green (0).\n"
                "Pick a colour — wins pay **1:1**. Green is always the house!"
            ),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        .add_field(name="Your Bet", value=f"**${amount:,}**", inline=True)
        .add_field(name="Balance",  value=f"**${u['balance']:,}**", inline=True)
        .set_footer(text="Press a button to spin!"))
        await message.channel.send(embed=e, view=view)

    # ── stocks ──
    elif resolved == "stocks":
        st = load_st()
        if not st["stocks"]:
            return await message.channel.send("No stocks yet — members need to chat first!")
        entries = sorted(st["stocks"].items(), key=lambda x: stock_price(x[1]), reverse=True)
        view = StockBrowseView(entries, message.guild)
        view.update_buttons()
        await message.channel.send(embed=view.make_embed(), view=view)

    # ── stockinfo ──
    elif resolved == "stockinfo":
        if not message.mentions:
            return await message.channel.send("Usage: `$stockinfo @member`")
        member = message.mentions[0]
        if member.bot: return await message.channel.send("Bots don't have stocks!")
        st = load_st()
        s  = get_stock(st, member.id)
        s["name"] = member.display_name
        save_st(st)
        view = StockInfoView(member, s, current_window=24.0)
        chart_file = make_chart(s, member.display_name, 24.0)
        e = view._make_embed()
        await message.channel.send(embed=e, file=chart_file, view=view)

    # ── buy ──
    elif resolved == "buy":
        if not message.mentions or len(args) < 2:
            return await message.channel.send("Usage: `$buy @member <shares>`")
        member = message.mentions[0]
        if member.bot: return await message.channel.send("Can't buy bot stocks!")
        if member.id == message.author.id: return await message.channel.send("Can't buy your own stock!")
        try: shares = int(args[-1])
        except ValueError: return await message.channel.send("Shares must be a number!")
        if shares < 1: return await message.channel.send("Buy at least 1 share!")
        db  = load_db(); eco = get_user(db, str(message.author.id))
        st  = load_st(); s   = get_stock(st, member.id)
        s["name"] = member.display_name
        price = stock_price(s); total = round(price * shares, 2)
        if total > eco["balance"]: return await message.channel.send(embed=insuf(eco["balance"]))
        eco["balance"] = round(eco["balance"] - total, 2)
        port = get_portfolio(st, message.author.id)
        tid  = str(member.id)
        port["longs"][tid] = port["longs"].get(tid, 0) + shares
        pkey = f"long_avg_{tid}"
        if pkey not in port: port[pkey] = price
        else:
            old = port["longs"][tid] - shares
            port[pkey] = round((port[pkey]*old + price*shares) / port["longs"][tid], 4)
        save_db(db); save_st(st)
        e = (discord.Embed(title="🟢 Shares Purchased!", description=f"You bought **{shares} share{'s' if shares>1 else ''}** of **{member.display_name}**'s stock.", color=0x2ECC71, timestamp=datetime.now(timezone.utc))
             .add_field(name="Price/Share",   value=f"**${price:.2f}**",           inline=True)
             .add_field(name="Total Cost",    value=f"**${total:,.2f}**",          inline=True)
             .add_field(name="New Balance",   value=f"**${eco['balance']:,}**",    inline=True)
             .add_field(name="Your Position", value=f"🟢 **{port['longs'][tid]} shares** long", inline=True)
             .set_footer(text="Use $portfolio to track your holdings • $sell to close position"))
        await message.channel.send(embed=e)

    # ── sell ──
    elif resolved == "sell":
        if not message.mentions:
            return await message.channel.send("Usage: `$sell @member <shares>` (0 = sell all)")
        member = message.mentions[0]
        try: shares = int(args[-1]) if len(args) >= 2 else 0
        except ValueError: shares = 0
        db  = load_db(); eco = get_user(db, str(message.author.id))
        st  = load_st(); s   = get_stock(st, member.id)
        port = get_portfolio(st, message.author.id)
        tid  = str(member.id)
        held = port["longs"].get(tid, 0)
        if held == 0: return await message.channel.send("You don't hold any shares of this stock!")
        if shares == 0: shares = held
        if shares > held: return await message.channel.send(f"You only have **{held} shares**!")
        price = stock_price(s); total = round(price * shares, 2)
        avg_cost = port.get(f"long_avg_{tid}", price)
        pnl = round((price - avg_cost) * shares, 2)
        eco["balance"] = round(eco["balance"] + total, 2)
        port["longs"][tid] -= shares
        if port["longs"][tid] == 0:
            del port["longs"][tid]; port.pop(f"long_avg_{tid}", None)
        save_db(db); save_st(st)
        e = (discord.Embed(title="💰 Shares Sold!", description=f"Sold **{shares} share{'s' if shares>1 else ''}** of **{member.display_name}**.", color=0x2ECC71 if pnl>=0 else 0xE74C3C, timestamp=datetime.now(timezone.utc))
             .add_field(name="Price/Share", value=f"**${price:.2f}**",                    inline=True)
             .add_field(name="Proceeds",    value=f"**${total:,.2f}**",                   inline=True)
             .add_field(name="P&L",         value=f"**{'+' if pnl>=0 else ''}${pnl:,.2f}**", inline=True)
             .add_field(name="New Balance", value=f"**${eco['balance']:,}**",             inline=True)
             .set_footer(text="Use $portfolio to see all holdings"))
        await message.channel.send(embed=e)

    # ── short ──
    elif resolved == "short":
        if not message.mentions or len(args) < 2:
            return await message.channel.send("Usage: `$short @member <shares>`")
        member = message.mentions[0]
        if member.bot: return await message.channel.send("Can't short bot stocks!")
        if member.id == message.author.id: return await message.channel.send("Can't short your own stock!")
        try: shares = int(args[-1])
        except ValueError: return await message.channel.send("Shares must be a number!")
        if shares < 1: return await message.channel.send("Short at least 1 share!")
        db  = load_db(); eco = get_user(db, str(message.author.id))
        st  = load_st(); s   = get_stock(st, member.id)
        s["name"] = member.display_name
        price  = stock_price(s)
        margin = round(price * shares * 0.5, 2)
        if margin > eco["balance"]:
            return await message.channel.send(embed=discord.Embed(title="❌ Insufficient Margin",
                description=f"Shorting {shares} shares requires **${margin:,.2f}** margin. You have **${eco['balance']:,}**.", color=0xE74C3C))
        eco["balance"] = round(eco["balance"] - margin, 2)
        port = get_portfolio(st, message.author.id)
        tid  = str(member.id)
        port["shorts"][tid] = port["shorts"].get(tid, 0) + shares
        skey = f"short_entry_{tid}"
        if skey not in port: port[skey] = price
        else:
            old = port["shorts"][tid] - shares
            port[skey] = round((port[skey]*old + price*shares) / port["shorts"][tid], 4)
        save_db(db); save_st(st)
        e = (discord.Embed(title="🔴 Short Position Opened!", description=f"You shorted **{shares} share{'s' if shares>1 else ''}** of **{member.display_name}**.", color=0xE74C3C, timestamp=datetime.now(timezone.utc))
             .add_field(name="Entry Price",   value=f"**${price:.2f}**",        inline=True)
             .add_field(name="Margin Posted", value=f"**${margin:,.2f}**",      inline=True)
             .add_field(name="New Balance",   value=f"**${eco['balance']:,}**", inline=True)
             .add_field(name="Your Short",    value=f"🔴 **{port['shorts'][tid]} shares**", inline=True)
             .set_footer(text="You profit if the stock price falls • $covershort to close • Shorting drains -1pt/hr from the target stock"))
        await message.channel.send(embed=e)

    # ── covershort ──
    elif resolved == "covershort":
        if not message.mentions:
            return await message.channel.send("Usage: `$covershort @member <shares>` (0 = cover all)")
        member = message.mentions[0]
        try: shares = int(args[-1]) if len(args) >= 2 else 0
        except ValueError: shares = 0
        db  = load_db(); eco = get_user(db, str(message.author.id))
        st  = load_st(); s   = get_stock(st, member.id)
        port = get_portfolio(st, message.author.id)
        tid  = str(member.id)
        held = port["shorts"].get(tid, 0)
        if held == 0: return await message.channel.send("No short position on this stock!")
        if shares == 0: shares = held
        if shares > held: return await message.channel.send(f"You only shorted **{held} shares**!")
        current_price = stock_price(s)
        entry_price   = port.get(f"short_entry_{tid}", current_price)
        pnl           = round((entry_price - current_price) * shares, 2)
        margin_return = round(entry_price * shares * 0.5, 2)
        payout        = round(margin_return + pnl, 2)
        eco["balance"] = round(eco["balance"] + payout, 2)
        port["shorts"][tid] -= shares
        if port["shorts"][tid] == 0:
            del port["shorts"][tid]; port.pop(f"short_entry_{tid}", None)
        save_db(db); save_st(st)
        e = (discord.Embed(title="✅ Short Covered!", description=f"Closed **{shares} share{'s' if shares>1 else ''}** short on **{member.display_name}**.", color=0x2ECC71 if pnl>=0 else 0xE74C3C, timestamp=datetime.now(timezone.utc))
             .add_field(name="Entry Price",  value=f"**${entry_price:.2f}**",   inline=True)
             .add_field(name="Exit Price",   value=f"**${current_price:.2f}**", inline=True)
             .add_field(name="P&L",          value=f"**{'+' if pnl>=0 else ''}${pnl:,.2f}**", inline=True)
             .add_field(name="Payout",       value=f"**${payout:,.2f}**",       inline=True)
             .add_field(name="New Balance",  value=f"**${eco['balance']:,}**",  inline=True)
             .set_footer(text="Short profit = price went down after you shorted"))
        await message.channel.send(embed=e)

    # ── portfolio ──
    elif resolved == "portfolio":
        db  = load_db(); eco  = get_user(db, str(message.author.id))
        st  = load_st(); port = get_portfolio(st, message.author.id)
        longs  = port.get("longs",  {})
        shorts = port.get("shorts", {})
        if not longs and not shorts:
            return await message.channel.send("Your portfolio is empty! Use `$stocks` to browse and `$buy` or `$short` to trade.")
        long_lines  = []; total_long_val = 0
        for tid, qty in longs.items():
            s = st["stocks"].get(tid)
            if not s: continue
            name  = s.get("name") or f"User#{tid[-4:]}"
            price = stock_price(s); avg = port.get(f"long_avg_{tid}", price)
            pnl   = round((price - avg) * qty, 2); val = round(price * qty, 2)
            total_long_val += val
            long_lines.append(f"🟢 **{name}** × {qty} @ ${avg:.2f}\n  Now **${price:.2f}** • Val **${val:,.2f}** • P&L **{'+' if pnl>=0 else ''}${pnl:,.2f}**")
        short_lines = []; total_short_pnl = 0
        for tid, qty in shorts.items():
            s = st["stocks"].get(tid)
            if not s: continue
            name  = s.get("name") or f"User#{tid[-4:]}"
            price = stock_price(s); entry = port.get(f"short_entry_{tid}", price)
            pnl   = round((entry - price) * qty, 2); total_short_pnl += pnl
            short_lines.append(f"🔴 **{name}** × {qty} short @ ${entry:.2f}\n  Now **${price:.2f}** • P&L **{'+' if pnl>=0 else ''}${pnl:,.2f}**")
        e = (discord.Embed(title=f"💼 {message.author.display_name}'s Portfolio", color=0x5865F2, timestamp=datetime.now(timezone.utc))
             .add_field(name="💰 Wallet Balance", value=f"**${eco['balance']:,}**",                inline=True)
             .add_field(name="📈 Long Value",     value=f"**${total_long_val:,.2f}**",            inline=True)
             .add_field(name="📉 Short P&L",      value=f"**{'+' if total_short_pnl>=0 else ''}${total_short_pnl:,.2f}**", inline=True))
        if long_lines:  e.add_field(name="── Long Positions ──",  value="\n\n".join(long_lines),  inline=False)
        if short_lines: e.add_field(name="── Short Positions ──", value="\n\n".join(short_lines), inline=False)
        e.set_footer(text="$sell to close longs • $covershort to close shorts • $stockinfo @user for charts")
        await message.channel.send(embed=e)

    # ── help ──
    elif resolved == "help":
        casino_cmds = (
            "**`$cf <heads/tails> <amount>`** — 🪙 Coin flip\n"
            "**`$bj <amount>`** — 🃏 Blackjack\n"
            "**`$slots <amount>`** — 🎰 Slot machine\n"
            "**`$plinko <amount>`** — 🎯 Plinko drop\n"
            "**`$roulette <amount>`** — 🎡 Roulette (Red/Black)\n"
        )
        stock_cmds = (
            "**`$stocks`** — 📊 Browse all stocks\n"
            "**`$stockinfo @member`** — 📈 View chart (15m/1h/2h/8h/24h)\n"
            "**`$buy @member <shares>`** — 🟢 Buy (long) shares\n"
            "**`$sell @member <shares>`** — 💰 Sell long shares\n"
            "**`$short @member <shares>`** — 🔴 Short a stock\n"
            "**`$covershort @member <shares>`** (or `$cs`) — Close short\n"
            "**`$portfolio`** (or `$port`) — 💼 Your holdings\n"
        )
        general_cmds = (
            "**`$balance`** (or `$bal`) — 💰 Check your balance\n"
            "**`$daily`** — 📅 Claim $5,000 daily\n"
            "**`$weekly`** — 📆 Claim $1,000 weekly\n"
            "**`$help`** — ❓ This menu\n"
        )
        e = (discord.Embed(
            title="❓ Help — All Commands",
            description="All commands use the **`$`** prefix. Slash (`/`) commands also work for most actions.",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        .add_field(name="🎰 Casino", value=casino_cmds, inline=False)
        .add_field(name="📊 Stock Market", value=stock_cmds, inline=False)
        .add_field(name="💼 General", value=general_cmds, inline=False)
        .add_field(name="📈 How Stocks Work",
            value=(
                "• Chatting earns **+1 pt/message**, voice earns **+2 pts/min**\n"
                "• Each point = **+$1** to that stock's price\n"
                "• **Shorting** a stock drains the target **-1 pt/hr per shorted share** — making it actually profitable to short inactive members!\n"
                "• Charts support **15m, 1h, 2h, 8h, 24h** windows\n"
            ),
            inline=False,
        )
        .set_footer(text="Tip: Use $roulette then click 🟥 Red or ⬛ Black to spin!"))
        await message.channel.send(embed=e)

@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot: return
    st  = load_st()
    uid = str(member.id)
    if "voice_sessions" not in st: st["voice_sessions"] = {}

    if after.channel and not before.channel:
        st["voice_sessions"][uid] = now_ts()
        save_st(st)
    elif before.channel and not after.channel:
        if uid in st["voice_sessions"]:
            elapsed_min = (now_ts() - st["voice_sessions"].pop(uid)) / 60
            pts_earned  = int(elapsed_min * VOICE_POINTS_PM)
            if pts_earned > 0:
                s = get_stock(st, uid)
                s["name"]    = member.display_name
                s["points"] += pts_earned
                push_snapshot(s)
            save_st(st)

# ══════════════════════════════════════════════════════
#  15-MIN SNAPSHOT LOOP — also applies short decay
# ══════════════════════════════════════════════════════
async def snapshot_loop():
    await client.wait_until_ready()
    last_decay_ts = now_ts()

    while not client.is_closed():
        await asyncio.sleep(SNAPSHOT_INTERVAL)
        st = load_st()

        # Award voice points to people still in voice
        if "voice_sessions" in st:
            for uid, join_ts in list(st["voice_sessions"].items()):
                elapsed_min = (now_ts() - join_ts) / 60
                pts = int(elapsed_min * VOICE_POINTS_PM)
                if pts > 0:
                    s = get_stock(st, uid)
                    s["points"] += pts
                    st["voice_sessions"][uid] = now_ts()
                    push_snapshot(s)

        # Short decay: -1 point per hour per shorted share on the target stock
        elapsed_hours = (now_ts() - last_decay_ts) / 3600
        if elapsed_hours >= 1.0:
            # Count total shorted shares per stock
            short_totals: dict[str, int] = {}
            for port in st["portfolios"].values():
                for tid, qty in port.get("shorts", {}).items():
                    short_totals[tid] = short_totals.get(tid, 0) + qty

            for uid, total_shorts in short_totals.items():
                if total_shorts <= 0: continue
                s = get_stock(st, uid)
                drain = int(total_shorts * SHORT_DECAY_PTS * elapsed_hours)
                if drain > 0:
                    s["points"] = max(0, s["points"] - drain)
                    push_snapshot(s)

            last_decay_ts = now_ts()

        # Snapshot all stocks
        for uid, s in st["stocks"].items():
            push_snapshot(s)

        save_st(st)
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M')}] ✅ Stock snapshots saved.")

# ══════════════════════════════════════════════════════
#  SLASH COMMANDS  (kept alongside $ prefix)
# ══════════════════════════════════════════════════════
@tree.command(name="balance", description="💰 Check your wallet balance")
async def slash_balance(interaction: discord.Interaction):
    db = load_db(); u = get_user(db, str(interaction.user.id))
    e = (discord.Embed(title="💰 Wallet Balance", description=f"**{interaction.user.display_name}**, here's your balance:", color=0xF1C40F, timestamp=datetime.now(timezone.utc))
         .add_field(name="Balance", value=f"**${u['balance']:,}**", inline=True)
         .set_footer(text="$cf $bj $slots $plinko $roulette • $daily $weekly • $stocks to trade"))
    await interaction.response.send_message(embed=e)

@tree.command(name="daily", description="📅 Claim your $5,000 daily reward")
async def slash_daily(interaction: discord.Interaction):
    db = load_db(); u = get_user(db, str(interaction.user.id)); now = now_ts(); cd = 86400
    if u["last_daily"] and now - u["last_daily"] < cd:
        return await interaction.response.send_message(embed=discord.Embed(title="⏰ Already Claimed!", description=f"Come back in **{fmt_time(cd-(now-u['last_daily']))}**!", color=0xE74C3C), ephemeral=True)
    u["balance"] += 5000; u["last_daily"] = now; save_db(db)
    e = (discord.Embed(title="📅 Daily Reward Claimed!", description=f"Here's your payout, **{interaction.user.display_name}**! 🎉", color=0x2ECC71, timestamp=datetime.now(timezone.utc))
         .add_field(name="Reward", value="**+$5,000**", inline=True)
         .add_field(name="New Balance", value=f"**${u['balance']:,}**", inline=True)
         .set_footer(text="Come back in 24 hours!"))
    await interaction.response.send_message(embed=e)

@tree.command(name="weekly", description="📆 Claim your $1,000 weekly reward")
async def slash_weekly(interaction: discord.Interaction):
    db = load_db(); u = get_user(db, str(interaction.user.id)); now = now_ts(); cd = 604800
    if u["last_weekly"] and now - u["last_weekly"] < cd:
        return await interaction.response.send_message(embed=discord.Embed(title="⏰ Already Claimed!", description=f"Come back in **{fmt_time(cd-(now-u['last_weekly']))}**!", color=0xE74C3C), ephemeral=True)
    u["balance"] += 1000; u["last_weekly"] = now; save_db(db)
    e = (discord.Embed(title="📆 Weekly Reward Claimed!", description=f"Here's your weekly payout, **{interaction.user.display_name}**! 🎉", color=0x9B59B6, timestamp=datetime.now(timezone.utc))
         .add_field(name="Reward", value="**+$1,000**", inline=True)
         .add_field(name="New Balance", value=f"**${u['balance']:,}**", inline=True)
         .set_footer(text="Come back in 7 days!"))
    await interaction.response.send_message(embed=e)

@tree.command(name="cf", description="🪙 Flip a coin and wager your money!")
@app_commands.describe(choice="Heads or tails", amount="Amount to wager")
@app_commands.choices(choice=[app_commands.Choice(name="🪙 Heads", value="heads"), app_commands.Choice(name="🌑 Tails", value="tails")])
async def slash_cf(interaction: discord.Interaction, choice: str, amount: int):
    if amount < 1: return await interaction.response.send_message("Min bet $1!", ephemeral=True)
    db = load_db(); u = get_user(db, str(interaction.user.id))
    if amount > u["balance"]: return await interaction.response.send_message(embed=insuf(u["balance"]), ephemeral=True)
    result = random.choice(["heads", "tails"]); won = choice == result
    emoji = "🪙" if result == "heads" else "🌑"
    u["balance"] += amount if won else -amount; save_db(db)
    e = (discord.Embed(title="🎉 You Won!" if won else "💀 You Lost!", description=f"Coin landed **{result}** {emoji}\nYou picked **{choice}** — {'correct! 🎊' if won else 'wrong! 😬'}", color=0x2ECC71 if won else 0xE74C3C, timestamp=datetime.now(timezone.utc))
         .add_field(name="Won" if won else "Lost", value=f"**{'+' if won else '-'}${amount:,}**", inline=True)
         .add_field(name="New Balance", value=f"**${u['balance']:,}**", inline=True))
    await interaction.response.send_message(embed=e)

@tree.command(name="bj", description="🃏 Play Blackjack!")
@app_commands.describe(amount="Amount to bet")
async def slash_bj(interaction: discord.Interaction, amount: int):
    if amount < 1: return await interaction.response.send_message("Min bet $1!", ephemeral=True)
    db = load_db(); u = get_user(db, str(interaction.user.id))
    if amount > u["balance"]: return await interaction.response.send_message(embed=insuf(u["balance"]), ephemeral=True)
    deck = new_deck(); player = [deck.pop(), deck.pop()]; dealer = [deck.pop(), deck.pop()]
    if hand_total(player) == 21:
        w = math.floor(amount * 1.5); u["balance"] += w; save_db(db)
        e = bj_embed(player, dealer, amount, u["balance"], "blackjack")
        e.add_field(name="Payout", value=f"**+${w:,}** (1.5x)", inline=True)
        return await interaction.response.send_message(embed=e)
    view = BlackjackView(deck, player, dealer, amount, interaction.user.id, db)
    await interaction.response.send_message(embed=bj_embed(player, dealer, amount, u["balance"], "playing", True), view=view)

@tree.command(name="slots", description="🎰 Spin the slot machine!")
@app_commands.describe(amount="Amount to bet")
async def slash_slots(interaction: discord.Interaction, amount: int):
    if amount < 1: return await interaction.response.send_message("Min bet $1!", ephemeral=True)
    db = load_db(); u = get_user(db, str(interaction.user.id))
    if amount > u["balance"]: return await interaction.response.send_message(embed=insuf(u["balance"]), ephemeral=True)
    reels = [w_sym(), w_sym(), w_sym()]; mult = slots_mult(reels); net = math.floor(amount * mult) - amount
    u["balance"] += net; save_db(db)
    is_win = net > 0; is_push = mult == 0.5
    if mult >= 50: title, desc = "🎰 ⭐ JACKPOT ⭐", "**TRIPLE JOKER!**\n"
    elif mult >= 20: title, desc = "🎰 🔥 MEGA WIN!", "**Triple 7s!**\n"
    elif mult >= 10: title, desc = "🎰 🎉 BIG WIN!", "**Triple Diamonds!**\n"
    elif is_win: title, desc = "🎰 You Win!", ""
    elif is_push: title, desc = "🎰 Partial Win", "Pair — partial return.\n"
    else: title, desc = "🎰 No Match", ""
    board = f"{desc}┌─────────────────┐\n│  {reels[0]}  {reels[1]}  {reels[2]}  │\n└─────────────────┘"
    e = (discord.Embed(title=title, description=board, color=0x2ECC71 if is_win else (0xF1C40F if is_push else 0xE74C3C), timestamp=datetime.now(timezone.utc))
         .add_field(name="Multiplier", value=f"**{mult}x**", inline=True)
         .add_field(name="Won" if net >= 0 else "Lost", value=f"**{'+' if net >= 0 else ''}${net:,}**", inline=True)
         .add_field(name="Balance", value=f"**${u['balance']:,}**", inline=True)
         .set_footer(text="🃏=50x • 7️⃣=20x • 💎=15x • 🍇=8x • 🍊=5x • 🍋=3x • 🍒=2x • Pair=0.5x"))
    await interaction.response.send_message(embed=e)

@tree.command(name="plinko", description="🎯 Drop the ball in Plinko!")
@app_commands.describe(amount="Amount to bet")
async def slash_plinko(interaction: discord.Interaction, amount: int):
    if amount < 1: return await interaction.response.send_message("Min bet $1!", ephemeral=True)
    db = load_db(); u = get_user(db, str(interaction.user.id))
    if amount > u["balance"]: return await interaction.response.send_message(embed=insuf(u["balance"]), ephemeral=True)
    pos, mult, path = sim_plinko(); net = math.floor(amount * mult) - amount; u["balance"] += net; save_db(db)
    md = "  ".join(f"❱**{m}x**❰" if i == pos else f"{m}x" for i, m in enumerate(PLINKO_M))
    ps = "".join("→" if d == "R" else "←" for d in path)
    title = "🎯 JACKPOT LANE!" if mult >= 10 else ("🎯 Great Drop!" if mult >= 3 else ("🎯 Plinko Result" if mult >= 1 else "🎯 Unlucky Drop"))
    e = (discord.Embed(title=title, description=f"**Path:** {ps}\n\n**Multipliers:**\n{md}", color=0x2ECC71 if mult >= 3 else (0xF1C40F if mult >= 1 else 0xE74C3C), timestamp=datetime.now(timezone.utc))
         .add_field(name="Multiplier", value=f"**{mult}x**", inline=True)
         .add_field(name="Won" if net >= 0 else "Lost", value=f"**{'+' if net >= 0 else ''}${net:,}**", inline=True)
         .add_field(name="Balance", value=f"**${u['balance']:,}**", inline=True)
         .set_footer(text="Multipliers: 10x 3x 1.5x 0.5x 0.3x 0.5x 1.5x 3x 10x"))
    await interaction.response.send_message(embed=e)

@tree.command(name="roulette", description="🎡 Spin the roulette wheel — bet Red or Black!")
@app_commands.describe(amount="Amount to bet")
async def slash_roulette(interaction: discord.Interaction, amount: int):
    if amount < 1: return await interaction.response.send_message("Min bet $1!", ephemeral=True)
    db = load_db(); u = get_user(db, str(interaction.user.id))
    if amount > u["balance"]: return await interaction.response.send_message(embed=insuf(u["balance"]), ephemeral=True)
    u["balance"] -= amount; save_db(db)
    view = RouletteView(amount, interaction.user.id, db)
    e = (discord.Embed(
        title="🎡 Roulette — Place Your Bet!",
        description=(
            "```\n"
            "  ╔══════════════════════╗\n"
            "  ║    🎡  ROULETTE      ║\n"
            "  ╠══════════════════════╣\n"
            "  ║  🟥 ⬛ 🟥 ⬛ 🟥  ║\n"
            "  ║  ⬛ 🟥  0  🟥 ⬛  ║\n"
            "  ║  🟥 ⬛ 🟥 ⬛ 🟥  ║\n"
            "  ╠══════════════════════╣\n"
            "  ║  Pick Red or Black   ║\n"
            "  ╚══════════════════════╝\n"
            "```\n"
            "The wheel has **37 pockets**: 18 🟥 Red, 18 ⬛ Black, 1 🟩 Green (0).\n"
            "Pick a colour — wins pay **1:1**. Green is always the house!"
        ),
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    .add_field(name="Your Bet", value=f"**${amount:,}**", inline=True)
    .add_field(name="Balance",  value=f"**${u['balance']:,}**", inline=True)
    .set_footer(text="Press a button to spin!"))
    await interaction.response.send_message(embed=e, view=view)

@tree.command(name="stocks", description="📊 Browse all member stocks")
async def slash_stocks(interaction: discord.Interaction):
    st = load_st()
    if not st["stocks"]:
        return await interaction.response.send_message("No stocks yet — members need to chat first!", ephemeral=True)
    entries = sorted(st["stocks"].items(), key=lambda x: stock_price(x[1]), reverse=True)
    view = StockBrowseView(entries, interaction.guild)
    view.update_buttons()
    await interaction.response.send_message(embed=view.make_embed(), view=view)

@tree.command(name="stockinfo", description="📈 View a member's stock chart and stats")
@app_commands.describe(member="The member whose stock to view")
async def slash_stockinfo(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    st = load_st()
    s  = get_stock(st, member.id)
    s["name"] = member.display_name
    save_st(st)
    view = StockInfoView(member, s, current_window=24.0)
    chart_file = make_chart(s, member.display_name, 24.0)
    e = view._make_embed()
    await interaction.followup.send(embed=e, file=chart_file, view=view)

@tree.command(name="buy", description="🟢 Buy shares of a member's stock")
@app_commands.describe(member="Member whose stock to buy", shares="Number of shares to buy")
async def slash_buy(interaction: discord.Interaction, member: discord.Member, shares: int):
    if member.bot: return await interaction.response.send_message("Can't buy bot stocks!", ephemeral=True)
    if shares < 1: return await interaction.response.send_message("Buy at least 1 share!", ephemeral=True)
    if member.id == interaction.user.id: return await interaction.response.send_message("Can't buy your own stock!", ephemeral=True)
    db  = load_db(); eco = get_user(db, str(interaction.user.id))
    st  = load_st(); s   = get_stock(st, member.id)
    s["name"] = member.display_name
    price = stock_price(s); total = round(price * shares, 2)
    if total > eco["balance"]: return await interaction.response.send_message(embed=insuf(eco["balance"]), ephemeral=True)
    eco["balance"] = round(eco["balance"] - total, 2)
    port = get_portfolio(st, interaction.user.id); tid = str(member.id)
    port["longs"][tid] = port["longs"].get(tid, 0) + shares
    pkey = f"long_avg_{tid}"
    if pkey not in port: port[pkey] = price
    else:
        old = port["longs"][tid] - shares
        port[pkey] = round((port[pkey]*old + price*shares) / port["longs"][tid], 4)
    save_db(db); save_st(st)
    e = (discord.Embed(title="🟢 Shares Purchased!", description=f"You bought **{shares} share{'s' if shares>1 else ''}** of **{member.display_name}**'s stock.", color=0x2ECC71, timestamp=datetime.now(timezone.utc))
         .add_field(name="Price/Share",   value=f"**${price:.2f}**",          inline=True)
         .add_field(name="Total Cost",    value=f"**${total:,.2f}**",         inline=True)
         .add_field(name="New Balance",   value=f"**${eco['balance']:,}**",   inline=True)
         .add_field(name="Your Position", value=f"🟢 **{port['longs'][tid]} shares** long", inline=True)
         .set_footer(text="Use $portfolio to track your holdings • $sell to close position"))
    await interaction.response.send_message(embed=e)

@tree.command(name="sell", description="💰 Sell your long shares of a member's stock")
@app_commands.describe(member="Member whose stock to sell", shares="Shares to sell (0 = sell all)")
async def slash_sell(interaction: discord.Interaction, member: discord.Member, shares: int):
    db  = load_db(); eco = get_user(db, str(interaction.user.id))
    st  = load_st(); s   = get_stock(st, member.id)
    port = get_portfolio(st, interaction.user.id); tid = str(member.id)
    held = port["longs"].get(tid, 0)
    if held == 0: return await interaction.response.send_message("You don't hold any shares!", ephemeral=True)
    if shares == 0: shares = held
    if shares > held: return await interaction.response.send_message(f"You only have **{held} shares**!", ephemeral=True)
    price = stock_price(s); total = round(price * shares, 2)
    avg_cost = port.get(f"long_avg_{tid}", price); pnl = round((price - avg_cost) * shares, 2)
    eco["balance"] = round(eco["balance"] + total, 2)
    port["longs"][tid] -= shares
    if port["longs"][tid] == 0: del port["longs"][tid]; port.pop(f"long_avg_{tid}", None)
    save_db(db); save_st(st)
    e = (discord.Embed(title="💰 Shares Sold!", description=f"Sold **{shares} share{'s' if shares>1 else ''}** of **{member.display_name}**.", color=0x2ECC71 if pnl>=0 else 0xE74C3C, timestamp=datetime.now(timezone.utc))
         .add_field(name="Price/Share", value=f"**${price:.2f}**",                    inline=True)
         .add_field(name="Proceeds",    value=f"**${total:,.2f}**",                   inline=True)
         .add_field(name="P&L",         value=f"**{'+' if pnl>=0 else ''}${pnl:,.2f}**", inline=True)
         .add_field(name="New Balance", value=f"**${eco['balance']:,}**",             inline=True)
         .set_footer(text="Use $portfolio to see all holdings"))
    await interaction.response.send_message(embed=e)

@tree.command(name="short", description="🔴 Short a member's stock (bet they go less active)")
@app_commands.describe(member="Member whose stock to short", shares="Number of shares to short")
async def slash_short(interaction: discord.Interaction, member: discord.Member, shares: int):
    if member.bot: return await interaction.response.send_message("Can't short bot stocks!", ephemeral=True)
    if shares < 1: return await interaction.response.send_message("Short at least 1 share!", ephemeral=True)
    if member.id == interaction.user.id: return await interaction.response.send_message("Can't short your own stock!", ephemeral=True)
    db  = load_db(); eco = get_user(db, str(interaction.user.id))
    st  = load_st(); s   = get_stock(st, member.id)
    s["name"] = member.display_name; price = stock_price(s)
    margin = round(price * shares * 0.5, 2)
    if margin > eco["balance"]:
        return await interaction.response.send_message(embed=discord.Embed(title="❌ Insufficient Margin", description=f"Requires **${margin:,.2f}** margin. You have **${eco['balance']:,}**.", color=0xE74C3C), ephemeral=True)
    eco["balance"] = round(eco["balance"] - margin, 2)
    port = get_portfolio(st, interaction.user.id); tid = str(member.id)
    port["shorts"][tid] = port["shorts"].get(tid, 0) + shares
    skey = f"short_entry_{tid}"
    if skey not in port: port[skey] = price
    else:
        old = port["shorts"][tid] - shares
        port[skey] = round((port[skey]*old + price*shares) / port["shorts"][tid], 4)
    save_db(db); save_st(st)
    e = (discord.Embed(title="🔴 Short Position Opened!", description=f"You shorted **{shares} share{'s' if shares>1 else ''}** of **{member.display_name}**.", color=0xE74C3C, timestamp=datetime.now(timezone.utc))
         .add_field(name="Entry Price",   value=f"**${price:.2f}**",        inline=True)
         .add_field(name="Margin Posted", value=f"**${margin:,.2f}**",      inline=True)
         .add_field(name="New Balance",   value=f"**${eco['balance']:,}**", inline=True)
         .add_field(name="Your Short",    value=f"🔴 **{port['shorts'][tid]} shares**", inline=True)
         .set_footer(text="Shorting drains -1pt/hr from the target stock • $covershort to close"))
    await interaction.response.send_message(embed=e)

@tree.command(name="covershort", description="🔴 Close your short position on a member's stock")
@app_commands.describe(member="Member whose stock you shorted", shares="Shares to cover (0 = cover all)")
async def slash_covershort(interaction: discord.Interaction, member: discord.Member, shares: int):
    db  = load_db(); eco = get_user(db, str(interaction.user.id))
    st  = load_st(); s   = get_stock(st, member.id)
    port = get_portfolio(st, interaction.user.id); tid = str(member.id)
    held = port["shorts"].get(tid, 0)
    if held == 0: return await interaction.response.send_message("No short position on this stock!", ephemeral=True)
    if shares == 0: shares = held
    if shares > held: return await interaction.response.send_message(f"You only shorted **{held} shares**!", ephemeral=True)
    current_price = stock_price(s); entry_price = port.get(f"short_entry_{tid}", current_price)
    pnl = round((entry_price - current_price) * shares, 2)
    margin_return = round(entry_price * shares * 0.5, 2); payout = round(margin_return + pnl, 2)
    eco["balance"] = round(eco["balance"] + payout, 2)
    port["shorts"][tid] -= shares
    if port["shorts"][tid] == 0: del port["shorts"][tid]; port.pop(f"short_entry_{tid}", None)
    save_db(db); save_st(st)
    e = (discord.Embed(title="✅ Short Covered!", description=f"Closed **{shares} share{'s' if shares>1 else ''}** short on **{member.display_name}**.", color=0x2ECC71 if pnl>=0 else 0xE74C3C, timestamp=datetime.now(timezone.utc))
         .add_field(name="Entry Price",  value=f"**${entry_price:.2f}**",   inline=True)
         .add_field(name="Exit Price",   value=f"**${current_price:.2f}**", inline=True)
         .add_field(name="P&L",          value=f"**{'+' if pnl>=0 else ''}${pnl:,.2f}**", inline=True)
         .add_field(name="Payout",       value=f"**${payout:,.2f}**",       inline=True)
         .add_field(name="New Balance",  value=f"**${eco['balance']:,}**",  inline=True)
         .set_footer(text="Short profit = price went down after you shorted"))
    await interaction.response.send_message(embed=e)

@tree.command(name="portfolio", description="💼 View your stock portfolio and P&L")
async def slash_portfolio(interaction: discord.Interaction):
    db  = load_db(); eco  = get_user(db, str(interaction.user.id))
    st  = load_st(); port = get_portfolio(st, interaction.user.id)
    longs  = port.get("longs",  {}); shorts = port.get("shorts", {})
    if not longs and not shorts:
        return await interaction.response.send_message("Your portfolio is empty! Use `$stocks` to browse and `$buy` or `$short` to trade.", ephemeral=True)
    long_lines  = []; total_long_val = 0
    for tid, qty in longs.items():
        s = st["stocks"].get(tid)
        if not s: continue
        name  = s.get("name") or f"User#{tid[-4:]}"
        price = stock_price(s); avg = port.get(f"long_avg_{tid}", price)
        pnl   = round((price - avg) * qty, 2); val = round(price * qty, 2); total_long_val += val
        long_lines.append(f"🟢 **{name}** × {qty} @ ${avg:.2f}\n  Now **${price:.2f}** • Val **${val:,.2f}** • P&L **{'+' if pnl>=0 else ''}${pnl:,.2f}**")
    short_lines = []; total_short_pnl = 0
    for tid, qty in shorts.items():
        s = st["stocks"].get(tid)
        if not s: continue
        name  = s.get("name") or f"User#{tid[-4:]}"
        price = stock_price(s); entry = port.get(f"short_entry_{tid}", price)
        pnl   = round((entry - price) * qty, 2); total_short_pnl += pnl
        short_lines.append(f"🔴 **{name}** × {qty} short @ ${entry:.2f}\n  Now **${price:.2f}** • P&L **{'+' if pnl>=0 else ''}${pnl:,.2f}**")
    e = (discord.Embed(title=f"💼 {interaction.user.display_name}'s Portfolio", color=0x5865F2, timestamp=datetime.now(timezone.utc))
         .add_field(name="💰 Wallet Balance", value=f"**${eco['balance']:,}**",                inline=True)
         .add_field(name="📈 Long Value",     value=f"**${total_long_val:,.2f}**",            inline=True)
         .add_field(name="📉 Short P&L",      value=f"**{'+' if total_short_pnl>=0 else ''}${total_short_pnl:,.2f}**", inline=True))
    if long_lines:  e.add_field(name="── Long Positions ──",  value="\n\n".join(long_lines),  inline=False)
    if short_lines: e.add_field(name="── Short Positions ──", value="\n\n".join(short_lines), inline=False)
    e.set_footer(text="$sell to close longs • $covershort to close shorts • $stockinfo @user for charts")
    await interaction.response.send_message(embed=e)

@tree.command(name="help", description="❓ View all bot commands")
async def slash_help(interaction: discord.Interaction):
    casino_cmds = (
        "**`$cf <heads/tails> <amount>`** — 🪙 Coin flip\n"
        "**`$bj <amount>`** — 🃏 Blackjack\n"
        "**`$slots <amount>`** — 🎰 Slot machine\n"
        "**`$plinko <amount>`** — 🎯 Plinko drop\n"
        "**`$roulette <amount>`** — 🎡 Roulette (Red/Black)\n"
    )
    stock_cmds = (
        "**`$stocks`** — 📊 Browse all stocks\n"
        "**`$stockinfo @member`** — 📈 View chart (15m/1h/2h/8h/24h)\n"
        "**`$buy @member <shares>`** — 🟢 Buy (long) shares\n"
        "**`$sell @member <shares>`** — 💰 Sell long shares\n"
        "**`$short @member <shares>`** — 🔴 Short a stock\n"
        "**`$covershort @member <shares>`** (or `$cs`) — Close short\n"
        "**`$portfolio`** (or `$port`) — 💼 Your holdings\n"
    )
    general_cmds = (
        "**`$balance`** (or `$bal`) — 💰 Check your balance\n"
        "**`$daily`** — 📅 Claim $5,000 daily\n"
        "**`$weekly`** — 📆 Claim $1,000 weekly\n"
        "**`$help`** — ❓ This menu\n"
    )
    e = (discord.Embed(
        title="❓ Help — All Commands",
        description="All commands use the **`$`** prefix. Slash (`/`) commands also work for most actions.",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    .add_field(name="🎰 Casino", value=casino_cmds, inline=False)
    .add_field(name="📊 Stock Market", value=stock_cmds, inline=False)
    .add_field(name="💼 General", value=general_cmds, inline=False)
    .add_field(name="📈 How Stocks Work",
        value=(
            "• Chatting earns **+1 pt/message**, voice earns **+2 pts/min**\n"
            "• Each point = **+$1** to that stock's price\n"
            "• **Shorting** drains the target **-1 pt/hr per shorted share**\n"
            "• Charts support **15m, 1h, 2h, 8h, 24h** windows\n"
        ),
        inline=False,
    )
    .set_footer(text="Tip: Use $roulette then click 🟥 Red or ⬛ Black to spin!"))
    await interaction.response.send_message(embed=e)

# ══════════════════════════════════════════════════════
#  READY
# ══════════════════════════════════════════════════════
@client.event
async def on_ready():
    await tree.sync()
    await client.change_presence(activity=discord.Game("📊 Stock Market • 🎰 Casino • $help"))
    client.loop.create_task(snapshot_loop())
    print(f"✅ Logged in as {client.user} (ID: {client.user.id})")
    print(f"✅ Slash commands synced!")
    print(f"✅ Stock snapshot loop started (every 15 min)")
    print(f"✅ Short decay loop active (-1pt/hr per shorted share)")

client.run(TOKEN)