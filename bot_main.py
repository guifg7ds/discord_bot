"""
╔═══════════════════════════════════════════════════════════╗
║                    PARADOX BOT                            ║
║          Discord Bot by Paradox · Python                  ║
║                                                           ║
║  Features:                                                ║
║   • Auto-role on join                                     ║
║   • Welcome & goodbye messages                            ║
║   • !hello / !goodbye custom commands                     ║
║   • Swear word auto-filter                                ║
║   • Moderation utilities                                  ║
╚═══════════════════════════════════════════════════════════╝
"""

try:
    import audioop
except ImportError:
    import sys
    try:
        import audioop_lts
        sys.modules['audioop'] = audioop_lts
    except ImportError:
        pass

import discord
from discord.ext import commands, tasks
import random
import secrets
import asyncio
from datetime import datetime, timedelta
from bot_functions import (
    load_config,
    save_config,
    contains_swear,
    censor_message,
    find_swear_word,
    format_welcome_message,
    format_goodbye_message,
    format_timestamp,
    parse_role_name,
)

# ──────────────────────────────────────────────
#  LOAD CONFIG
# ──────────────────────────────────────────────
import os
import re
from bot_database import db

async def save_config_sync(cfg: dict):
    """Save config to both config.json and MongoDB."""
    global config
    config = cfg
    save_config(cfg)
    if db.db is not None:
        try:
            await db.update_config(cfg)
        except Exception as e:
            print(f"  [ERROR] Failed to sync config to DB: {e}")

def is_authorized(exclude_ban=False):
    """Custom check for bypass system. Admin always allowed.
    Bypass users/roles allowed unless exclude_ban=True and command is a ban command.
    """
    async def predicate(ctx: commands.Context):
        if ctx.guild is None: return False
        if ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator:
            return True
        
        cfg = config
        bypass_users = cfg.get("BYPASS_USER_IDS", [])
        bypass_roles = cfg.get("BYPASS_ROLE_IDS", [])
        
        is_bypassed = (ctx.author.id in bypass_users or 
                      any(role.id in bypass_roles for role in ctx.author.roles))
        
        if is_bypassed:
            if exclude_ban and ctx.command.name in ["ban", "annihilate"]:
                return False
            return True
        return False
    return commands.check(predicate)

# ── SECURITY CONSTANTS ──
SCAM_LINKS = [
    "discord.gift/", "steamcommunity.com/gift", "nitro-", "free-nitro", 
    "steam-promo", "dicsord", "dlscord", "giveaway-nitro"
]
MAX_EVERYONE_MENTIONS = 1
QUARANTINE_ROLE_NAME = "Quarantined"
QUARANTINE_CHANNEL_NAME = "⚖️-contest-punishment"

config = load_config()

# Load token from environment variable (Railway)
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.environ.get("PREFIX") or config.get("PREFIX", "!")

# ──────────────────────────────────────────────
#  BOT SETUP
# ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True          # Needed for on_member_join / on_member_remove
intents.message_content = True  # Needed to read message content

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ── ECONOMY SETTINGS ──
CURRENCY_NAME = "paradoxy"

SHOP_ITEMS = {
    "Lucky Coin": {
        "price": 1500000,
        "desc": "Boosts luck by 5% in casino and chance games.",
        "buff": 1.05
    },
    "Golden Clover": {
        "price": 4500000,
        "desc": "Boosts luck by 15% in casino and chance games.",
        "buff": 1.15
    },
    "Thief Kit": {
        "price": 8500000,
        "desc": "Increase steal success rate by 12%.",
        "buff": 0.12
    },
    "Crime Mask": {
        "price": 5500000,
        "desc": "Reduces crime fines by 35% and increases success by 15%.",
        "buff_fine": 0.65,
        "buff_success": 0.15
    },
    "Shield": {
        "price": 3500000,
        "desc": "40% chance to block being robbed.",
        "buff": 0.40
    },
    "VIP Pass": {
        "price": 35000000,
        "desc": "75% bonus to daily rewards and 25% bonus to work.",
        "buff_daily": 1.75,
        "buff_work": 1.25
    }
}

HEIST_TARGETS = {
    "jewelry": {
        "name": "Jewelry Store",
        "easy": (30000, 50000),
        "normal": (70000, 100000),
        "hard": (150000, 200000),
        "minigames": ["lockpick", "circuit"]
    },
    "bank": {
        "name": "Main Bank",
        "easy": (40000, 60000),
        "normal": (100000, 150000),
        "hard": (250000, 400000),
        "minigames": ["safe", "hacking", "circuit"]
    },
    "truck": {
        "name": "Armored Truck",
        "easy": (25000, 40000),
        "normal": (60000, 90000),
        "hard": (120000, 180000),
        "minigames": ["lockpick", "vault"]
    }
}

COMMAND_COOLDOWNS = {
    "daily": 86400,
    "work": 300,
    "crime": 60,
    "heist": 300,
    "steal": 300,
    "casino": 5
}

class RiggedOdds:
    BASE_WIN_RATES = {
        "cf": 0.48,           # 48% base
        "slots_normal": 0.35, # 35% for any win
        "bj": 0.42,           # Blackjack base
        "roulette_red": 0.47, # Red/Black base
        "roulette_green": 0.02 # Green base
    }

    @staticmethod
    async def calculate_win_chance(game_type, inventory):
        chance = RiggedOdds.BASE_WIN_RATES.get(game_type, 0.45)
        
        # Apply Luck Buffs
        luck_buff = 0
        if "Lucky Coin" in inventory: luck_buff += 0.05
        if "Golden Clover" in inventory: luck_buff += 0.15
        
        # Cap max luck buff at 20%
        final_chance = chance + min(luck_buff, 0.20)
        return final_chance

# ── LEVELING SETTINGS ──
LEVEL_ROLES = {
    0: {"name": "Peasant", "color": 0x808080},
    5: {"name": "Squire", "color": 0x556B2F},
    10: {"name": "Knight", "color": 0x4682B4},
    15: {"name": "Baron", "color": 0x8A2BE2},
    20: {"name": "Viscount", "color": 0x9932CC},
    25: {"name": "Count", "color": 0xBA55D3},
    30: {"name": "Marquis", "color": 0xDA70D6},
    35: {"name": "Duke", "color": 0xC71585},
    40: {"name": "Grand Duke", "color": 0xFF1493},
    45: {"name": "Prince", "color": 0xFF69B4},
    50: {"name": "Archduke", "color": 0xFFD700},
    55: {"name": "Viceroy", "color": 0xFFA500},
    60: {"name": "Governor", "color": 0xFF8C00},
    65: {"name": "High Lord", "color": 0xFF4500},
    70: {"name": "Chancellor", "color": 0xFF0000},
    75: {"name": "Royal Advisor", "color": 0xB22222},
    80: {"name": "Guardian of the Realm", "color": 0x8B0000},
    85: {"name": "Hero of Paradox", "color": 0x00FFFF},
    90: {"name": "Legendary Sovereign", "color": 0x00BFFF},
    95: {"name": "Celestial Emperor", "color": 0x1E90FF},
    100: {"name": "Paradox Overlord", "color": 0xFFFFFF}
}

# Load custom roles from config if they exist
_cfg = load_config()
if "LEVEL_ROLES" in _cfg:
    LEVEL_ROLES.update({int(k): v for k, v in _cfg["LEVEL_ROLES"].items()})

def get_xp_for_level(level: int) -> int:
    """XP needed to reach the NEXT level from current."""
    if level < 0: return 0
    return 5 * (level ** 2) + 50 * level + 100

def get_total_xp_for_level(level: int) -> int:
    """Calculate total XP needed to reach a specific level."""
    total = 0
    for i in range(level):
        total += get_xp_for_level(i)
    return total

def get_level_from_xp(total_xp: int) -> int:
    """Calculate current level from total XP."""
    level = 0
    remaining_xp = total_xp
    while remaining_xp >= get_xp_for_level(level):
        remaining_xp -= get_xp_for_level(level)
        level += 1
    return level

def get_xp_progress(total_xp: int) -> tuple:
    """Returns (current_level, xp_into_level, xp_needed_for_next)."""
    level = 0
    remaining_xp = total_xp
    while remaining_xp >= get_xp_for_level(level):
        remaining_xp -= get_xp_for_level(level)
        level += 1
    return level, remaining_xp, get_xp_for_level(level)

@tasks.loop(minutes=1)
async def voice_xp_task():
    """Give 6 XP per minute to active users in voice channels."""
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            if vc == guild.afk_channel:
                continue
            
            # Filter real members (not bots, not deafened/muted)
            active_members = [m for m in vc.members if not m.bot and not (m.voice.self_deaf or m.voice.deaf)]
            
            for member in active_members:
                await add_xp_logic(member, amount=6, source="voice")

async def add_xp_logic(member: discord.Member, amount: int, source: str = "message", channel = None):
    """Core logic for adding XP, checking cooldowns, and handling level ups."""
    cfg = load_config()
    if not cfg.get("LEVELING_SYSTEM_ENABLED", True):
        return

    user_id = str(member.id)
    
    # ── Cooldown Check for Messages ──
    if source == "message":
        last_xp = await db.get_cooldown(user_id, "xp_cooldown")
        if last_xp and datetime.now() < last_xp + timedelta(minutes=1):
            return
        await db.set_cooldown(user_id, "xp_cooldown", datetime.now())

    # ── Update XP in Database ──
    new_doc = await db.add_xp(user_id, amount)
    total_xp = new_doc.get("xp", 0)
    old_level = new_doc.get("level", 0)
    
    # ── Calculate New Level ──
    new_level = get_level_from_xp(total_xp)
    
    if new_level > old_level:
        await db.set_level(user_id, new_level)
        await handle_level_up(member, new_level, channel)

async def handle_level_up(member: discord.Member, level: int, channel = None):
    """Assign roles and notify the user on level up."""
    # 1. Determine Role
    role_to_give = None
    role_info = None
    
    # Find highest role reward for this level
    for req_level in sorted(LEVEL_ROLES.keys(), reverse=True):
        if level >= req_level:
            role_info = LEVEL_ROLES[req_level]
            break
            
    if role_info:
        # Include level in the role name as requested
        role_name = f"Level {req_level}+ | {role_info['name']}"
        role_to_give = discord.utils.get(member.guild.roles, name=role_name)
        
        # 2. Auto-Create Role if Missing
        if not role_to_give:
            try:
                role_to_give = await member.guild.create_role(
                    name=role_name,
                    color=discord.Color(role_info["color"]),
                    hoist=True,
                    reason=f"Level {level} Kingdom Reward"
                )
                # Sort roles? (Simplified: Higher level roles should be higher)
                # We can't easily sort without potential permission issues, 
                # but let's try to put it below the bot's highest role.
            except discord.Forbidden:
                pass
        
        # 3. Assign Role and Remove Old Ones
        if role_to_give:
            try:
                # Remove any existing kingdom roles
                to_remove = []
                for r in member.roles:
                    # Check if the role is a kingdom role (starts with "Level ")
                    if r.name.startswith("Level ") and " | " in r.name and r.id != role_to_give.id:
                        to_remove.append(r)
                
                if to_remove:
                    await member.remove_roles(*to_remove)
                await member.add_roles(role_to_give)
            except:
                pass

    # 4. Notify User
    embed = discord.Embed(
        title="🎊 LEVEL UP! 🎊",
        description=f"Congratulations {member.mention}!\nYou've reached **Level {level}**!",
        color=role_info["color"] if role_info else 0x9B59B6,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if role_to_give:
        embed.add_field(name="New Rank", value=f"🛡️ **{role_to_give.name}**")
    
    embed.set_footer(text="Paradox Kingdom 💜")
    
    # 5. Send Notification
    cfg = load_config()
    lvl_channel_id = cfg.get("LEVEL_CHANNEL_ID")
    target_channel = None
    
    if lvl_channel_id:
        target_channel = member.guild.get_channel(int(lvl_channel_id))
    
    if not target_channel:
        target_channel = channel or member.guild.system_channel or member.guild.text_channels[0]

    if target_channel:
        try:
            await target_channel.send(content=member.mention, embed=embed)
        except:
            pass

# Dynamic interest rate tier probabilities:
#  80% → normal  (0.1% – 1.3%)
#  20% → negative (-1% – 0%)
@tasks.loop(hours=1)
async def apply_interest_task():
    """Apply dynamic variable interest to all bank balances every hour."""
    roll = random.random()
    if roll < 0.2:
        rate = random.uniform(-1.0, 0.0)
        tier = "📉 Negative"
    else:
        rate = random.uniform(0.1, 1.3)
        tier = "📊 Normal"
    multiplier = 1 + rate / 100
    await db.apply_bank_interest(multiplier)
    print(f"  [ECONOMY] Hourly bank interest applied: {rate:.2f}% ({tier})")

@tasks.loop(hours=24)
async def check_loans_task():
    """Check for overdue loans and apply penalties."""
    users = await db.get_all_users()
    now = datetime.now()
    for user_doc in users:
        loan = user_doc.get("loan")
        if not loan or loan.get("paid", False): continue
        
        due_date = datetime.strptime(loan["due_date"], "%Y-%m-%d %H:%M:%S")
        if now > due_date:
            # Overdue!
            user_id = user_doc["_id"]
            amount = loan["amount"]
            penalty = int(amount * 0.1) # 10% penalty
            await db.update_balance(user_id, -(amount + penalty))
            await db.clear_loan(user_id)
            print(f"  [ECONOMY] Loan overdue for {user_id}. Penalty applied.")

# ══════════════════════════════════════════════
#  TICKET SYSTEM UI
# ══════════════════════════════════════════════

class TicketControlView(discord.ui.View):
    """View inside a created ticket for claiming, closing, and vouching."""
    def __init__(self, vouch_enabled: bool = False, claimer_id: int = None, vouched: bool = False):
        super().__init__(timeout=None)
        self.vouch_enabled = vouch_enabled
        self.claimer_id = claimer_id
        self.vouched = vouched

        if not claimer_id:
            # Unclaimed
            btn_claim = discord.ui.Button(label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="claim_ticket", emoji="🙋")
            btn_claim.callback = self.claim_ticket
            self.add_item(btn_claim)
        elif vouch_enabled and not vouched:
            # Claimed, Vouch enabled, not vouched
            btn_vouch = discord.ui.Button(label="Vouch Staff", style=discord.ButtonStyle.success, custom_id="vouch_ticket", emoji="⭐")
            btn_vouch.callback = self.vouch_ticket
            self.add_item(btn_vouch)
            
        # Always have Close button
        btn_close = discord.ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket", emoji="🔒")
        btn_close.callback = self.close_ticket
        self.add_item(btn_close)

    async def claim_ticket(self, interaction: discord.Interaction):
        # Only users with manage_channels (mods) can claim
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ Only staff can claim tickets!", ephemeral=True)
            return
            
        self.claimer_id = interaction.user.id
        
        # We can update the message
        await interaction.response.send_message(f"✅ Ticket claimed by {interaction.user.mention}!")
        
        # Update view
        new_view = TicketControlView(vouch_enabled=self.vouch_enabled, claimer_id=self.claimer_id, vouched=False)
        await interaction.message.edit(view=new_view)

    async def vouch_ticket(self, interaction: discord.Interaction):
        # Vouch the claimer
        if not self.claimer_id:
            await interaction.response.send_message("⚠️ This ticket hasn't been claimed yet!", ephemeral=True)
            return
            
        if interaction.user.id == self.claimer_id:
            await interaction.response.send_message("❌ You cannot vouch yourself!", ephemeral=True)
            return

        cfg = load_config()
        p_id = str(self.claimer_id)
        
        # Database Update
        count = await db.get_vouches(p_id) + 1
        await db.set_vouches(p_id, count)
        
        level = (count // 5) + 1
        vouch_channel_id = cfg.get("VOUCH_CHANNEL_ID")
        
        member = interaction.guild.get_member(self.claimer_id)
        member_name = member.name if member else f"ID: {self.claimer_id}"
        member_mention = member.mention if member else f"<@{self.claimer_id}>"
        
        if vouch_channel_id:
            try:
                vouch_channel = interaction.guild.get_channel(int(vouch_channel_id))
                if vouch_channel:
                    embed = discord.Embed(
                        title="🌟 New Vouch!",
                        description=f"**{interaction.user.mention}** vouched for **{member_mention}** in {interaction.channel.mention}!",
                        color=0xF1C40F,
                        timestamp=discord.utils.utcnow()
                    )
                    embed.add_field(name="Staff Member", value=member_name, inline=True)
                    embed.add_field(name="Total Vouches", value=str(count), inline=True)
                    embed.add_field(name="Vouch Level", value=str(level), inline=True)
                    embed.set_footer(text="Paradox Bot 💜 | Helper Reputation")
                    await vouch_channel.send(embed=embed)
            except: pass

        await interaction.response.send_message(f"✅ You vouched for **{member_name}**! They now have **{count}** vouches.", ephemeral=True)
        
        # Update view to disable vouching
        new_view = TicketControlView(vouch_enabled=self.vouch_enabled, claimer_id=self.claimer_id, vouched=True)
        try:
            await interaction.message.edit(view=new_view)
        except: pass

    async def close_ticket(self, interaction: discord.Interaction):
        await interaction.response.send_message("🚨 **Closing ticket thread in 5 seconds...**")
        import asyncio
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except Exception as e:
            print(f"Failed to delete ticket: {e}")

class BoostRoleView(discord.ui.View):
    """Dropdown for boosters to pick a role."""
    def __init__(self, roles: list):
        super().__init__(timeout=180)
        self.add_item(BoostRoleSelect(roles))

class BoostRoleSelect(discord.ui.Select):
    def __init__(self, roles: list):
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles[:25]]
        super().__init__(placeholder="💎 Pick your booster role...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)
        if role:
            try:
                # Remove other selectable boost roles first (optional, but cleaner)
                cfg = load_config()
                selectable_names = cfg.get("SELECTABLE_BOOST_ROLES", [])
                for r_name in selectable_names:
                    r_old = discord.utils.get(interaction.user.roles, name=r_name)
                    if r_old and r_old.id != role.id:
                        await interaction.user.remove_roles(r_old)
                
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ You've been given the **{role.name}** role! Enjoy! ✨", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to give you that role!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Role not found.", ephemeral=True)

class SupportTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Support Ticket", style=discord.ButtonStyle.primary, custom_id="create_support_ticket", emoji="🎟️")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        
        channel_name = f"support-{member.name}"
        
        # Check if channel already exists
        existing_channel = discord.utils.get(guild.channels, name=channel_name.lower())
        if existing_channel:
            await interaction.response.send_message(f"⚠️ You already have an open support ticket: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        # Create channel in the SAME CATEGORY as the button
        category = interaction.channel.category
        
        channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=category,
            reason=f"Support ticket for {member.name}"
        )
        
        embed = discord.Embed(
            title="🎟️ Support Ticket Created",
            description=f"Hello {member.mention}! Staff will be with you shortly.\nThis is your private support channel.",
            color=0x3498DB
        )
        await channel.send(content=f"{member.mention} | Staff", embed=embed, view=TicketControlView(vouch_enabled=False))
        await interaction.response.send_message(f"✅ Created! Check {channel.mention}", ephemeral=True)

class HelpView(discord.ui.View):
    """View for the interactive help command."""
    def __init__(self, is_admin: bool):
        super().__init__(timeout=180)
        self.add_item(HelpSelect(is_admin))

class HelpSelect(discord.ui.Select):
    def __init__(self, is_admin: bool):
        options = [
            discord.SelectOption(label="General & Stats", description="Polls, Info & Server data", emoji="📊", value="general"),
            discord.SelectOption(label="Economy & Casino", description="Gambling, Bank & Paradoxals", emoji="🪙", value="economy"),
            discord.SelectOption(label="Leveling & Ranks", description="XP, Levels & Kingdom Roles", emoji="🏆", value="leveling"),
            discord.SelectOption(label="Social & Marriage", description="Marry, Hug, Kiss & Interactions", emoji="💖", value="social"),
        ]

        if is_admin:
            options.insert(0, discord.SelectOption(label="Setup & Config", description="Greetings, Roles & Channels", emoji="⚙️", value="setup"))
            options.insert(1, discord.SelectOption(label="Tickets & Apps", description="Support, Macros & Application setup", emoji="🎟️", value="tickets"))
            options.insert(2, discord.SelectOption(label="Moderation & Security", description="Moderation tools & Filter system", emoji="🛡️", value="security"))
            options.append(discord.SelectOption(label="Server Boost", description="Rewards, Logs & Special Roles", emoji="💎", value="boost"))
        super().__init__(placeholder="Select a category to view commands...", options=options)

class SetupGuideView(discord.ui.View):
    """View containing the button to reveal the Setup Guide."""
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="📖 Setup Guide", style=discord.ButtonStyle.secondary, custom_id="show_setup_guide")
    async def show_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        prefix = load_config().get("PREFIX", "!")
        
        embed = discord.Embed(
            title="📖 Paradox Bot | Setup Guide",
            description=(
                "Welcome to the official setup guide! Follow these steps to get your server running perfectly:\n\n"
                "### 1️⃣ Core Greetings\n"
                f"• Set where I talk: `{prefix}setwelcomechannel #channel`\n"
                f"• Customize the join message: `{prefix}setwelcome Welcome {mention}!`\n"
                f"• Test the result: `{prefix}testjoin`\n\n"
                "### 2️⃣ Automation\n"
                f"• Assign a role on join: `{prefix}autorole Member`\n"
                f"• Enable the swear filter: `{prefix}togglefilter`\n"
                f"• Choose a log channel: `{prefix}setlogchannel #logs`\n\n"
                "### 3️⃣ Support & Service\n"
                f"• Create a ticket panel: `{prefix}setupticket support`\n"
                f"• Setup carry requests: `{prefix}setupticket carry`\n"
                f"• Add game options: `{prefix}addgame ALS ⚔️ Anime Last Stand`\n\n"
                "### 4️⃣ Economy Management\n"
                f"• Set daily reward: (Managed via DB)\n"
                f"• Reset economy: `{prefix}reseteco all` (Owner only)\n\n"
                "**Need more help?** Join our support server or visit our documentation! 💜"
            ),
            color=0x9B59B6,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Paradox Bot | The ultimate server assistant")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def callback(self, interaction: discord.Interaction):
        cat = self.values[0]
        prefix = load_config().get("PREFIX", "!")
        
        embed = discord.Embed(color=0x9B59B6, timestamp=discord.utils.utcnow())
        
        if cat == "setup":
            embed.title = "⚙️ Server Setup Guide"
            embed.description = (
                "I'm here to help you build the perfect server! Here are the core settings we can adjust together:\n\n"
                f"`{prefix}autorole <role>` - Automatically give a role to new members\n"
                f"`{prefix}setwelcomechannel <#ch>` - Choose where I should say hello\n"
                f"`{prefix}setwelcome <msg>` - Tell me exactly what to say when someone joins\n"
                f"`{prefix}setgoodbye <msg/channel>` - Configure how we say goodbye\n"
                f"`{prefix}setimg <welcome/goodbye> <url>` - Add a beautiful banner to greetings\n"
                f"`{prefix}setcolor <hex>` - Match my greeting colors to your server theme\n"
                f"`{prefix}setgif <action> <url>` - Set GIFs for social/mod actions\n"
                f"`{prefix}togglewelcome` - Turn the greeting system on or off\n"
                f"`{prefix}testjoin` / `{prefix}testleave` - Let's see how the greetings look!\n\n"
                f"**Tip:** All changes are saved instantly to our cloud database. 🔄"
            )
        elif cat == "tickets":
            embed.title = "🎟️ Tickets & Applications"
            embed.description = (
                "Need an efficient way to handle support or applications? I can manage everything for you:\n\n"
                f"`{prefix}setupticket <support/macro/carry/helper>` - Create interactive ticket buttons\n"
                f"`{prefix}addgame <ID> <Emoji> <Name>` - Add games to the service list\n"
                f"`{prefix}togglegame <ID>` - Quickly enable or disable a specific game\n"
                f"`{prefix}add / {prefix}remove <@user>` - Manage who can see a ticket\n"
                f"`{prefix}setticketcategory <id>` - Organize where I create new tickets\n"
                f"`{prefix}setvouchchannel <#ch>` - Choose where satisfied users leave feedback\n\n"
                f"**Helper Apps:**\n"
                f"`{prefix}sethelpertext <id> <questions>` - Set up your application forms"
            )
        elif cat == "boost":
            embed.title = "💎 Server Boosting System"
            embed.description = (
                f"`{prefix}testboost` - Simulate a boost event\n"
                f"`{prefix}addboostselectrole <role>` - Add role to selector\n"
                f"`{prefix}removeboostselectrole <role>` - Remove from selector\n"
                f"`{prefix}setboostchannel <#ch>` - Set where boost messages go\n"
                f"`{prefix}setboostrole <role>` - Set auto-assigned boost role\n"
                f"`{prefix}setboostmessage <msg>` - Set custom boost message"
            )
        elif cat == "security":
            embed.title = "🛡️ Moderation & Security"
            embed.description = (
                "Let's keep your server safe and organized! Here are the tools I use to protect the community:\n\n"
                f"**Logging:**\n"
                f"`{prefix}setlogchannel <#ch>` - Tell me where to log server events\n\n"
                f"**Moderation Tools:**\n"
                f"`{prefix}purge <num>` - Clean up chat messages\n"
                f"`{prefix}mute <@user> <clause>` - Apply 1.x tiered punishments\n"
                f"`{prefix}kick <@user> <clause>` - Remove a member with a rule clause\n"
                f"`{prefix}ban <@user> <clause>` - Ban a member permanently\n"
                f"`{prefix}quarantine <@user>` - Send someone to isolation\n"
                f"`{prefix}unquarantine <@user>` - Release a user back to the server\n\n"
                f"**Automated Protection:**\n"
                f"`{prefix}togglefilter` - Turn the swear detection system on/off\n"
                f"`{prefix}addscam <link>` / `{prefix}addswear <word>` - Update my blacklist\n"
                f"`{prefix}whitelist <add/remove>` - Allow trusted users to bypass filters\n\n"
                f"**Flavor Moderation:**\n"
                f"`{prefix}blast <@user>` - 10m mute with a boom\n"
                f"`{prefix}rct` / `rcs` / `unmute` <@user> - Recovery/Unmute\n"
                f"`{prefix}annihilate <@user>` - Permanent ban with style"
            )
            await interaction.response.edit_message(embed=embed, view=SetupGuideView())
            return
        elif cat == "general":
            embed.title = "📊 General & Statistics"
            embed.description = (
                "I can provide information and help you engage with your community:\n\n"
                f"`{prefix}poll \"Question\" <time>` - Start a community vote\n"
                f"`{prefix}botinfo` - Check my current health and stats\n"
                f"`{prefix}serverinfo` - Get a detailed report on this server\n"
                f"`{prefix}swearlog [@user]` - View moderation history\n"
                f"`{prefix}vouches [@user]` - See reputation and vouch levels\n"
                f"`{prefix}saycolor <color> <text>` - Make your messages stand out!"
            )
        elif cat == "economy":
            embed.title = "🪙 Paradoxy Economy"
            embed.description = (
                f"`{prefix}balance [@user]` - Wallet, bank & active effects\n"
                f"`{prefix}daily` - Claim daily reward (streak bonus!)\n"
                f"`{prefix}work` - Safe earnings every 5 minutes\n"
                f"`{prefix}give <@user> <amount>` - Transfer paradoxy\n"
                f"`{prefix}leaderboard` - Top 10 richest members\n"
                f"`{prefix}quests` - View & track daily quests\n"
                f"`{prefix}quest claim` - Claim completed quest rewards\n"
                f"`{prefix}useitem <item>` - Activate/deactivate items (max 2 active)\n\n"
                f"**Casino:**\n"
                f"`{prefix}cf <bet> [heads/tails]` - Coinflip 50/50\n"
                f"`{prefix}slots <bet> [easy/medium/hard]` - Slot machine with different reel counts\n"
                f"`{prefix}bj <bet>` - Blackjack (Hit/Stand/Double)\n"
                f"`{prefix}roulette <bet> <red/black/green/num>` - Roulette\n"
                f"`{prefix}poker <min> <max>` - Start a poker game with buy-in limits\n"
                f"`{prefix}pokerjoin <amount>` - Join an active poker game\n"
                f"`{prefix}pokerstart` - Start poker when ready\n\n"
                f"**Heists:**\n"
                f"`{prefix}heist` - Choose target and difficulty for a heist\n"
                f"`{prefix}bail` - Pay to get out of jail early (500 paradoxy/minute)\n\n"
                f"**Shop & Inventory:**\n"
                f"`{prefix}shop` - Browse items\n"
                f"`{prefix}buy <item>` - Purchase an item\n"
                f"`{prefix}inventory [@user]` - View your items\n\n"
                f"**Bank & Loans:**\n"
                f"`{prefix}bank info` - Show your bank amount and interest earnings\n"
                f"`{prefix}bank deposit <amount>` - Deposit to bank\n"
                f"`{prefix}bank withdraw <amount>` - Withdraw from bank\n"
                f"`{prefix}loan <amount>` - Take a loan (max 300k, pay back in 24h)\n"
                f"`{prefix}payloan` - Pay off your loan\n"
                f"`{prefix}reseteco <@user/all>` - Reset economy for user or all (Owner)"
            )
        elif cat == "leveling":
            embed.title = "🏆 Leveling & Kingdom Ranks"
            embed.description = (
                f"`{prefix}level [@user]` - Check level and XP progress\n"
                f"`{prefix}rank` - Global XP leaderboard\n"
                f"`{prefix}setlevel <@user> <level>` - Set user level (Admin)\n"
                f"`{prefix}setxp <@user> <xp>` - Set user XP (Admin)\n"
                f"`{prefix}setlevelchannel <#ch>` - Set level-up channel (Admin)\n"
                f"`{prefix}setrank <level> <name>` - Set rank name for a level (Admin)\n"
                f"`{prefix}stoplevels` - Stop the leveling system (Admin)\n"
                f"`{prefix}returnlevels` - Restart the leveling system (Admin)\n\n"
                f"**📈 XP Generation:**\n"
                f"💬 Messages: **20-30 XP** (1m cooldown)\n"
                f"🎙️ Voice: **6 XP / minute**\n\n"
                f"**🏰 Kingdom Ranks:**\n"
                f"Unique tiered roles are auto-created and assigned every 5 levels (Level 5, 10, 15... up to 100)!"
            )

        elif cat == "social":
            embed.title = "💖 Social & Player Interactions"
            embed.description = (
                f"`{prefix}marry <@user>` - Propose to a member\n"
                f"`{prefix}divorce` - End your current marriage\n"
                f"`{prefix}marriage [@user]` - View marriage status\n\n"
                "**Cute & Positive:**\n"
                f"`{prefix}hug` / `{prefix}kiss` / `{prefix}cuddle` / `{prefix}pat` / `{prefix}nuzzle` / `{prefix}comfort`\n"
                f"`{prefix}feed` / `{prefix}carry` / `{prefix}sleep` / `{prefix}highfive` / `{prefix}holdhands` / `{prefix}wave`\n\n"
                "**Playful & Fun:**\n"
                f"`{prefix}punch` / `{prefix}slap` / `{prefix}bite` / `{prefix}poke` / `{prefix}tickle` / `{prefix}kick`\n"
                f"`{prefix}stab` / `{prefix}shoot` / `{prefix}tackle` / `{prefix}yeet` / `{prefix}bonk` / `{prefix}bully`\n"
                f"`{prefix}dance` / `{prefix}lick`\n\n"
                "**Special:**\n"
                f"`{prefix}kill <@user>` - 💀\n"
                f"`{prefix}revive <@user>` - ⚡"
            )

        embed.set_footer(text=f"Paradox Bot 💜 | {cat.capitalize()} Menu")
        await interaction.response.edit_message(embed=embed)

class HelperTicketView(discord.ui.View):
    """View for the Helper/Carry application dropdown."""
    def __init__(self, mode: str = "helper"):
        super().__init__(timeout=None)
        self.add_item(HelperTicketSelect(mode=mode))

class HelperTicketSelect(discord.ui.Select):
    def __init__(self, mode: str = "helper"):
        self.mode = mode
        cfg = load_config()
        # Fallback game list if not in config
        games = cfg.get("HELPER_GAMES", {
            "ALS": {"name": "Anime Last Stand (ALS)", "emoji": "⚔️"},
            "AG": {"name": "Anime Guardians (AG)", "emoji": "💠"},
            "AC": {"name": "Anime Crusaders (AC)", "emoji": "🗡️"},
            "UTD": {"name": "Universal Tower Defense (UTD)", "emoji": "🌍"},
            "AV": {"name": "Anime Vanguards (AV)", "emoji": "🛡️"},
            "BL": {"name": "Bizarre Lineage (BL)", "emoji": "💫"},
            "SP": {"name": "Sailor Piece (SP)", "emoji": "⛵"},
            "ARX": {"name": "Anime Rangers X (ARX)", "emoji": "🔥"},
            "ASTD": {"name": "All Star Tower Defense (ASTD)", "emoji": "⭐"},
            "AOL": {"name": "Anime Overlord (AOL)", "emoji": "👑"}
        })
        
        options = [
            discord.SelectOption(label=data["name"], value=code, emoji=data.get("emoji", "🎮"))
            for code, data in games.items() if data.get("active", True)
        ]
        super().__init__(placeholder="Select a game to start your ticket!", min_values=1, max_values=1, options=options, custom_id="helper_ticket_select")

    async def callback(self, interaction: discord.Interaction):
        game_code = self.values[0]
        guild = interaction.guild
        member = interaction.user
        cfg = load_config()
        
        game_data = cfg.get("HELPER_GAMES", {}).get(game_code, {})
        game_name = game_data.get("name", game_code)
        
        # Determine prefix and type
        if self.mode == "carry":
            prefix = "carry"
            ticket_type = "Carry Request"
            color = 0x3498DB
            instr_title = "🎮 Carry Request Instructions"
            instr_text = (
                f"Hello {member.mention}! You've requested a carry for **{game_name}**.\n\n"
                "### 📋 Instructions\n"
                "1. State exactly what you need help with (e.g., 'Raid Floor 5').\n"
                "2. Wait for an available booster to respond.\n"
                "3. Once the carry is done, please **Vouch** the booster using the button below!\n\n"
                "**Please provide your details:**"
            )
            form_text = f"1. Roblox Username?\n2. What do you need help with in {game_name}?\n3. What is your Timezone?"
        else:
            prefix = "apply"
            ticket_type = "Helper Application"
            color = 0xF1C40F
            instr_title = "📝 Helper Application"
            instr_text = (
                f"Hello {member.mention}! Thank you for applying for the **Helper/Booster** role.\n\n"
                "### 📋 Instructions\n"
                "1. Answer all questions clearly and honestly.\n"
                "2. If a question asks for a screenshot (SS), please attach it.\n"
                "3. Staff will review your application soon. **Do not ping staff.**\n\n"
                "**Application Form:**"
            )
            
            questions_raw = game_data.get("questions", ["Timezone?", "Roblox Level?", "Image of units?"])
            if isinstance(questions_raw, list):
                form_text = "\n".join([f"{i+1}. {q}" for i, q in enumerate(questions_raw)])
            else:
                form_text = questions_raw

        channel_name = f"{prefix}-{game_code.lower()}-{member.name}"
        
        # Check for existing
        existing = discord.utils.get(guild.channels, name=channel_name.lower())
        if existing:
            await interaction.response.send_message(f"⚠️ You already have an open ticket for this: {existing.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=interaction.channel.category,
            reason=f"{ticket_type} for {game_name}"
        )
        
        embed = discord.Embed(
            title=f"🎮 {game_name} | {ticket_type}",
            description=f"{instr_text}\n```\n{form_text}\n```",
            color=color
        )
        embed.set_footer(text=f"Paradox Bot 💜 | {ticket_type}")
        
        await channel.send(content=f"{member.mention} | Staff", embed=embed, view=TicketControlView(vouch_enabled=(self.mode == "carry")))
        await interaction.response.send_message(f"✅ Ticket created! Check {channel.mention}", ephemeral=True)

class MacroTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Macro Ticket", style=discord.ButtonStyle.success, custom_id="create_macro_ticket", emoji="⌨️")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        
        channel_name = f"macro-{member.name}"
        
        # Check if channel already exists
        existing_channel = discord.utils.get(guild.channels, name=channel_name.lower())
        if existing_channel:
            await interaction.response.send_message(f"⚠️ You already have an open macro ticket: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        category = interaction.channel.category
        
        channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=category,
            reason=f"Macro ticket for {member.name}"
        )
        
        embed = discord.Embed(
            title="⌨️ Macro Ticket Created",
            description=f"Hello {member.mention}! This is your direct channel for Macro support.\nStaff will assist you shortly.",
            color=0x2ECC71
        )
        await channel.send(content=f"{member.mention} | Staff", embed=embed, view=TicketControlView(vouch_enabled=False))
        await interaction.response.send_message(f"✅ Created! Check {channel.mention}", ephemeral=True)

# ══════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════

@bot.event
async def on_ready():
    """Fires when the bot is connected and ready."""
    
    # Initialize Database if URI is present
    mongo_uri = os.getenv("MONGO_URI")
    if mongo_uri:
        db.setup(mongo_uri)
        
        # ── Sync Config from Database ──
        db_cfg = await db.get_config()
        if db_cfg:
            local_cfg = load_config()
            local_cfg.update(db_cfg)
            save_config(local_cfg)
            
            # Update global LEVEL_ROLES if present in DB
            if "LEVEL_ROLES" in db_cfg:
                global LEVEL_ROLES
                LEVEL_ROLES.update({int(k): v for k, v in db_cfg["LEVEL_ROLES"].items()})
                
            print("  📁  Configuration synced from Database to local storage.")
    
    # Register persistent views
    bot.add_view(SupportTicketView())
    bot.add_view(MacroTicketView())
    bot.add_view(HelperTicketView())
    bot.add_view(TicketControlView())

    # Start economy background tasks
    if not apply_interest_task.is_running():
        apply_interest_task.start()
    if not check_loans_task.is_running():
        check_loans_task.start()
    if not voice_xp_task.is_running():
        voice_xp_task.start()

    print("═" * 50)
    print(f"  ✅  Paradox Bot is ONLINE!")
    print(f"  🤖  Logged in as: {bot.user} (ID: {bot.user.id})")
    print(f"  🌐  Servers: {len(bot.guilds)}")
    print(f"  ⏰  {format_timestamp()}")
    print("═" * 50)

    # Set a custom status
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=f"{PREFIX}help | Paradox Bot 💜"
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)

# ── AUTO-ROLE + WELCOME MESSAGE ──────────────

@bot.event
async def on_member_join(member: discord.Member):
    """Auto-assign role and send a welcome message when someone joins."""
    cfg = load_config()  # Reload in case it changed

    # ── Auto-role ──
    role_name = cfg.get("AUTO_ROLE_NAME", "Member")
    role = parse_role_name(member.guild, role_name)
    if role:
        try:
            await member.add_roles(role)
            print(f"  [AUTO-ROLE] Gave '{role.name}' to {member.name}")
        except discord.Forbidden:
            print(f"  [ERROR] Missing permissions to assign role '{role_name}'")
        except Exception as e:
            print(f"  [ERROR] Auto-role failed: {e}")
    else:
        print(f"  [WARN] Role '{role_name}' not found in {member.guild.name}. Create it first!")

    # ── Welcome message in channel ──
    if not cfg.get("WELCOME_ENABLED", True):
        return

    channel_id = cfg.get("WELCOME_CHANNEL_ID")
    if channel_id:
        channel = bot.get_channel(int(channel_id))
        if channel:
            welcome_tpl = cfg.get("JOIN_MESSAGE", "Welcome {mention}!")
            color_hex = cfg.get("WELCOME_COLOR", "#9B59B6")
            try:
                color_val = int(color_hex.lstrip('#'), 16)
            except:
                color_val = 0x9B59B6

            embed = discord.Embed(
                title="👋 Welcome!",
                description=format_welcome_message(member, welcome_tpl),
                color=color_val,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            
            img_url = cfg.get("WELCOME_IMAGE_URL")
            if img_url:
                embed.set_image(url=img_url)
                
            embed.set_footer(text="Paradox Bot 💜")
            await channel.send(content=f"🎉 Welcome {member.mention}! 🎉", embed=embed)

    # ── Optional DM ──
    if cfg.get("WELCOME_DM", False):
        try:
            dm_embed = discord.Embed(
                title=f"Welcome to {member.guild.name}! 🎉",
                description=(
                    f"Hey **{member.name}**, thanks for joining **{member.guild.name}**!\n\n"
                    f"Make sure to check out the rules and enjoy your stay! 💜"
                ),
                color=0x9B59B6,
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            print(f"  [WARN] Can't DM {member.name} (DMs disabled)")

# ── GOODBYE MESSAGE ──────────────────────────

@bot.event
async def on_member_remove(member: discord.Member):
    """Send a goodbye message when someone leaves."""
    cfg = load_config()
    if not cfg.get("WELCOME_ENABLED", True):
        return

    channel_id = cfg.get("GOODBYE_CHANNEL_ID") or cfg.get("WELCOME_CHANNEL_ID")
    if channel_id:
        channel = bot.get_channel(int(channel_id))
        if channel:
            leave_tpl = cfg.get("LEAVE_MESSAGE", "{member} has left.")
            color_hex = cfg.get("WELCOME_COLOR", "#E74C3C")
            try:
                color_val = int(color_hex.lstrip('#'), 16)
            except:
                color_val = 0xE74C3C

            embed = discord.Embed(
                title="😢 Goodbye!",
                description=format_goodbye_message(member, leave_tpl),
                color=color_val,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            
            img_url = cfg.get("GOODBYE_IMAGE_URL")
            if img_url:
                embed.set_image(url=img_url)

            embed.set_footer(text="Paradox Bot 💜")
            await channel.send(embed=embed)

# ── SWEAR WORD FILTER ────────────────────────

@bot.event
async def on_message(message: discord.Message):
    """Filter swear words from messages."""
    # Don't respond to ourselves
    if message.author == bot.user:
        return
    # Ignore DMs
    if not message.guild:
        await bot.process_commands(message)
        return

    cfg = load_config()

    # ── Leveling XP ──
    # 20-30 XP per message, handled by add_xp_logic (which has a 1-min cooldown)
    if not message.content.startswith(PREFIX):
        await add_xp_logic(message.author, amount=random.randint(20, 30), source="message", channel=message.channel)

    # ── Boost Detection (System Message) ──
    if message.type in (
        discord.MessageType.premium_guild_subscription,
        discord.MessageType.premium_guild_tier_1,
        discord.MessageType.premium_guild_tier_2,
        discord.MessageType.premium_guild_tier_3,
    ):
        boost_channel_id = cfg.get("BOOST_CHANNEL_ID")
        channel = bot.get_channel(int(boost_channel_id)) if boost_channel_id else message.channel
        
        if channel:
            boost_tpl = cfg.get("BOOST_MESSAGE", "Thank you for boosting the server, {mention}! 💖")
            
            embed = discord.Embed(
                title="✨ Server Boosted! ✨",
                description=boost_tpl.replace("{mention}", message.author.mention).replace("{member}", message.author.name).replace("{server}", message.guild.name),
                color=0xF47FFF,
                timestamp=discord.utils.utcnow()
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            embed.set_footer(text="Paradox Bot 💜")
            
            # Interactive Role Selector for Boosters
            selectable_names = cfg.get("SELECTABLE_BOOST_ROLES", [])
            if selectable_names:
                roles = []
                for name in selectable_names:
                    r = parse_role_name(message.guild, name)
                    if r: roles.append(r)
                
                if roles:
                    selector_view = BoostRoleView(roles)
                    await channel.send(content=f"🎁 {message.author.mention}, you can pick one special booster role below!", view=selector_view)
            else:
                await channel.send(embed=embed)
            
        # Add boost role
        role_id_or_name = cfg.get("BOOST_ROLE_NAME", "Server Booster")
        role = parse_role_name(message.guild, role_id_or_name)
        
        if not role:
            try:
                # Create the role if it doesn't exist
                role = await message.guild.create_role(
                    name=role_id_or_name,
                    color=0xF47FFF, # Premium Pink/Purple
                    hoist=True,
                    reason="Auto-created boost role for boosters"
                )
                print(f"  [BOOST] Created missing role: {role_id_or_name}")
            except discord.Forbidden:
                print(f"  [ERROR] Lacking permissions to create Boost role '{role_id_or_name}'.")

        if role:
            try:
                await message.author.add_roles(role)
                print(f"  [BOOST] Assigned role '{role.name}' to {message.author.name}")
            except discord.Forbidden:
                print(f"  [ERROR] Lacking permissions to give Boost role '{role.name}'.")
        return

    # ── LOG EVERY MESSAGE ────────────────────────
    whitelisted_users = cfg.get("WHITELISTED_USERS", [])
    log_whitelisted = cfg.get("LOG_WHITELISTED_USERS", [])
    log_channel_id = cfg.get("LOG_CHANNEL_ID")
    
    # Check if user is in log whitelist
    if log_channel_id and not message.author.bot and message.channel.id != int(log_channel_id) and message.author.id not in log_whitelisted:
        try:
            log_channel = bot.get_channel(int(log_channel_id)) or await bot.fetch_channel(int(log_channel_id))
            if log_channel:
                log_embed = discord.Embed(
                    title="💬 Message Sent",
                    color=0x2ECC71, # Green
                    timestamp=discord.utils.utcnow()
                )
                log_embed.add_field(name="Author", value=message.author.mention, inline=True)
                log_embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                log_embed.add_field(name="Content", value=message.content or "*No text content*", inline=False)
                log_embed.set_footer(text=f"User ID: {message.author.id}")
                await log_channel.send(embed=log_embed)
        except Exception as e:
            print(f"  [ERROR] Failed to log message: {e}")

    # Skip moderation for whitelisted users, commands or bot messages
    if (message.author.id in whitelisted_users or message.content.startswith(PREFIX)):
        await bot.process_commands(message)
        return

    # ── SECURITY & MODERATION SYSTEM ──
    swear_filter_on = cfg.get("SWEAR_FILTER_ENABLED", True)
    swear_list = cfg.get("SWEAR_WORDS", [])
    user_id = str(message.author.id)

    # 1. PHISHING & SCAM DETECTION
    content_lower = message.content.lower()
    is_scam = any(link in content_lower for link in SCAM_LINKS)
    
    if is_scam or (message.mention_everyone and not message.author.guild_permissions.mention_everyone):
        try:
            await message.delete()
            scam_count = await db.add_scam_strike(user_id)

            scam_cfg = cfg.get("SCAM_THRESHOLDS", {"warn": 1, "mute1": 2, "mute2": 3, "quarantine": 4, "ban": 5})
            
            if scam_count == scam_cfg.get("warn"):
                await message.channel.send(f"⚠️ {message.author.mention}, phishing links are prohibited! (Warning 1)", delete_after=15)
            elif scam_count == scam_cfg.get("mute1"):
                await message.author.timeout(timedelta(hours=1), reason="Scam Strike 2")
                await message.channel.send(f"🔇 {message.author.mention} timed out for 1h (Scam Strike 2)", delete_after=15)
            elif scam_count == scam_cfg.get("mute2"):
                await message.author.timeout(timedelta(days=1), reason="Scam Strike 3")
                await message.channel.send(f"🔇 {message.author.mention} timed out for 1 day (Scam Strike 3)", delete_after=15)
            elif scam_count == scam_cfg.get("quarantine"):
                await apply_quarantine(message.author, "Scam Strikes")
                await message.channel.send(f"⚖️ {message.author.mention} sent to Quarantine (Scam Strike 4)", delete_after=15)
            elif scam_count >= scam_cfg.get("ban"):
                await message.author.ban(reason="Scam Strikes Limit")
                await message.channel.send(f"🚫 {message.author.name} has been banned for phishing links.")
            return 
        except Exception as e:
            print(f"  [ERROR] Scam detection failed: {e}")

    # 2. SWEAR FILTER
    if swear_filter_on and swear_list and contains_swear(message.content, swear_list):
        # Safeguard: If DB is not connected, just delete the message and stop
        if db.db is None:
            try:
                await message.delete()
                await message.channel.send(f"⚠️ {message.author.mention}, watch your language! (Database Offline - Message Removed)", delete_after=5)
            except: pass
            return

        try:
            user_infs = await db.get_infractions(user_id)
        
            # Cooldown/Reset Check
            if user_infs:
                last_inf = user_infs[-1]
                last_time = datetime.strptime(last_inf["time"], "%Y-%m-%d %H:%M:%S")
                time_diff = datetime.now() - last_time
                count_before = len(user_infs)
                
                reset_needed = False
                if count_before <= 3 and time_diff > timedelta(minutes=30): reset_needed = True
                elif count_before == 4 and time_diff > timedelta(hours=1): reset_needed = True
                elif count_before >= 5 and time_diff > timedelta(days=1): reset_needed = True
                
                if reset_needed:
                    await db.clear_infractions(user_id)
                    user_infs = []

            # Log new infraction
            word_found = find_swear_word(message.content, swear_list)
            count = await db.add_infraction(user_id, word_found, message.channel.name)
            swear_cfg = cfg.get("SWEAR_THRESHOLDS", {"silent": 1, "warn1": 2, "warn2": 3, "mute": 4, "quarantine": 8})
            
            # 1. Action: Silent delete
            if count <= swear_cfg.get("silent", 1):
                try: await message.delete()
                except: pass
                print(f"  [FILTER] Silent strike {count} for {message.author.name}")
                return

            # Subsequent strikes
            try: await message.delete()
            except: pass
            
            punishment_msg = ""
            try:
                if count == swear_cfg.get("warn1"):
                    punishment_msg = "⚠️ This is your **1st warning**. Please keep it respectful!"
                elif count == swear_cfg.get("warn2"):
                    punishment_msg = "⚠️ This is your **2nd warning**. The next one will result in a timeout!"
                elif count == swear_cfg.get("mute"):
                    duration = cfg.get("SWEAR_PUNISHMENT_DURATION", 1)
                    await message.author.timeout(timedelta(minutes=duration), reason="Swear Strike")
                    punishment_msg = f"🔇 You have been timed out for **{duration} minute(s)**."
                elif count >= swear_cfg.get("quarantine"):
                    await apply_quarantine(message.author, "Swear Strikes")
                    punishment_msg = "⚖️ You have been sent to **Quarantine** due to excessive warnings."
            except: pass

            warn_msg = f"⚠️ {message.author.mention}, watch your language!"
            if punishment_msg:
                warn_msg += f"\n{punishment_msg}"
            
            await message.channel.send(warn_msg, delete_after=10)

            log_channel_id = cfg.get("LOG_CHANNEL_ID")
            if log_channel_id:
                log_channel = bot.get_channel(int(log_channel_id)) or await bot.fetch_channel(int(log_channel_id))
                if log_channel:
                    censored = censor_message(message.content, swear_list)
                    log_embed = discord.Embed(title="🚨 Swear Filter Triggered", color=0xE74C3C, timestamp=discord.utils.utcnow())
                    log_embed.add_field(name="User", value=message.author.mention, inline=True)
                    log_embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                    log_embed.add_field(name="Message (censored)", value=censored, inline=False)
                    log_embed.set_footer(text="Paradox Bot 💜")
                    await log_channel.send(embed=log_embed)

        except Exception as e:
            print(f"  [ERROR] Swear filter execution failed: {e}")

        print(f"  [FILTER] Deleted message from {message.author.name}")
        return  # Stop here if the message was filtered

    # Process commands if no swear words were found
    await bot.process_commands(message)

# ── LOGGING EVENTS ───────────────────────────

@bot.event
async def on_message_delete(message: discord.Message):
    """Log when a message is deleted."""
    if message.author.bot:
        return
    
    cfg = load_config()
    log_whitelisted = cfg.get("LOG_WHITELISTED_USERS", [])
    if message.author.id in log_whitelisted:
        return

    log_channel_id = cfg.get("LOG_CHANNEL_ID")
    if not log_channel_id:
        return
        
    try:
        log_channel = bot.get_channel(int(log_channel_id)) or await bot.fetch_channel(int(log_channel_id))
    except:
        return

    if not log_channel:
        return

    embed = discord.Embed(
        title="🗑️ Message Deleted",
        color=0xE74C3C,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Author", value=message.author.mention, inline=True)
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    embed.add_field(name="Content", value=message.content or "*No text content (likely an embed or image)*", inline=False)
    embed.set_footer(text=f"User ID: {message.author.id}")
    
    await log_channel.send(embed=embed)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Log when a message is edited."""
    if before.author.bot or before.content == after.content:
        return
        
    cfg = load_config()
    log_whitelisted = cfg.get("LOG_WHITELISTED_USERS", [])
    if before.author.id in log_whitelisted:
        return

    log_channel_id = cfg.get("LOG_CHANNEL_ID")
    if not log_channel_id:
        return
        
    try:
        log_channel = bot.get_channel(int(log_channel_id)) or await bot.fetch_channel(int(log_channel_id))
    except:
        return

    if not log_channel:
        return

    embed = discord.Embed(
        title="📝 Message Edited",
        color=0x3498DB,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Author", value=before.author.mention, inline=True)
    embed.add_field(name="Channel", value=before.channel.mention, inline=True)
    embed.add_field(name="Before", value=before.content or "*No text content*", inline=False)
    embed.add_field(name="After", value=after.content or "*No text content*", inline=False)
    embed.set_footer(text=f"User ID: {before.author.id}")
    
    await log_channel.send(embed=embed)

# ══════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════

# ── !help ────────────────────────────────────
bot.remove_command('help')

@bot.command(name="help")
async def help_cmd(ctx: commands.Context, *sub: str):
    """Custom interactive help command."""
    sub_text = " ".join(sub).strip().lower()
    if sub_text in {"paradox hidden", "paradoxy hidden"}:
        hidden_commands = (
            "**Hidden Commands (Owner/Admin/Allowed Users Only):**\n\n"
            f"`{PREFIX}reseteco all` - Reset economy for all users (Owner)\n"
            f"`{PREFIX}reseteco @user` - Reset a user's economy (Owner)\n"
            f"`{PREFIX}help paradox hidden` - Show this message (Hidden)\n"
            f"`{PREFIX}help paradoxy hidden` - Show this message (Hidden)"
        )
        try:
            await ctx.author.send(hidden_commands)
            await ctx.message.delete()
        except discord.Forbidden:
            await ctx.send("❌ I can't DM you. Please enable DMs from server members.")
        return
    if sub_text not in {"paradox", ""}:
        await ctx.send(f"❓ Type `{PREFIX}help paradox` to open my interactive menu!")
        return

    is_admin = ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild
    
    embed = discord.Embed(
        title="🤖 Paradox Help Assistant",
        description=(
            f"Hello **{ctx.author.name}**! I'm here to help you get the most out of Paradox.\n\n"
            "Please select a category from the menu below to explore my commands. "
            "If you're an admin, you'll see extra management sections!"
        ),
        color=0x9B59B6,
        timestamp=discord.utils.utcnow()
    )
    
    if is_admin:
        embed.add_field(name="⚙️ Management", value="Setup, Tickets & Moderation", inline=False)
    
    embed.add_field(name="🎮 Fun & Social", value="Economy, Games & Social Actions", inline=False)
    
    embed.set_footer(text="Guided by Paradox 💜")
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    
    await ctx.send(embed=embed, view=HelpView(is_admin=is_admin))

# ── !setupticket ─────────────────────────────

@bot.command(name="setupticket")
@commands.has_permissions(administrator=True)
async def setup_ticket_cmd(ctx: commands.Context, mode: str = "support"):
    """Setup the ticket system. Modes: support, macro, helper. Admin only."""
    mode = mode.lower()
    
    if mode == "macro":
        embed = discord.Embed(
            title="⌨️ Macro Tickets",
            description=(
                "Purchase or inquire about Macros! Click the button below.\n"
                "Requests will be sent directly to the team/owner."
            ),
            color=0x2ECC71
        )
        view = MacroTicketView()
    elif mode == "carry":
        cfg = load_config()
        games = cfg.get("HELPER_GAMES", {})
        active_games_text = ""
        for code, data in games.items():
            if data.get("active", True):
                active_games_text += f"{data.get('emoji', '🎮')} {data.get('name', code)}\n"
                
        embed = discord.Embed(
            title="🎮 PARADOX | Carry Requests",
            description=(
                "**Welcome to our Carry Service!**\n"
                "Your reliable place for fast and professional anime carries.\n\n"
                "🎯 **FREE SERVICE**\n"
                "We help you complete runs for free — no hidden fees, no premium memberships.\n\n"
                "👻 **BOOSTER PERKS**\n"
                "Professional boosters earn reputation through customer vouches and build trust in the community.\n\n"
                "⚡ **QUICK SUPPORT**\n"
                "Get connected with experienced boosters usually within minutes. Fast responses & quality service.\n\n"
                "📋 **HOW IT WORKS**\n"
                "Simply select your game from the menu below to start your ticket!\n\n"
                "🎮 **Supported Games:**\n"
                f"{active_games_text}\n"
                "**Select your game below to get started!**"
            ),
            color=0x3498DB
        )
        # Use the bot avatar as thumbnail for a professional look
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        view = HelperTicketView(mode="carry")
    elif mode == "helper":
        embed = discord.Embed(
            title="📝 Helper Applications",
            description=(
                "**Apply to become a Paradox Helper!**\n"
                "Help our community and earn reputation as a professional booster.\n\n"
                "⭐ **BOOSTER PERKS**\n"
                "Get access to exclusive channels, roles, and community trust.\n\n"
                "⚡ **REQUIREMENTS**\n"
                "You must have meta units and be active daily to apply.\n\n"
                "📋 **HOW IT WORKS**\n"
                "Select your main game below to start your application ticket!"
            ),
            color=0xF1C40F
        )
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        view = HelperTicketView(mode="helper")
    else:  # Default to support
        embed = discord.Embed(
            title="🎟️ Support Tickets",
            description=(
                "Need help? Click the button below to open a support ticket!\n"
                "Our staff team will assist you as soon as possible."
            ),
            color=0x3498DB
        )
        view = SupportTicketView()
        
    embed.set_footer(text=f"Paradox Bot 💜 {mode.capitalize()} Tickets")
    await ctx.send(embed=embed, view=view)
    await ctx.message.delete()

@bot.command(name="sethelpertext")
@commands.has_permissions(administrator=True)
async def set_helper_text(ctx: commands.Context, game_code: str, *, questions_input: str):
    """
    Set or update application questions. 
    Usage: !sethelpertext astd q1 New Question ; q7 Another Question ; q3 remove
    """
    game_code = game_code.upper()
    cfg = load_config()
    games = cfg.get("HELPER_GAMES", {})
    
    if game_code not in games:
        await ctx.send(f"❌ Game `{game_code}` not found. (Examples: ALS, AV, ASTD)")
        return

    # Get current questions and ensure they are in list format
    current_questions = games[game_code].get("questions", [])
    if isinstance(current_questions, str):
        # Migrate string to list
        lines = current_questions.strip().split("\n")
        current_questions = [re.sub(r'^\d+\.\s*', '', line).strip() for line in lines if line.strip()]

    # Advanced Parsing Logic
    if ";" not in questions_input and not re.match(r'^q\d+\s+', questions_input, re.IGNORECASE):
        # Bulk update if no qN syntax is used
        new_questions = [q.strip() for q in questions_input.split("\n") if q.strip()]
    else:
        parts = questions_input.split(";")
        new_questions = list(current_questions)
        to_delete = set()
        updates = []

        for part in parts:
            part = part.strip()
            if not part: continue
            
            match = re.match(r'^q(\d+)\s*(.*)', part, re.IGNORECASE)
            if match:
                num = int(match.group(1))
                text = match.group(2).strip()
                idx = num - 1
                
                if text.lower() in ["delete", "remove", "none"]:
                    to_delete.add(idx)
                else:
                    updates.append((idx, text))
            else:
                # If no qN prefix but semicolons exist, treat as an append or ignore
                if part: updates.append((len(new_questions) + len(updates), part))

        # Apply updates
        for idx, text in updates:
            while len(new_questions) <= idx:
                new_questions.append("...")
            new_questions[idx] = text
        
        # Apply deletions (reverse to keep indices valid)
        for idx in sorted(list(to_delete), reverse=True):
            if idx < len(new_questions):
                new_questions.pop(idx)

    games[game_code]["questions"] = new_questions
    cfg["HELPER_GAMES"] = games
    await save_config_sync(cfg)
    
    await ctx.send(f"✅ Questions updated for **{games[game_code]['name']}**! (Total: {len(new_questions)})")

# ── !testjoin ────────────────────────────────

@bot.command(name="testjoin")
@commands.has_permissions(administrator=True)
async def test_join_cmd(ctx: commands.Context):
    """Simulate a member join to test the welcome message. Admin only."""
    member = ctx.author
    cfg = load_config()

    channel_id = cfg.get("WELCOME_CHANNEL_ID")
    if channel_id:
        channel = bot.get_channel(int(channel_id))
        if channel:
            welcome_tpl = cfg.get("JOIN_MESSAGE", "Welcome {mention}!")
            color_hex = cfg.get("WELCOME_COLOR", "#9B59B6")
            try:
                color_val = int(color_hex.lstrip('#'), 16)
            except:
                color_val = 0x9B59B6

            embed = discord.Embed(
                title="👋 Welcome!",
                description=format_welcome_message(member, welcome_tpl),
                color=color_val,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            
            img_url = cfg.get("WELCOME_IMAGE_URL")
            if img_url:
                embed.set_image(url=img_url)

            embed.set_footer(text="Paradox Bot 💜")
            await channel.send(content=f"🎉 Welcome {member.mention}! 🎉", embed=embed)
            if channel != ctx.channel:
                await ctx.send(f"✅ Test welcome sent in {channel.mention}!")
        else:
            await ctx.send("❌ Welcome channel not found!")
    else:
        await ctx.send("❌ No welcome channel set!")

# ── !testleave ───────────────────────────────

@bot.command(name="testleave")
@commands.has_permissions(administrator=True)
async def test_leave_cmd(ctx: commands.Context):
    """Simulate a member leave to test the goodbye message. Admin only."""
    member = ctx.author
    cfg = load_config()

    channel_id = cfg.get("GOODBYE_CHANNEL_ID") or cfg.get("WELCOME_CHANNEL_ID")
    if channel_id:
        channel = bot.get_channel(int(channel_id))
        if channel:
            leave_tpl = cfg.get("LEAVE_MESSAGE", "{member} has left.")
            color_hex = cfg.get("WELCOME_COLOR", "#E74C3C")
            try:
                color_val = int(color_hex.lstrip('#'), 16)
            except:
                color_val = 0xE74C3C

            embed = discord.Embed(
                title="😢 Goodbye!",
                description=format_goodbye_message(member, leave_tpl),
                color=color_val,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            
            img_url = cfg.get("GOODBYE_IMAGE_URL")
            if img_url:
                embed.set_image(url=img_url)

            embed.set_footer(text="Paradox Bot 💜")
            await channel.send(embed=embed)
            if channel != ctx.channel:
                await ctx.send(f"✅ Test goodbye sent in {channel.mention}!")
        else:
            await ctx.send("❌ Goodbye channel not found!")
    else:
        await ctx.send("❌ No goodbye channel set!")

# ── !goodbye ─────────────────────────────────

@bot.command(name="goodbye")
async def goodbye_cmd(ctx: commands.Context):
    """Sends the custom goodbye message."""
    cfg = load_config()
    msg = cfg.get("GOODBYE_MESSAGE", "😢 Goodbye! Hope to see you again!")

    embed = discord.Embed(
        title="😢 Goodbye!",
        description=msg,
        color=0xE74C3C,  # Red
    )
    embed.set_footer(text=f"Requested by {ctx.author.name} · Paradox Bot 💜")
    await ctx.send(embed=embed)
# ── !setwelcome ──────────────────────────────

@bot.command(name="setwelcome")
@commands.has_permissions(administrator=True)
async def set_welcome_cmd(ctx: commands.Context, *, message: str):
    """Set a custom automated join message. Admin only.
    Usage: !setwelcome Welcome {mention}! You are member #{count}.
    """
    cfg = load_config()
    cfg["JOIN_MESSAGE"] = message
    await save_config_sync(cfg)
    await ctx.send(f"✅ Join message updated! Try `!testjoin` to see it.")

# ── !set ───────────────────────────────────────

@bot.group(name="set", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def set_cmd(ctx: commands.Context):
    """Configuration commands for the bot."""
    if ctx.invoked_subcommand is None:
        await ctx.send(f"❓ Usage: `{PREFIX}set paradoxy @user <amount>`")

@set_cmd.command(name="paradoxy")
@commands.has_permissions(administrator=True)
async def set_paradoxy_cmd(ctx: commands.Context, member: discord.Member = None, amount: int = None):
    """Set a user's paradoxy (economy balance). Admin only."""
    if member is None or amount is None:
        return await ctx.send(f"❓ Usage: `{PREFIX}set paradoxy @user <amount>`")
    
    await db.db.users.update_one({"_id": str(member.id)}, {"$set": {"balance": amount}}, upsert=True)
    await ctx.send(f"✅ Paradoxy setting updated to: {member.mention} {amount}")

# ── !setgoodbye ──────────────────────────────

@bot.group(name="setgoodbye", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def set_goodbye_cmd(ctx: commands.Context, *, message: str = None):
    """Set a custom automated leave message or channel. Admin only.
    Usage: !setgoodbye {member} has left the server.
    """
    if ctx.invoked_subcommand is None:
        if message:
            cfg = load_config()
            cfg["LEAVE_MESSAGE"] = message
            await save_config_sync(cfg)
            await ctx.send(f"✅ Leave message updated! Try `!testleave` to see it.")
        else:
            await ctx.send(f"❓ Usage: `{PREFIX}setgoodbye <message>` or `{PREFIX}setgoodbye channel <#ch>`")

@set_goodbye_cmd.command(name="channel")
@commands.has_permissions(administrator=True)
async def set_goodbye_channel_cmd(ctx: commands.Context, channel: discord.TextChannel):
    """Set the goodbye channel only. Admin only.
    Usage: !setgoodbye channel #channel
    """
    cfg = load_config()
    cfg["GOODBYE_CHANNEL_ID"] = channel.id
    await save_config_sync(cfg)
    await ctx.send(f"✅ Goodbye channel set to {channel.mention}")

# ── !setimg ──────────────────────────────────

@bot.command(name="setimg")
@commands.has_permissions(administrator=True)
async def set_img_cmd(ctx: commands.Context, mode: str, url: str = None):
    """Set welcome/goodbye image. Attach an image or provide URL.
    Usage: !setimg welcome [url]
    """
    mode = mode.lower()
    if mode in ["welcome", "join"]:
        mode = "welcome"
    elif mode in ["goodbye", "leave"]:
        mode = "goodbye"
    else:
        await ctx.send("❌ Mode must be `welcome`/`join` or `goodbye`/`leave`.")
        return

    img_url = url
    if not img_url and ctx.message.attachments:
        img_url = ctx.message.attachments[0].url
    
    if not img_url:
        await ctx.send("❌ Please provide a URL or attach an image!")
        return

    cfg = load_config()
    key = "WELCOME_IMAGE_URL" if mode == "welcome" else "GOODBYE_IMAGE_URL"
    cfg[key] = img_url
    await save_config_sync(cfg)
    await ctx.send(f"✅ {mode.capitalize()} image updated!")

# ── !logwhitelist ────────────────────────────

@bot.group(name="logwhitelist", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def log_whitelist_grp(ctx: commands.Context):
    """Manage the log whitelist (users who won't be logged). Usage: !logwhitelist <add/remove/list>"""
    await ctx.send(f"❓ Usage: `{PREFIX}logwhitelist <add/remove/list> @user`")

@log_whitelist_grp.command(name="add")
@commands.has_permissions(administrator=True)
async def log_whitelist_add(ctx: commands.Context, member: discord.Member):
    """Add a user to the log whitelist."""
    cfg = load_config()
    whitelist = cfg.get("LOG_WHITELISTED_USERS", [])
    
    if member.id in whitelist:
        await ctx.send(f"⚠️ {member.display_name} is already in the log whitelist.")
        return
        
    whitelist.append(member.id)
    cfg["LOG_WHITELISTED_USERS"] = whitelist
    await save_config_sync(cfg)
    await ctx.send(f"✅ {member.mention} has been added to the log whitelist! Their messages will no longer be logged.")

@log_whitelist_grp.command(name="remove")
@commands.has_permissions(administrator=True)
async def log_whitelist_remove(ctx: commands.Context, member: discord.Member):
    """Remove a user from the log whitelist."""
    cfg = load_config()
    whitelist = cfg.get("LOG_WHITELISTED_USERS", [])
    
    if member.id not in whitelist:
        await ctx.send(f"⚠️ {member.display_name} não está na whitelist de logs.")
        return
        
    whitelist.remove(member.id)
    cfg["LOG_WHITELISTED_USERS"] = whitelist
    await save_config_sync(cfg)
    await ctx.send(f"✅ {member.mention} removed from the log whitelist. Their activities will be logged again.")

# ── !level ───────────────────────────────────

@bot.command(name="level", aliases=["lvl", "xp"])
async def level_cmd(ctx: commands.Context, member: discord.Member = None):
    """Check your current level and XP progress."""
    member = member or ctx.author
    user_id = str(member.id)
    
    total_xp = await db.get_xp(user_id)
    lvl, cur_xp, next_xp = get_xp_progress(total_xp)
    
    # Progress Bar
    bar_length = 20
    filled = int((cur_xp / next_xp) * bar_length)
    bar = "█" * filled + "░" * (bar_length - filled)
    percent = int((cur_xp / next_xp) * 100)
    
    role_info = None
    for req_level in sorted(LEVEL_ROLES.keys(), reverse=True):
        if lvl >= req_level:
            role_info = LEVEL_ROLES[req_level]
            break

    embed = discord.Embed(
        title=f"🏆 {member.display_name}'s Level",
        color=role_info["color"] if role_info else 0x9B59B6,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=f"✨ **{lvl}**", inline=True)
    embed.add_field(name="Rank", value=f"🛡️ **{role_info['name'] if role_info else 'Novice'}**", inline=True)
    embed.add_field(name="Progress", value=f"`{bar}` {percent}%\n({cur_xp:,} / {next_xp:,} XP)", inline=False)
    embed.add_field(name="Total XP", value=f"💎 {total_xp:,} XP", inline=True)
    
    embed.set_footer(text="Paradox Kingdom 💜")
    await ctx.send(embed=embed)

# ── !rank ────────────────────────────────────

@bot.command(name="rank", aliases=["leaderboard_xp", "top_xp"])
async def rank_cmd(ctx: commands.Context):
    """Show the top 10 users with the most XP."""
    top_users = await db.get_level_leaderboard(10)
    
    if not top_users:
        await ctx.send("ℹ️ No one has earned any XP yet!")
        return

    description = ""
    for i, user_doc in enumerate(top_users):
        user_id = int(user_doc["_id"])
        total_xp = user_doc.get("xp", 0)
        level = get_level_from_xp(total_xp)
        
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"User ID: {user_id}"
        
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"`#{i+1}`"
        description += f"{emoji} **{name}** - Level {level} ({total_xp:,} XP)\n"

    embed = discord.Embed(
        title="🏆 Kingdom Leaderboard",
        description=description,
        color=0xF1C40F,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Paradox Bot 💜 | Top XP Earners")
    await ctx.send(embed=embed)

# ── !setlevel ────────────────────────────────

@bot.command(name="setlevel")
@commands.has_permissions(administrator=True)
async def set_level_cmd(ctx: commands.Context, member: discord.Member, level: int):
    """Set a user's level. Admin only."""
    if level < 0:
        await ctx.send("❌ Level cannot be negative!")
        return
        
    total_xp = get_total_xp_for_level(level)
    user_id = str(member.id)
    
    await db.set_xp(user_id, total_xp)
    await db.set_level(user_id, level)
    
    await handle_level_up(member, level, ctx.channel)
    await ctx.send(f"✅ Set {member.mention}'s level to **{level}** (Total XP: {total_xp:,}).")

# ── !setxp ───────────────────────────────────

@bot.command(name="setxp")
@commands.has_permissions(administrator=True)
async def set_xp_cmd(ctx: commands.Context, member: discord.Member, xp: int):
    """Set a user's total XP. Admin only."""
    if xp < 0:
        await ctx.send("❌ XP cannot be negative!")
        return
        
    user_id = str(member.id)
    level = get_level_from_xp(xp)
    
    await db.set_xp(user_id, xp)
    await db.set_level(user_id, level)
    
    await handle_level_up(member, level, ctx.channel)
    await ctx.send(f"✅ Set {member.mention}'s XP to **{xp:,}** (New Level: {level}).")

# ── !stoplevels ──────────────────────────────

@bot.command(name="stoplevels")
@commands.has_permissions(administrator=True)
async def stop_levels_cmd(ctx: commands.Context):
    """Stop the leveling system. Admin only."""
    cfg = load_config()
    cfg["LEVELING_SYSTEM_ENABLED"] = False
    await save_config_sync(cfg)
    await ctx.send("✅ The leveling system has been **disabled**. Users will no longer earn XP or level up.")

# ── !returnlevels ────────────────────────────

@bot.command(name="returnlevels")
@commands.has_permissions(administrator=True)
async def return_levels_cmd(ctx: commands.Context):
    """Restart the leveling system. Admin only."""
    cfg = load_config()
    cfg["LEVELING_SYSTEM_ENABLED"] = True
    await save_config_sync(cfg)
    await ctx.send("✅ The leveling system has been **enabled**. Users can now earn XP and level up.")

@bot.command(name="setlevelchannel")
@commands.has_permissions(administrator=True)
async def set_level_channel_cmd(ctx: commands.Context, channel: discord.TextChannel):
    """Set the channel for level-up notifications. Admin only."""
    cfg = load_config()
    cfg["LEVEL_CHANNEL_ID"] = channel.id
    await save_config_sync(cfg)
    await ctx.send(f"✅ Level-up notifications will now be sent in {channel.mention}")

@bot.command(name="setrank")
@commands.has_permissions(administrator=True)
async def set_rank_cmd(ctx: commands.Context, level: int, *, name: str):
    """Set the name of a rank for a specific level. Admin only.
    Usage: !setrank 5 Squire
    """
    global LEVEL_ROLES
    LEVEL_ROLES[level] = {"name": name, "color": 0x9B59B6} # Default purple if new
    
    # Save to config to persist
    cfg = load_config()
    cfg["LEVEL_ROLES"] = {str(k): v for k, v in LEVEL_ROLES.items()}
    await save_config_sync(cfg)
    
    await ctx.send(f"✅ Level **{level}** rank set to **{name}**.")
    await ctx.send("ℹ️ New roles will include the level prefix automatically (e.g. `Level {level}+ | {name}`).")

@log_whitelist_grp.command(name="list")
@commands.has_permissions(administrator=True)
async def log_whitelist_list(ctx: commands.Context):
    """List all log-whitelisted users."""
    cfg = load_config()
    whitelist = cfg.get("LOG_WHITELISTED_USERS", [])
    
    if not whitelist:
        await ctx.send("ℹ️ The log whitelist is empty.")
        return
        
    mentions = [f"<@{uid}>" for uid in whitelist]
    embed = discord.Embed(
        title="👻 Log Whitelist (Invisíveis)",
        description="\n".join(mentions),
        color=0xF1C40F
    )
    await ctx.send(embed=embed)

# ── !setcolor ────────────────────────────────

@bot.command(name="setcolor")
@commands.has_permissions(administrator=True)
async def set_color_cmd(ctx: commands.Context, hex_code: str):
    """Set the embed color (Hex). Usage: !setcolor #FF00FF"""
    if not hex_code.startswith("#") or len(hex_code) != 7:
        await ctx.send("❌ Please provide a valid hex code (e.g., #7289da)")
        return

    cfg = load_config()
    cfg["WELCOME_COLOR"] = hex_code
    await save_config_sync(cfg)
    await ctx.send(f"✅ Embed color updated to **{hex_code}**!")

@bot.command(name="autorole")
@commands.has_permissions(administrator=True)
async def set_autorole_cmd(ctx: commands.Context, *, role_name: str):
    """Set the auto-role name. Admin only.
    Usage: !autorole RoleName
    """
    # Verify the role exists
    role = parse_role_name(ctx.guild, role_name)
    if not role:
        await ctx.send(f"❌ Role `{role_name}` not found! Create it first.")
        return

    cfg = load_config()
    cfg["AUTO_ROLE_NAME"] = role.name
    await save_config_sync(cfg)
    await ctx.send(f"✅ Auto-role set to **{role.name}**")

# ── !setwelcomechannel ───────────────────────

@bot.command(name="setwelcomechannel")
@commands.has_permissions(administrator=True)
async def set_welcome_channel_cmd(ctx: commands.Context, channel: discord.TextChannel):
    """Set the welcome/goodbye channel. Admin only.
    Usage: !setwelcomechannel #channel
    """
    cfg = load_config()
    cfg["WELCOME_CHANNEL_ID"] = channel.id
    cfg["GOODBYE_CHANNEL_ID"] = channel.id
    await save_config_sync(cfg)
    await ctx.send(f"✅ Welcome & goodbye channel set to {channel.mention}")

# ── !setlogchannel ───────────────────────────

@bot.command(name="setlogchannel")
@commands.has_permissions(administrator=True)
async def set_log_channel_cmd(ctx: commands.Context, channel: discord.TextChannel):
    """Set the channel for logs (moderation, edits, deletes). Admin only.
    Usage: !setlogchannel #channel
    """
    cfg = load_config()
    cfg["LOG_CHANNEL_ID"] = channel.id
    await save_config_sync(cfg)
    await ctx.send(f"✅ Log channel set to {channel.mention}")

@bot.command(name="togglewelcome")
@commands.has_permissions(administrator=True)
async def toggle_welcome_cmd(ctx: commands.Context):
    """Enable or disable welcome messages. Admin only."""
    cfg = load_config()
    current = cfg.get("WELCOME_ENABLED", True)
    cfg["WELCOME_ENABLED"] = not current
    await save_config_sync(cfg)
    
    status = "🟢 **ENABLED**" if not current else "🔴 **DISABLED**"
    await ctx.send(f"Welcome messages are now {status}")

# ── !whitelist ───────────────────────────────

@bot.group(name="whitelist", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def whitelist_grp(ctx: commands.Context):
    """Manage the swear filter whitelist. Usage: !whitelist <add/remove/list>"""
    await ctx.send(f"❓ Usage: `{PREFIX}whitelist <add/remove/list> @user`")

@whitelist_grp.command(name="add")
@commands.has_permissions(administrator=True)
async def whitelist_add(ctx: commands.Context, member: discord.Member):
    """Add a user to the swear filter whitelist."""
    cfg = load_config()
    whitelist = cfg.get("WHITELISTED_USERS", [])
    
    if member.id in whitelist:
        await ctx.send(f"⚠️ {member.display_name} is already whitelisted.")
        return
        
    whitelist.append(member.id)
    cfg["WHITELISTED_USERS"] = whitelist
    await save_config_sync(cfg)
    await ctx.send(f"✅ {member.mention} has been added to the whitelist! They can now bypass the swear filter.")

@whitelist_grp.command(name="remove")
@commands.has_permissions(administrator=True)
async def whitelist_remove(ctx: commands.Context, member: discord.Member):
    """Remove a user from the swear filter whitelist."""
    cfg = load_config()
    whitelist = cfg.get("WHITELISTED_USERS", [])
    
    if member.id not in whitelist:
        await ctx.send(f"⚠️ {member.display_name} is not in the whitelist.")
        return
        
    whitelist.remove(member.id)
    cfg["WHITELISTED_USERS"] = whitelist
    await save_config_sync(cfg)
    await ctx.send(f"✅ {member.mention} has been removed from the whitelist.")

@whitelist_grp.command(name="list")
@commands.has_permissions(administrator=True)
async def whitelist_list(ctx: commands.Context):
    """List all whitelisted users."""
    cfg = load_config()
    whitelist = cfg.get("WHITELISTED_USERS", [])
    
    if not whitelist:
        await ctx.send("ℹ️ The whitelist is currently empty (Administrators bypass it automatically).")
        return
        
    mentions = [f"<@{uid}>" for uid in whitelist]
    embed = discord.Embed(
        title="🛡️ Swear Filter Whitelist",
        description="\n".join(mentions),
        color=0x3498DB
    )
    await ctx.send(embed=embed)

@bot.command(name="addswear")
@commands.has_permissions(administrator=True)
async def add_swear_cmd(ctx: commands.Context, *, word: str):
    """Add a word to the swear filter. Admin only.
    Usage: !addswear badword
    """
    cfg = load_config()
    swear_list = cfg.get("SWEAR_WORDS", [])
    word_lower = word.lower().strip()

    if word_lower in [w.lower() for w in swear_list]:
        await ctx.send(f"⚠️ `{word_lower}` is already in the filter.")
        return

    swear_list.append(word_lower)
    cfg["SWEAR_WORDS"] = swear_list
    await save_config_sync(cfg)

    # Delete the command message so the swear word isn't visible
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass

    await ctx.send(f"✅ Word added to the swear filter. (Total: {len(swear_list)} words)")

# ── !removeswear ─────────────────────────────

class PollView(discord.ui.View):
    def __init__(self, timeout=None, question=""):
        super().__init__(timeout=timeout)
        self.likes = 0
        self.dislikes = 0
        self.voters = set()
        self.question = question
        self.message = None

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            
            embed = discord.Embed(title="🗳️ Poll Ended", color=0x34495E, timestamp=discord.utils.utcnow())
            embed.description = f"**{self.question}**\n\n**Final Results:**\n👍 Agree: **{self.likes}**\n👎 Disagree: **{self.dislikes}**"
            try:
                await self.message.edit(embed=embed, view=self)
            except: pass

    @discord.ui.button(label="Agree (0)", style=discord.ButtonStyle.success, emoji="👍", custom_id="poll_like")
    async def like(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voters:
            await interaction.response.send_message("❌ You have already voted in this poll!", ephemeral=True)
            return
        self.likes += 1
        self.voters.add(interaction.user.id)
        button.label = f"Agree ({self.likes})"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Disagree (0)", style=discord.ButtonStyle.danger, emoji="👎", custom_id="poll_dislike")
    async def dislike(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voters:
            await interaction.response.send_message("❌ You have already voted in this poll!", ephemeral=True)
            return
        self.dislikes += 1
        self.voters.add(interaction.user.id)
        button.label = f"Disagree ({self.dislikes})"
        await interaction.response.edit_message(view=self)

@bot.command(name="poll")
async def poll_cmd(ctx: commands.Context, question: str, duration: str = "60"):
    """Create a poll. Usage: !poll "Question" [time] (e.g. 1h, 30m, 1d)"""
    from bot_functions import parse_duration, format_duration
    
    time_seconds = parse_duration(duration)
    readable_time = format_duration(time_seconds)
    
    embed = discord.Embed(
        title="🗳️ Paradox Poll",
        description=f"**{question}**\n\nVote using the buttons below!",
        color=0x9B59B6,
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
    embed.set_footer(text=f"Poll duration: {readable_time}")
    
    view = PollView(timeout=time_seconds, question=question)
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg
    
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name="removeswear")
@commands.has_permissions(administrator=True)
async def remove_swear_cmd(ctx: commands.Context, *, word: str):
    """Remove a word from the swear filter. Admin only.
    Usage: !removeswear badword
    """
    cfg = load_config()
    swear_list = cfg.get("SWEAR_WORDS", [])
    word_lower = word.lower().strip()

    new_list = [w for w in swear_list if w.lower() != word_lower]
    if len(new_list) == len(swear_list):
        await ctx.send(f"⚠️ `{word_lower}` was not in the filter.")
        return

    cfg["SWEAR_WORDS"] = new_list
    await save_config_sync(cfg)
    await ctx.send(f"✅ Word removed from the swear filter. (Total: {len(new_list)} words)")

# ── !togglefilter ────────────────────────────

@bot.command(name="togglefilter")
@commands.has_permissions(administrator=True)
async def toggle_filter_cmd(ctx: commands.Context):
    """Toggle the swear word filter on/off. Admin only."""
    cfg = load_config()
    current = cfg.get("SWEAR_FILTER_ENABLED", True)
    cfg["SWEAR_FILTER_ENABLED"] = not current
    await save_config_sync(cfg)

    status = "🟢 **ON**" if not current else "🔴 **OFF**"
    await ctx.send(f"Swear filter is now {status}")

# ── !swearlog ───────────────────────────────

@bot.command(name="swearlog")
@commands.has_permissions(administrator=True)
async def swear_log_cmd(ctx: commands.Context, member: discord.Member = None):
    """View the history of filtered words. Usage: !swearlog [@user]"""
    if member:
        # Show log for specific user
        user_id = str(member.id)
        user_data = await db.get_infractions(user_id)
        if not user_data:
            await ctx.send(f"✅ **{member.display_name}** has a clean history!")
            return
            
        embed = discord.Embed(title=f"🚨 History: {member.display_name}", color=0xE74C3C)
        embed.set_thumbnail(url=member.display_avatar.url)
        
        text = ""
        for i, inf in enumerate(user_data[-10:], 1): # Show last 10
            text += f"{i}. `{inf['word']}` at {inf['time']} (#{inf['channel']})\n"
        
        embed.description = text
        embed.set_footer(text=f"Total infractions: {len(user_data)}")
        await ctx.send(embed=embed)
    else:
        # Show general stats
        infractions = await db.get_all_infractions()
        if not infractions:
            await ctx.send("ℹ️ No swear words recorded yet.")
            return
            
        embed = discord.Embed(title="📊 Top Offenders", color=0xE74C3C)
        sorted_inf = sorted(infractions.items(), key=lambda x: len(x[1]), reverse=True)
        
        text = ""
        for i, (uid, data) in enumerate(sorted_inf[:10], 1):
            user = bot.get_user(int(uid))
            name = user.name if user else f"ID: {uid}"
            text += f"{i}. **{name}**: {len(data)} infractions\n"
        
        embed.description = text or "No data available."
        await ctx.send(embed=embed)

# ── SECURITY: QUARANTINE SYSTEM ──

async def get_or_create_quarantine(guild):
    role = discord.utils.get(guild.roles, name=QUARANTINE_ROLE_NAME)
    if not role:
        role = await guild.create_role(name=QUARANTINE_ROLE_NAME, color=0x34495E, reason="Security system setup")
        for channel in guild.channels:
            try:
                await channel.set_permissions(role, view_channel=False, send_messages=False)
            except: pass
            
    channel = discord.utils.get(guild.text_channels, name=QUARANTINE_CHANNEL_NAME)
    if not channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        channel = await guild.create_text_channel(name=QUARANTINE_CHANNEL_NAME, overwrites=overwrites)
        await channel.send("⚠️ **You have been placed in quarantine.**\nSpeak with the moderators here to appeal your punishment.")
    return role, channel

async def apply_quarantine(member: discord.Member, reason: str, expiry: datetime = None):
    """Saves roles, removes them, and adds Quarantined role."""
    guild = member.guild
    
    # Save current roles (excluding @everyone and Quarantined)
    role_ids = [role.id for role in member.roles if not role.is_default() and role.name != QUARANTINE_ROLE_NAME]
    
    await db.save_quarantine_roles(str(member.id), role_ids)
    if expiry:
        await db.db.users.update_one({"_id": str(member.id)}, {"$set": {"quarantine_expiry": expiry}})
    
    role, ch = await get_or_create_quarantine(guild)
    
    try:
        roles_to_remove = [r for r in member.roles if not r.is_default() and r < guild.me.top_role]
        await member.remove_roles(*roles_to_remove, reason=f"Quarantine: {reason}")
        await member.add_roles(role, reason=f"Quarantine: {reason}")
    except:
        await member.add_roles(role)

async def log_moderation(guild, action_text, reason):
    """Logs moderation actions to the configured log channel."""
    cfg = load_config()
    log_ch_id = cfg.get("LOG_CHANNEL_ID")
    if log_ch_id:
        channel = guild.get_channel(int(log_ch_id))
        if channel:
            embed = discord.Embed(
                title="🛡️ Moderation Log",
                description=action_text,
                color=0xE74C3C,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Reason", value=reason or "No reason provided")
            embed.set_footer(text=f"Server: {guild.name}")
            try:
                await channel.send(embed=embed)
            except: pass

async def apply_tiered_moderation(ctx, member: discord.Member, clause: str, custom_reason: str = None):
    """Handles 1.x tiered punishments."""
    mapping = {
        "1.1": {"action": "mute", "duration": 10, "label": "Rule 1.1 (Minor)"},
        "1.2": {"action": "quarantine", "duration": 120, "label": "Rule 1.2 (Medium)"},
        "1.3": {"action": "quarantine", "duration": None, "label": "Rule 1.3 (Severe)"}
    }
    
    tier = mapping.get(clause)
    if not tier:
        return False
        
    action = tier["action"]
    label = tier["label"]
    reason = f"[{label}] {custom_reason}" if custom_reason else label
    
    if action == "mute":
        await member.timeout(timedelta(minutes=tier["duration"]), reason=reason)
        msg = f"🔇 **{member.display_name}** was muted for **10 minutes** under **{label}**."
    elif action == "quarantine":
        expiry = datetime.now() + timedelta(minutes=tier["duration"]) if tier["duration"] else None
        await apply_quarantine(member, reason, expiry)
        duration_str = "2 hours" if tier["duration"] else "Indefinite"
        msg = f"⚖️ **{member.display_name}** was sent to **Quarantine** for **{duration_str}** under **{label}**."
    
    await ctx.send(msg)
    await log_moderation(ctx.guild, msg, reason)
    return True

# ── MODERATION COMMANDS ───────────────────────

@bot.command(name="softban")
@is_authorized(exclude_ban=True)
async def softban_cmd(ctx, member: discord.Member, *, reason="No reason provided"):
    """Ban and immediately unban to clear messages. Admin only."""
    await member.ban(reason=f"Softban: {reason}", delete_message_days=7)
    await ctx.guild.unban(member, reason="Softban completion")
    await ctx.send(f"🧼 **{member.display_name}** was softbanned. (Messages cleared)")

@bot.command(name="mute")
@is_authorized()
async def mute_cmd(ctx, member: discord.Member, duration_or_clause: str = "10", *, reason: str = None):
    """Timeout a member or apply 1.x clause. Usage: !mute @user 1.1 [reason] or !mute @user 10 [reason]"""
    if await apply_tiered_moderation(ctx, member, duration_or_clause, reason):
        return
    
    try:
        minutes = int(duration_or_clause)
    except:
        minutes = 10
        
    await member.timeout(timedelta(minutes=minutes), reason=reason or "No reason provided")
    msg = f"🔇 **{member.display_name}** was muted for **{minutes}** minutes."
    await ctx.send(msg)
    await log_moderation(ctx.guild, msg, reason)

@bot.command(name="quarantine")
@is_authorized()
async def quarantine_cmd(ctx, member: discord.Member, clause: str = None, *, reason: str = None):
    """Manually send a member to quarantine or apply clause. Admin only."""
    if clause and await apply_tiered_moderation(ctx, member, clause, reason):
        return
        
    await apply_quarantine(member, reason or "Manual Moderator Action")
    msg = f"⚖️ **{member.display_name}** has been sent to quarantine."
    await ctx.send(msg)
    await log_moderation(ctx.guild, msg, reason)

@bot.command(name="addscam")
@commands.has_permissions(administrator=True)
async def add_scam_cmd(ctx, link: str):
    """Add a new link to the phishing blacklist."""
    global SCAM_LINKS
    if link in SCAM_LINKS:
        await ctx.send("⚠️ This link is already in the blacklist.")
        return
    SCAM_LINKS.append(link)
    
    cfg = load_config()
    cfg["SCAM_LINKS"] = SCAM_LINKS
    await save_config_sync(cfg)
    
    await ctx.send(f"✅ Link `{link}` added to phishing filter!")

@bot.command(name="clearscamlog")
@commands.has_permissions(administrator=True)
async def clear_scam_log_cmd(ctx, member: discord.Member):
    """Reset the scam/phishing infraction count for a user."""
    strikes = await db.get_scam_strikes(str(member.id))
    if strikes > 0:
        await db.clear_scam_strikes(str(member.id))
        await ctx.send(f"✅ Phishing history for {member.display_name} has been cleared.")
    else:
        await ctx.send("ℹ️ This user has no phishing history.")

@bot.command(name="unquarantine")
@commands.has_permissions(administrator=True)
async def unquarantine_cmd(ctx, member: discord.Member):
    """Manually release a member from quarantine and restore roles."""
    cfg = load_config()
    role = discord.utils.get(ctx.guild.roles, name=QUARANTINE_ROLE_NAME)
    
    if role and role in member.roles:
        await member.remove_roles(role)
        
        # Restore saved roles
        saved_role_ids = await db.get_quarantine_roles(str(member.id))
        
        roles_to_add = []
        for rid in saved_role_ids:
            r = ctx.guild.get_role(int(rid))
            if r and r < ctx.guild.me.top_role:
                roles_to_add.append(r)
        
        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="Released from quarantine")
                await ctx.send(f"✅ **{member.display_name}** released! {len(roles_to_add)} roles returned.")
            except:
                await ctx.send(f"✅ **{member.display_name}** released, but there was an error returning some roles.")
        else:
            await ctx.send(f"✅ **{member.display_name}** released from quarantine!")
            
        # Clean up db
        await db.clear_quarantine_roles(str(member.id))
    else:
        await ctx.send(f"ℹ️ **{member.display_name}** is not in quarantine.")

@bot.command(name="setthreshold")
@commands.has_permissions(administrator=True)
async def set_threshold_cmd(ctx, system: str, key: str, value: int):
    """Set security thresholds. Usage: !setthreshold <swear/scam> <key> <value>"""
    cfg = load_config()
    system = system.lower()
    
    if system == "swear":
        thresholds = cfg.get("SWEAR_THRESHOLDS", {"silent": 1, "warn1": 2, "warn2": 3, "mute": 4, "quarantine": 8})
        thresholds[key] = value
        cfg["SWEAR_THRESHOLDS"] = thresholds
    elif system == "scam":
        thresholds = cfg.get("SCAM_THRESHOLDS", {"warn": 1, "mute1": 2, "mute2": 3, "quarantine": 4, "ban": 5})
        thresholds[key] = value
        cfg["SCAM_THRESHOLDS"] = thresholds
    else:
        await ctx.send("❌ Use `swear` or `scam` as the system.")
        return
        
    await save_config_sync(cfg)
    await ctx.send(f"✅ Threshold for `{system}` ({key}) updated to `{value}`!")

# ── !botinfo ─────────────────────────────────

@bot.command(name="botinfo")
async def bot_info_cmd(ctx: commands.Context):
    """Show information about Paradox Bot."""
    embed = discord.Embed(
        title="🤖 Paradox Bot",
        description="A multi-purpose Discord bot with auto-role, greetings, and moderation!",
        color=0x9B59B6,
    )
    embed.add_field(name="📡 Ping", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="🌐 Servers", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="👥 Users", value=str(sum(g.member_count for g in bot.guilds)), inline=True)
    embed.add_field(
        name="⚙️ Features",
        value=(
            "• Auto-role on join\n"
            "• Welcome & goodbye messages\n"
            "• Custom goodbye command\n"
            "• Swear word auto-filter\n"
            "• Admin configuration commands\n"
            "• Professional Ticket System"
        ),
        inline=False,
    )
    embed.set_footer(text="Paradox Bot 💜 | Made with discord.py")
    await ctx.send(embed=embed)

# ── !serverinfo ──────────────────────────────

@bot.command(name="serverinfo")
async def server_info_cmd(ctx: commands.Context):
    """Show server information."""
    guild = ctx.guild
    embed = discord.Embed(
        title=f"📊 {guild.name}",
        color=0x3498DB,
        timestamp=discord.utils.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="👑 Owner", value=guild.owner.mention if guild.owner else "N/A", inline=True)
    embed.add_field(name="👥 Members", value=str(guild.member_count), inline=True)
    embed.add_field(name="💬 Channels", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="🎭 Roles", value=str(len(guild.roles)), inline=True)
    embed.add_field(name="📅 Created", value=guild.created_at.strftime("%b %d, %Y"), inline=True)
    embed.set_footer(text="Paradox Bot 💜")
    await ctx.send(embed=embed)

# ── !purge ───────────────────────────────────

@bot.command(name="purge")
@is_authorized()
async def purge_cmd(ctx: commands.Context, amount: int = 5):
    """Delete messages in bulk. Admin/Bypass only.
    Usage: !purge 10
    """
    if amount < 1 or amount > 100:
        await ctx.send("⚠️ Please specify a number between 1 and 100.")
        return

    deleted = await ctx.channel.purge(limit=amount + 1)  # +1 for the command message
    msg = await ctx.send(f"🗑️ Deleted **{len(deleted) - 1}** messages.")
    await msg.delete(delay=3)

# ── !kick ────────────────────────────────────

@bot.command(name="kick")
@is_authorized()
async def kick_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    """Kick a member or apply 1.x clause. Usage: !kick @user 1.2 [reason]"""
    parts = reason.split(" ", 1)
    clause = parts[0]
    extra_reason = parts[1] if len(parts) > 1 else None
    
    if await apply_tiered_moderation(ctx, member, clause, extra_reason):
        return

    try:
        await member.kick(reason=reason)
        msg = f"👢 **{member.name}** was kicked by {ctx.author.mention}"
        embed = discord.Embed(
            title="👢 Member Kicked",
            description=f"{msg}\n**Reason:** {reason}",
            color=0xE67E22,
        )
        embed.set_footer(text="Paradox Bot 💜")
        await ctx.send(embed=embed)
        await log_moderation(ctx.guild, msg, reason)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to kick that user.")

# ── !ban ─────────────────────────────────────

@bot.command(name="ban")
@is_authorized(exclude_ban=True)
async def ban_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    """Ban a member or apply 1.x clause. Usage: !ban @user 1.3 [reason]"""
    parts = reason.split(" ", 1)
    clause = parts[0]
    extra_reason = parts[1] if len(parts) > 1 else None
    
    if await apply_tiered_moderation(ctx, member, clause, extra_reason):
        return

    try:
        await member.ban(reason=reason)
        msg = f"🔨 **{member.name}** was banned by {ctx.author.mention}"
        embed = discord.Embed(
            title="🔨 Member Banned",
            description=f"{msg}\n**Reason:** {reason}",
            color=0xE74C3C,
        )
        embed.set_footer(text="Paradox Bot 💜")
        await ctx.send(embed=embed)
        await log_moderation(ctx.guild, msg, reason)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to ban that user.")

# ── !unban ───────────────────────────────────

@bot.command(name="unban")
@is_authorized(exclude_ban=True)
async def unban_cmd(ctx: commands.Context, *, name: str):
    """Unban a member by their name. Usage: !unban Username#1234 or Username"""
    bans = [entry async for entry in ctx.guild.bans()]
    target_user = None
    
    for ban_entry in bans:
        user = ban_entry.user
        # Check both name only and name#discriminator
        if user.name.lower() == name.lower() or str(user).lower() == name.lower():
            target_user = user
            break
            
    if target_user:
        try:
            await ctx.guild.unban(target_user)
            await ctx.send(f"✅ **{target_user}** has been unbanned!")
            await log_moderation(ctx.guild, f"🔓 {target_user.name} was unbanned by {ctx.author.name}", "Manual Unban")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to unban that user.")
    else:
        await ctx.send(f"❌ Could not find a banned user with the name **{name}**.")

@bot.command(name="modlist")
@is_authorized()
async def modlist_cmd(ctx: commands.Context):
    """List all banned, quarantined, and timed-out users."""
    # Banned
    bans = [entry async for entry in ctx.guild.bans()]
    ban_list = "\n".join([f"• {entry.user} (Reason: {entry.reason})" for entry in bans]) or "None"
    
    # Quarantined
    role = discord.utils.get(ctx.guild.roles, name=QUARANTINE_ROLE_NAME)
    quarantined = [m.mention for m in ctx.guild.members if role in m.roles] if role else []
    quarantine_list = "\n".join(quarantined) or "None"
    
    # Timed Out
    timed_out = [m.mention for m in ctx.guild.members if m.communication_disabled_until and m.communication_disabled_until > discord.utils.utcnow()]
    timeout_list = "\n".join(timed_out) or "None"
    
    embed = discord.Embed(title="🛡️ Server Punishment List", color=0xE74C3C, timestamp=discord.utils.utcnow())
    embed.add_field(name="🔨 Banned Users", value=ban_list[:1024], inline=False)
    embed.add_field(name="⚖️ Quarantined Users", value=quarantine_list[:1024], inline=False)
    embed.add_field(name="🔇 Timed Out Users", value=timeout_list[:1024], inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name="swearlist")
@is_authorized()
async def swearlist_cmd(ctx: commands.Context):
    """List all blacklisted words."""
    cfg = load_config()
    swears = cfg.get("SWEAR_WORDS", [])
    
    if not swears:
        return await ctx.send("ℹ️ No swear words are currently blacklisted.")
        
    text = ", ".join([f"`{w}`" for w in swears])
    embed = discord.Embed(title="🚫 Blacklisted Words", description=text[:4000], color=0xE74C3C)
    await ctx.send(embed=embed)

# ── !setbypass ────────────────────────────────

@bot.command(name="setbypass")
@commands.has_permissions(administrator=True)
async def set_bypass_cmd(ctx: commands.Context, target: str):
    """Give a user or role access to all commands except ban. Usage: !setbypass @user or !setbypass @role"""
    cfg = load_config()
    
    # Try to parse as member
    try:
        member = await commands.MemberConverter().convert(ctx, target)
        if "BYPASS_USER_IDS" not in cfg: cfg["BYPASS_USER_IDS"] = []
        if member.id not in cfg["BYPASS_USER_IDS"]:
            cfg["BYPASS_USER_IDS"].append(member.id)
            await save_config_sync(cfg)
            return await ctx.send(f"✅ **{member.display_name}** now has global command bypass! (Except Ban)")
        else:
            cfg["BYPASS_USER_IDS"].remove(member.id)
            await save_config_sync(cfg)
            return await ctx.send(f"❌ **{member.display_name}** removed from global command bypass.")
    except:
        pass
        
    # Try to parse as role
    try:
        role = await commands.RoleConverter().convert(ctx, target)
        if "BYPASS_ROLE_IDS" not in cfg: cfg["BYPASS_ROLE_IDS"] = []
        if role.id not in cfg["BYPASS_ROLE_IDS"]:
            cfg["BYPASS_ROLE_IDS"].append(role.id)
            await save_config_sync(cfg)
            return await ctx.send(f"✅ Role **{role.name}** now has global command bypass! (Except Ban)")
        else:
            cfg["BYPASS_ROLE_IDS"].remove(role.id)
            await save_config_sync(cfg)
            return await ctx.send(f"❌ Role **{role.name}** removed from global command bypass.")
    except:
        return await ctx.send("❌ Please mention a valid user or role.")

@bot.command(name="setswearthreshold")
@is_authorized()
async def set_swear_threshold_cmd(ctx: commands.Context, action: str, count: int):
    """Set infraction count for a swear action. Admin/Bypass only.
    Actions: silent, warn1, warn2, mute, quarantine
    """
    cfg = load_config()
    if "SWEAR_THRESHOLDS" not in cfg:
        cfg["SWEAR_THRESHOLDS"] = {"silent": 1, "warn1": 2, "warn2": 3, "mute": 4, "quarantine": 8}
    
    action = action.lower()
    if action not in cfg["SWEAR_THRESHOLDS"]:
        return await ctx.send(f"❌ Invalid action. Choose from: {', '.join(cfg['SWEAR_THRESHOLDS'].keys())}")
        
    cfg["SWEAR_THRESHOLDS"][action] = count
    await save_config_sync(cfg)
    await ctx.send(f"✅ Swear threshold for **{action}** set to **{count}** infractions.")

@bot.command(name="setswearpenalty")
@is_authorized()
async def set_swear_penalty_cmd(ctx: commands.Context, duration: str):
    """Set how long a user is muted for swearing. Admin/Bypass only.
    Usage: !setswearpenalty 10m, 1h, 1d
    """
    from bot_functions import parse_duration, format_duration
    seconds = parse_duration(duration)
    minutes = seconds // 60
    
    cfg = load_config()
    cfg["SWEAR_PUNISHMENT_DURATION"] = minutes
    await save_config_sync(cfg)
    await ctx.send(f"✅ Swear mute penalty set to **{format_duration(seconds)}**.")

@bot.command(name="settier")
@is_authorized()
async def set_tier_cmd(ctx: commands.Context, clause: str, duration: str):
    """Set duration for a punishment tier. Admin/Bypass only.
    Usage: !settier 1.1 1h
    """
    from bot_functions import parse_duration, format_duration
    seconds = parse_duration(duration)
    minutes = seconds // 60
    
    cfg = load_config()
    if "PUNISHMENT_TIERS" not in cfg:
        cfg["PUNISHMENT_TIERS"] = {
            "1.1": {"action": "mute", "duration": 10, "label": "Rule 1.1 (Minor)"},
            "1.2": {"action": "mute", "duration": 30, "label": "Rule 1.2 (Standard)"},
            "1.3": {"action": "mute", "duration": 60, "label": "Rule 1.3 (Serious)"},
            "1.4": {"action": "quarantine", "duration": 1440, "label": "Rule 1.4 (Severe)"},
            "1.5": {"action": "quarantine", "duration": 10080, "label": "Rule 1.5 (Critical)"}
        }
        
    if clause not in cfg["PUNISHMENT_TIERS"]:
        return await ctx.send(f"❌ Invalid tier clause. (e.g. 1.1, 1.2)")
        
    cfg["PUNISHMENT_TIERS"][clause]["duration"] = minutes
    await save_config_sync(cfg)
    await ctx.send(f"✅ Duration for tier **{clause}** set to **{format_duration(seconds)}**.")

@bot.command(name="setcooldown")
@is_authorized()
async def set_cooldown_cmd(ctx: commands.Context, command_name: str, duration: str):
    """Set cooldown for an economy command. Admin/Bypass only.
    Usage: !setcooldown work 10m
    """
    from bot_functions import parse_duration, format_duration
    seconds = parse_duration(duration)
    
    cfg = load_config()
    if "COMMAND_COOLDOWNS" not in cfg:
        cfg["COMMAND_COOLDOWNS"] = {"daily": 86400, "work": 300, "crime": 60, "heist": 300, "steal": 300, "casino": 5}
        
    cmd_key = command_name.lower()
    if cmd_key not in cfg["COMMAND_COOLDOWNS"]:
        return await ctx.send(f"❌ Invalid command. Choose from: {', '.join(cfg['COMMAND_COOLDOWNS'].keys())}")
        
    cfg["COMMAND_COOLDOWNS"][cmd_key] = seconds
    await save_config_sync(cfg)
    await ctx.send(f"✅ Cooldown for **!{cmd_key}** set to **{format_duration(seconds)}**.")

# ── !setboostchannel ──────────────────────────

@bot.command(name="setboostchannel")
@commands.has_permissions(administrator=True)
async def set_boost_channel(ctx: commands.Context, channel: discord.TextChannel):
    """Set the channel for boost messages. Admin only."""
    cfg = load_config()
    cfg["BOOST_CHANNEL_ID"] = channel.id
    await save_config_sync(cfg)
    await ctx.send(f"✅ Boost messages will now be sent in {channel.mention}.")

@bot.command(name="setboostrole")
@commands.has_permissions(administrator=True)
async def set_boost_role(ctx: commands.Context, *, role_name: str):
    """Set the custom role given when a user boosts. Admin only."""
    cfg = load_config()
    cfg["BOOST_ROLE_NAME"] = role_name
    await save_config_sync(cfg)
    await ctx.send(f"✅ Users who boost will receive the role **{role_name}**.")

# ── CONFIG CUSTOMIZATION ──────────────────────

@bot.command(name="setswearthreshold")
@is_authorized()
async def set_swear_threshold_cmd(ctx: commands.Context, action: str, count: int):
    """Set infraction count for a swear action. Admin/Bypass only.
    Actions: silent, warn1, warn2, mute, quarantine
    """
    cfg = load_config()
    if "SWEAR_THRESHOLDS" not in cfg:
        cfg["SWEAR_THRESHOLDS"] = {"silent": 1, "warn1": 2, "warn2": 3, "mute": 4, "quarantine": 8}
    
    action = action.lower()
    if action not in cfg["SWEAR_THRESHOLDS"]:
        return await ctx.send(f"❌ Invalid action. Choose from: {', '.join(cfg['SWEAR_THRESHOLDS'].keys())}")
        
    cfg["SWEAR_THRESHOLDS"][action] = count
    await save_config_sync(cfg)
    await ctx.send(f"✅ Swear threshold for **{action}** set to **{count}** infractions.")

@bot.command(name="setswearpenalty")
@is_authorized()
async def set_swear_penalty_cmd(ctx: commands.Context, duration: str):
    """Set how long a user is muted for swearing. Admin/Bypass only.
    Usage: !setswearpenalty 10m, 1h, 1d
    """
    from bot_functions import parse_duration, format_duration
    seconds = parse_duration(duration)
    minutes = seconds // 60
    
    cfg = load_config()
    cfg["SWEAR_PUNISHMENT_DURATION"] = minutes
    await save_config_sync(cfg)
    await ctx.send(f"✅ Swear mute penalty set to **{format_duration(seconds)}**.")

@bot.command(name="settier")
@is_authorized()
async def set_tier_cmd(ctx: commands.Context, clause: str, duration: str):
    """Set duration for a punishment tier. Admin/Bypass only.
    Usage: !settier 1.1 1h
    """
    from bot_functions import parse_duration, format_duration
    seconds = parse_duration(duration)
    minutes = seconds // 60
    
    cfg = load_config()
    if "PUNISHMENT_TIERS" not in cfg:
        cfg["PUNISHMENT_TIERS"] = {
            "1.1": {"action": "mute", "duration": 10, "label": "Rule 1.1 (Minor)"},
            "1.2": {"action": "mute", "duration": 30, "label": "Rule 1.2 (Standard)"},
            "1.3": {"action": "mute", "duration": 60, "label": "Rule 1.3 (Serious)"},
            "1.4": {"action": "quarantine", "duration": 1440, "label": "Rule 1.4 (Severe)"},
            "1.5": {"action": "quarantine", "duration": 10080, "label": "Rule 1.5 (Critical)"}
        }
        
    if clause not in cfg["PUNISHMENT_TIERS"]:
        return await ctx.send(f"❌ Invalid tier clause. (e.g. 1.1, 1.2)")
        
    cfg["PUNISHMENT_TIERS"][clause]["duration"] = minutes
    await save_config_sync(cfg)
    await ctx.send(f"✅ Duration for tier **{clause}** set to **{format_duration(seconds)}**.")

@bot.command(name="setcooldown")
@is_authorized()
async def set_cooldown_cmd(ctx: commands.Context, command_name: str, duration: str):
    """Set cooldown for an economy command. Admin/Bypass only.
    Usage: !setcooldown work 10m
    """
    from bot_functions import parse_duration, format_duration
    seconds = parse_duration(duration)
    
    cfg = load_config()
    if "COMMAND_COOLDOWNS" not in cfg:
        cfg["COMMAND_COOLDOWNS"] = {"daily": 86400, "work": 300, "crime": 60, "heist": 300, "steal": 300, "casino": 5}
        
    cmd_key = command_name.lower()
    if cmd_key not in cfg["COMMAND_COOLDOWNS"]:
        return await ctx.send(f"❌ Invalid command. Choose from: {', '.join(cfg['COMMAND_COOLDOWNS'].keys())}")
        
    cfg["COMMAND_COOLDOWNS"][cmd_key] = seconds
    await save_config_sync(cfg)
    await ctx.send(f"✅ Cooldown for **!{cmd_key}** set to **{format_duration(seconds)}**.")

@bot.command(name="setboostmessage")
@commands.has_permissions(administrator=True)
async def set_boost_message(ctx: commands.Context, *, message: str):
    """Set custom boost message. Admin only."""
    cfg = load_config()
    cfg["BOOST_MESSAGE"] = message
    await save_config_sync(cfg)
    await ctx.send(f"✅ Boost message updated! Try `!testboost` to see it.")

# ── !testboost / !setboost ────────────────────
@bot.command(name="testboost", aliases=["setboost"])
@commands.has_permissions(administrator=True)
async def test_boost(ctx: commands.Context, member: discord.Member = None):
    """Simulate a server boost for yourself or another member. Admin only.
    Usage: !testboost [@user] or !setboost @user
    """
    member = member or ctx.author
    cfg = load_config()
    
    boost_channel_id = cfg.get("BOOST_CHANNEL_ID")
    channel = bot.get_channel(int(boost_channel_id)) if boost_channel_id else ctx.channel
    
    if channel:
        boost_tpl = cfg.get("BOOST_MESSAGE", "Thank you for boosting the server, {mention}! 💖")
        
        embed = discord.Embed(
            title="✨ Server Boosted! ✨",
            description=boost_tpl.replace("{mention}", member.mention).replace("{member}", member.name).replace("{server}", ctx.guild.name),
            color=0xF47FFF,
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Paradox Bot 💜")
        
        # Test Role selector if configured
        selectable_names = cfg.get("SELECTABLE_BOOST_ROLES", [])
        if selectable_names:
            roles = []
            for name in selectable_names:
                r = parse_role_name(ctx.guild, name)
                if r: roles.append(r)
            if roles:
                await channel.send(content=f"🎁 {member.mention}, you can pick one special booster role below!", view=BoostRoleView(roles))
        else:
            await channel.send(embed=embed)

        # Test Auto-Role creation/assignment
        role_name = cfg.get("BOOST_ROLE_NAME", "Server Booster")
        role = parse_role_name(ctx.guild, role_name)
        if not role:
            try:
                role = await ctx.guild.create_role(name=role_name, color=0xF47FFF, hoist=True, reason="Test Boost Role Creation")
                await ctx.send(f"🛠️ Created testing role: **{role_name}**")
            except discord.Forbidden:
                await ctx.send(f"❌ Failed to create role **{role_name}** (Permissions)")
        
        if role:
            try:
                await member.add_roles(role)
                await ctx.send(f"✅ Assigned **{role.name}** to **{member.display_name}**!")
            except discord.Forbidden:
                await ctx.send(f"❌ Failed to assign role (Permissions)")

        if channel != ctx.channel:
            await ctx.send(f"✅ Test boost for {member.display_name} complete! Check {channel.mention}")
    else:
        await ctx.send("❌ Boost channel not found! Set it with `!setboostchannel #channel`.")

# ── !setticketcategory ───────────────────────

@bot.command(name="setticketcategory")
@commands.has_permissions(administrator=True)
async def set_ticket_category(ctx: commands.Context, category_id: str):
    """Set the category where new tickets are opened. Admin only."""
    cfg = load_config()
    cfg["TICKET_CATEGORY_ID"] = category_id
    await save_config_sync(cfg)
    await ctx.send(f"✅ All new tickets will now be created in category ID: `{category_id}`")

# ── !addboostselectrole ───────────────────────

@bot.command(name="addboostselectrole")
@commands.has_permissions(administrator=True)
async def add_boost_select_role(ctx: commands.Context, *, role_name: str):
    """Add a role to the booster selection menu. Admin only."""
    cfg = load_config()
    roles = cfg.get("SELECTABLE_BOOST_ROLES", [])
    if role_name not in roles:
        roles.append(role_name)
        cfg["SELECTABLE_BOOST_ROLES"] = roles
        await save_config_sync(cfg)
        await ctx.send(f"✅ Role **{role_name}** added to the booster selector!")
    else:
        await ctx.send("⚠️ That role is already in the list.")

# ── !removeboostselectrole ────────────────────

@bot.command(name="removeboostselectrole")
@commands.has_permissions(administrator=True)
async def remove_boost_select_role(ctx: commands.Context, *, role_name: str):
    """Remove a role from the booster selection menu. Admin only."""
    cfg = load_config()
    roles = cfg.get("SELECTABLE_BOOST_ROLES", [])
    if role_name in roles:
        roles.remove(role_name)
        cfg["SELECTABLE_BOOST_ROLES"] = roles
        await save_config_sync(cfg)
        await ctx.send(f"✅ Role **{role_name}** removed from the booster selector.")
    else:
        await ctx.send("⚠️ That role was not in the list.")

# ── !setvouchchannel ──────────────────────────

@bot.command(name="setvouchchannel")
@commands.has_permissions(administrator=True)
async def set_vouch_channel(ctx: commands.Context, channel: discord.TextChannel):
    """Set the channel where ticket vouches are logged. Admin only."""
    cfg = load_config()
    cfg["VOUCH_CHANNEL_ID"] = channel.id
    await save_config_sync(cfg)
    await ctx.send(f"✅ Vouch logs will now be sent to {channel.mention}")

# ── !vouches ──────────────────────────────────

@bot.command(name="vouches")
async def check_vouches(ctx: commands.Context, member: discord.Member = None):
    """Check how many vouches you or someone else has."""
    member = member or ctx.author
    vouches = await db.get_vouches(str(member.id))
    level = (vouches // 5) + 1
    
    embed = discord.Embed(
        title=f"⭐ Vouches for {member.name}",
        color=0xF1C40F,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Total Vouches", value=str(vouches), inline=True)
    embed.add_field(name="Level", value=str(level), inline=True)
    
    # Progress to next level
    next_level_vouches = (level * 5)
    remaining = next_level_vouches - vouches
    embed.add_field(name="Next Level In", value=f"{remaining} more vouches", inline=False)
    
    embed.set_footer(text="Paradox Bot 💜 | Level System")
    await ctx.send(embed=embed)
# ── !addgame & !togglegame ────────────────────

@bot.command(name="addgame")
@commands.has_permissions(administrator=True)
async def addgame_cmd(ctx: commands.Context, game_id: str, emoji: str, *, name: str):
    """Add a new game to the ticket system. Usage: !addgame ID Emoji Full Name"""
    game_id = game_id.upper()
    cfg = load_config()
    games = cfg.get("HELPER_GAMES", {})
    games[game_id] = {
        "name": name,
        "emoji": emoji,
        "questions": "1. Roblox Username?\n2. What do you need help with?\n3. Timezone?",
        "active": True
    }
    cfg["HELPER_GAMES"] = games
    await save_config_sync(cfg)
    await ctx.send(f"✅ Game **{name}** ({game_id}) added and set to active!")

@bot.command(name="togglegame")
@commands.has_permissions(administrator=True)
async def togglegame_cmd(ctx: commands.Context, game_id: str):
    """Toggle whether a game is active in the ticket menus."""
    game_id = game_id.upper()
    cfg = load_config()
    games = cfg.get("HELPER_GAMES", {})
    if game_id not in games:
        await ctx.send(f"❌ Game `{game_id}` not found.")
        return
    
    current_status = games[game_id].get("active", True)
    games[game_id]["active"] = not current_status
    cfg["HELPER_GAMES"] = games
    await save_config_sync(cfg)
    
    status_text = "🟢 Active" if not current_status else "🔴 Inactive"
    await ctx.send(f"✅ Game **{games[game_id]['name']}** is now {status_text}.")

# ── !setrank & !setvouches & !autorole ────────

@bot.command(name="setrole")
@commands.has_permissions(manage_roles=True)
async def setrole_cmd(ctx: commands.Context, member: discord.Member, *, role: discord.Role):
    """Give or take a role from a user. Usage: !setrole @user RoleName"""
    try:
        if role in member.roles:
            await member.remove_roles(role)
            await ctx.send(f"✅ Removed the role **{role.name}** from {member.mention}")
        else:
            await member.add_roles(role)
            await ctx.send(f"✅ Added the role **{role.name}** to {member.mention}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to manage this role (it might be higher than my top role).")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {e}")

@bot.command(name="listroles")
@commands.has_permissions(manage_roles=True)
async def list_roles_cmd(ctx: commands.Context):
    """List all roles and show which ones the bot can manage."""
    bot_top_role = ctx.guild.me.top_role
    manageable = []
    unmanageable = []
    
    for role in sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True):
        if role.is_default(): continue
        
        if role < bot_top_role and not role.managed:
            manageable.append(f"✅ {role.name}")
        else:
            reason = "(Acima do bot)" if role >= bot_top_role else "(Integração)"
            unmanageable.append(f"❌ {role.name} {reason}")

    embed = discord.Embed(title="🎭 Server Roles", color=0x3498DB)
    if manageable:
        embed.add_field(name="Can Manage", value="\n".join(manageable[:25]) or "None", inline=False)
    if unmanageable:
        embed.add_field(name="Cannot Manage", value="\n".join(unmanageable[:25]) or "None", inline=False)
    
    embed.set_footer(text=f"Total: {len(ctx.guild.roles)-1} roles")
    await ctx.send(embed=embed)

@bot.command(name="setvouches")
@commands.has_permissions(administrator=True)
async def setvouches_cmd(ctx: commands.Context, member: discord.Member, vouches: int):
    """Set a member's exact vouch count manually."""
    if vouches < 0:
        await ctx.send("❌ Vouches cannot be negative.")
        return
        
    await db.set_vouches(str(member.id), vouches)
    
    level = (vouches // 5) + 1
    await ctx.send(f"✅ Set **{member.display_name}** to **{vouches}** vouches (Level {level}).")

@bot.command(name="saycolor")
async def say_color_cmd(ctx: commands.Context, color: str, *, text: str):
    """Sends a message in a specific color using ANSI blocks."""
    colors = {
        "grey": "30", "red": "31", "green": "32", "yellow": "33",
        "blue": "34", "pink": "35", "cyan": "36", "white": "37"
    }
    
    code = colors.get(color.lower())
    if not code:
        await ctx.send(f"❌ Color not found! Use: {', '.join(colors.keys())}")
        return
        
    # \u001b is the escape character for ANSI
    ansi_text = f"```ansi\n\u001b[{code}m{text}\u001b[0m\n```"
    await ctx.send(ansi_text)
    
    # Optional: Delete the user's original command to make it look cleaner
    try: await ctx.message.delete()
    except: pass

# ── !add & !remove (Ticket Management) ────────

@bot.command(name="add")
@commands.has_permissions(manage_channels=True)
async def add_ticket_user(ctx: commands.Context, member: discord.Member):
    """Add a user to the current ticket."""
    if "support-" not in ctx.channel.name and "macro-" not in ctx.channel.name and "carry-" not in ctx.channel.name and "apply-" not in ctx.channel.name:
        await ctx.send("❌ This command can only be used in tickets.")
        return
        
    await ctx.channel.set_permissions(member, read_messages=True, send_messages=True, embed_links=True, attach_files=True)
    await ctx.send(f"✅ Added {member.mention} to the ticket.")

@bot.command(name="remove")
@commands.has_permissions(manage_channels=True)
async def remove_ticket_user(ctx: commands.Context, member: discord.Member):
    """Remove a user from the current ticket."""
    if "support-" not in ctx.channel.name and "macro-" not in ctx.channel.name and "carry-" not in ctx.channel.name and "apply-" not in ctx.channel.name:
        await ctx.send("❌ This command can only be used in tickets.")
        return
        
    await ctx.channel.set_permissions(member, overwrite=None)
    await ctx.send(f"✅ Removed {member.display_name} from the ticket.")

@bot.command(name="migrate")
@commands.has_permissions(administrator=True)
async def migrate_cmd(ctx: commands.Context, arg: str = None):
    """Migrate data. Usage: !migrate db (JSON -> DB) or !migrate json (DB -> JSON)"""
    if arg == "db":
        cfg = load_config()
        await ctx.send("🔄 Starting database migration (JSON -> MongoDB)...")
        
        # Migrate Vouches
        vouches = cfg.get("VOUCHES", {})
        for uid, count in vouches.items():
            await db.set_vouches(uid, count)
            
        # Migrate Scam Strikes
        scams = cfg.get("SCAM_INFRACTIONS", {})
        for uid, count in scams.items():
            # We use set_scam_strikes logic or just add them
            await db.db.users.update_one({"_id": uid}, {"$set": {"scam_strikes": count}}, upsert=True)
            
        # Migrate Infractions
        infractions = cfg.get("INFRACTIONS", {})
        for uid, inf_list in infractions.items():
            await db.set_infractions(uid, inf_list)
            
        # Migrate Quarantine
        quarantine = cfg.get("QUARANTINE_ROLES", {})
        for uid, roles in quarantine.items():
            await db.save_quarantine_roles(uid, roles)
            
        await ctx.send("✅ Migration complete! All user data has been transferred to MongoDB.")
        
    elif arg == "json":
        if db.db is None:
            await ctx.send("❌ Database not connected. Cannot export data.")
            return

        await ctx.send("🔄 Starting export (MongoDB -> JSON)...")
        all_users = await db.get_all_users()
        cfg = load_config()
        
        # Initialize sections
        cfg["VOUCHES"] = {}
        cfg["SCAM_INFRACTIONS"] = {}
        cfg["INFRACTIONS"] = {}
        cfg["QUARANTINE_ROLES"] = {}
        
        for user in all_users:
            uid = user["_id"]
            if "vouches" in user:
                cfg["VOUCHES"][uid] = user["vouches"]
            if "scam_strikes" in user:
                cfg["SCAM_INFRACTIONS"][uid] = user["scam_strikes"]
            if "infractions" in user:
                cfg["INFRACTIONS"][uid] = user["infractions"]
            if "quarantine_roles" in user:
                cfg["QUARANTINE_ROLES"][uid] = user["quarantine_roles"]
                
        await save_config_sync(cfg)
        await ctx.send(f"✅ Export complete! Data for {len(all_users)} users has been saved to config.json.")
    else:
        await ctx.send("❓ Usage: `!migrate db` (Import to DB) or `!migrate json` (Export to JSON)")

# Poker evaluation logic is consolidated in the main Poker section below.

# Removed duplicate Poker implementation to resolve registration error.
# The main Poker logic is located later in the file (lines 3829+).


# ══════════════════════════════════════════════
#  HEIST MINIGAMES
# ══════════════════════════════════════════════

class LockpickMinigame(discord.ui.View):
    def __init__(self, user_id, difficulty, callback):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.callback = callback
        self.zones = 3 if difficulty == "easy" else 5 if difficulty == "normal" else 8
        self.correct = random.randint(1, self.zones)
        self.attempts = 3 if difficulty == "easy" else 2 if difficulty == "normal" else 1
        self.setup_buttons()

    def setup_buttons(self):
        for i in range(1, self.zones + 1):
            btn = discord.ui.Button(label="🔒", style=discord.ButtonStyle.secondary, custom_id=str(i))
            btn.callback = self.check_lock
            self.add_item(btn)

    async def check_lock(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: return
        choice = int(interaction.data['custom_id'])
        if choice == self.correct:
            await self.callback(interaction, True)
        else:
            self.attempts -= 1
            if self.attempts <= 0: await self.callback(interaction, False)
            else: await interaction.response.send_message(f"❌ Wrong pin! {self.attempts} left.", ephemeral=True)

class SafeMinigame(discord.ui.View):
    def __init__(self, user_id, difficulty, callback):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.callback = callback
        self.range = 5 if difficulty == "easy" else 10 if difficulty == "normal" else 20
        self.correct = random.randint(1, self.range)
        self.setup_buttons()

    def setup_buttons(self):
        for i in range(1, self.range + 1):
            btn = discord.ui.Button(label=str(i), style=discord.ButtonStyle.secondary, custom_id=str(i))
            btn.callback = self.check_safe
            self.add_item(btn)

    async def check_safe(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: return
        choice = int(interaction.data['custom_id'])
        await self.callback(interaction, choice == self.correct)

class HackingMinigame(discord.ui.View):
    def __init__(self, user_id, difficulty, callback):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.callback = callback
        self.codes = ["0x4F", "0x2A", "0x91", "0xBC", "0xE3", "0x7D", "0xA5"]
        self.target = random.choice(self.codes)
        self.setup_buttons()

    def setup_buttons(self):
        random.shuffle(self.codes)
        for code in self.codes:
            btn = discord.ui.Button(label=code, style=discord.ButtonStyle.secondary, custom_id=code)
            btn.callback = self.check_hack
            self.add_item(btn)

    async def check_hack(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: return
        await self.callback(interaction, interaction.data['custom_id'] == self.target)

class VaultMinigame(discord.ui.View):
    def __init__(self, user_id, difficulty, callback):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.callback = callback
        seq_len = 3 if difficulty == "easy" else 4 if difficulty == "normal" else 5
        self.sequence = [random.randint(1, 4) for _ in range(seq_len)]
        self.player_input = []
        self.setup_buttons()

    def setup_buttons(self):
        for i in range(1, 5):
            btn = discord.ui.Button(label=f"Module {i}", style=discord.ButtonStyle.secondary, custom_id=str(i))
            btn.callback = self.check_sequence
            self.add_item(btn)

    async def check_sequence(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: return
        choice = int(interaction.data['custom_id'])
        self.player_input.append(choice)
        idx = len(self.player_input) - 1
        if self.player_input[idx] != self.sequence[idx]:
            await self.callback(interaction, False)
        elif len(self.player_input) == len(self.sequence):
            await self.callback(interaction, True)
        else:
            await interaction.response.send_message(f"✅ Correct! {len(self.player_input)}/{len(self.sequence)}", ephemeral=True)

class HeistTargetView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.user_id = ctx.author.id

    @discord.ui.button(label="Jewelry Store", style=discord.ButtonStyle.primary)
    async def jewelry(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        await self.choose_target(interaction, "jewelry")

    @discord.ui.button(label="Main Bank", style=discord.ButtonStyle.primary)
    async def bank(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        await self.choose_target(interaction, "bank")

    @discord.ui.button(label="Armored Truck", style=discord.ButtonStyle.primary)
    async def truck(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        await self.choose_target(interaction, "truck")

    async def choose_target(self, interaction: discord.Interaction, target: str):
        embed = discord.Embed(title="🏦 Strategic Heist", color=0x34495E)
        embed.description = f"Target: **{HEIST_TARGETS[target]['name']}**. Choose difficulty:"
        view = HeistDifficultyView(self.ctx, target)
        await interaction.response.edit_message(embed=embed, view=view)

class HeistDifficultyView(discord.ui.View):
    def __init__(self, ctx, target: str):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.user_id = ctx.author.id
        self.target = target

    @discord.ui.button(label="Easy", style=discord.ButtonStyle.success)
    async def easy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.start_minigame(interaction, "easy")

    @discord.ui.button(label="Normal", style=discord.ButtonStyle.primary)
    async def normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.start_minigame(interaction, "normal")

    @discord.ui.button(label="Hard", style=discord.ButtonStyle.danger)
    async def hard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.start_minigame(interaction, "hard")

    async def start_minigame(self, interaction: discord.Interaction, difficulty: str):
        if interaction.user.id != self.user_id: return
        
        target_info = HEIST_TARGETS[self.target]
        game_type = random.choice(target_info["minigames"])
        
        callback = self.make_callback(difficulty)
        
        if game_type == "lockpick": view = LockpickMinigame(self.user_id, difficulty, callback)
        elif game_type == "safe": view = SafeMinigame(self.user_id, difficulty, callback)
        elif game_type == "hacking": view = HackingMinigame(self.user_id, difficulty, callback)
        elif game_type == "vault": view = VaultMinigame(self.user_id, difficulty, callback)
        else: # Fallback
            view = HackingMinigame(self.user_id, difficulty, callback)

        await interaction.response.edit_message(content=f"🎯 **MISSION:** Complete the **{game_type.upper()}** challenge!", view=view)

    def make_callback(self, difficulty):
        async def heist_callback(interaction, success):
            target_data = HEIST_TARGETS[self.target]
            base_low, base_high = target_data[difficulty]
            
            if success:
                amount = random.randint(base_low, base_high)
                await db.update_balance(str(self.user_id), amount)
                embed = discord.Embed(title="💰 HEIST SUCCESSFUL", description=f"You looted **{amount:,}** {CURRENCY_NAME}!", color=0x2ECC71)
            else:
                loss = random.randint(10000, 50000)
                await db.update_balance(str(self.user_id), -loss)
                await db.set_cooldown(str(self.user_id), "jail", datetime.now() + timedelta(minutes=30))
                embed = discord.Embed(title="🚨 HEIST FAILED", description=f"Busted! Fined **{loss:,}** and jailed for 30m.", color=0xE74C3C)
            
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        return heist_callback

# ══════════════════════════════════════════════
#  ERROR HANDLING
# ══════════════════════════════════════════════

@bot.event
async def on_command_error(ctx: commands.Context, error):
    """Global error handler for commands."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🔒 You don't have permission to use that command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing argument: `{error.param.name}`. Use `{PREFIX}help {ctx.command}` for usage.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found. Make sure to mention them or use their exact name.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Bad argument: {error}. Please check your input.")
    elif isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)
        minutes, seconds = divmod(retry, 60)
        if minutes:
            await ctx.send(f"⏳ Please wait **{minutes}m {seconds}s** before using `{ctx.command}` again.")
        else:
            await ctx.send(f"⏳ Please wait **{seconds}s** before using `{ctx.command}` again.")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Silently ignore unknown commands
    else:
        print(f"  [ERROR] {type(error).__name__}: {error}")

# ══════════════════════════════════════════════
#  ECONOMY & CASINO SYSTEM
# ══════════════════════════════════════════════

@bot.command(name="balance", aliases=["bal", "money"])
async def balance_cmd(ctx: commands.Context, member: discord.Member = None):
    """Check your Paradoxal balance and bank storage."""
    member = member or ctx.author
    wallet = await db.get_balance(str(member.id))
    bank = await db.get_bank(str(member.id))
    inventory = await db.get_inventory(str(member.id))
    
    embed = discord.Embed(title=f"💰 Economy: {member.display_name}", color=0xF1C40F)
    embed.add_field(name="Wallet", value=f"**{wallet:,}** {CURRENCY_NAME}", inline=True)
    embed.add_field(name="Bank", value=f"**{bank:,}** {CURRENCY_NAME}", inline=True)
    embed.add_field(name="Total", value=f"**{wallet + bank:,}** {CURRENCY_NAME}", inline=False)
    
    if inventory:
        effects = []
        if "Lucky Coin" in inventory: effects.append("🍀 Luck +5%")
        if "Golden Clover" in inventory: effects.append("🍀 Luck +15%")
        if "Thief Kit" in inventory: effects.append("🧤 Steal +12%")
        if "Crime Mask" in inventory: effects.append("👺 Crime +15%")
        if "Shield" in inventory: effects.append("🛡️ Shielded (40%)")
        if "VIP Pass" in inventory: effects.append("💎 VIP (+75% Daily, +25% Work)")
        
        if effects:
            embed.add_field(name="Active Effects", value=", ".join(effects), inline=False)

    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Paradox Bot 💜 | Bank earns variable interest (-1% to 1.3% hourly)")
    await ctx.send(embed=embed)

async def send_bank_interest(ctx: commands.Context):
    user_id = str(ctx.author.id)
    bank = await db.get_bank(user_id)
    if bank <= 0:
        return await ctx.send("💤 Your bank is empty. Deposit some paradoxals first to start earning interest.")

    low_rate = -1.0
    high_rate = 1.3
    projected_low = int(bank * low_rate / 100)
    projected_high = int(bank * high_rate / 100)

    embed = discord.Embed(
        title="📈 Bank Interest Forecast",
        description=(
            f"Your current bank balance is **{bank:,}** {CURRENCY_NAME}.\n\n"
            f"Estimated hourly change: **{projected_low:,}** - **{projected_high:,}** {CURRENCY_NAME}.\n"
            "Interest varies from -1% to 1.3% each hour (20% chance negative)."
        ),
        color=0x2ECC71
    )
    embed.set_footer(text="Keep money in the bank to earn variable hourly interest.")
    await ctx.send(embed=embed)

@bot.group(name="bank", invoke_without_command=True)
async def bank_group(ctx: commands.Context):
    """Manage your bank. Usage: !bank deposit <amount> or !bank withdraw <amount>"""
    await ctx.send("❓ Usage: `!bank deposit <amount>` or `!bank withdraw <amount>`")

@bank_group.command(name="info", aliases=["growth", "increase"])
async def bank_info(ctx: commands.Context):
    """Show your bank amount and how much interest it may earn."""
    await send_bank_interest(ctx)

async def handle_overdue_loan(ctx_or_user, user_id, loan_data):
    """Handle overdue loan with fines and jail."""
    warnings = loan_data.get("warnings", 0)
    amount = loan_data["amount"]

    async def safe_send(msg):
        if hasattr(ctx_or_user, 'send'):
            try: await ctx_or_user.send(msg)
            except: pass
        else:
            user = bot.get_user(int(user_id))
            if user:
                try: await user.send(msg)
                except: pass

    if warnings == 0:
        fine = int(amount * 0.05)
        jail_time = 2
        warnings = 1
    elif warnings == 1:
        fine = int(amount * 0.12)
        jail_time = 4
        warnings = 2
    else:
        # Sell inventory
        inventory = await db.get_inventory(user_id)
        if inventory:
            item = inventory[0]  # Sell first item
            price = int(SHOP_ITEMS.get(item, {"price": 0})["price"] * 0.8)  # 20% cheaper
            await db.update_balance(user_id, price)
            await db.remove_item(user_id, item)
            await safe_send(f"⚠️ Your loan is overdue. Sold **{item}** for **{price:,}** {CURRENCY_NAME}.")
            return
        else:
            fine = int(amount * 0.2)  # Increase fine
            await db.update_balance(user_id, -fine)
            await safe_send(f"⚠️ Your loan is overdue. Fined **{fine:,}** {CURRENCY_NAME}.")

    await db.update_balance(user_id, -fine)
    await db.set_cooldown(user_id, "jail", datetime.now() + timedelta(hours=jail_time))
    loan_data["fines"] = loan_data.get("fines", 0) + fine
    loan_data["warnings"] = warnings
    await db.set_loan(user_id, loan_data)

    await safe_send(f"🚨 Loan overdue! Fined **{fine:,}** {CURRENCY_NAME} and jailed for **{jail_time}** hours.")


@tasks.loop(minutes=10)
async def check_loans_task():
    """Check for overdue loans every 10 minutes."""
    now = datetime.now()
    cursor = db.db.users.find({"loan": {"$exists": True}})
    async for user in cursor:
        loan_data = user.get("loan", {})
        due_date = loan_data.get("due_date")
        if due_date and now > due_date:
            user_id = user["_id"]
            await handle_overdue_loan(None, user_id, loan_data)

@bot.command(name="loan")
async def loan_cmd(ctx: commands.Context, amount: str):
    """Take a loan from the bank. Max 300k. Pay back by depositing to cover negative bank."""
    user_id = str(ctx.author.id)
    loan_data = await db.get_loan(user_id)
    if loan_data:
        due_date = loan_data.get("due_date")
        if due_date and datetime.now() < due_date:
            return await ctx.send("❌ You already have an active loan. Pay it back first.")
        else:
            # Overdue, handle fines
            await handle_overdue_loan(ctx, user_id, loan_data)
            return

    try:
        amount = int(amount)
    except ValueError:
        return await ctx.send("❌ Please enter a valid loan amount.")

    if amount <= 0 or amount > 300000:
        return await ctx.send("❌ Loan amount must be between 1 and 300,000.")

    # Add to wallet, subtract from bank (making it negative)
    await db.update_balance(user_id, amount)
    await db.update_bank(user_id, -amount)

    due_date = datetime.now() + timedelta(hours=24)
    loan_data = {
        "amount": amount,
        "due_date": due_date,
        "fines": 0,
        "warnings": 0
    }
    await db.set_loan(user_id, loan_data)

    await ctx.send(f"✅ You took a loan of **{amount:,}** {CURRENCY_NAME}. Pay it back within 24 hours by depositing to your bank to cover the negative balance.")

@bot.command(name="payloan")
async def pay_loan_cmd(ctx: commands.Context):
    """Pay off your loan by covering the negative bank balance."""
    user_id = str(ctx.author.id)
    loan_data = await db.get_loan(user_id)
    if not loan_data:
        return await ctx.send("❌ You have no active loan.")

    bank = await db.get_bank(user_id)
    if bank >= 0:
        await db.clear_loan(user_id)
        await ctx.send("✅ Your loan is paid off!")
    else:
        needed = -bank
        await ctx.send(f"❌ You need to deposit **{needed:,}** more {CURRENCY_NAME} to pay off your loan.")

@bank_group.command(name="deposit", aliases=["dep"])
async def bank_deposit(ctx: commands.Context, amount: str):
    user_id = str(ctx.author.id)
    wallet = await db.get_balance(user_id)
    
    if amount.lower() == "all":
        amount = wallet
    else:
        try: amount = int(amount)
        except: return await ctx.send("❌ Please provide a valid number.")

    if amount <= 0 or amount > wallet:
        return await ctx.send("❌ Invalid amount.")

    await db.update_balance(user_id, -amount)
    await db.update_bank(user_id, amount)
    await ctx.send(f"🏦 Deposited **{amount:,}** {CURRENCY_NAME} into your bank!")

@bank_group.command(name="withdraw", aliases=["with"])
async def bank_withdraw(ctx: commands.Context, amount: str):
    user_id = str(ctx.author.id)
    bank = await db.get_bank(user_id)
    
    if amount.lower() == "all":
        amount = bank
    else:
        try: amount = int(amount)
        except: return await ctx.send("❌ Please provide a valid number.")

    if amount <= 0 or amount > bank:
        return await ctx.send("❌ Invalid amount.")

    await db.update_bank(user_id, -amount)
    await db.update_balance(user_id, amount)
    await ctx.send(f"🏦 Withdrew **{amount:,}** {CURRENCY_NAME} from your bank!")

@bot.command(name="shop")
async def shop_cmd(ctx: commands.Context):
    """Browse shop items available for purchase."""
    embed = discord.Embed(title="🛒 Paradox Shop", color=0x2ECC71)
    embed.description = "Use `!buy <item name>` to purchase an item. Example: `!buy Lucky Coin`"
    for name, data in SHOP_ITEMS.items():
        embed.add_field(name=f"{name} — {data['price']:,} {CURRENCY_NAME}", value=data["desc"], inline=False)
    embed.set_footer(text="Some items are unique and cannot be purchased more than once.")
    await ctx.send(embed=embed)

@bot.command(name="buy")
async def buy_cmd(ctx: commands.Context, *, item_name: str):
    """Purchase a shop item for your economy inventory."""
    search = next((name for name in SHOP_ITEMS if name.lower() == item_name.strip().lower()), None)
    if not search:
        return await ctx.send("❌ Item not found. Use `!shop` to see available items.")

    user_id = str(ctx.author.id)
    inventory = await db.get_inventory(user_id)
    if search in inventory:
        return await ctx.send(f"❌ You already own **{search}**.")

    price = SHOP_ITEMS[search]["price"]
    wallet = await db.get_balance(user_id)
    if wallet < price:
        return await ctx.send(f"❌ You need **{price - wallet:,}** more {CURRENCY_NAME} to buy **{search}**.")

    await db.update_balance(user_id, -price)
    await db.add_item(user_id, search)
    await ctx.send(f"✅ You purchased **{search}** for **{price:,}** {CURRENCY_NAME}!")

@bot.command(name="inventory", aliases=["inv"])
async def inventory_cmd(ctx: commands.Context, member: discord.Member = None):
    """View your owned shop items and active effects."""
    member = member or ctx.author
    inventory = await db.get_inventory(str(member.id))
    if not inventory:
        return await ctx.send(f"{member.display_name} has no items in their inventory.")

    item_counts = {}
    for item in inventory:
        item_counts[item] = item_counts.get(item, 0) + 1

    embed = discord.Embed(title=f"🧾 {member.display_name}'s Inventory", color=0x9B59B6)
    for item, count in item_counts.items():
        value = f"Quantity: {count}" if count > 1 else "Owned"
        embed.add_field(name=item, value=value, inline=False)
    embed.set_footer(text="Use !shop to browse items and !buy <item> to purchase.")
    await ctx.send(embed=embed)

@bot.command(name="bj", aliases=["blackjack"])
async def bj_cmd(ctx: commands.Context, amount: str):
    """Start a round of blackjack with an interactive button interface."""
    user_id = str(ctx.author.id)
    cfg = load_config()
    last_gamble = await db.get_cooldown(user_id, "casino")
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd = cds.get("casino", 5)
    if last_gamble and datetime.now() < last_gamble + timedelta(seconds=cd):
        rem = (last_gamble + timedelta(seconds=cd)) - datetime.now()
        return await ctx.send(f"⏳ Don't go broke too fast! Wait **{int(rem.total_seconds())}s**.")

    balance = await db.get_balance(user_id)
    if amount.lower() == "all":
        bet = balance
    else:
        try:
            bet = int(amount)
        except ValueError:
            return await ctx.send("❌ Please enter a valid bet amount or `all`.")

    if bet <= 0:
        return await ctx.send("❌ Bet must be greater than zero.")
    if bet > balance:
        return await ctx.send(f"❌ You do not have enough {CURRENCY_NAME}. Your wallet has {balance:,}.")

    inventory = await db.get_inventory(user_id)
    win_chance = await RiggedOdds.calculate_win_chance("bj", inventory)
    
    # Logic for rigged blackjack (dealer gets slightly better cards if luck is low)
    # This is handled inside BlackjackView, but we pass the win_chance
    await db.update_balance(user_id, -bet)
    view = BlackjackView(ctx, ctx.author.id, bet)
    view.win_chance = win_chance # Pass win_chance to view
    embed = view.create_embed()
    await db.set_cooldown(user_id, "casino", datetime.now())
    await ctx.send(embed=embed, view=view)

@bot.command(name="daily")
async def daily_cmd(ctx: commands.Context):
    """Claim your daily paradoxals."""
    user_id = str(ctx.author.id)
    cfg = load_config()
    last_claim = await db.get_cooldown(user_id, "daily")
    
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd_seconds = cds.get("daily", 86400)
    if last_claim and datetime.now() < last_claim + timedelta(seconds=cd_seconds):
        rem = (last_claim + timedelta(seconds=cd_seconds)) - datetime.now()
        hours, remainder = divmod(int(rem.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return await ctx.send(f"❌ You already claimed your daily! Try again in **{hours}h {minutes}m {seconds}s**.")

    # Buffed amount: 15,000 - 35,000
    amount = random.randint(15000, 35000)
    inventory = await db.get_inventory(user_id)
    if "VIP Pass" in inventory: amount = int(amount * 1.75)
        
    await db.update_balance(user_id, amount)
    await db.set_cooldown(user_id, "daily", datetime.now())
    await db.update_quest_progress(user_id, "daily")
    await ctx.send(f"🎁 You claimed your daily reward of **{amount:,}** {CURRENCY_NAME}!")

@bot.command(name="work")
async def work_cmd(ctx: commands.Context):
    """Work to earn some paradoxals safely."""
    user_id = str(ctx.author.id)
    cfg = load_config()
    last_work = await db.get_cooldown(user_id, "work")
    
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd = cds.get("work", 300)
    if last_work and datetime.now() < last_work + timedelta(seconds=cd):
        rem = (last_work + timedelta(seconds=cd)) - datetime.now()
        return await ctx.send(f"⏳ You are tired! Rest for **{int(rem.total_seconds())}s**.")

    amount = random.randint(1000, 5000)
    inventory = await db.get_inventory(user_id)
    if "VIP Pass" in inventory: amount = int(amount * 1.25)
    
    jobs = ["Quantum Developer", "Void Designer", "Reality Scripter", "Timeline Moderator", "Paradox Artist"]
    job = random.choice(jobs)
    
    await db.update_balance(user_id, amount)
    await db.set_cooldown(user_id, "work", datetime.now())
    await db.update_quest_progress(user_id, "work")
    await ctx.send(f"💼 You worked as a **{job}** and earned **{amount:,}** {CURRENCY_NAME}!")

@bot.command(name="give", aliases=["pay", "transfer"])
async def give_cmd(ctx: commands.Context, member: discord.Member, amount: str):
    """Transfer paradoxy to another user. Usage: !give @user <amount>"""
    if member.id == ctx.author.id:
        return await ctx.send("❌ You can't give money to yourself!")
    if member.bot:
        return await ctx.send("❌ You can't give money to bots!")

    user_id = str(ctx.author.id)
    wallet = await db.get_balance(user_id)

    if amount.lower() == "all":
        amount = wallet
    else:
        try: amount = int(amount)
        except: return await ctx.send("❌ Please provide a valid number.")

    if amount <= 0:
        return await ctx.send("❌ Amount must be positive.")
    if amount > wallet:
        return await ctx.send("❌ You don't have enough paradoxy in your wallet!")

    await db.update_balance(user_id, -amount)
    await db.update_balance(str(member.id), amount)
    
    embed = discord.Embed(
        description=f"💸 {ctx.author.mention} transferred **{amount:,}** {CURRENCY_NAME} to {member.mention}!",
        color=0x2ECC71
    )
    await ctx.send(embed=embed)

@bot.command(name="cf", aliases=["coinflip", "flip"])
async def coinflip_cmd(ctx: commands.Context, bet: str, choice: str = "heads"):
    """Flip a coin with rigged house odds. Usage: !cf <bet> [heads/tails]"""
    user_id = str(ctx.author.id)
    cfg = load_config()
    last_gamble = await db.get_cooldown(user_id, "casino")
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd = cds.get("casino", 5)
    if last_gamble and datetime.now() < last_gamble + timedelta(seconds=cd):
        rem = (last_gamble + timedelta(seconds=cd)) - datetime.now()
        return await ctx.send(f"⏳ Don't go broke too fast! Wait **{int(rem.total_seconds())}s**.")

    balance = await db.get_balance(user_id)

    if bet.lower() == "all": bet_amount = balance
    else:
        try: bet_amount = int(bet)
        except: return await ctx.send("❌ Valid bet amount required.")

    if bet_amount <= 0 or bet_amount > balance:
        return await ctx.send("❌ Invalid bet amount.")

    choice = choice.lower()
    if choice not in ["heads", "tails", "h", "t"]:
        return await ctx.send("❌ Choose heads or tails.")
    
    msg = await ctx.send("🪙 **Flipping...**")
    for _ in range(2):
        await asyncio.sleep(0.8)
        await msg.edit(content="📀 **Flipping...** (Tails)")
        await asyncio.sleep(0.8)
        await msg.edit(content="🪙 **Flipping...** (Heads)")
    await asyncio.sleep(0.8)

    inventory = await db.get_inventory(user_id)
    win_chance = await RiggedOdds.calculate_win_chance("cf", inventory)
    
    win = random.random() < win_chance
    result = choice if win else ("tails" if choice in ["heads", "h"] else "heads")
    if result == "h": result = "heads"
    if result == "t": result = "tails"

    await db.update_quest_progress(user_id, "gamble")
    await db.set_cooldown(user_id, "casino", datetime.now())
    if win:
        await db.update_balance(user_id, bet_amount)
        await msg.edit(content=None, embed=discord.Embed(title="🪙 Coinflip WIN", description=f"Landed on **{result.upper()}**!\n🎉 Won **{bet_amount:,}** {CURRENCY_NAME}!", color=0x2ECC71))
    else:
        await db.update_balance(user_id, -bet_amount)
        await msg.edit(content=None, embed=discord.Embed(title="🪙 Coinflip LOSE", description=f"Landed on **{result.upper()}**!\n💀 Lost **{bet_amount:,}** {CURRENCY_NAME}.", color=0xE74C3C))

@bot.command(name="leaderboard", aliases=["lb", "rich", "top"])
async def leaderboard_cmd(ctx: commands.Context):
    """View the richest users in the server."""
    lb_data = await db.get_leaderboard(10)
    if not lb_data:
        return await ctx.send("ℹ️ No wealth data available yet.")

    embed = discord.Embed(title=f"🏆 Wealth Leaderboard | {ctx.guild.name}", color=0xF1C40F)
    
    desc = ""
    for i, user_doc in enumerate(lb_data, 1):
        user_id = int(user_doc["_id"])
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        name = user.name if user else f"ID: {user_id}"
        total = user_doc["total"]
        desc += f"{i}. **{name}** — {total:,} {CURRENCY_NAME}\n"
    
    embed.description = desc
    embed.set_footer(text="Total wealth = Wallet + Bank")
    await ctx.send(embed=embed)

@bot.command(name="allow")
@commands.has_permissions(administrator=True)
async def allow_cmd(ctx: commands.Context, member: discord.Member, *, command: str):
    """Allow a user to use a hidden command. Admin only."""
    await ctx.send("❌ No commands currently available to allow.")

# ── GAMBLING GAMES ────────────────────────────

@bot.command(name="slots")
async def slots_cmd(ctx: commands.Context, bet: int):
    """Rigged slot machine with inventory luck modifiers."""
    user_id = str(ctx.author.id)
    cfg = load_config()
    last_gamble = await db.get_cooldown(user_id, "casino")
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd = cds.get("casino", 5)
    if last_gamble and datetime.now() < last_gamble + timedelta(seconds=cd):
        rem = (last_gamble + timedelta(seconds=cd)) - datetime.now()
        return await ctx.send(f"⏳ Don't go broke too fast! Wait **{int(rem.total_seconds())}s**.")

    balance = await db.get_balance(user_id)
    if bet <= 0 or bet > balance: return await ctx.send("❌ Invalid bet.")
    
    symbols = ["🍒", "🍋", "🍇", "💎", "⭐", "🔔"]
    msg = await ctx.send("🎰 **Spinning...**\n**[ ❓ | ❓ | ❓ ]**")
    
    inventory = await db.get_inventory(user_id)
    win_chance = await RiggedOdds.calculate_win_chance("slots_normal", inventory)
    
    win = random.random() < win_chance
    
    if win:
        res = [random.choice(symbols)] * 3
        payout = bet * 5
        await db.update_balance(user_id, payout)
        emb = discord.Embed(title="🎰 Slots WIN", description=f"**[ {' | '.join(res)} ]**\n🎉 Won **{payout:,}** {CURRENCY_NAME}!", color=0x2ECC71)
    else:
        res = [random.choice(symbols) for _ in range(3)]
        while res[0] == res[1] == res[2]: res = [random.choice(symbols) for _ in range(3)]
        await db.update_balance(user_id, -bet)
        emb = discord.Embed(title="🎰 Slots LOSE", description=f"**[ {' | '.join(res)} ]**\n💀 Lost **{bet:,}** {CURRENCY_NAME}.", color=0xE74C3C)
        
    await asyncio.sleep(1.0)
    await msg.edit(content=f"🎰 **Spinning...**\n**[ {res[0]} | ❓ | ❓ ]**")
    await asyncio.sleep(1.0)
    await msg.edit(content=f"🎰 **Spinning...**\n**[ {res[0]} | {res[1]} | ❓ ]**")
    await asyncio.sleep(1.0)
        
    await db.update_quest_progress(user_id, "gamble")
    await db.set_cooldown(user_id, "casino", datetime.now())
    await msg.edit(content=None, embed=emb)

@bot.command(name="roulette")
async def roulette_cmd(ctx: commands.Context, bet: int, choice: str):
    """Rigged roulette with inventory luck modifiers."""
    user_id = str(ctx.author.id)
    cfg = load_config()
    last_gamble = await db.get_cooldown(user_id, "casino")
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd = cds.get("casino", 5)
    if last_gamble and datetime.now() < last_gamble + timedelta(seconds=cd):
        rem = (last_gamble + timedelta(seconds=cd)) - datetime.now()
        return await ctx.send(f"⏳ Don't go broke too fast! Wait **{int(rem.total_seconds())}s**.")

    balance = await db.get_balance(user_id)
    if bet <= 0 or bet > balance: return await ctx.send("❌ Invalid bet.")
    
    inventory = await db.get_inventory(user_id)
    win_chance = await RiggedOdds.calculate_win_chance("roulette_red", inventory)
    
    msg = await ctx.send("🎡 **Spinning...**")
    await asyncio.sleep(2)
    
    win = random.random() < win_chance
    reds = [1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36]
    
    if win:
        if choice.lower() == "red": res_num = random.choice(reds)
        elif choice.lower() == "black": res_num = random.choice([n for n in range(1, 37) if n not in reds])
        else: res_num = random.choice([n for n in range(0, 37)]) # Catch-all for numbers/green
    else:
        res_num = random.randint(0, 36)
        # Ensure it's NOT a win if it was rigged to lose
        is_red = res_num in reds
        if choice.lower() == "red" and is_red: res_num = 2 # Force black
        elif choice.lower() == "black" and not is_red and res_num != 0: res_num = 1 # Force red

    color = "GREEN" if res_num == 0 else ("RED" if res_num in reds else "BLACK")
    
    actually_won = False
    if choice.lower() == "red" and res_num in reds: actually_won = True
    elif choice.lower() == "black" and res_num != 0 and res_num not in reds: actually_won = True
    elif choice.lower() == "green" and res_num == 0: actually_won = True
    elif choice.isdigit() and int(choice) == res_num: actually_won = True

    await db.update_quest_progress(user_id, "gamble")
    await db.set_cooldown(user_id, "casino", datetime.now())
    if actually_won:
        payout = bet * (35 if choice.lower() == "green" or choice.isdigit() else 2)
        await db.update_balance(user_id, payout - bet)
        emb = discord.Embed(title="🎡 Roulette WIN", description=f"Landed on **{color} {res_num}**!\n🎉 Won **{payout:,}** {CURRENCY_NAME}!", color=0x2ECC71)
    else:
        await db.update_balance(user_id, -bet)
        emb = discord.Embed(title="🎡 Roulette LOSE", description=f"Landed on **{color} {res_num}**!\n💀 Lost **{bet:,}** {CURRENCY_NAME}.", color=0xE74C3C)

    await msg.edit(content=None, embed=emb)
    color_name = "🟢 Green" if number == 0 else ("🔴 Red" if number in red_numbers else "⚫ Black")
    embed.description = f"The ball landed on: **{number} ({color_name})**\n\n"
    
    if win:
        await db.update_balance(user_id, payout)
        embed.description += f"🎉 **WIN!** You won **{payout:,}** {CURRENCY_NAME}!"
        embed.color = 0x2ECC71
    else:
        await db.update_balance(user_id, -bet)
        embed.description += f"💀 **LOSE!** You lost **{bet:,}** {CURRENCY_NAME}."
        embed.color = 0xE74C3C
        
    await roll_msg.edit(content=None, embed=embed)

# ── POKER SYSTEM ──────────────────────────────

# ── POKER SYSTEM ──────────────────────────────

active_poker_games = {}

def evaluate_poker_hand(hole_cards, community_cards):
    """Evaluate poker hand and return (rank, description)."""
    all_cards = hole_cards + community_cards
    ranks = []
    suits = []
    for card in all_cards:
        rank = card[:-1]
        suit = card[-1]
        if rank == 'A': ranks.append(14)
        elif rank == 'K': ranks.append(13)
        elif rank == 'Q': ranks.append(12)
        elif rank == 'J': ranks.append(11)
        else: ranks.append(int(rank))
        suits.append(suit)
    
    # Count frequencies
    rank_counts = {}
    suit_counts = {}
    for r, s in zip(ranks, suits):
        rank_counts[r] = rank_counts.get(r, 0) + 1
        suit_counts[s] = suit_counts.get(s, 0) + 1
    
    # Check for flush
    flush = any(count >= 5 for count in suit_counts.values())
    flush_suit = next((s for s, c in suit_counts.items() if c >= 5), None) if flush else None
    
    # Check for straight
    sorted_ranks = sorted(set(ranks), reverse=True)
    straight = False
    straight_high = 0
    for i in range(len(sorted_ranks) - 4):
        if sorted_ranks[i] - sorted_ranks[i+4] == 4:
            straight = True
            straight_high = sorted_ranks[i]
            break
    # Ace low straight
    if set([14, 2, 3, 4, 5]).issubset(set(ranks)):
        straight = True
        straight_high = 5
    
    # Royal flush
    if flush and straight and straight_high == 14 and all(r in ranks for r in [10,11,12,13,14]) and all(suits[i] == flush_suit for i, r in enumerate(ranks) if r in [10,11,12,13,14]):
        return (10, "Royal Flush 👑")
    
    # Straight flush
    if flush and straight:
        return (9, f"Straight Flush {straight_high} 🔥")
    
    # Four of a kind
    if 4 in rank_counts.values():
        quad = next(r for r, c in rank_counts.items() if c == 4)
        return (8, f"Four of a Kind 🔷")
    
    # Full house
    if 3 in rank_counts.values() and 2 in rank_counts.values():
        trips = next(r for r, c in rank_counts.items() if c == 3)
        pair = next(r for r, c in rank_counts.items() if c == 2)
        return (7, f"Full House 🏠")
    
    # Flush
    if flush:
        return (6, f"Flush 💧")
    
    # Straight
    if straight:
        return (5, f"Straight 〰️")
    
    # Three of a kind
    if 3 in rank_counts.values():
        trips = next(r for r, c in rank_counts.items() if c == 3)
        return (4, f"Three of a Kind 🎲")
    
    # Two pair
    pairs = [r for r, c in rank_counts.items() if c == 2]
    if len(pairs) >= 2:
        pairs.sort(reverse=True)
        return (3, f"Two Pair 👥")
    
    # One pair
    if 2 in rank_counts.values():
        pair = next(r for r, c in rank_counts.items() if c == 2)
        return (2, f"Pair 2️⃣")
    
    # High card
    high = max(ranks)
    return (1, f"High Card 🃏")

class PokerGame:
    def __init__(self, ctx, min_buyin, max_buyin):
        self.ctx = ctx
        self.min_buyin = min_buyin
        self.max_buyin = max_buyin
        self.players = {}  # user_id: {'buyin': amount, 'cards': [], 'folded': False, 'bet': 0, 'chips': amount, 'all_in': False}
        self.deck = self.create_deck()
        self.community_cards = []
        self.pot = 0
        self.current_bet = 0
        self.dealer_pos = 0
        self.current_player_index = 0
        self.round = 'waiting'
        self.message = None
        self.view = None
        self.last_raiser = None
        self.betting_round_complete = False

    def create_deck(self):
        suits = ['♠', '♥', '♦', '♣']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        deck = [f"{r}{s}" for s in suits for r in ranks]
        random.shuffle(deck)
        return deck

    def add_player(self, user_id, buyin):
        if user_id in self.players:
            return False
        if not (self.min_buyin <= buyin <= self.max_buyin):
            return False
        self.players[user_id] = {
            'buyin': buyin, 
            'cards': [], 
            'folded': False, 
            'bet': 0, 
            'chips': buyin, 
            'all_in': False
        }
        # Pot remains 0 at join; it builds from bets.
        return True

    def start_game(self):
        if len(self.players) < 2:
            return False
        for player in self.players.values():
            player['cards'] = [self.deck.pop(), self.deck.pop()]
        self.round = 'preflop'
        self.current_player_index = (self.dealer_pos + 1) % len(self.players)
        self.current_bet = 0
        self.last_raiser = None
        self.betting_round_complete = False
        self.acted_players = set()
        return True

    def get_current_player_id(self):
        player_ids = list(self.players.keys())
        return player_ids[self.current_player_index]

    def get_active_players_count(self):
        """Count players still in the game (not folded)"""
        return sum(1 for p in self.players.values() if not p['folded'])

    def next_player(self):
        active_count = self.get_active_players_count()
        if active_count <= 1:
            return
            
        self.current_player_index = (self.current_player_index + 1) % len(self.players)
        # Prevent infinite loop if something is wrong
        start_idx = self.current_player_index
        while self.players[self.get_current_player_id()]['folded'] or self.players[self.get_current_player_id()].get('all_in', False):
            self.current_player_index = (self.current_player_index + 1) % len(self.players)
            if self.current_player_index == start_idx:
                break

    def advance_round(self):
        if self.round == 'preflop':
            self.community_cards = [self.deck.pop() for _ in range(3)]
            self.round = 'flop'
        elif self.round == 'flop':
            self.community_cards.append(self.deck.pop())
            self.round = 'turn'
        elif self.round == 'turn':
            self.community_cards.append(self.deck.pop())
            self.round = 'river'
        elif self.round == 'river':
            self.round = 'showdown'
            return

        self.current_bet = 0
        self.last_raiser = None
        self.acted_players = set()
        for p in self.players.values():
            p['bet'] = 0
        
        # Start with player after dealer
        self.current_player_index = (self.dealer_pos + 1) % len(self.players)
        # Skip folded/all-in players
        while self.players[self.get_current_player_id()]['folded'] or self.players[self.get_current_player_id()].get('all_in', False):
            self.current_player_index = (self.current_player_index + 1) % len(self.players)
            if self.get_active_players_count() <= 1: break

    def check_betting_complete(self):
        """Check if all non-folded players have matched the current bet and acted"""
        active_ids = [uid for uid, p in self.players.items() if not p['folded'] and not p.get('all_in', False)]
        
        # If 1 or 0 active players who can still bet, betting is complete
        if len(active_ids) <= 1:
            return True
            
        # Everyone must have matched the current bet
        for uid in active_ids:
            p = self.players[uid]
            if p['bet'] != self.current_bet:
                return False
            if uid not in self.acted_players:
                return False
                
        return True

    def calculate_hand_strength(self, user_id):
        """Calculate win probability based on current hand"""
        player = self.players[user_id]
        hand = player['cards']
        
        rank, desc = evaluate_poker_hand(hand, self.community_cards)
        
        # Base strength calculation
        base_strength = (rank / 10) * 100
        
        if len(self.community_cards) == 0:
            # Preflop
            h1 = hand[0][:-1]
            h2 = hand[1][:-1]
            if h1 == h2:
                base_strength += 15  # Pocket pair boost
            rank_vals = {'A': 14, 'K': 13, 'Q': 12, 'J': 11}
            h1_val = rank_vals.get(h1, int(h1) if h1.isdigit() else 0)
            h2_val = rank_vals.get(h2, int(h2) if h2.isdigit() else 0)
            if h1_val >= 10 and h2_val >= 10:
                base_strength += 10
        elif len(self.community_cards) < 5:
            # Mid-hand: boost if showing strength
            if rank >= 5:
                base_strength += (rank - 4) * 5
        
        return min(95, max(5, base_strength))

    def get_winners(self):
        active_players = {uid: p for uid, p in self.players.items() if not p['folded']}
        if not active_players:
            return []
        hands = {}
        for uid, p in active_players.items():
            hands[uid] = evaluate_poker_hand(p['cards'], self.community_cards)
        max_rank = max(h[0] for h in hands.values())
        winners = [uid for uid, h in hands.items() if h[0] == max_rank]
        return winners, hands

    async def update_embed(self):
        # We don't stop the view if we are just editing, unless we want to refresh buttons.
        # However, it's cleaner to keep the view active.
        
        embed = discord.Embed(title="🃏 Paradox Poker Table", color=0x2ECC71)
        embed.add_field(name="Round Phase", value=f"**{self.round.upper()}**", inline=True)
        embed.add_field(name="Current Pot", value=f"**{self.pot:,}** {CURRENCY_NAME}", inline=True)
        embed.add_field(name="Bet to Call", value=f"**{self.current_bet:,}** {CURRENCY_NAME}", inline=True)
        
        if self.community_cards:
            embed.add_field(name="Community Cards", value=" ".join(self.community_cards), inline=False)
        
        players_text = ""
        for uid, p in self.players.items():
            turn_marker = "➡️ " if str(uid) == self.get_current_player_id() and self.round != 'waiting' else ""
            if p['folded']:
                status = "❌ Folded"
            elif p.get('all_in', False):
                status = f"🔴 **All-In** ({p['bet']:,})"
            else:
                status = f"Stack: {p['chips']:,} | Bet: {p['bet']:,}"
            players_text += f"{turn_marker}<@{uid}>: {status}\n"
        embed.add_field(name="Players", value=players_text or "No players joined.", inline=False)
        
        active_count = self.get_active_players_count()
        if active_count > 1 and self.round != 'showdown' and self.round != 'waiting':
            current_player = self.get_current_player_id()
            embed.add_field(name="⏸️ Current Turn", value=f"<@{current_player}>", inline=False)
        
        if not self.view:
            self.view = PokerView(self)

        try:
            # Use edit instead of delete/send to keep table history clean
            await self.message.edit(embed=embed, view=self.view)
        except Exception as e:
            # Fallback if message was deleted
            self.message = await self.ctx.send(embed=embed, view=self.view)

    def stop(self):
        self.view.stop()

class PokerView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=120)
        self.game = game

    async def on_timeout(self):
        """Automatically fold the current player if they take too long."""
        try:
            uid = self.game.get_current_player_id()
            if uid and uid in self.game.players:
                # If already folded, ignore
                if self.game.players[uid]['folded']:
                    return

                self.game.players[uid]['folded'] = True
                
                # Check if game should end
                if self.game.get_active_players_count() <= 1:
                    await self.end_game_early(None)
                else:
                    self.game.next_player()
                    await self.game.update_embed()
        except Exception as e:
            print(f"Poker timeout error: {e}")

    @discord.ui.button(label="Fold", style=discord.ButtonStyle.red, emoji="🚫")
    async def fold_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_action(interaction, 'fold')

    @discord.ui.button(label="Check/Call", style=discord.ButtonStyle.blurple, emoji="✅")
    async def call_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_action(interaction, 'call')

    async def handle_action(self, interaction, action):
        user_id = str(interaction.user.id)
        if user_id != self.game.get_current_player_id():
            return await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
        
        player = self.game.players[user_id]
        self.game.acted_players.add(user_id)
        
        if action == 'fold':
            player['folded'] = True
            await interaction.response.defer()
        elif action == 'call':
            call_amount = min(self.game.current_bet - player['bet'], player['chips'])
            
            if call_amount > 0:
                player['bet'] += call_amount
                player['chips'] -= call_amount
                self.game.pot += call_amount
                
                if player['chips'] == 0:
                    player['all_in'] = True
                
                await interaction.response.defer()
            else:
                await interaction.response.defer()
        
        if self.game.get_active_players_count() == 1:
            await self.end_game_early(interaction)
            return
            
        if self.game.check_betting_complete():
            self.game.advance_round()
            if self.game.round == 'showdown':
                await self.finish_game(interaction)
            else:
                await self.game.update_embed()
        else:
            self.game.next_player()
            await self.game.update_embed()

    @discord.ui.button(label="Raise", style=discord.ButtonStyle.success, emoji="📈")
    async def raise_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        if user_id != self.game.get_current_player_id():
            return await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
        
        modal = RaiseModal(self.game, interaction.user)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Check Hand", style=discord.ButtonStyle.secondary, emoji="🤔")
    async def check_hand(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        if user_id not in self.game.players:
            return await interaction.response.send_message("❌ You're not in this game!", ephemeral=True)
        
        player = self.game.players[user_id]
        rank, desc = evaluate_poker_hand(player['cards'], self.game.community_cards)
        strength = self.game.calculate_hand_strength(user_id)
        
        # Show initial analyzing message
        await interaction.response.send_message(embed=discord.Embed(title="🤔 Analyzing Your Hand...", description="⏳ Computing hand strength...", color=0x3498DB), ephemeral=True)
        
        await asyncio.sleep(1.5)
        
        # Show results
        result_embed = discord.Embed(title="🃏 Your Hand Analysis", color=0x9B59B6)
        result_embed.add_field(name="Your Cards", value=f"{player['cards'][0]} {player['cards'][1]}", inline=False)
        
        if self.game.community_cards:
            result_embed.add_field(name="Community Cards", value=" ".join(self.game.community_cards), inline=False)
        
        result_embed.add_field(name="Hand", value=f"**{desc}** {rank}/10 strength", inline=False)
        
        # Strength visualization
        strength_bar = "█" * int(strength / 10) + "░" * (10 - int(strength / 10))
        result_embed.add_field(name="Win Probability", value=f"`{strength_bar}` **{strength:.0f}%**", inline=False)
        
        # Strategy tip
        if strength > 70: tip = "🟢 Strong hand - Consider raising!"
        elif strength > 50: tip = "🟡 Medium hand - Good position to call"
        elif strength > 30: tip = "🟠 Weak hand - Be cautious"
        else: tip = "🔴 Very weak hand - Fold might be best"
        result_embed.add_field(name="Suggestion", value=tip, inline=False)
        
        result_embed.set_footer(text="This is a rough estimate based on visible cards")
        await interaction.edit_original_response(embed=result_embed)

    async def end_game_early(self, interaction: discord.Interaction = None):
        """End game when only 1 player remains"""
        self.game.stop()
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
            
        winner = None
        for uid, p in self.game.players.items():
            if not p['folded']:
                winner = uid
                break
        
        if winner:
            await db.update_balance(winner, self.game.pot)
            embed = discord.Embed(
                title="🏆 Game Over - Winner!",
                description=f"<@{winner}> won **{self.game.pot:,}** 💰\n\nAll other players folded!",
                color=0x2ECC71
            )
            try:
                await self.game.message.edit(embed=embed, view=None)
            except:
                await self.game.ctx.send(embed=embed)
                
            if self.game.ctx.channel.id in active_poker_games:
                del active_poker_games[self.game.ctx.channel.id]

    async def finish_game(self, interaction: discord.Interaction):
        """Distribute pot at showdown"""
        self.game.stop()
        for item in self.children:
            item.disabled = True
        
        # Show all community cards
        await self.game.message.edit(view=self)
        
        # Animate dealer thinking
        await interaction.channel.send("🤔 **The dealer is comparing hands...**")
        await asyncio.sleep(2)
        
        winners, hands = self.game.get_winners()
        
        if not winners:
            await interaction.channel.send("❌ Error determining winner")
            return
        
        # Split pot equally among winners
        pot_split = self.game.pot // len(winners)
        
        results_text = "```\n╔════════════════════════════════╗\n║       FINAL SHOWDOWN RESULTS   ║\n╚════════════════════════════════╝\n\n"
        
        for uid, (rank, desc) in hands.items():
            player = self.game.players[uid]
            user = bot.get_user(int(uid))
            name = user.name if user else uid
            status = "🏆 WINNER" if uid in winners else "❌ Loser"
            results_text += f"{status}: {name}\n  Hand: {desc}\n  Cards: {player['cards'][0]} {player['cards'][1]}\n\n"
        
        results_text += "```"
        
        # Award winners
        for winner in winners:
            await db.update_balance(winner, pot_split)
        
        results_text += f"\n💰 **Each winner receives: {pot_split:,} {CURRENCY_NAME}**"
        
        embed = discord.Embed(
            title="🃏 Poker Game Results",
            description=results_text,
            color=0x2ECC71 if len(winners) == 1 else 0x3498DB
        )
        embed.set_footer(text="Thanks for playing Paradox Poker!")
        
        await interaction.channel.send(embed=embed)
        if self.game.ctx.channel.id in active_poker_games:
            del active_poker_games[self.game.ctx.channel.id]

class RaiseModal(discord.ui.Modal, title="💰 Raise Amount"):
    amount_input = discord.ui.TextInput(
        label="Amount to Raise",
        placeholder="Enter the total bet amount",
        required=True
    )

    def __init__(self, game, user):
        super().__init__()
        self.game = game
        self.user = user

    async def on_submit(self, interaction: discord.Interaction):
        try:
            raise_amount = int(self.amount_input.value)
        except ValueError:
            return await interaction.response.send_message("❌ Please enter a valid number!", ephemeral=True)
        
        user_id = str(self.user.id)
        if user_id != self.game.get_current_player_id():
            return await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
        
        player = self.game.players[user_id]
        
        # Validate raise is higher than current bet
        if raise_amount <= self.game.current_bet:
            return await interaction.response.send_message(
                f"❌ Raise must be higher than current bet ({self.game.current_bet:,} 💰)",
                ephemeral=True
            )
        
        # Handle ALL-IN logic
        call_cost = self.game.current_bet - player['bet']
        total_cost = call_cost + (raise_amount - self.game.current_bet)
        
        if total_cost > player['chips']:
            if player['chips'] > 0:
                # Go all-in with what they have
                all_in_amount = player['chips']
                player['bet'] += all_in_amount
                player['chips'] = 0
                player['all_in'] = True
                self.game.pot += all_in_amount
                
                await interaction.response.send_message(
                    f"**{self.user.display_name}** went **All-In** with {all_in_amount:,} 💰 🔴",
                    ephemeral=False
                )
            else:
                return await interaction.response.send_message("❌ You have no chips left!", ephemeral=True)
        else:
            # Normal raise
            player['bet'] = raise_amount
            player['chips'] -= total_cost
            self.game.pot += total_cost
            self.game.current_bet = raise_amount
            self.game.last_raiser = user_id
            
            await interaction.response.send_message(
                f"**{self.user.display_name}** raised to {raise_amount:,} 💰",
                ephemeral=False
            )
        
        # Check if game should end
        if self.game.get_active_players_count() == 1:
            # Only need to recreate the view to get access to interaction
            view = PokerView(self.game)
            await view.end_game_early(interaction)
            return
        
        self.game.next_player()
        await self.game.update_embed()

@bot.group(name="poker", invoke_without_command=True)
@commands.cooldown(1, 5, commands.BucketType.user)
async def poker_cmd(ctx: commands.Context, min_buyin: int = None, max_buyin: int = None):
    """Start a poker game with buy-in limits or show poker usage."""
    if min_buyin is None or max_buyin is None:
        return await ctx.send(f"❓ Usage: `{PREFIX}poker <min> <max>` or `{PREFIX}poker join <amount>`.")
    if min_buyin > max_buyin or min_buyin <= 0:
        return await ctx.send("❌ Invalid buy-in limits.")

    channel_id = ctx.channel.id
    if channel_id in active_poker_games:
        return await ctx.send("❌ There's already a poker game in this channel.")

    game = PokerGame(ctx, min_buyin, max_buyin)
    active_poker_games[channel_id] = game
    embed = discord.Embed(
        title="🃏 Paradox Poker",
        description=(
            f"Buy-in: {min_buyin:,} - {max_buyin:,} {CURRENCY_NAME}\n\n"
            "Players: 0\n\n"
            "Use `!poker join <amount>` or `!pokerjoin <amount>` to join.\n"
            "Use `!poker start` or `!pokerstart` to start the game when ready."
        ),
        color=0x34495E
    )
    msg = await ctx.send(embed=embed)
    game.message = msg

@poker_cmd.command(name="join")
@commands.cooldown(1, 5, commands.BucketType.user)
async def poker_join_subcmd(ctx: commands.Context, amount: int):
    return await poker_join_handler(ctx, amount)

@bot.command(name="pokerjoin")
@commands.cooldown(1, 5, commands.BucketType.user)
async def poker_join_cmd(ctx: commands.Context, amount: int):
    return await poker_join_handler(ctx, amount)

async def poker_join_handler(ctx: commands.Context, amount: int):
    channel_id = ctx.channel.id
    if channel_id not in active_poker_games:
        return await ctx.send("❌ No active poker game in this channel.")
    game = active_poker_games[channel_id]
    user_id = str(ctx.author.id)
    if user_id in game.players:
        return await ctx.send("❌ You are already in the game.")
    balance = await db.get_balance(user_id)
    if amount < game.min_buyin or amount > game.max_buyin or amount > balance:
        return await ctx.send(f"❌ Buy-in must be between {game.min_buyin:,} and {game.max_buyin:,}, and you must have enough {CURRENCY_NAME}.")
    if game.add_player(user_id, amount):
        await db.update_balance(user_id, -amount)
        embed = game.message.embeds[0]
        embed.description = (
            f"Buy-in: {game.min_buyin:,} - {game.max_buyin:,} {CURRENCY_NAME}\n\n"
            f"Players: {len(game.players)}\n"
            + "\n".join([f"<@{uid}>" for uid in game.players])
            + "\n\nUse `!poker join <amount>` or `!pokerjoin <amount>` to join.\n"
            + "Use `!poker start` or `!pokerstart` to start the game when ready."
        )
        await game.message.edit(embed=embed)
        try:
            await ctx.message.delete()
        except: pass
    else:
        await ctx.send("❌ Failed to join.", delete_after=5)

@poker_cmd.command(name="start")
@commands.cooldown(1, 5, commands.BucketType.user)
async def poker_start_subcmd(ctx: commands.Context):
    return await poker_start_handler(ctx)

@bot.command(name="pokerstart")
@commands.cooldown(1, 5, commands.BucketType.user)
async def poker_start_cmd(ctx: commands.Context):
    return await poker_start_handler(ctx)

async def poker_start_handler(ctx: commands.Context):
    channel_id = ctx.channel.id
    if channel_id not in active_poker_games:
        return await ctx.send("❌ No active poker game in this channel.")
    game = active_poker_games[channel_id]
    if len(game.players) < 2:
        return await ctx.send("❌ Need at least 2 players to start.")
    if game.start_game():
        await game.update_embed()
    else:
        await ctx.send("❌ Failed to start game.")

@bot.command(name="pokerforfeit", aliases=["pokerleave", "pforfeit"])
@commands.cooldown(1, 5, commands.BucketType.user)
async def poker_forfeit_cmd(ctx: commands.Context):
    """Leave the poker game and forfeit your chips to the pot."""
    channel_id = ctx.channel.id
    if channel_id not in active_poker_games:
        return await ctx.send("❌ No active poker game in this channel.")
    
    game = active_poker_games[channel_id]
    user_id = str(ctx.author.id)
    if user_id not in game.players:
        return await ctx.send("❌ You are not in the game.")
    
    player = game.players[user_id]
    if player['folded']:
        return await ctx.send("❌ You already folded.")

    player['folded'] = True
    game.pot += player['chips']
    player['chips'] = 0
    
    await ctx.send(f"🏳️ **{ctx.author.display_name}** has forfeited! Their chips were added to the pot.")
    
    if game.get_active_players_count() <= 1:
        # End game if only 1 left
        # We need to find the view or create one to end
        if not game.view:
            game.view = PokerView(game)
        await game.view.end_game_early(None)
    else:
        if str(ctx.author.id) == game.get_current_player_id():
            game.next_player()
        await game.update_embed()

# ── BLACKJACK SYSTEM ──────────────────────────

# ── BLACKJACK SYSTEM ──────────────────────────

class BlackjackView(discord.ui.View):
    def __init__(self, ctx, user_id, bet):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.user_id = user_id
        self.initial_bet = bet
        self.deck = self.create_deck()
        
        # Support for multiple hands (splitting)
        self.hands = [[self.deck.pop(), self.deck.pop()]]
        self.current_hand_index = 0
        self.dealer_hand = [self.deck.pop(), self.deck.pop()]
        self.bets = [bet]
        self.is_over = False

    def create_deck(self):
        suits = ["♠", "♥", "♦", "♣"]
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
        deck = [f"{r}{s}" for s in suits for r in ranks]
        random.shuffle(deck)
        return deck

    def get_score(self, hand):
        score = 0
        aces = 0
        for card in hand:
            rank = card[:-1]
            if rank in ["J", "Q", "K"]: score += 10
            elif rank == "A": aces += 1; score += 11
            else: score += int(rank)
        while score > 21 and aces:
            score -= 10
            aces -= 1
        return score

    async def on_timeout(self):
        """Handle inactivity by making the user lose and potentially go to jail."""
        if not self.is_over:
            self.is_over = True
            self.stop()
            
            # Loss due to inactivity
            total_bet = sum(self.bets)
            jail_time = 15 # 15 minutes jail for inactivity
            await db.update_balance(str(self.user_id), -total_bet)
            await db.set_cooldown(str(self.user_id), "jail", datetime.now() + timedelta(minutes=jail_time))
            
            embed = discord.Embed(title="⏰ Blackjack Timeout", color=0xE74C3C)
            embed.description = f"You took too long to act! Lost **{total_bet:,}** {CURRENCY_NAME} and jailed for **{jail_time}m**."
            
            try:
                # Try to edit the message to show the timeout
                await self.ctx.channel.send(content=f"<@{self.user_id}>", embed=embed)
            except: pass

    def can_split(self):
        if len(self.hands) >= 2: return False
        hand = self.hands[self.current_hand_index]
        if len(hand) != 2: return False
        v1 = self.get_card_value(hand[0])
        v2 = self.get_card_value(hand[1])
        return v1 == v2

    def get_card_value(self, card):
        rank = card[:-1]
        if rank in ["J", "Q", "K"]: return 10
        if rank == "A": return 11
        return int(rank)

    def create_embed(self, revealed=False):
        embed = discord.Embed(title="🃏 Paradox Blackjack", color=0x34495E)
        
        # Dealer Section
        d_cards = " ".join(self.dealer_hand) if revealed else f"{self.dealer_hand[0]} ❓"
        d_score = self.get_score(self.dealer_hand) if revealed else "?"
        embed.add_field(name=f"Dealer: {d_score}", value=f"`{d_cards}`", inline=False)

        # Player Hands Section
        for i, hand in enumerate(self.hands):
            prefix = "▶️ " if i == self.current_hand_index and not revealed else ""
            status = f" (Hand {i+1})" if len(self.hands) > 1 else ""
            score = self.get_score(hand)
            embed.add_field(name=f"{prefix}You{status}: {score}", value=f"`{' '.join(hand)}`", inline=True)
            
        embed.set_footer(text=f"Total Bet: {sum(self.bets):,} {CURRENCY_NAME}")
        return embed

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        
        current_hand = self.hands[self.current_hand_index]
        current_hand.append(self.deck.pop())
        score = self.get_score(current_hand)

        # Disable Split/Double after hitting
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label in ["Split", "Double Down"]:
                item.disabled = True

        if score > 21:
            await self.next_hand_or_finish(interaction)
        else:
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.success)
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        
        balance = await db.get_balance(str(self.user_id))
        bet_to_add = self.bets[self.current_hand_index]
        if balance < bet_to_add:
            return await interaction.response.send_message("❌ Not enough paradoxy to double!", ephemeral=True)
        
        await db.update_balance(str(self.user_id), -bet_to_add)
        self.bets[self.current_hand_index] *= 2
        self.hands[self.current_hand_index].append(self.deck.pop())
        
        await self.next_hand_or_finish(interaction)

    @discord.ui.button(label="Split", style=discord.ButtonStyle.secondary, emoji="✂️")
    async def split(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        if not self.can_split():
            return await interaction.response.send_message("❌ You cannot split this hand!", ephemeral=True)
            
        balance = await db.get_balance(str(self.user_id))
        if balance < self.initial_bet:
            return await interaction.response.send_message("❌ Not enough paradoxy to split!", ephemeral=True)
            
        await db.update_balance(str(self.user_id), -self.initial_bet)
        
        # Split logic
        old_hand = self.hands[self.current_hand_index]
        card1, card2 = old_hand[0], old_hand[1]
        
        self.hands[self.current_hand_index] = [card1, self.deck.pop()]
        self.hands.append([card2, self.deck.pop()])
        self.bets.append(self.initial_bet)
        
        button.disabled = True
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.danger)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return
        await self.next_hand_or_finish(interaction)

    async def next_hand_or_finish(self, interaction: discord.Interaction):
        # Re-enable Double Down for next hand if it's a split
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == "Double Down":
                item.disabled = False

        self.current_hand_index += 1
        if self.current_hand_index < len(self.hands):
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=self.create_embed(), view=self)
            else:
                await interaction.edit_original_response(embed=self.create_embed(), view=self)
        else:
            await self.finish_game(interaction)

    async def finish_game(self, interaction: discord.Interaction):
        self.is_over = True
        self.stop()

        if not interaction.response.is_done():
            await interaction.response.edit_message(content="⏳ **Dealer is thinking...**", view=None)
        else:
            await interaction.edit_original_response(content="⏳ **Dealer is thinking...**", view=None)

        await asyncio.sleep(1.5)

        while self.get_score(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())

        dealer_score = self.get_score(self.dealer_hand)
        results = []
        net_change = 0

        for index, hand in enumerate(self.hands, start=1):
            bet = self.bets[index - 1]
            player_score = self.get_score(hand)
            hand_name = f"Hand {index}" if len(self.hands) > 1 else "Your Hand"

            if player_score > 21 and dealer_score > 21:
                await db.update_balance(str(self.user_id), bet)
                results.append(f"**{hand_name}**: BOTH BUST with **{player_score}** vs Dealer **{dealer_score}**. Bet returned.")
            elif player_score > 21:
                results.append(f"**{hand_name}**: BUST with **{player_score}**. Lost **{bet:,}** {CURRENCY_NAME}.")
                net_change -= bet
            elif dealer_score > 21 or player_score > dealer_score:
                await db.update_balance(str(self.user_id), bet * 2)
                results.append(f"**{hand_name}**: WIN with **{player_score}** vs Dealer **{dealer_score}**. Gained **{bet:,}** {CURRENCY_NAME}.")
                net_change += bet
            elif player_score == dealer_score:
                await db.update_balance(str(self.user_id), bet)
                results.append(f"**{hand_name}**: PUSH with **{player_score}**. Bet returned.")
            else:
                results.append(f"**{hand_name}**: LOSE with **{player_score}** vs Dealer **{dealer_score}**. Lost **{bet:,}** {CURRENCY_NAME}.")
                net_change -= bet

        embed = discord.Embed(title="🃏 Blackjack Results", color=0x2ECC71 if net_change >= 0 else 0xE74C3C)
        embed.add_field(name=f"Dealer ({dealer_score})", value=f"`{' '.join(self.dealer_hand)}`", inline=False)
        embed.description = "\n".join(results)
        embed.set_footer(text=f"Net change: {'+' if net_change >= 0 else '-'}{abs(net_change):,} {CURRENCY_NAME}")

        if not interaction.response.is_done():
            await interaction.response.defer()
        
        # To keep it at the bottom, we delete the old message and send a new one
        try:
            await interaction.message.delete()
        except: pass
        
        await self.ctx.send(embed=embed)

# ── HEIST & JAIL SYSTEM ────────────────────────

class HeistTargetView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=30)
        self.ctx = ctx  
        self.user_id = ctx.author.id
        self.selection_made = False

    async def on_timeout(self):
        if not self.selection_made:
            user_id = str(self.user_id)
            jail_time = 10
            await db.set_cooldown(user_id, "jail", datetime.now() + timedelta(minutes=jail_time))
            try:
                await self.ctx.send(f"🚨 <@{self.user_id}>, you took too long to plan the heist! You've been spotted and jailed for **{jail_time}m**.")
            except: pass

    @discord.ui.button(label="Jewelry Store", style=discord.ButtonStyle.primary)
    async def jewelry(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.selection_made: return
        self.selection_made = True
        await self.choose_target(interaction, "jewelry")

    @discord.ui.button(label="Main Bank", style=discord.ButtonStyle.primary)
    async def bank(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.selection_made: return
        self.selection_made = True
        await self.choose_target(interaction, "bank")

    @discord.ui.button(label="Armored Truck", style=discord.ButtonStyle.primary)
    async def truck(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.selection_made: return
        self.selection_made = True
        await self.choose_target(interaction, "truck")

    async def choose_target(self, interaction: discord.Interaction, target: str):
        embed = discord.Embed(title="🏦 Strategic Heist", color=0x34495E)
        embed.description = f"You chose the **{HEIST_TARGETS[target]['name']}**. Now choose your difficulty. Easy is lower risk with extra attempts."
        view = CrimeDifficultyView(self.ctx, target)
        await interaction.response.edit_message(embed=embed, view=view)

class CrimeDifficultyView(discord.ui.View):
    def __init__(self, ctx, target: str):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.user_id = ctx.author.id
        self.target = target
        self.active_view = None
        self.is_over = False
        self.selection_made = False

    async def on_timeout(self):
        if not self.selection_made:
            user_id = str(self.user_id)
            jail_time = 15
            await db.set_cooldown(user_id, "jail", datetime.now() + timedelta(minutes=jail_time))
            try:
                await self.ctx.send(f"🚨 <@{self.user_id}>, hesitation is fatal! You've been caught while deciding and jailed for **{jail_time}m**.")
            except: pass

    @discord.ui.button(label="Easy", style=discord.ButtonStyle.success)
    async def easy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.selection_made:
            return
        self.selection_made = True
        await self.start_heist(interaction, "easy")

    @discord.ui.button(label="Normal", style=discord.ButtonStyle.primary)
    async def normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.selection_made:
            return
        self.selection_made = True
        await self.start_heist(interaction, "normal")

    @discord.ui.button(label="Hard", style=discord.ButtonStyle.danger)
    async def hard(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.selection_made:
            return
        self.selection_made = True
        await self.start_heist(interaction, "hard")

    async def start_heist(self, interaction: discord.Interaction, difficulty: str):
        """Start heist with interactive minigames."""
        minigame = random.choice(["lockpick", "circuit", "safe"])
        if minigame == "lockpick":
            await self.lockpick_minigame(interaction, difficulty)
        elif minigame == "circuit":
            await self.circuit_minigame(interaction, difficulty)
        else:
            await self.safe_minigame(interaction, difficulty)

    async def lockpick_minigame(self, interaction: discord.Interaction, difficulty: str):
        """Lockpick minigame - click correct zones."""
        zone_count = 3 if difficulty == "easy" else 4 if difficulty == "normal" else 5
        max_attempts = 3 if difficulty == "easy" else 2 if difficulty == "normal" else 1
        embed = discord.Embed(title="🔓 Lockpicking Challenge", color=0xF1C40F)
        embed.description = f"Click the correct zone among {zone_count}. You have {max_attempts} attempts."

        correct = random.randint(1, zone_count)
        attempts = [0]

        async def check_zone(inter, num: int):
            if inter.user.id != self.user_id:
                return await inter.response.send_message("❌ This isn't your heist challenge!", ephemeral=True)
            attempts[0] += 1
            if num == correct:
                await self.finish_heist(inter, difficulty, True)
            elif attempts[0] >= max_attempts:
                await self.finish_heist(inter, difficulty, False)
            else:
                await inter.response.send_message(f"❌ Wrong zone! {max_attempts - attempts[0]} attempts left.", ephemeral=True)

        view = discord.ui.View(timeout=30)
        self.active_view = view

        async def on_timeout():
            if not self.is_over:
                await self.finish_heist(None, difficulty, False)
        view.on_timeout = on_timeout

        for i in range(1, zone_count + 1):
            btn = discord.ui.Button(label=f"Zone {i}", style=discord.ButtonStyle.secondary)
            btn.callback = lambda inter, num=i: check_zone(inter, num)
            view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

    async def circuit_minigame(self, interaction: discord.Interaction, difficulty: str):
        """Memory-based circuit board minigame."""
        # Difficulty scales the sequence length
        num_colors = 3 if difficulty == "easy" else 4 if difficulty == "normal" else 5
        all_colors = ["Red", "Blue", "Green", "Yellow"]
        target_sequence = [random.choice(all_colors) for _ in range(num_colors)]
        
        embed = discord.Embed(title="⚡ Circuit Board: Memorize!", color=0x3498DB)
        embed.description = (
            f"**Memorize this sequence in 5 seconds!**\n\n"
            f"▶️ " + " ➡️ ".join([f"**{c}**" for c in target_sequence])
        )
        embed.set_footer(text="The wires will be hidden soon...")
        
        # Initial message showing the sequence
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        
        # Wait for memorization
        await asyncio.sleep(5)
        
        if self.is_over: return # In case they somehow finished or cancelled
        
        # Hide sequence and show buttons
        embed.title = "⚡ Circuit Board: Enter Sequence"
        embed.description = f"Enter the **{num_colors}** colors in the correct order!"
        embed.clear_fields()
        embed.set_footer(text="Timer: 15 seconds")
        
        user_sequence = []
        
        async def handle_color(inter, chosen_color: str):
            if inter.user.id != self.user_id:
                return await inter.response.send_message("❌ This isn't your heist challenge!", ephemeral=True)
            
            user_sequence.append(chosen_color)
            current_index = len(user_sequence) - 1
            
            if chosen_color != target_sequence[current_index]:
                # Failed a step
                await inter.response.send_message(f"❌ Short circuit! Wrong color. Sequence failed.", ephemeral=True)
                await self.finish_heist(inter, difficulty, False)
                return

            if len(user_sequence) == num_colors:
                # Completed sequence
                await self.finish_heist(inter, difficulty, True)
            else:
                # Correct so far - edit original message to show progress
                embed.description = f"Enter the **{num_colors}** colors in the correct order!\n\n✅ Progress: **{len(user_sequence)}/{num_colors}**"
                await inter.response.edit_message(embed=embed)

        view = discord.ui.View(timeout=15)
        self.active_view = view
        
        async def on_timeout():
            if not self.is_over:
                # Time's up - send to jail
                await self.finish_heist(None, difficulty, False)
        view.on_timeout = on_timeout

        for color in all_colors:
            btn = discord.ui.Button(label=color, style=discord.ButtonStyle.secondary)
            # Use lambda to capture color correctly
            btn.callback = lambda inter, c=color: handle_color(inter, c)
            view.add_item(btn)
        
        await msg.edit(embed=embed, view=view)

    async def safe_minigame(self, interaction: discord.Interaction, difficulty: str):
        """Safe cracking minigame - guess the dial number."""
        if difficulty == "easy":
            correct = random.randint(1, 5)
            range_text = "1-5"
            max_attempts = 3
        elif difficulty == "normal":
            correct = random.randint(1, 10)
            range_text = "1-10"
            max_attempts = 2
        else:
            correct = random.randint(1, 15)
            range_text = "1-15"
            max_attempts = 1

        embed = discord.Embed(title="🔐 Safe Cracking", color=0xE74C3C)
        embed.description = f"Guess the correct number on the dial. Range: {range_text}. You have {max_attempts} attempts."

        attempts = [0]

        async def handle_guess(inter, guess: int):
            if inter.user.id != self.user_id:
                return await inter.response.send_message("❌ This isn't your heist challenge!", ephemeral=True)
            attempts[0] += 1
            if guess == correct:
                await self.finish_heist(inter, difficulty, True)
            elif attempts[0] >= max_attempts:
                await inter.response.send_message("❌ Wrong number! No attempts left.", ephemeral=True)
                await self.finish_heist(inter, difficulty, False)
            else:
                await inter.response.send_message(f"❌ Wrong number! {max_attempts - attempts[0]} attempts left.", ephemeral=True)

        view = discord.ui.View(timeout=30)
        self.active_view = view

        async def on_timeout():
            if not self.is_over:
                await self.finish_heist(None, difficulty, False)
        view.on_timeout = on_timeout
        for number in range(1, int(range_text.split("-")[1]) + 1):
            btn = discord.ui.Button(label=str(number), style=discord.ButtonStyle.secondary)
            btn.callback = lambda inter, n=number: handle_guess(inter, n)
            view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

    async def finish_heist(self, interaction: discord.Interaction, difficulty: str, success: bool):
        """Complete heist and reward/fine player."""
        target_data = HEIST_TARGETS.get(self.target, HEIST_TARGETS["bank"])
        base_low, base_high = target_data[difficulty]

        if success:
            amount = random.randint(int(base_low), int(base_high))
            await db.update_balance(str(self.user_id), amount)
            embed = discord.Embed(
                title="💰 Heist Successful!",
                description=(f"You pulled off the {target_data['name']} heist and got away with **{amount:,}** {CURRENCY_NAME}!"),
                color=0x2ECC71
            )
        else:
            fine_base = {"easy": 5000, "normal": 15000, "hard": 40000}[difficulty]
            loss = random.randint(int(fine_base * 0.8), int(fine_base * 1.2))
            jail_time = {"easy": 10, "normal": 30, "hard": 60}[difficulty]
            await db.update_balance(str(self.user_id), -loss)
            await db.set_cooldown(str(self.user_id), "jail", datetime.now() + timedelta(minutes=jail_time))
            embed = discord.Embed(
                title="🚨 Heist Failed!",
                description=(f"You failed the {target_data['name']} job. Fined **{loss:,}** {CURRENCY_NAME} and jailed **{jail_time}m**."),
                color=0xE74C3C
            )

        if self.is_over:
            return
        self.is_over = True

        edit_view = self.active_view or self
        for item in edit_view.children:
            item.disabled = True

        if interaction is None:
            # Handle timeout scenario where no interaction exists
            await self.ctx.send(embed=embed)
            return

        if not interaction.response.is_done():
            try:
                await interaction.response.edit_message(content=None, embed=embed, view=edit_view)
                return
            except Exception:
                pass

        try:
            await interaction.edit_original_response(content=None, embed=embed, view=edit_view)
            return
        except Exception:
            pass

        try:
            await interaction.followup.send(embed=embed)
        except Exception:
            pass

class TeamHeistView(discord.ui.View):
    def __init__(self, leader):
        super().__init__(timeout=45)
        self.leader = leader
        self.members = [leader.id]

    @discord.ui.button(label="Join Heist", style=discord.ButtonStyle.primary, emoji="👥")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.members:
            return await interaction.response.send_message("You are already in the team!", ephemeral=True)
        if len(self.members) >= 5:
            return await interaction.response.send_message("The team is full!", ephemeral=True)
        self.members.append(interaction.user.id)
        await interaction.response.send_message(f"✅ You joined the heist team! ({len(self.members)}/5)", ephemeral=True)
        embed = interaction.message.embeds[0]
        embed.description = f"**Current Team:**\n" + "\n".join([f"<@{m}>" for m in self.members]) + f"\n\n*Starting in 30s...*"
        await interaction.message.edit(embed=embed)

@bot.command(name="crime")
async def crime_cmd(ctx: commands.Context):
    """Commit a quick random crime for fast cash."""
    user_id = str(ctx.author.id)
    cfg = load_config()
    jail_end = await db.get_cooldown(user_id, "jail")
    if jail_end and datetime.now() < jail_end:
        rem = jail_end - datetime.now()
        return await ctx.send(f"🔒 You are in **Jail**! Release in **{int(rem.total_seconds()//60)}m**.")

    last_crime = await db.get_cooldown(user_id, "crime")
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd = cds.get("crime", 60)
    if last_crime and datetime.now() < last_crime + timedelta(seconds=cd):
        rem = (last_crime + timedelta(seconds=cd)) - datetime.now()
        return await ctx.send(f"⏳ Wait **{int(rem.total_seconds())}s**.")

    scenarios = [
        {"name": "Pickpocketing", "msg": "You swiped a wallet!", "win_range": (2000, 5000), "fail_msg": "Caught!", "fine_range": (1000, 3000), "chance": 0.65},
        {"name": "Vandalism", "msg": "Spray-painted a car!", "win_range": (3000, 7000), "fail_msg": "Alarm!", "fine_range": (2000, 4000), "chance": 0.55},
        {"name": "Hacking", "msg": "Bypassed an ATM!", "win_range": (8000, 15000), "fail_msg": "Tracked!", "fine_range": (4000, 8000), "chance": 0.45}
    ]
    
    msg = await ctx.send("🕵️ **Planning crime...**")
    await asyncio.sleep(1.5)
    
    crime = random.choice(scenarios)
    inventory = await db.get_inventory(user_id)
    chance_mod = 0.15 if "Crime Mask" in inventory else 0
    
    success = random.random() < (crime["chance"] + chance_mod)
    await db.set_cooldown(user_id, "crime", datetime.now())
    await db.update_quest_progress(user_id, "crime")

    if success:
        amount = random.randint(*crime["win_range"])
        await db.update_balance(user_id, amount)
        embed = discord.Embed(title=f"✅ Crime: {crime['name']}", description=f"{crime['msg']}\n\nYou earned **{amount:,}** {CURRENCY_NAME}!", color=0x2ECC71)
    else:
        loss = random.randint(*crime["fine_range"])
        if "Crime Mask" in inventory: loss = int(loss * 0.7)
        await db.update_balance(user_id, -loss)
        embed = discord.Embed(title=f"🚨 Busted: {crime['name']}", description=f"{crime['fail_msg']}\n\nYou were fined **{loss:,}** {CURRENCY_NAME}!", color=0xE74C3C)
    
    await msg.edit(content=None, embed=embed)

@bot.command(name="steal")
async def steal_cmd(ctx: commands.Context, target: discord.Member):
    """Attempt to rob another user's wallet."""
    if target.id == ctx.author.id: return await ctx.send("❌ Can't rob yourself.")
    if target.bot: return await ctx.send("❌ Can't rob bots.")
    
    t_id = str(target.id)
    a_id = str(ctx.author.id)
    cfg = load_config()
    t_bal = await db.get_balance(t_id)
    
    if t_bal < 5000: return await ctx.send(f"❌ {target.display_name} is too poor.")

    last_steal = await db.get_cooldown(a_id, "steal")
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd = cds.get("steal", 300)
    if last_steal and datetime.now() < last_steal + timedelta(seconds=cd):
        rem = (last_steal + timedelta(seconds=cd)) - datetime.now()
        return await ctx.send(f"⏳ Wait **{int(rem.total_seconds()//60)}m**.")

    t_inv = await db.get_inventory(t_id)
    if "Shield" in t_inv and random.random() < 0.4:
        return await ctx.send(f"🛡️ **{target.display_name}**'s **Shield** blocked you!")

    a_inv = await db.get_inventory(a_id)
    success_chance = 0.35 + (0.12 if "Thief Kit" in a_inv else 0)
    
    success = random.random() < success_chance
    await db.set_cooldown(a_id, "steal", datetime.now())
    await db.update_quest_progress(a_id, "steal")

    if success:
        stolen = random.randint(int(t_bal * 0.1), int(t_bal * 0.3))
        await db.update_balance(t_id, -stolen)
        await db.update_balance(a_id, stolen)
        await ctx.send(f"💸 You robbed **{target.display_name}** for **{stolen:,}** {CURRENCY_NAME}!")
    else:
        fine = random.randint(2000, 6000)
        await db.update_balance(a_id, -fine)
        await ctx.send(f"🚨 Caught! paid **{fine:,}** fine.")

@bot.command(name="bail")
async def bail_cmd(ctx: commands.Context):
    """Pay bail to get out of jail early."""
    user_id = str(ctx.author.id)
    jail_end = await db.get_cooldown(user_id, "jail")
    if not jail_end or datetime.now() >= jail_end: return await ctx.send("❌ Not in jail.")
    
    rem = jail_end - datetime.now()
    cost = max(1, int(rem.total_seconds() / 60)) * 1000
    
    bal = await db.get_balance(user_id)
    if bal < cost: return await ctx.send(f"❌ Bail costs **{cost:,}**. You have **{bal:,}**.")
    
    await db.update_balance(user_id, -cost)
    await db.set_cooldown(user_id, "jail", datetime.now())
    await ctx.send(f"✅ Paid **{cost:,}** bail and released!")

@bot.command(name="heist")
async def heist_cmd(ctx: commands.Context):
    """Start a strategic heist. Choose your difficulty!"""
    user_id = str(ctx.author.id)
    jail_end = await db.get_cooldown(user_id, "jail")
    if jail_end and datetime.now() < jail_end:
        rem = jail_end - datetime.now()
        return await ctx.send(f"🔒 You are currently in **Jail**! You'll be released in **{int(rem.total_seconds()//60)}m**.")

    # Check cooldown
    last_heist = await db.get_cooldown(user_id, "heist")
    cfg = load_config()
    cds = cfg.get("COMMAND_COOLDOWNS", COMMAND_COOLDOWNS)
    cd = cds.get("heist", 300)
    if last_heist and datetime.now() < last_heist + timedelta(seconds=cd):
        rem = (last_heist + timedelta(seconds=cd)) - datetime.now()
        return await ctx.send(f"⏳ Your heist crew is laying low. Try again in **{int(rem.total_seconds()//60)}m**.")

    # Set cooldown IMMEDIATELY to prevent race conditions
    await db.set_cooldown(user_id, "heist", datetime.now())

    msg = await ctx.send("🏦 **Preparing the heist equipment...**")
    await asyncio.sleep(2.0)

    embed = discord.Embed(title="🏦 Strategic Heist", color=0x34495E)
    embed.description = "Choose the target for your operation. Higher risk = higher reward!"
    view = HeistTargetView(ctx)
    await msg.edit(content=None, embed=embed, view=view)

@bot.command(name="resetcd")
@commands.is_owner()
async def reset_cooldowns_cmd(ctx: commands.Context, target: str = None):
    """Admin command to reset cooldowns (Owner only)."""
    past = datetime.now() - timedelta(days=1)
    
    if target == "all":
        # Wipe cooldowns for everyone
        await db.db.users.update_many({}, {
            "$set": {
                "cooldowns.heist": past,
                "cooldowns.work": past,
                "cooldowns.crime": past,
                "cooldowns.steal": past,
                "cooldowns.daily": past,
                "cooldowns.jail": past
            }
        })
        return await ctx.send("✅ **Cooldowns and Jail status have been reset for EVERYONE!**")
    
    # Target self or mentioned user
    user_id = str(ctx.author.id)
    if target and (target.startswith("<@") or target.isdigit()):
        user_id = target.strip("<@!>")
    
    for key in ["heist", "work", "crime", "steal", "daily", "jail"]:
        await db.set_cooldown(user_id, key, past)
    
    target_msg = f"<@{user_id}>'s" if user_id != str(ctx.author.id) else "Your"
    await ctx.send(f"✅ **{target_msg} cooldowns and Jail status have been reset!**")

@bot.command(name="reseteco")
@commands.is_owner()
async def reset_eco_cmd(ctx: commands.Context, target: str = None):
    """Reset the economy for all users or a specific user (Owner only)."""
    if target == "all":
        confirm_msg = await ctx.send("⚠️ **WARNING:** You are about to wipe the economy for **ALL** users. Type `CONFIRM` in 10s to proceed.")
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRM"
        try:
            await bot.wait_for("message", check=check, timeout=10.0)
        except asyncio.TimeoutError:
            return await confirm_msg.edit(content="❌ **Reset cancelled.** You didn't confirm in time.")
        await db.db.users.update_many({}, {"$set": {"balance": 0, "bank": 0, "inventory": [], "quests": {}, "loan": {}}})
        await ctx.send("💥 **ECONOMY RESET!** All balances, banks, and inventories have been wiped for everyone.")
    elif target and (target.startswith("<@") or target.isdigit()):
        user_id = target.strip("<@!>")
        await db.db.users.update_one({"_id": user_id}, {"$set": {"balance": 0, "bank": 0, "inventory": [], "quests": {}, "loan": {}}})
        await ctx.send(f"🧹 **Economy reset for <@{user_id}>.**")
    else:
        await ctx.send(f"❓ Usage: `{PREFIX}reseteco all` or `{PREFIX}reseteco @user`.")
# ── SOCIAL ACTIONS ─────────────────────────────
SOCIAL_MESSAGES = {
    "punch": {"msgs": ["{author} punched {target} square in the face!", "{author} gave {target} a quick jab!"], "color": 0xE74C3C, "emoji": "🤜"},
    "slap": {"msgs": ["{author} slapped {target} with a wet noodle!", "{author} slapped {target}! *SMACK*"], "color": 0xE74C3C, "emoji": "✋"},
    "kick": {"msgs": ["{author} sent {target} flying with a powerful kick!"], "color": 0xE74C3C, "emoji": "🦵"},
    "bite": {"msgs": ["{author} bit {target}! Ouch!", "{author} gave {target} a playful love bite!"], "color": 0xE74C3C, "emoji": "🦷"},
    "bully": {"msgs": ["{author} is stuffing {target} into a locker!"], "color": 0x34495E, "emoji": "💢"},
    "bonk": {"msgs": ["{author} bonked {target} on the head with a hammer!"], "color": 0xF1C40F, "emoji": "🔨"},
    "stab": {"msgs": ["{author} stabbed {target} with a plastic spoon!"], "color": 0xE74C3C, "emoji": "🔪"},
    "yeet": {"msgs": ["{author} yeeted {target} across the server!"], "color": 0xE67E22, "emoji": "🚀"},
    "hug": {"msgs": ["{author} gave {target} a warm, fuzzy hug!"], "color": 0x3498DB, "emoji": "🫂"},
    "kiss": {"msgs": ["{author} gave {target} a sweet kiss!"], "color": 0xFF69B4, "emoji": "💋"},
    "cuddle": {"msgs": ["{author} is cuddling with {target}!"], "color": 0x3498DB, "emoji": "🫂"},
    "pat": {"msgs": ["{author} patted {target} on the head."], "color": 0xF1C40F, "emoji": "🤚"},
    "highfive": {"msgs": ["{author} and {target} shared an epic high five!"], "color": 0xF1C40F, "emoji": "🙌"},
    "holdhands": {"msgs": ["{author} is shyly holding hands with {target}."], "color": 0xFF69B4, "emoji": "🤝"},
    "tickle": {"msgs": ["{author} is tickling {target} relentlessly!"], "color": 0xF1C40F, "emoji": "🤣"},
    "nuzzle": {"msgs": ["{author} nuzzled against {target} affectionately."], "color": 0xFF69B4, "emoji": "😽"},
    "feed": {"msgs": ["{author} fed {target} a delicious cookie!"], "color": 0x2ECC71, "emoji": "🍔"},
    "carry": {"msgs": ["{author} is carrying {target} bridal style!"], "color": 0x3498DB, "emoji": "💪"},
    "sleep": {"msgs": ["{author} tucked {target} into bed. Sleep tight!"], "color": 0x34495E, "emoji": "😴"},
    "lick": {"msgs": ["{author} licked {target}! That's... a bit awkward."], "color": 0xFF69B4, "emoji": "👅"},
    "poke": {"msgs": ["{author} poked {target}! Are you awake?"], "color": 0x3498DB, "emoji": "👉"},
    "comfort": {"msgs": ["{author} patted {target} on the back. It's going to be okay."], "color": 0x3498DB, "emoji": "🤝"},
    "shoot": {"msgs": ["{author} shot {target} with a nerf gun! *Pew pew*"], "color": 0xE74C3C, "emoji": "🔫"},
    "tackle": {"msgs": ["{author} tackled {target} to the ground! 🏈"], "color": 0xE67E22, "emoji": "🤸"},
    "wave": {"msgs": ["{author} waved at {target}. Hello there!"], "color": 0x3498DB, "emoji": "👋"},
    "dance": {"msgs": ["{author} is dancing with {target}! 💃🕺"], "color": 0x9B59B6, "emoji": "💃"},
    "snuggle": {"msgs": ["{author} snuggled up close to {target}!"], "color": 0xFF69B4, "emoji": "🧸"},
    "stare": {"msgs": ["{author} is staring intensely at {target}..."], "color": 0x34495E, "emoji": "👀"},
    "scare": {"msgs": ["{author} jumped out and scared {target}! BAA!"], "color": 0x9B59B6, "emoji": "👻"},
    "dodge": {"msgs": ["{author} smoothly dodged {target}'s attack!"], "color": 0x2ECC71, "emoji": "💨"},
    "flex": {"msgs": ["{author} is flexing their muscles at {target}! 💪"], "color": 0xF1C40F, "emoji": "💪"},
}
async def send_social_embed(ctx, target, action):
    data = SOCIAL_MESSAGES.get(action)
    if not data: return
    
    # Load GIF from config
    gifs = load_config().get("ACTION_GIFS", {})
    gif_url = gifs.get(action)
    
    import random
    msg_tpl = random.choice(data["msgs"])
    desc = msg_tpl.replace("{author}", ctx.author.mention).replace("{target}", target.mention)
    embed = discord.Embed(description=f"{data['emoji']} {desc}", color=data["color"])
    
    if gif_url:
        embed.set_image(url=gif_url)
        
    await ctx.send(embed=embed)

@bot.command(name="setgif")
@commands.has_permissions(administrator=True)
async def setgif_cmd(ctx: commands.Context, action: str, url: str):
    """Set a GIF for a social or flavor action. Usage: !setgif <action> <url>"""
    global config
    cfg = load_config()
    
    # Auto-fix common Tenor link issues
    if "tenor.com/view/" in url and not url.endswith(".gif"):
        return await ctx.send("❌ **Invalid Link!** Please right-click the GIF and select 'Copy Image Link'. The link should end in `.gif`.")

    if "ACTION_GIFS" not in cfg:
        cfg["ACTION_GIFS"] = {}
    
    action_key = action.lower()
    cfg["ACTION_GIFS"][action_key] = url
    
    # Update global config for immediate use
    config = cfg
    
    await save_config_sync(cfg)
    
    # Send a preview to confirm Discord can load it
    preview = discord.Embed(title="🖼️ GIF Preview", description=f"This is how the GIF will look for **{action_key}**:", color=0x2ECC71)
    preview.set_image(url=url)
    await ctx.send(f"✅ GIF for **{action_key}** has been updated and applied!", embed=preview)
@bot.command(name="punch")
async def punch_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "punch")
@bot.command(name="slap")
async def slap_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "slap")
@bot.command(name="bite")
async def bite_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "bite")
@bot.command(name="bully")
async def bully_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "bully")
@bot.command(name="bonk")
async def bonk_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "bonk")
@bot.command(name="stab")
async def stab_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "stab")
@bot.command(name="yeet")
async def yeet_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "yeet")
@bot.command(name="hug")
async def hug_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "hug")
@bot.command(name="kiss")
async def kiss_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "kiss")
@bot.command(name="cuddle")
async def cuddle_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "cuddle")
@bot.command(name="pat", aliases=["headpat"])
async def pat_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "pat")
@bot.command(name="highfive", aliases=["h5"])
async def highfive_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "highfive")
@bot.command(name="holdhands")
async def holdhands_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "holdhands")
@bot.command(name="tickle")
async def tickle_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "tickle")
@bot.command(name="nuzzle")
async def nuzzle_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "nuzzle")
@bot.command(name="feed")
async def feed_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "feed")
@bot.command(name="carry")
async def carry_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "carry")
@bot.command(name="sleep")
async def sleep_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "sleep")
@bot.command(name="lick")
async def lick_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "lick")
@bot.command(name="poke")
async def poke_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "poke")
@bot.command(name="comfort")
async def comfort_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "comfort")
@bot.command(name="shoot")
async def shoot_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "shoot")
@bot.command(name="tackle")
async def tackle_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "tackle")
@bot.command(name="wave")
async def wave_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "wave")
@bot.command(name="dance")
async def dance_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "dance")
@bot.command(name="snuggle")
async def snuggle_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "snuggle")
@bot.command(name="stare")
async def stare_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "stare")
@bot.command(name="scare")
async def scare_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "scare")
@bot.command(name="dodge")
async def dodge_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "dodge")
@bot.command(name="flex")
async def flex_cmd(ctx, target: discord.Member): await send_social_embed(ctx, target, "flex")

# ── FLAVOR MODERATION ──────────────────────────
@bot.command(name="blast")
@commands.has_permissions(moderate_members=True)
async def blast_cmd(ctx, member: discord.Member, *, reason: str = "Blasted!"):
    """Mute a member with a blast GIF. Mod only."""
    await member.timeout(timedelta(minutes=10), reason=reason)
    # Use global config for reliability
    gifs = config.get("ACTION_GIFS", {})
    gif_url = gifs.get("blast", "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExNHJqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqJmVwPXYxX2ludGVybmFsX2dpZl9ieV9pZCZjdD1n/lT4Ix992z2zfO/giphy.gif")
    
    embed = discord.Embed(title="💥 BLASTED!", description=f"{member.mention} was blasted away for 10 minutes!", color=0xE74C3C)
    if gif_url:
        embed.set_image(url=gif_url)
    
    await ctx.send(embed=embed)
    await log_moderation(ctx.guild, f"💥 {member.name} was blasted (10m mute)", reason)

@bot.command(name="rct", aliases=["rcs", "unmute"])
@commands.has_permissions(moderate_members=True)
async def rct_cmd(ctx, member: discord.Member):
    """Unmute a member with a recovery GIF. Mod only."""
    await member.timeout(None, reason="RCT Recovery")
    # Use global config for reliability
    gifs = config.get("ACTION_GIFS", {})
    gif_url = gifs.get("rct", "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExNHJqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqJmVwPXYxX2ludGVybmFsX2dpZl9ieV9pZCZjdD1n/3o7TKVUn7iM8FMEU24/giphy.gif")
    
    embed = discord.Embed(title="✨ RECOVERED!", description=f"{member.mention} was brought back into the timeline!", color=0x2ECC71)
    if gif_url:
        embed.set_image(url=gif_url)
        
    await ctx.send(embed=embed)
    await log_moderation(ctx.guild, f"✨ {member.name} was recovered (unmute)", "RCT")

@bot.command(name="annihilate")
@commands.has_permissions(ban_members=True)
async def annihilate_cmd(ctx, member: discord.Member, *, reason: str = "Annihilated!"):
    """Ban a member with an annihilation GIF. Admin only."""
    await member.ban(reason=reason)
    # Use global config for reliability
    gifs = config.get("ACTION_GIFS", {})
    gif_url = gifs.get("annihilate", "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExNHJqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqZ3JqJmVwPXYxX2ludGVybmFsX2dpZl9ieV9pZCZjdD1n/XzkGfSRJQCxgI/giphy.gif")
    
    embed = discord.Embed(title="☄️ ANNIHILATED!", description=f"{member.name} has been erased from existence!", color=0x000000)
    if gif_url:
        embed.set_image(url=gif_url)
    
    await ctx.send(embed=embed)
    await log_moderation(ctx.guild, f"☄️ {member.name} was annihilated (ban)", reason)
# ── MARRIAGE SYSTEM ───────────────────────────
class MarriageProposalView(discord.ui.View):
    def __init__(self, requester, target):
        super().__init__(timeout=60)
        self.requester = requester
        self.target = target
        self.accepted = False
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="💍")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.target:
            await interaction.response.send_message("❌ This proposal is not for you!", ephemeral=True)
            return
        self.accepted = True
        self.stop()
        await db.marry(str(self.requester.id), str(self.target.id))
        embed = discord.Embed(title="💖 Just Married! 💖", description=f"🎊 {self.requester.mention} and {self.target.mention} are now married! 🎊", color=0xFF69B4, timestamp=discord.utils.utcnow())
        await interaction.response.edit_message(content=None, embed=embed, view=None)
    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="💔")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.target:
            await interaction.response.send_message("❌ This proposal is not for you!", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content=f"💔 {self.target.mention} declined the proposal from {self.requester.mention}.", embed=None, view=None)
@bot.command(name="marry")
async def marry_cmd(ctx: commands.Context, target: discord.Member):
    if target == ctx.author: return await ctx.send("❌ You can't marry yourself!")
    m1 = await db.get_marriage(str(ctx.author.id))
    if m1: return await ctx.send("❌ You are already married!")
    m2 = await db.get_marriage(str(target.id))
    if m2: return await ctx.send("❌ They are already married!")
    view = MarriageProposalView(ctx.author, target)
    await ctx.send(f"💍 {target.mention}, **{ctx.author.name}** has proposed to you! Do you accept?", view=view)
@bot.command(name="divorce")
async def divorce_cmd(ctx: commands.Context):
    p_id = await db.get_marriage(str(ctx.author.id))
    if not p_id: return await ctx.send("❌ You are not married!")
    await db.divorce(str(ctx.author.id), str(p_id))
    await ctx.send("💔 You are now divorced.")
@bot.command(name="marriage")
async def marriage_cmd(ctx: commands.Context, member: discord.Member = None):
    member = member or ctx.author
    p_id = await db.get_marriage(str(member.id))
    if not p_id: return await ctx.send(f"💔 **{member.display_name}** is single.")
    await ctx.send(f"💖 **{member.mention}** is married to <@{p_id}>!")
# ── LIFE & DEATH ──────────────────────────────
@bot.command(name="kill")
async def kill_cmd(ctx: commands.Context, target: discord.Member):
    if target == ctx.author: return await ctx.send("❌ You can't kill yourself!")
    reasons = ["slipped on a banana peel.", "forgot how to breathe.", "was hit by a falling piano"]
    import random
    reason = random.choice(reasons)
    await db.set_life_status(str(target.id), False)
    await ctx.send(f"💀 {target.mention} has been killed by {ctx.author.mention}! Reason: {reason}")
@bot.command(name="revive")
async def revive_cmd(ctx: commands.Context, target: discord.Member):
    await db.set_life_status(str(target.id), True)
    await ctx.send(f"✨ {target.mention} has been brought back to life!")
# ── QUEST SYSTEM ─────────────────────────────

QUEST_TEMPLATES = [
    {"type": "gamble", "desc": "Gamble {goal} times in the casino", "reward": 5000, "goals": [5, 10, 15]},
    {"type": "work", "desc": "Work {goal} times", "reward": 3000, "goals": [3, 5, 8]},
    {"type": "crime", "desc": "Commit {goal} successful crimes", "reward": 8000, "goals": [3, 5, 10]},
    {"type": "steal", "desc": "Attempt to steal {goal} times", "reward": 6000, "goals": [2, 4, 6]},
    {"type": "daily", "desc": "Claim your daily reward", "reward": 2000, "goals": [1]}
]

async def generate_daily_quests(user_id: str):
    import random
    today = datetime.now().strftime("%Y-%m-%d")
    selected = random.sample(QUEST_TEMPLATES, 3)
    quests_list = []
    for q in selected:
        goal = random.choice(q["goals"])
        quests_list.append({
            "type": q["type"],
            "desc": q["desc"].format(goal=goal),
            "goal": goal,
            "progress": 0,
            "reward": q["reward"],
            "claimed": False
        })
    quests = {"date": today, "list": quests_list}
    await db.set_quests(user_id, quests)
    return quests

@bot.command(name="quests", aliases=["q", "tasks"])
async def quests_cmd(ctx: commands.Context):
    """View your daily quests and progress."""
    user_id = str(ctx.author.id)
    quests = await db.get_quests(user_id)
    today = datetime.now().strftime("%Y-%m-%d")

    if not quests or quests.get("date") != today:
        quests = await generate_daily_quests(user_id)

    embed = discord.Embed(title=f"📋 Daily Quests | {ctx.author.name}", color=0x9B59B6)
    embed.description = "Complete these tasks to earn extra rewards! Reset happens every 24h.\n\n"
    
    for q in quests["list"]:
        status = "✅ Claimed" if q.get("claimed") else ("💎 Ready to claim!" if q["progress"] >= q["goal"] else f"🔄 {q['progress']}/{q['goal']}")
        embed.add_field(
            name=f"{q['desc']}",
            value=f"**Status:** {status} | **Reward:** {q['reward']:,} {CURRENCY_NAME}",
            inline=False
        )
    
    embed.set_footer(text="Usage: !quest claim")
    await ctx.send(embed=embed)

@bot.command(name="quest")
async def quest_claim_cmd(ctx: commands.Context, action: str = "claim"):
    """Claim your quest rewards."""
    if action != "claim": return
    
    user_id = str(ctx.author.id)
    quests = await db.get_quests(user_id)
    if not quests: return await ctx.send("❌ You don't have any quests.")

    claimed_count = 0
    total_reward = 0
    for q in quests["list"]:
        if q["progress"] >= q["goal"] and not q.get("claimed"):
            q["claimed"] = True
            total_reward += q["reward"]
            claimed_count += 1
    
    if claimed_count == 0:
        return await ctx.send("❌ No rewards to claim right now!")
    
    await db.set_quests(user_id, quests)
    await db.update_balance(user_id, total_reward)
    
    await ctx.send(f"🎊 You claimed **{claimed_count}** quest rewards and earned **{total_reward:,}** {CURRENCY_NAME}!")

if __name__ == "__main__":
    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("═" * 50)
        print("  ❌  ERROR: No bot token found!")
        print("  📂  Locally: Add it to config.json")
        print("  🚢  Railway: Add DISCORD_TOKEN in variables")
        print("═" * 50)
    else:
        # Run the bot
        bot.run(TOKEN)
