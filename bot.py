import logging
import json
import os
import time
import asyncio
import re

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, PeerIdInvalid, ChatAdminRequired, MessageNotModified

# ---- CONFIGURATION ----
API_ID = 34801155          
API_HASH = "d7846c4d0f2c343dd5b67c80d45409e8" 
BOT_TOKEN = "8808145635:AAE4KqnrT-7hDSoW7svkVvsfthj9NINN5x0"
OWNER_ID = 6598432032  # ✨ Strict Security: Sirf aap hi is bot ko chala payenge

app = Client("ultimate_forward_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

CHANNELS_FILE = "channels_data.json"
STATES_FILE = "user_states_data.json"
file_lock = asyncio.Lock()

# Global variables for smart debouncing and API optimization
active_timers = {}
last_edit_time = {}
pending_status_creation = {}  # 🚀 Naya lock race conditions ko rokne ke liye

# ---- CAPTION CLEANER HELPER ----
def clean_caption(text):
    if not text:
        return ""
    # Remove t.me links, http/https URLs, www links, and @usernames
    text = re.sub(r'(https?://\S+|www\.\S+|t\.me/\S+|@\w+)', '', text)
    # Remove multiple spaces
    text = re.sub(r' +', ' ', text)
    # Remove duplicate blank lines and trim whitespace
    text = re.sub(r'\n\s*\n+', '\n', text).strip()
    return text

# ---- ASYNC JSON DATABASE SYSTEM ----
def load_json(file_name, default_value):
    if not os.path.exists(file_name):
        with open(file_name, "w") as f:
            json.dump(default_value, f, indent=4)
        return default_value
    try:
        with open(file_name, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading {file_name}: {e}")
        return default_value

def save_json(file_name, data):
    try:
        with open(file_name, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving {file_name}: {e}")

# ---- UNIVERSAL FLOODWAIT WRAPPER ----
async def safe_api_call(func, *args, **kwargs):
    """
    Wraps API calls to automatically catch FloodWait, wait it out, and retry.
    Ignores MessageNotModified to allow seamless UI updates.
    """
    retries = 5
    attempt = 0
    while attempt < retries:
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            logging.warning(f"⏳ FloodWait hit! Sleeping for {e.value} seconds before retrying...")
            await asyncio.sleep(e.value)
            attempt += 1
        except MessageNotModified:
            # Safely ignore if the edit text is the same as the current text
            return True 
        except PeerIdInvalid:
            logging.warning("⚠️ PeerIdInvalid encountered. Refreshing cache (sleeping 2s)...")
            await asyncio.sleep(2)
            attempt += 1
        except Exception as e:
            logging.error(f"❌ API Error in {func.__name__}: {e}")
            return None
    return None

# ---- FILTERS ----
async def owner_filter(_, __, message):
    return message.from_user and message.from_user.id == OWNER_ID

is_owner = filters.create(owner_filter)

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

        async with file_lock:
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

            channels_dict = load_json(CHANNELS_FILE, {})
            channels_dict[ch_name] = ch_id
            save_json(CHANNELS_FILE, channels_dict)
        
        await safe_api_call(message.reply_text, f"✅ **Channel Saved & Verified!**\nName: {ch_name}\nID: `{ch_id}`")
    except Exception as e:
        await safe_api_call(message.reply_text, f"❌ Error: {str(e)}")

# 2. REMOVE CHANNEL COMMAND
@app.on_message(filters.command("removechannel") & filters.private & is_owner)
async def remove_channel_cmd(client, message):
    args = message.command
    if len(args) < 2:
        await safe_api_call(message.reply_text, "❌ **Format:** `/removechannel <Channel_Name>`")
        return
    ch_name = args[1]
    
    async with file_lock:
        channels_dict = load_json(CHANNELS_FILE, {})
        if ch_name in channels_dict:
            del channels_dict[ch_name]
            save_json(CHANNELS_FILE, channels_dict)
            await safe_api_call(message.reply_text, f"🗑️ Channel `{ch_name}` ko database se hata diya gaya hai.")
        else:
            await safe_api_call(message.reply_text, "❌ Is naam ka koi channel database mein nahi mila.")

# 3. LIST CHANNELS COMMAND
@app.on_message(filters.command("listchannels") & filters.private & is_owner)
async def list_channels_cmd(client, message):
    channels_dict = load_json(CHANNELS_FILE, {})
    if not channels_dict:
        await safe_api_call(message.reply_text, "📂 Database abhi khali hai.")
        return
    res = "📋 **Saved Channels List:**\n\n"
    for name, cid in channels_dict.items():
        res += f"🔹 **{name}**: `{cid}`\n"
    await safe_api_call(message.reply_text, res)

# 4. START COMMAND
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    user_id = str(message.from_user.id) 
    
    if message.from_user.id != OWNER_ID:
        await safe_api_call(message.reply_text, "❌ **Sorry!** Aap is bot ke admin/owner nahi hain. Aap is bot ko use nahi kar sakte.")
        return
    
    async with file_lock:
        user_states = load_json(STATES_FILE, {})
        
        if user_id in user_states and user_states[user_id].get("files"):
            count = len(user_states[user_id]["files"])
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

        user_states[user_id] = {"target_channel": None, "ch_name": "", "files": [], "status_msg_id": None}
        save_json(STATES_FILE, user_states)
    
    keyboard = [[InlineKeyboardButton("Select Target Channel 🎯", callback_data="choose_ch")]]
    await safe_api_call(message.reply_text, "👋 Welcome! Apna target channel select karein jahan files bhejna chahte hain:", reply_markup=InlineKeyboardMarkup(keyboard))

# 5. CLEAR QUEUE COMMAND
@app.on_message(filters.command("clear") & filters.private & is_owner)
async def clear_queue_cmd(client, message):
    user_id = str(message.from_user.id)
    async with file_lock:
        user_states = load_json(STATES_FILE, {})
        if user_id in user_states:
            user_states[user_id]["files"] = []
            user_states[user_id]["status_msg_id"] = None
            save_json(STATES_FILE, user_states)
        await safe_api_call(message.reply_text, "🗑️ Aapki pending files ki queue ko poori tarah saaf kar diya gaya hai.")

# 6. CALLBACK QUERY HANDLER
@app.on_callback_query()
async def handle_buttons(client, callback_query):
    user_id = str(callback_query.from_user.id)
    
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("❌ Aapko is button par click karne ki permission nahi hai!", show_alert=True)
        return

    data = callback_query.data
    async with file_lock:
        channels_dict = load_json(CHANNELS_FILE, {})
        user_states = load_json(STATES_FILE, {})

    if data == "clear_and_start":
        async with file_lock:
            user_states[user_id] = {"target_channel": None, "ch_name": "", "files": [], "status_msg_id": None}
            save_json(STATES_FILE, user_states)
        buttons = [[InlineKeyboardButton("Select Target Channel 🎯", callback_data="choose_ch")]]
        await safe_api_call(callback_query.message.edit_text, "🗑️ Purani queue clear ho gayi. Naya channel select karein:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "choose_ch":
        if not channels_dict:
            await safe_api_call(callback_query.message.edit_text, "⚠️ Koi channel added nahi hai. Use `/addchannel` first.")
            return
        buttons = []
        channel_names = list(channels_dict.keys())
        for i in range(0, len(channel_names), 2):
            row = [InlineKeyboardButton(name, callback_data=f"select_{name}") for name in channel_names[i:i+2]]
            buttons.append(row)
        await safe_api_call(callback_query.message.edit_text, "🎯 Kis channel par data forward karna hai? Select karein:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("select_"):
        ch_name = data.replace("select_", "")
        async with file_lock:
            user_states[user_id]["target_channel"] = int(channels_dict[ch_name])
            user_states[user_id]["ch_name"] = ch_name
            user_states[user_id]["status_msg_id"] = None # Reset status message block
            if "files" not in user_states[user_id]:
                user_states[user_id]["files"] = []
            save_json(STATES_FILE, user_states)
        
        await safe_api_call(
            callback_query.message.edit_text,
            f"✅ **Target Set:** `{ch_name}`\n\nAb aap bulk files bina dare forward karke yahan bhej dijiye. Saari files bhejne ke baad niche click karein:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Send All Files 🚀", callback_data="send_batch")],
                [InlineKeyboardButton("Cancel / Clear Queue ❌", callback_data="clear_and_start")]
            ])
        )

    elif data == "send_batch":
        u_state = user_states.get(user_id)
        if not u_state or "files" not in u_state or len(u_state["files"]) == 0:
            await callback_query.answer("⚠️ Queue khali hai! Pehle files forward karke bhejien.", show_alert=True)
            return

        # Remove the button interface safely
        await safe_api_call(callback_query.message.edit_reply_markup, reply_markup=None)
        
        u_state["files"].sort(key=lambda x: x["msg_id"])
        total_files = len(u_state["files"])
        
        # We edit the text to show progress
        await safe_api_call(callback_query.message.edit_text, f"🔄 Copying & Posting: 0/{total_files} completed...")
        target_chat_id = int(u_state["target_channel"])
        
        success_count = 0
        failed_files = [] 

        # --- Helper for processing single message sending ---
        async def process_message_send(file_info):
            # Fetch message to read its attributes/type safely
            msg_obj = await client.get_messages(chat_id=int(file_info["from_chat_id"]), message_ids=int(file_info["msg_id"]))
            if not msg_obj:
                return None
            
            # Feature check: Perform link removal ONLY if the message type is Audio
            if msg_obj.audio:
                cleaned_caption_text = clean_caption(msg_obj.caption)
                return await safe_api_call(
                    client.send_audio,
                    chat_id=target_chat_id,
                    audio=msg_obj.audio.file_id,
                    caption=cleaned_caption_text if cleaned_caption_text else None,
                    duration=msg_obj.audio.duration,
                    performer=msg_obj.audio.performer,
                    title=msg_obj.audio.title
                    # Thumbnail metadata is preserved inside file_id structure
                )
            else:
                # Standard fallback for all other media categories
                return await safe_api_call(
                    client.copy_message,
                    chat_id=target_chat_id,
                    from_chat_id=int(file_info["from_chat_id"]),
                    message_id=int(file_info["msg_id"])
                )

        # --- Phase 1: Serial Execution Queue ---
        for index, file_data in enumerate(u_state["files"], 1):
            is_copied = await process_message_send(file_data)
            
            if is_copied:
                success_count += 1
            else:
                failed_files.append(file_data)
                
            # Update progress UI every 5 messages or on the last message to conserve API limits
            if index % 5 == 0 or index == total_files:
                await safe_api_call(callback_query.message.edit_text, f"🚀 Live Progress: {index}/{total_files} files processed...")
            
            await asyncio.sleep(1.2) # Keeping the rate limit breathing room

        # --- Phase 2: Final Fallback Rescue Loop ---
        if failed_files:
            await safe_api_call(callback_query.message.edit_text, f"🔄 Retrying failed items one final time ({len(failed_files)} remaining)...")
            still_failed = []
            for file_data in failed_files:
                is_copied_final = await process_message_send(file_data)
                if not is_copied_final:
                    still_failed.append(file_data)
                await asyncio.sleep(1.5)
            failed_files = still_failed

        # --- Final Metrics Output ---
        final_failed_count = len(failed_files)
        await safe_api_call(
            callback_query.message.edit_text,
            f"✅ **Batch Process Finished!**\n\n"
            f"🔹 **Successfully Copied:** {success_count}\n"
            f"🔸 **Failed:** {final_failed_count}\n"
            f"✨ **Total Queue:** {total_files}\n\n"
            f"Destination Target Channel: `{u_state['ch_name']}`"
        )
        
        async with file_lock:
            user_states = load_json(STATES_FILE, {})
            user_states[user_id]["files"] = []
            user_states[user_id]["status_msg_id"] = None
            save_json(STATES_FILE, user_states)


# ---- SMART DEBOUNCE BACKGROUND TASK ----
async def finalize_batch(client, chat_id, user_id):
    """Wait for silent period, then inject the Action buttons into the UI."""
    try:
        await asyncio.sleep(3) # Wait 3 seconds to confirm user has stopped sending files
    except asyncio.CancelledError:
        return # Task cancelled because user is still forwarding files, exit quietly
    
    async with file_lock:
        user_states = load_json(STATES_FILE, {})
        u_state = user_states.get(user_id)
        if not u_state: return
        
        status_msg_id = u_state.get("status_msg_id")
        total_queued = len(u_state.get("files", []))
        
        # Prep for next batch by untethering the message ID
        user_states[user_id]["status_msg_id"] = None
        save_json(STATES_FILE, user_states)

    if status_msg_id and total_queued > 0:
        text = f"✅ {total_queued} files successfully added to the secure queue.\nPress Send to start forwarding."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Send All Files 🚀", callback_data="send_batch")],
            [InlineKeyboardButton("Cancel / Clear Queue ❌", callback_data="clear_and_start")]
        ])
        await safe_api_call(client.edit_message_text, chat_id, status_msg_id, text, reply_markup=keyboard)


# 7. UNIVERSAL MESSAGE HANDLER (Optimized Batch UI)
@app.on_message(filters.private & ~filters.command(["start", "addchannel", "removechannel", "listchannels", "clear"]))
async def collect_batch(client, message):
    if message.from_user.id != OWNER_ID:
        return

    user_id = str(message.from_user.id)
    chat_id = message.chat.id
    
    async with file_lock:
        user_states = load_json(STATES_FILE, {})

    if user_id not in user_states or not user_states[user_id].get("target_channel"):
        await safe_api_call(message.reply_text, "⚠️ Kripya pehle `/start` dabakar target channel select karein!")
        return

    if "files" not in user_states[user_id]:
        user_states[user_id]["files"] = []

    # Ignore duplicates safely and silently
    if any(f["msg_id"] == message.id for f in user_states[user_id]["files"]):
        return

    async with file_lock:
        user_states[user_id]["files"].append({
            "msg_id": message.id,
            "from_chat_id": message.chat.id
        })
        total_queued = len(user_states[user_id]["files"])
        status_msg_id = user_states[user_id].get("status_msg_id")
        save_json(STATES_FILE, user_states)
    
    # 🚀 FIX: Race Condition check for bulk forwards
    is_creating = pending_status_creation.get(user_id, False)

    # 1. First file sets up the tracking message (Sirf tab jab koi aur message create na kar raha ho)
    if not status_msg_id and not is_creating:
        pending_status_creation[user_id] = True # Lock lag gaya
        msg = await safe_api_call(message.reply_text, f"📥 Queueing files... ({total_queued})", quote=True)
        if msg:
            async with file_lock:
                user_states = load_json(STATES_FILE, {})
                user_states[user_id]["status_msg_id"] = msg.id
                save_json(STATES_FILE, user_states)
        pending_status_creation[user_id] = False # Lock khul gaya

    # 2. Update subsequent files (Throttled to every 1.5s)
    elif status_msg_id:
        now = time.time()
        if now - last_edit_time.get(user_id, 0) > 1.5:
            last_edit_time[user_id] = now
            await safe_api_call(client.edit_message_text, chat_id, status_msg_id, f"📥 Queueing files... ({total_queued})")

    # 3. Timer Debounce Logic: Cancel old countdown and start a new one
    if user_id in active_timers:
        active_timers[user_id].cancel()
    
    active_timers[user_id] = asyncio.create_task(finalize_batch(client, chat_id, user_id))


if __name__ == "__main__":
    print("Core Production Bot started successfully with Advanced Anti-Flood Protections!")
    app.run()
