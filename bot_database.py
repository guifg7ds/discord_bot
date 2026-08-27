import motor.motor_asyncio
from datetime import datetime
from pymongo import ReturnDocument

class BotDatabase:
    def __init__(self):
        self.client = None
        self.db = None

    def setup(self, mongo_uri: str):
        self.client = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
        self.db = self.client.paradox_bot
        print("  [DATABASE] Connected to MongoDB!")

    # ── VOUCHES ──────────────────────────────
    async def get_vouches(self, user_id: str) -> int:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("vouches", 0) if user else 0

    async def set_vouches(self, user_id: str, count: int):
        await self.db.users.update_one({"_id": user_id}, {"$set": {"vouches": count}}, upsert=True)

    # ── SCAM STRIKES ─────────────────────────
    async def get_scam_strikes(self, user_id: str) -> int:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("scam_strikes", 0) if user else 0

    async def add_scam_strike(self, user_id: str) -> int:
        result = await self.db.users.find_one_and_update(
            {"_id": user_id}, {"$inc": {"scam_strikes": 1}},
            upsert=True, return_document=ReturnDocument.AFTER)
        return result.get("scam_strikes", 0)

    async def clear_scam_strikes(self, user_id: str):
        await self.db.users.update_one({"_id": user_id}, {"$unset": {"scam_strikes": ""}})

    # ── INFRACTIONS ──────────────────────────
    async def get_infractions(self, user_id: str) -> list:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("infractions", []) if user else []

    async def add_infraction(self, user_id: str, word: str, channel_name: str) -> int:
        infraction = {"word": word, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "channel": channel_name}
        result = await self.db.users.find_one_and_update(
            {"_id": user_id}, {"$push": {"infractions": infraction}},
            upsert=True, return_document=ReturnDocument.AFTER)
        return len(result.get("infractions", []))

    async def set_infractions(self, user_id: str, infractions: list):
        if not infractions:
            await self.clear_infractions(user_id)
        else:
            await self.db.users.update_one({"_id": user_id}, {"$set": {"infractions": infractions}}, upsert=True)

    async def clear_infractions(self, user_id: str):
        await self.db.users.update_one({"_id": user_id}, {"$unset": {"infractions": ""}})

    async def get_all_infractions(self) -> dict:
        cursor = self.db.users.find({"infractions": {"$exists": True, "$ne": []}})
        results = {}
        async for doc in cursor:
            results[doc["_id"]] = doc["infractions"]
        return results

    # ── QUARANTINE ───────────────────────────
    async def save_quarantine_roles(self, user_id: str, roles: list):
        await self.db.users.update_one({"_id": user_id}, {"$set": {"quarantine_roles": roles}}, upsert=True)

    async def get_quarantine_roles(self, user_id: str) -> list:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("quarantine_roles", []) if user else []

    async def clear_quarantine_roles(self, user_id: str):
        await self.db.users.update_one({"_id": user_id}, {"$unset": {"quarantine_roles": ""}})

    # ── ECONOMY ──────────────────────────────
    async def get_balance(self, user_id: str) -> int:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("balance", 0) if user else 0

    async def update_balance(self, user_id: str, amount: int):
        await self.db.users.update_one({"_id": user_id}, {"$inc": {"balance": amount}}, upsert=True)

    async def get_bank(self, user_id: str) -> int:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("bank", 0) if user else 0

    async def update_bank(self, user_id: str, amount: int):
        await self.db.users.update_one({"_id": user_id}, {"$inc": {"bank": amount}}, upsert=True)

    async def apply_bank_interest(self, multiplier: float):
        """Apply a variable interest rate to all bank balances > 0."""
        await self.db.users.update_many(
            {"bank": {"$gt": 0}},
            [{"$set": {"bank": {"$round": [{"$multiply": ["$bank", multiplier]}, 0]}}}]
        )

    # ── COOLDOWNS ────────────────────────────
    async def get_cooldown(self, user_id: str, type: str) -> datetime:
        user = await self.db.users.find_one({"_id": user_id})
        if not user or "cooldowns" not in user:
            return None
        return user["cooldowns"].get(type)

    async def set_cooldown(self, user_id: str, type: str, time: datetime):
        await self.db.users.update_one(
            {"_id": user_id}, {"$set": {f"cooldowns.{type}": time}}, upsert=True)

    # ── GENERIC USER FIELDS ──────────────────
    async def get_user_field(self, user_id: str, field: str, default=None):
        user = await self.db.users.find_one({"_id": user_id})
        return user.get(field, default) if user else default

    async def set_user_field(self, user_id: str, field: str, value):
        await self.db.users.update_one({"_id": user_id}, {"$set": {field: value}}, upsert=True)

    # ── INVENTORY ────────────────────────────
    async def get_inventory(self, user_id: str) -> list:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("inventory", []) if user else []

    async def add_item(self, user_id: str, item_name: str):
        await self.db.users.update_one({"_id": user_id}, {"$push": {"inventory": item_name}}, upsert=True)

    async def remove_item(self, user_id: str, item_name: str):
        await self.db.users.update_one({"_id": user_id}, {"$pull": {"inventory": item_name}})

    # ── QUESTS ───────────────────────────────
    async def get_quests(self, user_id: str) -> dict:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("quests", {}) if user else {}

    async def set_quests(self, user_id: str, quests: dict):
        await self.db.users.update_one({"_id": user_id}, {"$set": {"quests": quests}}, upsert=True)

    async def update_quest_progress(self, user_id: str, quest_type: str, amount: int = 1):
        """Increment progress on any quest matching quest_type."""
        user = await self.db.users.find_one({"_id": user_id})
        if not user or "quests" not in user:
            return
        quests = user["quests"]
        if quests.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return  # Quests are stale, don't update
        changed = False
        for q in quests.get("list", []):
            if q["type"] == quest_type and not q.get("claimed", False):
                q["progress"] = min(q["progress"] + amount, q["goal"])
                changed = True
        if changed:
            await self.db.users.update_one({"_id": user_id}, {"$set": {"quests": quests}})

    # ── LEADERBOARD ──────────────────────────
    async def get_leaderboard(self, limit: int = 10) -> list:
        """Return top users sorted by wallet + bank."""
        pipeline = [
            {"$project": {"_id": 1, "balance": {"$ifNull": ["$balance", 0]}, "bank": {"$ifNull": ["$bank", 0]}}},
            {"$addFields": {"total": {"$add": ["$balance", "$bank"]}}},
            {"$match": {"total": {"$gt": 0}}},
            {"$sort": {"total": -1}},
            {"$limit": limit}
        ]
        cursor = self.db.users.aggregate(pipeline)
        results = []
        async for doc in cursor:
            results.append(doc)
        return results

    # ── LEVELING ─────────────────────────────
    async def get_xp(self, user_id: str) -> int:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("xp", 0) if user else 0

    async def add_xp(self, user_id: str, amount: int) -> dict:
        """Add XP and return the new document."""
        return await self.db.users.find_one_and_update(
            {"_id": user_id}, {"$inc": {"xp": amount}},
            upsert=True, return_document=ReturnDocument.AFTER)

    async def set_xp(self, user_id: str, amount: int):
        await self.db.users.update_one({"_id": user_id}, {"$set": {"xp": amount}}, upsert=True)

    async def get_level(self, user_id: str) -> int:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("level", 0) if user else 0

    async def set_level(self, user_id: str, level: int):
        await self.db.users.update_one({"_id": user_id}, {"$set": {"level": level}}, upsert=True)

    async def get_level_leaderboard(self, limit: int = 10) -> list:
        """Return top users sorted by XP."""
        cursor = self.db.users.find({"xp": {"$gt": 0}}).sort("xp", -1).limit(limit)
        results = []
        async for doc in cursor:
            results.append(doc)
        return results

    # ── ALL USERS ────────────────────────────
    async def get_all_users(self) -> list:
        if self.db is None:
            return []
        cursor = self.db.users.find({})
        users = []
        async for doc in cursor:
            users.append(doc)
        return users

    # ── LOANS ────────────────────────────────
    async def get_loan(self, user_id: str) -> dict:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("loan", {}) if user else {}

    async def set_loan(self, user_id: str, loan_data: dict):
        await self.db.users.update_one({"_id": user_id}, {"$set": {"loan": loan_data}}, upsert=True)

    async def clear_loan(self, user_id: str):
        await self.db.users.update_one({"_id": user_id}, {"$unset": {"loan": ""}})

    # ── GLOBAL CONFIG ────────────────────────
    async def get_config(self) -> dict:
        if self.db is None: return {}
        doc = await self.db.settings.find_one({"_id": "global"})
        return doc.get("data", {}) if doc else {}

    async def update_config(self, config_data: dict):
        if self.db is None: return
        await self.db.settings.update_one(
            {"_id": "global"},
            {"$set": {"data": config_data}},
            upsert=True
        )


    # ── MARRIAGE ─────────────────────────────
    async def marry(self, user_id1: str, user_id2: str):
        await self.db.users.update_one({"_id": user_id1}, {"$set": {"partner": user_id2}}, upsert=True)
        await self.db.users.update_one({"_id": user_id2}, {"$set": {"partner": user_id1}}, upsert=True)

    async def divorce(self, user_id1: str, user_id2: str):
        await self.db.users.update_one({"_id": user_id1}, {"$unset": {"partner": ""}})
        await self.db.users.update_one({"_id": user_id2}, {"$unset": {"partner": ""}})

    async def get_marriage(self, user_id: str) -> str:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("partner") if user else None

    # ── LIFE STATUS ──────────────────────────
    async def set_life_status(self, user_id: str, status: bool):
        """True = Alive, False = Dead"""
        await self.db.users.update_one({"_id": user_id}, {"$set": {"alive": status}}, upsert=True)

    async def get_life_status(self, user_id: str) -> bool:
        user = await self.db.users.find_one({"_id": user_id})
        return user.get("alive", True) if user else True

db = BotDatabase()
