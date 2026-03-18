import asyncio
import json
import re
import ssl
import websockets
import os
import base64
from db import DBManager

# --- CONFIG ---
import os
from dotenv import load_dotenv
load_dotenv()
BOT_ID = os.getenv("JOYSTICK_BOT_ID")
BOT_SECRET = os.getenv("JOYSTICK_BOT_SECRET")

class BotManager:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, 'buddy.db')
        print(f"🤖 Bot Manager looking for DB at: {db_path}")
        self.db = DBManager(db_path=db_path)
        self.running = True

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

    async def start(self):
        print("🤖 Bot Manager Started - GLOBAL GATEWAY MODE")
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
                
                # Subscribe
                subscribe_cmd = {
                    "command": "subscribe",
                    "identifier": json.dumps({"channel": "GatewayChannel"})
                }
                await ws.send(json.dumps(subscribe_cmd))
                
                print("✅ Connected! Waiting for events...")

                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("type") in ["ping", "confirm_subscription"]: continue

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
            if content.lower().startswith("!help"):
                await self.send_chat(ws, channel_id, "✨ Commands: !boop @user | !pet @user | !avatar 🎭 (subs) | !paint #hex (subs)")

            elif content.lower().startswith("!boop"):
                parts = content.split(" ")
                if len(parts) > 1:
                    target = parts[1].replace("@", "")
                    viewer = self.db.get_viewers(channel_id)
                    target_data = next((v for v in viewer if v["username"].lower() == target.lower()), None)
                    streamer = self.db.get_streamer(channel_id)
                    default_emoji = streamer.get("default_emoji", "🙂") if streamer else "🙂"
                    target_emoji = target_data["emoji"] if target_data and target_data.get("emoji") else default_emoji
                    print(f"👉 BOOP! {author} -> {target}")
                    await self.send_chat(ws, channel_id, f"@{author} boops @{target}! {target_emoji} ✨")

            elif content.lower().startswith("!pet"):
                target = author 
                parts = content.split(" ")
                if len(parts) > 1:
                    target = parts[1].replace("@", "")
                
                print(f"❤️ PET DETECTED! From {author} -> {target}")
                self.db.log_event(channel_id, "pet", json.dumps({"source": author, "target": target}))

            elif content.lower().startswith("!paint") and is_sub:
                parts = content.split(" ")
                if len(parts) > 1:
                    color = parts[1]
                    if re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color):
                        print(f"🎨 PAINT DETECTED! {author} -> {color}")
                        self.db.update_viewer(channel_id, author, color=color)
                        await self.send_chat(ws, channel_id, f"@{author} your buddy has been repainted! 🎨")
                    else:
                        await self.send_chat(ws, channel_id, f"@{author} please use a valid hex color, e.g. !paint #ff6600")

            elif content.lower().startswith("!avatar") and is_sub:
                parts = content.split(" ")
                if len(parts) > 1:
                    emoji = parts[1]
                    print(f"🎭 AVATAR DETECTED! {author} -> {emoji}")
                    self.db.update_viewer(channel_id, author, emoji=emoji)
                    await self.send_chat(ws, channel_id, f"@{author} your avatar has been updated! {emoji}")
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
