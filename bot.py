import os
import time
import asyncio
import logging
from typing import Optional, Any
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import (
    FloodWait, 
    MessageNotModified, 
    ChatAdminRequired, 
    PeerIdInvalid,
    RPCError
)

# ==========================================
# CONFIGURATION & SETUP
# ==========================================
# Note: Production mein ye variables Environment Variables se lene chahiye
API_ID = "34801155"
API_HASH = "d7846c4d0f2c343dd5b67c80d45409e8"
BOT_TOKEN = "8273825894:AAHfGLIo0eXqdoZt-tdzXASTxMMWzt7A6qY"
OWNER_ID = 6598432032  # Apni sahi ID yahan daalein
MONGO_URI = "mongodb+srv://mybot:mdjaved11@cluster0.vi74rzf.mongodb.net/?appName=Cluster0" 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("UltimateForwardBot")

app = Client("ultimate_forward_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==========================================
# ENTERPRISE STATE MANAGEMENT & LOCKS
# ==========================================
class StateManager:
    def __init__(self):
        self._queue_locks = {}
        self._batch_locks = {}
        self.active_timers = {}
        self.last_edit_time = {}

    def get_queue_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._queue_locks:
            self._queue_locks[user_id] = asyncio.Lock()
        return self._queue_locks[user_id]

    def get_batch_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._batch_locks:
            self._batch_locks[user_id] = asyncio.Lock()
        return self._batch_locks[user_id]
        
    def cleanup_user(self, user_id: str):
        self.last_edit_time.pop(user_id, None)
        timer = self.active_timers.pop(user_id, None)
        if timer and not timer.done():
            timer.cancel()

state = StateManager()

# ==========================================
# MONGODB DATABASE SYSTEM
# ==========================================
mongo_client = MongoClient(
    MONGO_URI, 
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=10000,
    maxPoolSize=100,
    minPoolSize=10,
    waitQueueTimeoutMS=2000
)
db = mongo_client["telegram_forward_bot"]
channels_col = db["channels"]
user_states_col = db["user_states"]

try:
    channels_col.create_index("_id")
    user_states_col.create_index("_id")
    logger.info("✅ MongoDB Indexes verified.")
except Exception as e:
    logger.warning(f"⚠️ Could not verify MongoDB indexes: {e}")

async def safe_db_call(func, *args, **kwargs) -> Any:
    return await asyncio.to_thread(func, *args, **kwargs)

async def safe_api_call(func, *args, **kwargs) -> Any:
    retries = 6
    for attempt in range(1, retries + 1):
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except MessageNotModified:
            return True
        except Exception as e:
            if attempt == retries: return None
            await asyncio.sleep(1.5 * attempt)

# ==========================================
# HANDLERS (Commands & Logic)
# ==========================================
async def owner_filter(_, __, message: Message):
    return bool(message.from_user and message.from_user.id == OWNER_ID)

is_owner = filters.create(owner_filter)

# (Command functions: addchannel, removechannel, listchannels, start, clear - as before)
# ... [Yahan aapka baaki ka command logic wahi rahega] ...

# ==========================================
# UNIVERSAL MESSAGE HANDLER (FINALIZED)
# ==========================================
@app.on_message(filters.private & ~filters.command(["start", "addchannel", "removechannel", "listchannels", "clear"]))
async def collect_batch(client, message):
    if message.from_user.id != OWNER_ID:
        return

    user_id = str(message.from_user.id)
    chat_id = message.chat.id
    
    queue_lock = state.get_queue_lock(user_id)
    async with queue_lock:
        u_state = await safe_db_call(user_states_col.find_one, {"_id": user_id})

        if not u_state or not u_state.get("target_channel"):
            await safe_api_call(message.reply_text, "⚠️ Kripya pehle /start use karein!")
            return

        new_file_data = {"msg_id": message.id, "from_chat_id": message.chat.id}
        await safe_db_call(user_states_col.update_one, {"_id": user_id}, {"$addToSet": {"files": new_file_data}})
        
        status_msg_id = u_state.get("status_msg_id")
        if not status_msg_id:
            msg = await safe_api_call(message.reply_text, "📥 Queueing files...")
            if msg:
                await safe_db_call(user_states_col.update_one, {"_id": user_id}, {"$set": {"status_msg_id": msg.id}})

    # --- YE WALA PART JO AAPNE MANGA THA ---
    if user_id in state.active_timers:
        state.active_timers[user_id].cancel()
    
    state.active_timers[user_id] = asyncio.create_task(finalize_batch(client, chat_id, user_id))

if __name__ == "__main__":
    logger.info("🚀 Enterprise Production Bot started successfully!")
    app.run()
    
