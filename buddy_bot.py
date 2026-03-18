import asyncio
import websockets
import json
import logging
import os
from config import *

# SETUP LOGGING
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("buddy.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# FILES
DATA_FILE = "buddy_data.json"
OUTPUT_HTML = "obs_buddies.html"

# COLOR MAP (For !paint command)
COLOR_MAP = {
    "red": "#ff0000", "blue": "#00aaff", "green": "#00ff00", "pink": "#ff00ff",
    "orange": "#ffaa00", "purple": "#aa00ff", "yellow": "#ffff00", "cyan": "#00ffff",
    "white": "#ffffff", "gold": "#ffd700", "silver": "#c0c0c0"
}

class EmojiBuddyBot:
    def __init__(self):
        self.user_data = self.load_data()
        self.active_users = set() # Users active in THIS session
        self.ws_url = "wss://joystick.tv/cable"
        
    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save_data(self):
        with open(DATA_FILE, 'w') as f:
            json.dump(self.user_data, f, indent=2)
        self.update_html()

    def update_html(self):
        """Generates the HTML file for OBS"""
        try:
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta http-equiv="refresh" content="3"> <style>
                    body {{ background-color: {DOCK_BG_COLOR}; color: {TEXT_COLOR}; font-family: {FONT_FAMILY}; overflow: hidden; margin: 0; padding: 10px; }}
                    h2 {{ color: {HEADER_COLOR}; border-bottom: 2px solid {HEADER_COLOR}; margin: 0 0 10px 0; font-size: 1.2em; text-transform: uppercase; }}
                    .buddy-row {{ font-size: 1.1em; margin-bottom: 5px; font-weight: bold; text-shadow: 1px 1px 0 #000; }}
                    .emoji {{ margin-right: 8px; }}
                </style>
            </head>
            <body>
                <h2>{COLLECTION_NAME}</h2>
                <div id="list">
            """
            
            # Add rows for all ACTIVE users
            for username in sorted(list(self.active_users)):
                user_key = username.lower()
                # Get preferences or defaults
                prefs = self.user_data.get(user_key, {"color": TEXT_COLOR, "emoji": DEFAULT_EMOJI})
                
                # Ensure emoji exists (fallback if missing in saved data)
                emoji = prefs.get("emoji", DEFAULT_EMOJI)
                color = prefs.get("color", TEXT_COLOR)
                
                html_content += f'<div class="buddy-row" style="color: {color}"><span class="emoji">{emoji}</span>{username}</div>\n'
            
            html_content += """
                </div>
            </body>
            </html>
            """
            
            with open(OUTPUT_HTML, 'w', encoding="utf-8") as f:
                f.write(html_content)
                
        except Exception as e:
            logger.error(f"Error writing HTML: {e}")

    async def send_chat(self, ws, channel_id, text):
        payload = {
            "command": "message",
            "identifier": json.dumps({"channel": "GatewayChannel", "id": str(channel_id)}),
            "data": json.dumps({"action": "send_message", "text": text})
        }
        await ws.send(json.dumps(payload))

    async def handle_message(self, ws, message):
        try:
            data = json.loads(message)
            
            # Ignore Keep-Alive Pings (which are lists) or system messages
            if isinstance(data, list) or "ping" in data: return
            if data.get("type") == "confirm_subscription": return

            # Look for actual event data
            if "message" in data and "event" in data["message"]:
                event = data["message"]["event"]
                payload = data["message"].get("data", {})
                channel_id = data["message"].get("channelId")
                
                if event == "ChatMessage":
                    author = payload.get("author", {}).get("username", "Unknown")
                    text = payload.get("text", "")
                    is_sub = payload.get("author", {}).get("isSubscriber", False)
                    
                    # 1. Mark User as Active
                    if author not in self.active_users:
                        self.active_users.add(author)
                        self.update_html() # Refresh display immediately
                    
                    user_key = author.lower()
                    words = text.split()
                    command = words[0].lower() if words else ""

                    # --- COMMAND: !paint ---
                    if command == "!paint" and len(words) > 1:
                        if SUBS_ONLY_CUSTOMIZATION and not is_sub: return
                        
                        color_name = words[1].lower()
                        hex_code = COLOR_MAP.get(color_name)
                        
                        # Allow direct hex codes if they start with #
                        if not hex_code and color_name.startswith("#") and len(color_name) == 7:
                            hex_code = color_name
                            
                        if hex_code:
                            if user_key not in self.user_data: self.user_data[user_key] = {}
                            self.user_data[user_key]["color"] = hex_code
                            self.save_data()
                            await self.send_chat(ws, channel_id, f"@{author}, your buddy has been repainted!")

                    # --- COMMAND: !avatar ---
                    elif command == "!avatar" and len(words) > 1:
                        if SUBS_ONLY_CUSTOMIZATION and not is_sub: return
                        
                        new_emoji = words[1]
                        # Simple check: Ensure it's not a long text string (likely an emoji)
                        if len(new_emoji) < 4: 
                            if user_key not in self.user_data: self.user_data[user_key] = {}
                            self.user_data[user_key]["emoji"] = new_emoji
                            self.save_data()
                            await self.send_chat(ws, channel_id, f"@{author}, your avatar is updated: {new_emoji}")

                    # --- COMMAND: !boop ---
                    elif command == "!boop" and len(words) > 1:
                        target = words[1].replace("@", "")
                        target_key = target.lower()
                        
                        # Get Target's Emoji (or Default)
                        target_prefs = self.user_data.get(target_key, {})
                        target_emoji = target_prefs.get("emoji", DEFAULT_EMOJI)
                        
                        await self.send_chat(ws, channel_id, f"@{author} boops @{target}! {target_emoji} ✨")

        except Exception as e:
            logger.error(f"Message Error: {e}")

    async def run(self):
        while True:
            try:
                logger.info("Connecting to Joystick.TV...")
                # TURBOTACK LESSON: Force subprotocols to prevent disconnects
                async with websockets.connect(self.ws_url, subprotocols=["actioncable-v1-json", "actioncable-unsupported"]) as ws:
                    logger.info("Connected!")
                    
                    # Authenticate/Subscribe using the Token from Config
                    # We have to decode the token to find the User ID (Channel ID)
                    # NOTE: Since we are manual, we assume the token is valid. 
                    # We subscribe to the GatewayChannel using the token.
                    
                    # 1. Send Subscribe Command
                    # We need the Channel ID. Usually inside the JWT, but let's try 
                    # a generic subscribe if we don't parse the JWT. 
                    # ACTUALLY: Joystick requires the Channel ID in the subscribe command.
                    # We will parse the JWT simply to get the ID.
                    import base64
                    
                    try:
                        token_parts = ACCESS_TOKEN.split('.')
                        payload = json.loads(base64.urlsafe_b64decode(token_parts[1] + '==').decode('utf-8'))
                        channel_id = payload.get('channel_id') or payload.get('user_id')
                    except:
                        logger.error("Invalid Token in config.py")
                        return

                    sub_msg = {
                        "command": "subscribe",
                        "identifier": json.dumps({"channel": "GatewayChannel", "id": str(channel_id)})
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info(f"Subscribed to Channel {channel_id}")

                    async for msg in ws:
                        await self.handle_message(ws, msg)
                        
            except Exception as e:
                logger.error(f"Connection lost: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

if __name__ == "__main__":
    bot = EmojiBuddyBot()
    # Initialize the HTML on start
    bot.update_html()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
