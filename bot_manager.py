import asyncio
import json
import re
import ssl
import websockets
import os
import base64
import time
import hashlib
import emoji
from dotenv import load_dotenv
from db import DBManager

# --- CONFIG ---
load_dotenv()
BOT_ID = os.getenv("JOYSTICK_BOT_ID")
BOT_SECRET = os.getenv("JOYSTICK_BOT_SECRET")

# Users who can use all commands regardless of subscription status
PRIVILEGED_USERS = {"silasblu", "rustyblu"}

class BotManager:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, 'buddy.db')
        print(f"🤖 Bot Manager looking for DB at: {db_path}")
        self.db = DBManager(db_path=db_path)
        self.running = True

    def get_viewer_display_emoji(self, channel_id, username, streamer):
        viewers = self.db.get_viewers(channel_id)
        v = next((v for v in viewers if v["username"].lower() == username.lower()), None)
        if v and v.get("emoji"):
            return v["emoji"]
        if streamer and username.lower() == (streamer.get("streamer_username") or "").lower():
            return streamer.get("streamer_own_emoji", "👑")
        return streamer.get("default_emoji", "🙂") if streamer else "🙂"

    async def send_chat(self, ws, channel_id, text):
        payload = {
            "command": "message",
            "identifier": json.dumps({"channel": "GatewayChannel", "id": str(channel_id)}),
            "data": json.dumps({"action": "send_message", "text": text})
        }
        await ws.send(json.dumps(payload))

    def get_basic_auth_token(self):
        creds = f"{BOT_ID}:{BOT_SECRET}"
        return base64.b64encode(creds.encode()).decode()

    async def timeout_loop(self):
        while self.running:
            await asyncio.sleep(60)
            streamers = self.db.get_all_streamers()
            for streamer in streamers:
                channel_id = streamer["channel_id"]
                full = self.db.get_streamer(channel_id)
                timeout_minutes = int(full.get("viewer_timeout_minutes", 45))
                removed = self.db.remove_timed_out_viewers(channel_id, timeout_minutes)
                if removed:
                    print(f"⏱️ Timed out {removed} viewer(s) from [{channel_id}]")

    async def start(self):
        print("🤖 Bot Manager Started - GLOBAL GATEWAY MODE")
        self.db.clear_all_active_viewers()
        print("🧹 Cleared stale active viewers from previous session.")
        asyncio.create_task(self.timeout_loop())
        while self.running:
            try:
                await self.connect_to_gateway()
            except Exception as e:
                print(f"💥 Global Crash: {e}")
                await asyncio.sleep(5)

    async def connect_to_gateway(self):
        print(f"🔌 Connecting to Joystick.TV Global Gateway...")
        token = self.get_basic_auth_token()
        uri = f"wss://joystick.tv/cable?token={token}"
        ssl_context = ssl.create_default_context()
        
        try:
            async with websockets.connect(
                uri, 
                ssl=ssl_context, 
                subprotocols=["actioncable-v1-json", "actioncable-unsupported"]
            ) as ws:
                
                # Per-channel subscriptions — handles both receiving events AND sending
                streamers = self.db.get_all_streamers()
                for s in streamers:
                    cid = str(s['channel_id'])
                    await ws.send(json.dumps({
                        "command": "subscribe",
                        "identifier": json.dumps({"channel": "GatewayChannel", "id": cid})
                    }))
                    print(f"📡 Subscribed to channel {cid}")

                print("✅ Connected! Waiting for events...")
                seen = {}  # hash -> timestamp, deduplicates events received per-subscription

                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("type") in ["ping", "confirm_subscription"]: continue

                    # Deduplicate: gateway sends each event once per subscription,
                    # each wrapped in a different identifier — hash the inner message only
                    now = time.time()
                    inner = json.dumps(data.get("message", {}), sort_keys=True)
                    h = hashlib.md5(inner.encode()).hexdigest()
                    seen = {k: v for k, v in seen.items() if now - v < 5}
                    if h in seen:
                        continue
                    seen[h] = now

                    # 1. Get the raw message payload
                    message = data.get("message", {})
                    if not message: continue

                    # 2. Extract Channel ID
                    channel_id = message.get("channelId")
                    if not channel_id: continue

                    # 3. Check if we manage this streamer
                    streamer = self.db.get_streamer(channel_id)
                    if not streamer:
                        # Silent ignore for other channels to keep logs clean
                        continue

                    # 4. Process the event
                    await self.process_event(ws, channel_id, message)

        except Exception as e:
            print(f"⚠️ Connection lost: {e}")
            await asyncio.sleep(5)

    async def process_event(self, ws, channel_id, message):
        event_name = message.get("event")
        event_type = message.get("type")

        # --- CHAT MESSAGE ---
        if event_name == 'ChatMessage':
            content = message.get("text", "")
            author_data = message.get("author", {})
            author = author_data.get("username", "Unknown")
            is_sub = author_data.get("isSubscriber", False) # Fixed parsing
            
            print(f"📩 [{channel_id}] {author}: {content}")
            
            self.db.add_active_viewer(channel_id, author)

            # COMMANDS
            if content.lower().startswith("!emojihelp"):
                await self.send_chat(ws, channel_id, "✨ Commands: !boop @user | !pet @user (streamer) | !emoji 🎭 (subs) | !namecolor pink (subs) | !emojimarch / !emojichaos (streamer)")

            elif content.lower().startswith("!boop"):
                parts = content.split()
                if len(parts) > 1:
                    target = parts[1].replace("@", "")
                    streamer = self.db.get_streamer(channel_id)
                    source_emoji = self.get_viewer_display_emoji(channel_id, author, streamer)
                    target_emoji = self.get_viewer_display_emoji(channel_id, target, streamer)
                    print(f"👉 BOOP! {author} -> {target}")
                    await self.send_chat(ws, channel_id, f"{source_emoji} {author} booped {target_emoji} {target}!")
                    self.db.log_event(channel_id, "boop", json.dumps({"source": author, "target": target}))

            elif content.lower().startswith("!pet"):
                parts = content.split()
                if len(parts) > 1:
                    target = parts[1].replace("@", "")
                    streamer = self.db.get_streamer(channel_id)
                    streamer_username = (streamer.get("streamer_username") or "") if streamer else ""
                    if author.lower() != streamer_username.lower():
                        return
                    source_emoji = self.get_viewer_display_emoji(channel_id, author, streamer)
                    target_emoji = self.get_viewer_display_emoji(channel_id, target, streamer)
                    print(f"❤️ PET! {author} -> {target}")
                    await self.send_chat(ws, channel_id, f"{source_emoji} {author} petted {target_emoji} {target}!")
                    self.db.log_event(channel_id, "pet", json.dumps({"source": author, "target": target}))

            elif content.lower().startswith("!emojimarch"):
                streamer = self.db.get_streamer(channel_id)
                streamer_username = (streamer.get("streamer_username") or "") if streamer else ""
                if author.lower() == streamer_username.lower():
                    self.db.update_config(channel_id, {"physics_mode": "march"})
                    self.db.log_event(channel_id, "mode_change", json.dumps({"mode": "march"}))

            elif content.lower().startswith("!emojichaos"):
                streamer = self.db.get_streamer(channel_id)
                streamer_username = (streamer.get("streamer_username") or "") if streamer else ""
                if author.lower() == streamer_username.lower():
                    self.db.update_config(channel_id, {"physics_mode": "chaos"})
                    self.db.log_event(channel_id, "mode_change", json.dumps({"mode": "chaos"}))

            elif content.lower().startswith("!namecolor") and not is_sub and author.lower() not in PRIVILEGED_USERS:
                print(f"🚫 [{channel_id}] {author} tried !namecolor but is_sub={is_sub}")
                await self.send_chat(ws, channel_id, f"@{author} !namecolor is for subscribers only. 🎨")

            elif content.lower().startswith("!namecolor") and (is_sub or author.lower() in PRIVILEGED_USERS):
                parts = content.split()
                if len(parts) > 1:
                    color = parts[1]
                    if re.fullmatch(r"[a-zA-Z]+", color) or re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color):
                        print(f"🎨 PAINT DETECTED! {author} -> {color}")
                        self.db.update_viewer(channel_id, author, color=color)
                        await self.send_chat(ws, channel_id, f"@{author} your buddy has been repainted! 🎨")
                    else:
                        await self.send_chat(ws, channel_id, f"@{author} try a color name like !namecolor pink or a hex code like !namecolor #ff6600")

            elif content.lower().startswith("!emoji") and not is_sub and author.lower() not in PRIVILEGED_USERS:
                print(f"🚫 [{channel_id}] {author} tried !emoji but is_sub={is_sub}")
                await self.send_chat(ws, channel_id, f"@{author} !emoji is for subscribers only. 🎭")

            elif content.lower().startswith("!emoji") and (is_sub or author.lower() in PRIVILEGED_USERS):
                parts = content.split()
                if len(parts) > 1:
                    avatar = parts[1]
                    if emoji.emoji_count(avatar) >= 1:
                        print(f"🎭 EMOJI DETECTED! {author} -> {avatar}")
                        self.db.update_viewer(channel_id, author, emoji=avatar)
                        await self.send_chat(ws, channel_id, f"@{author} your buddy has been updated! {avatar}")
                    else:
                        await self.send_chat(ws, channel_id, f"@{author} please use a single emoji, e.g. !emoji 🐸")
            else:
                self.db.update_viewer(channel_id, author, is_subscriber=is_sub)

        # --- USER ENTERS STREAM ---
        elif event_name == 'UserPresence' and event_type == 'enter_stream':
            username = message.get('text') # In Gateway, text is the username here
            if username:
                print(f"👉 [{channel_id}] {username} entered.")
                self.db.add_active_viewer(channel_id, username)

        # --- USER LEAVES STREAM ---
        elif event_name == 'UserPresence' and event_type == 'leave_stream':
            username = message.get('text')
            if username:
                print(f"👈 [{channel_id}] {username} left.")
                self.db.remove_active_viewer(channel_id, username)
