"""
Paradox Bot - Helper Functions
Swear filter, message formatting, config management, and utilities.
"""

import json
import re
import os
from datetime import datetime


# ──────────────────────────────────────────────
#  CONFIG MANAGEMENT
# ──────────────────────────────────────────────

CONFIG_FILE = "config.json"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config():
    """Load configuration from JSON or Environment Variables (for Railway)."""
    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"Error loading config file: {e}")

    # Prioritize Environment Variables (Higher priority for Railway/hosting)
    if os.environ.get("DISCORD_TOKEN"):
        config["TOKEN"] = os.environ.get("DISCORD_TOKEN")
    if os.environ.get("PREFIX"):
        config["PREFIX"] = os.environ.get("PREFIX")
        
    return config


def save_config(config: dict) -> None:
    """Save configuration back to config.json."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def get_config_value(key: str, default=None):
    """Get a single value from config."""
    config = load_config()
    return config.get(key, default)


def set_config_value(key: str, value) -> None:
    """Set a single value in config and save."""
    config = load_config()
    config[key] = value
    save_config(config)


# ──────────────────────────────────────────────
#  SWEAR WORD FILTER
# ──────────────────────────────────────────────

def build_swear_pattern(word: str) -> re.Pattern:
    """Builds a regex pattern that matches repeated characters and leetspeak."""
    # Common leetspeak substitutions
    leet_map = {
        'a': r'[a@4λ∆α]',
        'b': r'[b8ßвь]',
        'e': r'[e3€єe]',
        'g': r'[g69qɢ]',
        'h': r'[hнћ]',
        'i': r'[i1!l|ι¡ï]',
        'o': r'[o0øθо]',
        's': r'[s\$5z§]',
        't': r'[t7\+†]',
        'u': r'[uυμµv]',
        'w': r'[wωvv]',
        'l': r'[l1i!|]',
        'n': r'[nиηñ]',
        'r': r'[rя®]',
    }
    
    regex_body = ""
    for char in word.lower():
        if char in leet_map:
            regex_body += f"{leet_map[char]}+"
        else:
            regex_body += f"{re.escape(char)}+"
            
    # Removed \b for more aggressive matching (catches words inside other words)
    return re.compile(regex_body, re.IGNORECASE)


def contains_swear(message_content: str, swear_list: list[str]) -> bool:
    """
    Check if a message contains any swear words from the list.
    Automatically handles repeated characters and leetspeak evasions.
    """
    for word in swear_list:
        pattern = build_swear_pattern(word)
        if pattern.search(message_content):
            return True
    return False


def find_swear_word(message_content: str, swear_list: list[str]) -> str:
    """Find and return the first swear word found in the message."""
    for word in swear_list:
        pattern = build_swear_pattern(word)
        match = pattern.search(message_content)
        if match:
            return match.group()
    return "Unknown"


def censor_message(message_content: str, swear_list: list[str]) -> str:
    """
    Replace swear words with asterisks, preserving first letter.
    Works with our advanced leetspeak patterns.
    """
    result = message_content
    for word in swear_list:
        pattern = build_swear_pattern(word)
        def replacer(match):
            w = match.group()
            if len(w) <= 1:
                return '*'
            return w[0] + '*' * (len(w) - 1)
        result = pattern.sub(replacer, result)
    return result


# ──────────────────────────────────────────────
#  MESSAGE FORMATTING
# ──────────────────────────────────────────────

def format_welcome_message(member, template: str) -> str:
    """Format a welcome embed description for a new member."""
    return template.format(
        member=member.name,
        mention=member.mention,
        count=member.guild.member_count
    )


def format_goodbye_message(member, template: str) -> str:
    """Format a goodbye message for a departing member."""
    return template.format(
        member=member.name,
        mention=member.mention,
        count=member.guild.member_count
    )


def format_timestamp() -> str:
    """Get a nicely formatted timestamp."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────
#  UTILITIES
# ──────────────────────────────────────────────

def truncate(text: str, max_len: int = 2000) -> str:
    """Truncate text to Discord's message limit."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def parse_role_name(guild, role_name: str):
    """Find a role in a guild by name (case-insensitive)."""
    for role in guild.roles:
        if role.name.lower() == role_name.lower():
            return role
    return None


def parse_duration(duration_str: str) -> int:
    """Convert 1h, 2d, 30m etc to seconds. If just numbers, treated as seconds."""
    if duration_str.isdigit():
        return int(duration_str)
    
    match = re.match(r"(\d+)([smhdw])", duration_str.lower())
    if not match:
        return 60  # Default to 60s
    
    amount, unit = match.groups()
    amount = int(amount)
    
    units = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400,
        'w': 604800
    }
    
    return amount * units.get(unit, 1)


def format_duration(seconds: int) -> str:
    """Convert seconds to human readable string (e.g. 1h 30m)."""
    if seconds < 60:
        return f"{seconds}s"
    
    intervals = (
        ('weeks', 604800),
        ('days', 86400),
        ('hours', 3600),
        ('minutes', 60),
        ('seconds', 1),
    )
    
    result = []
    for name, count in intervals:
        value = seconds // count
        if value:
            seconds -= value * count
            if value == 1:
                name = name.rstrip('s')
            result.append(f"{value} {name}")
            
    return ", ".join(result[:2]) or "0s"