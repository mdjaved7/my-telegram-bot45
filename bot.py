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
API_ID = "34801155"
API_HASH = "d7846c4d0f2c343dd5b67c80d45409e8"
BOT_TOKEN = "8273825894:AAHfGLIo0eXqdoZt-tdzXASTxMMWzt7A6qY"
OWNER_ID = 6598432032  # Replace with your 
MONGO_URI = "mongodb+srv://mybot:mdjaved11@cluster0.vi74rzf.mongodb.net/?appName=Cluster0"  # Replace with your MongoDB connection string

# Logging configuration for Enterprise Grade Monitoring
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("UltimateForwardBot")

app = Client("ultimate_forward_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==========================================
# ENTERPRISE STATE MANAGEMENT & LOCKS
# ==========================================
class StateManager:
    """Manages asynchronous locks and timers to completely prevent race conditions and memory leaks."""
    def __init__(self):
        self._queue_locks = {}
        self._batch_locks = {}
        self.active_timers = {}
        self.last_edit_time = {}

    def get_queue_lock(self, user_id: str) -> asyncio.Lock:
        """Lock for serializing incoming files to prevent duplicate UI messages."""
        if user_id not in self._queue_locks:
            self._queue_locks[user_id] = asyncio.Lock()
        return self._queue_locks[user_id]

    def get_batch_lock(self, user_id: str) -> asyncio.Lock:
        """Lock for serializing the sending process to prevent double execution."""
        if user_id not in self._batch_locks:
            self._batch_locks[user_id] = asyncio.Lock()
        return self._batch_locks[user_id]
        
    def cleanup_user(self, user_id: str):
        """Frees up memory for unused tracking variables."""
        self.last_edit_time.pop(user_id, None)
        timer = self.active_timers.pop(user_id, None)
        if timer and not timer.done():
            timer.cancel()

state = StateManager()

# ==========================================
# MONGODB DATABASE SYSTEM
# ==========================================
# Optimized connection pool for production and thread safety
mongo_client = MongoClient(
    MONGO_URI, 
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=10000,
    maxPoolSize=100,       # Increased for high concurrency
    minPoolSize=10,        # Keep connections alive
    waitQueueTimeoutMS=2000
)
db = mongo_client["telegram_forward_bot"]
channels_col = db["channels"]
user_states_col = db["user_states"]

# Ensure indexes for performance (optimized queries)
try:
    channels_col.create_index("_id")
    user_states_col.create_index("_id")
    logger.info("✅ MongoDB Indexes verified.")
except Exception as e:
    logger.warning(f"⚠️ Could not verify MongoDB indexes: {e}")


async def safe_db_call(func, *args, **kwargs) -> Any:
    """
    Wraps synchronous PyMongo calls in an asyncio thread to prevent blocking the event loop.
    Includes built-in exponential backoff retries if the database is temporarily unavailable.
    """
    retries = 5
    base_delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except (ConnectionFailure, PyMongoError) as e:
            if attempt == retries:
                logger.critical(f"❌ DB Call Failed permanently after {retries} attempts. Error: {e}")
                return None
            sleep_time = base_delay * attempt
            logger.error(f"⚠️ Database error in {func.__name__}: {e}. Retrying in {sleep_time}s... ({attempt}/{retries})")
            await asyncio.sleep(sleep_time)

async def safe_api_call(func, *args, **kwargs) -> Any:
    """
    Wraps API calls to automatically catch FloodWait, wait it out, and retry safely.
    Ignores MessageNotModified to allow seamless UI updates.
    Handles network drops and generic RPC errors safely.
    """
    retries = 6
    base_delay = 1.5
    for attempt in range(1, retries + 1):
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            wait_time = e.value + 1
            logger.warning(f"⏳ FloodWait hit in {func.__name__}! Sleeping for {wait_time}s...")
            await asyncio.sleep(wait_time)
        except MessageNotModified:
            return True 
        except PeerIdInvalid:
            logger.warning("⚠️ PeerIdInvalid encountered. Refreshing cache (sleeping 2s)...")
            await asyncio.sleep(2)
        except RPCError as e:
            if attempt == retries:
                logger.error(f"❌ Telegram RPC Error in {func.__name__} after {retries} attempts: {e}")
                return None
            logger.error(f"⚠️ RPC Error: {e}. Retrying ({attempt}/{retries})...")
            await asyncio.sleep(base_delay * attempt)
        except Exception as e:
            if attempt == retries:
                logger.error(f"❌ Unexpected Error in {func.__name__}: {e}")
                return None
            logger.error(f"⚠️ Error: {e}. Retrying ({attempt}/{retries})...")
            await asyncio.sleep(base_delay * attempt)
    return None

# ==========================================
# FILTERS & PERMISSIONS
# ==========================================
async def owner_filter(_, __, message: Message):
    return bool(message.from_user and message.from_user.id == OWNER_ID)

is_owner = filters.create(owner_filter)

# ==========================================
# BOT COMMANDS
# ==========================================

# 1. ADD CHANNEL COMMAND
@app.on_message(filters.command("addchannel") & filters.private & is_owner)
async def add_channel_cmd(client, message):
    try:
        args = message.command
        if len(args) < 3:
            await safe_api_call(message.reply_text, "❌ **Format:** `/addchannel <Channel_Name> <Channel_ID>`")
            return
        
        ch_name = args[1]
        try:
            ch_id = int(args[2])
        except ValueError:
            await safe_api_call(message.reply_text, "❌ Channel ID hamesha ek number hona chahiye!")
            return

        try:
            member = await client.get_chat_member(ch_id, "me")
            if not member.privileges or not member.privileges.can_post_messages:
                await safe_api_call(message.reply_text, "⚠️ Bot channel mein toh hai, par uske paas **Post Messages** ki permission nahi hai!")
                return
        except ChatAdminRequired:
            await safe_api_call(message.reply_text, "❌ Bot ko us channel mein Admin banana zaroori hai!")
            return
        except Exception as e:
            await safe_api_call(message.reply_text, f"❌ Verification Fail: User/Bot ko channel nahi mila. Error: {e}")
            return

        # Atomic Upsert
        await safe_db_call(
            channels_col.update_one, 
            {"_id": ch_name}, 
            {"$set": {"channel_id": ch_id}}, 
            upsert=True
        )
        
        await safe_api_call(message.reply_text, f"✅ **Channel Saved & Verified!**\nName: {ch_name}\nID: `{ch_id}`")
    except Exception as e:
        logger.error(f"Error in add_channel_cmd: {e}")
        await safe_api_call(message.reply_text, f"❌ Error: {str(e)}")

# 2. REMOVE CHANNEL COMMAND
@app.on_message(filters.command("removechannel") & filters.private & is_owner)
async def remove_channel_cmd(client, message):
    args = message.command
    if len(args) < 2:
        await safe_api_call(message.reply_text, "❌ **Format:** `/removechannel <Channel_Name>`")
        return
    ch_name = args[1]
    
    result = await safe_db_call(channels_col.delete_one, {"_id": ch_name})
    if result and result.deleted_count > 0:
        await safe_api_call(message.reply_text, f"🗑️ Channel `{ch_name}` ko database se hata diya gaya hai.")
    else:
        await safe_api_call(message.reply_text, "❌ Is naam ka koi channel database mein nahi mila.")

# 3. LIST CHANNELS COMMAND
@app.on_message(filters.command("listchannels") & filters.private & is_owner)
async def list_channels_cmd(client, message):
    cursor = await safe_db_call(channels_col.find, {})
    channels_list = list(cursor) if cursor else []
    
    if not channels_list:
        await safe_api_call(message.reply_text, "📂 Database abhi khali hai.")
        return
        
    res = "📋 **Saved Channels List:**\n\n"
    for doc in channels_list:
        res += f"🔹 **{doc['_id']}**: `{doc['channel_id']}`\n"
    await safe_api_call(message.reply_text, res)

# 4. START COMMAND
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    user_id = str(message.from_user.id) 
    
    if message.from_user.id != OWNER_ID:
        await safe_api_call(message.reply_text, "❌ **Sorry!** Aap is bot ke admin/owner nahi hain. Aap is bot ko use nahi kar sakte.")
        return
    
    u_state = await safe_db_call(user_states_col.find_one, {"_id": user_id})
    
    if u_state and u_state.get("files"):
        count = len(u_state["files"])
        keyboard = [
            [InlineKeyboardButton("Purani Queue Use Karein 🔄", callback_data="choose_ch")],
            [InlineKeyboardButton("Nayi Queue Banayein (Clear) 🗑️", callback_data="clear_and_start")]
        ]
        await safe_api_call(
            message.reply_text,
            f"⚠️ Aapki queue mein pehle se **{count} files** pending hain! Aap kya karna chahte hain?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Create empty state securely
    await safe_db_call(
        user_states_col.update_one,
        {"_id": user_id},
        {"$set": {"target_channel": None, "ch_name": "", "files": [], "status_msg_id": None}},
        upsert=True
    )
    
    keyboard = [[InlineKeyboardButton("Select Target Channel 🎯", callback_data="choose_ch")]]
    await safe_api_call(message.reply_text, "👋 Welcome! Apna target channel select karein jahan files bhejna chahte hain:", reply_markup=InlineKeyboardMarkup(keyboard))

# 5. CLEAR QUEUE COMMAND
@app.on_message(filters.command("clear") & filters.private & is_owner)
async def clear_queue_cmd(client, message):
    user_id = str(message.from_user.id)
    
    # Safely clear DB
    await safe_db_call(
        user_states_col.update_one,
        {"_id": user_id},
        {"$set": {"files": [], "status_msg_id": None}},
        upsert=True
    )
    state.cleanup_user(user_id)
    await safe_api_call(message.reply_text, "🗑️ Aapki pending files ki queue ko poori tarah saaf kar diya gaya hai.")

# ==========================================
# CALLBACK QUERY HANDLER
# ==========================================
@app.on_callback_query()
async def handle_buttons(client, callback_query):
    user_id = str(callback_query.from_user.id)
    
    if callback_query.from_user.id != OWNER_ID:
        await safe_api_call(callback_query.answer, "❌ Aapko is button par click karne ki permission nahi hai!", show_alert=True)
        return

    data = callback_query.data

    if data == "clear_and_start":
        await safe_db_call(
            user_states_col.update_one,
            {"_id": user_id},
            {"$set": {"target_channel": None, "ch_name": "", "files": [], "status_msg_id": None}},
            upsert=True
        )
        state.cleanup_user(user_id)
        
        buttons = [[InlineKeyboardButton("Select Target Channel 🎯", callback_data="choose_ch")]]
        await safe_api_call(callback_query.message.edit_text, "🗑️ Purani queue clear ho gayi. Naya channel select karein:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "choose_ch":
        cursor = await safe_db_call(channels_col.find, {})
        channels_list = list(cursor) if cursor else []
        
        if not channels_list:
            await safe_api_call(callback_query.message.edit_text, "⚠️ Koi channel added nahi hai. Use `/addchannel` first.")
            return
            
        buttons = []
        channel_names = [doc["_id"] for doc in channels_list]
        for i in range(0, len(channel_names), 2):
            row = [InlineKeyboardButton(name, callback_data=f"select_{name}") for name in channel_names[i:i+2]]
            buttons.append(row)
            
        await safe_api_call(callback_query.message.edit_text, "🎯 Kis channel par data forward karna hai? Select karein:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("select_"):
        ch_name = data.replace("select_", "")
        ch_doc = await safe_db_call(channels_col.find_one, {"_id": ch_name})
        
        if not ch_doc:
            await safe_api_call(callback_query.answer, "❌ Channel database mein nahi mila!", show_alert=True)
            return

        await safe_db_call(
            user_states_col.update_one,
            {"_id": user_id},
            {"$set": {
                "target_channel": int(ch_doc["channel_id"]), 
                "ch_name": ch_name, 
                "status_msg_id": None
            }},
            upsert=True
        )
        
        await safe_api_call(
            callback_query.message.edit_text,
            f"✅ **Target Set:** `{ch_name}`\n\nAb aap bulk files bina dare forward karke yahan bhej dijiye. Saari files bhejne ke baad niche click karein:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Send All Files 🚀", callback_data="send_batch")],
                [InlineKeyboardButton("Cancel / Clear Queue ❌", callback_data="clear_and_start")]
            ])
        )

    elif data == "send_batch":
        # Protect against double clicks and simultaneous execution
        batch_lock = state.get_batch_lock(user_id)
        if batch_lock.locked():
            await safe_api_call(callback_query.answer, "⚠️ Processing already in progress. Please wait!", show_alert=True)
            return

        async with batch_lock:
            u_state = await safe_db_call(user_states_col.find_one, {"_id": user_id})
            
            if not u_state or not u_state.get("files"):
                await safe_api_call(callback_query.answer, "⚠️ Queue khali hai! Pehle files forward karke bhejien.", show_alert=True)
                return

            await safe_api_call(callback_query.message.edit_reply_markup, reply_markup=None)
            
            files_queue = u_state["files"]
            files_queue.sort(key=lambda x: x["msg_id"])
            total_files = len(files_queue)
            
            await safe_api_call(callback_query.message.edit_text, f"🔄 Copying & Posting: 0/{total_files} completed...")
            target_chat_id = int(u_state["target_channel"])
            
            success_count = 0
            failed_files = [] 

            logger.info(f"🚀 Starting batch copy for {total_files} files to {target_chat_id}")

            # --- Phase 1: Serial Execution Queue ---
            for index, file_data in enumerate(files_queue, 1):
                is_copied = await safe_api_call(
                    client.copy_message,
                    chat_id=target_chat_id,
                    from_chat_id=int(file_data["from_chat_id"]),
                    message_id=int(file_data["msg_id"])
                )
                
                if is_copied:
                    success_count += 1
                else:
                    failed_files.append(file_data)
                    logger.warning(f"❌ Failed to copy msg_id {file_data['msg_id']}")
                    
                # Throttle UI updates to prevent FloodWait during massive batches
                if index % 10 == 0 or index == total_files:
                    await safe_api_call(
                        callback_query.message.edit_text, 
                        f"🚀 Live Progress: {index}/{total_files} files processed..."
                    )
                
                # Protect API limits natively
                await asyncio.sleep(1.2)

            # --- Phase 2: Final Fallback Rescue Loop ---
            if failed_files:
                await safe_api_call(
                    callback_query.message.edit_text, 
                    f"🔄 Retrying failed items one final time ({len(failed_files)} remaining)..."
                )
                still_failed = []
                for file_data in failed_files:
                    is_copied_final = await safe_api_call(
                        client.copy_message,
                        chat_id=target_chat_id,
                        from_chat_id=int(file_data["from_chat_id"]),
                        message_id=int(file_data["msg_id"])
                    )
                    if not is_copied_final:
                        still_failed.append(file_data)
                    else:
                        success_count += 1
                    await asyncio.sleep(2.0) # Slower delay for retries
                failed_files = still_failed

            final_failed_count = len(failed_files)
            await safe_api_call(
                callback_query.message.edit_text,
                f"✅ **Batch Process Finished!**\n\n"
                f"🔹 **Successfully Copied:** {success_count}\n"
                f"🔸 **Failed:** {final_failed_count}\n"
                f"✨ **Total Queue:** {total_files}\n\n"
                f"Destination Target Channel: `{u_state.get('ch_name', 'Unknown')}`"
            )
            
            # Atomic Queue Clear on Finish
            await safe_db_call(
                user_states_col.update_one,
                {"_id": user_id},
                {"$set": {"files": [], "status_msg_id": None}}
            )
            state.cleanup_user(user_id)
            logger.info("✅ Batch process completed and queue cleared.")

# ==========================================
# SMART DEBOUNCE BACKGROUND TASK
# ==========================================
async def finalize_batch(client, chat_id, user_id):
    """Wait 30 seconds for the queue to stabilize, then inject the Action buttons into the SAME message."""
    try:
        await asyncio.sleep(30) 
    except asyncio.CancelledError:
        return 
    finally:
        # Safely remove self from active timers if not replaced
        if state.active_timers.get(user_id) == asyncio.current_task():
            state.active_timers.pop(user_id, None)

    # Re-check DB state
    u_state = await safe_db_call(user_states_col.find_one, {"_id": user_id})
    if not u_state: return
    
    status_msg_id = u_state.get("status_msg_id")
    total_queued = len(u_state.get("files", []))
    
    # Requirement: Do NOT reset status_msg_id after 30 seconds.
    # Edit identically matching message into final payload.
    if status_msg_id and total_queued > 0:
        text = f"✅ {total_queued} files successfully added to the secure queue.\nPress Send to start forwarding."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Send All Files 🚀", callback_data="send_batch")],
            [InlineKeyboardButton("Cancel / Clear Queue ❌", callback_data="clear_and_start")]
        ])
        await safe_api_call(client.edit_message_text, chat_id, status_msg_id, text, reply_markup=keyboard)

# ==========================================
# UNIVERSAL MESSAGE HANDLER (Optimized Batch UI)
# ==========================================
@app.on_message(filters.private & ~filters.command(["start", "addchannel", "removechannel", "listchannels", "clear"]))
async def collect_batch(client, message):
    if message.from_user.id != OWNER_ID:
        return

    user_id = str(message.from_user.id)
    chat_id = message.chat.id
    
    # STRICT ASYNC LOCK: Eliminates all race conditions for
