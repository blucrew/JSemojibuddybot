import aiohttp
from aiohttp import web
import json
import base64
import traceback
import asyncio
import time
import os
import sqlite3
from datetime import datetime
from db import DBManager
from bot_manager import BotManager

# --- CONFIG ---
from dotenv import load_dotenv
load_dotenv()
BOT_ID = os.getenv("JOYSTICK_BOT_ID")
BOT_SECRET = os.getenv("JOYSTICK_BOT_SECRET")
REDIRECT_URI = os.getenv("JOYSTICK_REDIRECT_URI")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

# Initialize DB
db_path = os.path.join(BASE_DIR, 'buddy.db')
print(f"--- SERVER STARTUP: Using Database at {db_path} ---", flush=True)
db = DBManager(db_path=db_path)

# --- HELPER ---
def get_template(filename):
    try:
        path = os.path.join(TEMPLATE_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Error loading {filename}: {e}", flush=True)
        return f"<h1>Error loading template: {filename}</h1>"

async def refresh_joystick_token(channel_id):
    print(f"🔄 REFRESH: Attempting to refresh token for {channel_id}...", flush=True)
    streamer = db.get_streamer(channel_id)
    if not streamer or not streamer.get('refresh_token'):
        print(f"❌ REFRESH: No refresh token found in DB for {channel_id}", flush=True)
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://joystick.tv/api/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": streamer['refresh_token']
                },
                auth=aiohttp.BasicAuth(BOT_ID, BOT_SECRET)) as resp:

                response_text = await resp.text()
                
                if resp.status == 200:
                    new_tokens = json.loads(response_text)
                    new_access = new_tokens.get('access_token')
                    new_refresh = new_tokens.get('refresh_token')
                    
                    if new_access:
                        final_refresh = new_refresh if new_refresh else streamer['refresh_token']
                        db.update_streamer_tokens(channel_id, new_access, final_refresh)
                        print(f"✅ REFRESH: Success! New token saved for {channel_id}", flush=True)
                        return new_access
                    else:
                        print(f"❌ REFRESH: Response 200 but missing access_token. Body: {response_text}", flush=True)
                        return None
                else:
                    print(f"❌ REFRESH: Failed with status {resp.status}. Body: {response_text}", flush=True)
                    return None
    except Exception as e:
        print(f"💥 REFRESH CRASH: {e}", flush=True)
        return None

# --- ROUTES ---

async def handle_home(request):
    auth_url = f"https://joystick.tv/api/oauth/authorize?client_id={BOT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=bot%20channel%3Aread%20user%3Aread%3Aemail%20ViewSubscriptions"
    raise web.HTTPFound(auth_url)

async def handle_styler(request):
    html = get_template("css_styler.html")
    return web.Response(text=html, content_type='text/html')

async def handle_callback(request):
    try:
        code = request.query.get('code')
        if not code: return web.Response(text="Error: No code.")

        async with aiohttp.ClientSession() as session:
            async with session.post("https://joystick.tv/api/oauth/token",
                data={ "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI },
                auth=aiohttp.BasicAuth(BOT_ID, BOT_SECRET)) as resp:
                tokens = json.loads(await resp.text())

            if 'access_token' not in tokens: return web.Response(text=f"No token received. {tokens}")

            parts = tokens['access_token'].split('.')
            padding = 4 - (len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + ("=" * padding)).decode('utf-8'))
            channel_id = payload.get('channel_id') or payload.get('user_id')

            username = "Streamer"
            try:
                async with session.get(
                    "https://joystick.tv/api/users/me", 
                    headers={ "Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json" }
                ) as u_resp:
                    if u_resp.status == 200:
                        u_data = await u_resp.json(content_type=None)
                        if 'data' in u_data and 'slug' in u_data['data']:
                            username = u_data['data']['slug']
                        elif 'username' in u_data:
                            username = u_data['username']
            except: pass

            db.update_streamer(str(channel_id), tokens['access_token'], tokens.get('refresh_token'))
            db.update_config(str(channel_id), {'streamer_username': username})
            
            raise web.HTTPFound(f'/emojibuddy/dashboard/{channel_id}')

    except web.HTTPFound: raise
    except Exception as e: return web.Response(text=f"ERROR: {traceback.format_exc()}")

async def handle_dashboard(request):
    try:
        cid = request.match_info['channel_id']
        s = db.get_streamer(cid)
        if not s: return web.Response(text="User not found (Try re-authorizing)", status=404)
        
        template = get_template("dashboard.html")
        
        # Use active viewers for the main list
        all_viewers = db.get_active_viewers(cid)
        all_viewers.sort(key=lambda x: x['username'].lower())
        
        # --- HTML Generation (Standard) ---
        all_viewers_html = ""
        if not all_viewers:
            all_viewers_html = "<div style='text-align:center; padding: 20px; color: #999;'>No active viewers in chat.</div>"
        else:
            for user in all_viewers:
                u = user['username']
                db_emoji = user.get('emoji') 
                is_sub = user.get('is_subscriber')
                calc_default = s.get('subscriber_emoji') if is_sub else s.get('default_emoji')
                val_e = db_emoji if db_emoji else ""
                ph_e = calc_default if calc_default else "Standard"
                c = user.get('color') or s.get('text_color')

                all_viewers_html += f"""
                <div class="sub-row">
                    <div class="sub-name">{u}</div>
                    <input type="text" name="v_emoji_{u}" value="{val_e}" class="sub-emoji-in" placeholder="{ph_e}">
                    <input type="color" name="v_color_{u}" value="{c}" class="sub-color-in">
                </div>
                """
        s['subscriber_list_html'] = all_viewers_html

        # --- HTML Generation (Subs Only) ---
        db_subs = db.get_viewers(cid)
        subs_only = [v for v in db_subs if v.get('is_subscriber')]
        
        sub_only_list_html = ""
        if not subs_only:
            sub_only_list_html = "<div style='text-align:center; padding: 20px; color: #999;'>No active subscribers. Try the 'Sync Subscribers' button!</div>"
        else:
            for user in subs_only:
                u = user['username']
                db_emoji = user.get('emoji') 
                is_sub = user.get('is_subscriber')
                calc_default = s.get('subscriber_emoji') if is_sub else s.get('default_emoji')
                val_e = db_emoji if db_emoji else ""
                ph_e = calc_default if calc_default else "Standard"
                c = user.get('color') or s.get('text_color')

                sub_only_list_html += f"""
                <div class="sub-row">
                    <div class="sub-name">{u}</div>
                    <input type="text" name="v_emoji_{u}" value="{val_e}" class="sub-emoji-in" placeholder="{ph_e}">
                    <input type="color" name="v_color_{u}" value="{c}" class="sub-color-in">
                </div>
                """
        s['sub_only_list_html'] = sub_only_list_html

        # Fill defaults
        layout = s.get('layout_mode', 'standard')
        s['chk_std'] = 'checked' if layout == 'standard' else ''
        s['chk_tall'] = 'checked' if layout == 'tall' else ''
        s['chk_wide'] = 'checked' if layout == 'wide' else ''

        physics = s.get('physics_mode', 'chaos')
        s['chk_chaos'] = 'checked' if physics == 'chaos' else ''
        s['chk_march'] = 'checked' if physics == 'march' else ''

        s['show_title_checked'] = 'checked' if s.get('show_title', 1) else ''
        s['show_border_checked'] = 'checked' if s.get('show_border', 0) else ''
        if 'box_border_radius' not in s: s['box_border_radius'] = 15
        if 'viewer_timeout_minutes' not in s: s['viewer_timeout_minutes'] = 45
        if 'subscriber_emoji' not in s: s['subscriber_emoji'] = '⭐'
        if 'streamer_own_emoji' not in s: s['streamer_own_emoji'] = '👑'
        if 'border_color' not in s: s['border_color'] = '#ffffff'
        if 'streamer_username' not in s: s['streamer_username'] = ''
        
        font = s.get('font_family', 'Nunito')
        for f in ['Nunito', 'Arial', 'Courier', 'Impact', 'Orbitron', 'Permanent Marker', 'Inter']:
            s[f'sel_{f.replace(" ", "_")}'] = 'selected' if font == f else ''

        # Replace keys
        for key, value in s.items():
            template = template.replace(f"{{{key}}}", str(value))

        return web.Response(text=template, content_type='text/html')
    except Exception as e:
        return web.Response(text=f"ERROR: {traceback.format_exc()}")

async def handle_sync_subs(request):
    try:
        cid = request.match_info['channel_id']
        print(f"🔄 SYNC: Starting subscriber sync for {cid}", flush=True)
        
        all_subs = []
        page = 1
        
        # Retry loop (Attempt 0 = normal, Attempt 1 = after refresh)
        for attempt in range(2):
            streamer = db.get_streamer(cid)
            if not streamer or not streamer.get('access_token'):
                print("❌ SYNC: Streamer missing or no token", flush=True)
                return web.Response(text="Streamer not found or token missing", status=404)

            access_token = streamer['access_token']
            success_in_this_attempt = False
            
            print(f"🔄 SYNC: Using Token (Attempt {attempt})...", flush=True)

            async with aiohttp.ClientSession() as session:
                while True: # Pagination Loop
                    api_url = f"https://joystick.tv/api/users/subscriptions?page={page}&per_page=50"
                    headers = {"Authorization": f"Bearer {access_token}"}
                    
                    async with session.get(api_url, headers=headers) as resp:
                        # CASE 1: Token Expired (401)
                        if resp.status == 401:
                            if attempt == 0:
                                print(f"⚠️ SYNC: Token expired (401). Refreshing...", flush=True)
                                new_token = await refresh_joystick_token(cid)
                                if new_token:
                                    print("✅ SYNC: Token refreshed! Retrying sync loop...", flush=True)
                                    break 
                                else:
                                    return web.Response(text="Token expired and auto-refresh failed.", status=401)
                            else:
                                print("❌ SYNC: Token still invalid after refresh.", flush=True)
                                return web.Response(text="Token invalid even after refresh.", status=401)
                        
                        # CASE 2: Other API Error
                        if resp.status != 200:
                            err = await resp.text()
                            print(f"❌ SYNC: API Error {resp.status}: {err}", flush=True)
                            return web.Response(text=f"API Error: {err}", status=resp.status)
                        
                        # CASE 3: Success (200)
                        data = await resp.json()
                        items = data.get('items', [])
                        
                        # --- FILTER: TIME COP MODE ---
                        for item in items:
                            expiry_str = item.get('expires_at')
                            if expiry_str:
                                try:
                                    # Normalize format (handle Z for UTC)
                                    expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                                    
                                    # Compare with current time (aware)
                                    if expiry_dt < datetime.now(expiry_dt.tzinfo):
                                        print(f"⏳ Skipping expired sub: {item['username']} (Ended: {expiry_str})", flush=True)
                                        continue
                                except Exception as e:
                                    print(f"⚠️ Date Parse Error for {item['username']}: {e}", flush=True)
                            
                            # If we get here, they are active!
                            all_subs.append(item)

                        print(f"➡️ SYNC: Page {page} fetched {len(items)} items.", flush=True)
                        
                        # Pagination Check
                        if not items or not data.get('pagination') or not data['pagination'].get('next_page'):
                            success_in_this_attempt = True # We are done!
                            break 
                        
                        page += 1
            
            if success_in_this_attempt:
                break

        print(f"✅ SYNC COMPLETE: Found {len(all_subs)} VALID/ACTIVE subscribers.", flush=True)

        # --- RESET OLD SUBSCRIBERS ---
        print(f"🧹 SYNC: Wiping old subscriber statuses...", flush=True)
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("UPDATE viewers SET is_subscriber = 0 WHERE channel_id = ?", (cid,))
                conn.commit()
        except Exception as e:
            print(f"⚠️ Warning: Could not reset subscriber flags: {e}", flush=True)
        
        # Save to DB
        for sub in all_subs:
            db.update_viewer(cid, sub['username'], is_subscriber=True)

        raise web.HTTPFound(f'/emojibuddy/dashboard/{cid}')

    except web.HTTPFound:
        raise
    except Exception as e:
        print(f"💥 SYNC CRASH: {traceback.format_exc()}", flush=True)
        return web.Response(text=f"ERROR during sync: {traceback.format_exc()}")

async def handle_save(request):
    cid = request.match_info['channel_id']
    data = await request.post()
    
    config_data = {}
    viewer_updates = {}
    
    allowed_keys = [
        'collection_name', 'viewer_timeout_minutes', 'font_family',
        'streamer_own_emoji', 'subscriber_emoji', 'default_emoji',
        'bg_color', 'header_color', 'show_title',
        'text_color', 'border_color', 'show_border',
        'box_opacity', 'box_border_radius', 'layout_mode', 'physics_mode'
    ]

    config_data['show_title'] = 1 if 'show_title' in data else 0
    config_data['show_border'] = 1 if 'show_border' in data else 0

    for k, v in data.items():
        if k.startswith('v_emoji_'):
            user = k.replace('v_emoji_', '')
            if user not in viewer_updates: viewer_updates[user] = {}
            viewer_updates[user]['emoji'] = v if v.strip() else None
        elif k.startswith('v_color_'):
            user = k.replace('v_color_', '')
            if user not in viewer_updates: viewer_updates[user] = {}
            viewer_updates[user]['color'] = v
        elif k in allowed_keys and k not in ['show_title', 'show_border']:
            config_data[k] = v

    db.update_config(cid, config_data)
    
    for user, updates in viewer_updates.items():
        if user != 'StreamerExample':
            db.update_viewer(cid, user, emoji=updates.get('emoji'), color=updates.get('color'))

    raise web.HTTPFound(f'/emojibuddy/dashboard/{cid}')

async def handle_overlay(request):
    try:
        cid = request.match_info['channel_id']
        html = get_template("overlay.html")
        return web.Response(text=html.replace("REPLACE_ME_CHANNEL_ID", cid), content_type='text/html')
    except Exception as e:
        return web.Response(text=f"Overlay Error: {e}", status=500)

async def handle_api_data(request):
    cid = request.match_info['channel_id']
    since = float(request.query.get('since', 0))
    s = db.get_streamer(cid)
    if not s: return web.json_response({})
    
    active_viewers = db.get_active_viewers(cid)
    streamer_name = (s.get('streamer_username') or '').strip()
    
    if streamer_name and not any(v['username'].lower() == streamer_name.lower() for v in active_viewers):
        streamer_data = db.get_viewers(cid) 
        streamer_details = next((v for v in streamer_data if v['username'].lower() == streamer_name.lower()), None)
        
        if streamer_details:
            active_viewers.append(streamer_details)
        else: 
            active_viewers.append({
                'username': streamer_name,
                'emoji': s.get('streamer_own_emoji'),
                'color': s.get('text_color'),
                'is_subscriber': True
            })

    return web.json_response({
        "config": s,
        "viewers": active_viewers,
        "events": db.get_events(cid, since)
    })

def check_db_migrations():
    db_file = os.path.join(BASE_DIR, 'buddy.db')
    try:
        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        try:
            cur.execute("SELECT streamer_username FROM streamers LIMIT 1")
        except sqlite3.OperationalError:
            print("Patching Database: Adding streamer_username column...")
            cur.execute("ALTER TABLE streamers ADD COLUMN streamer_username TEXT")
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Migration Check: {e}")

async def start_background_bot(app):
    manager = BotManager()
    app['bot_task'] = asyncio.create_task(manager.start())

if __name__ == '__main__':
    check_db_migrations()
    app = web.Application()
    app.router.add_static('/emojibuddy/static/', path=os.path.join(BASE_DIR, 'static'), name='static')
    
    app.router.add_get('/emojibuddy/', handle_home)
    app.router.add_get('/emojibuddy/styler', handle_styler)
    app.router.add_get('/emojibuddy/auth/callback', handle_callback)
    app.router.add_get('/emojibuddy/dashboard/{channel_id}', handle_dashboard)
    app.router.add_post('/emojibuddy/api/save/{channel_id}', handle_save)
    app.router.add_get('/emojibuddy/api/sync-subs/{channel_id}', handle_sync_subs)
    app.router.add_get('/emojibuddy/overlay/{channel_id}', handle_overlay)
    app.router.add_get('/emojibuddy/api/data/{channel_id}', handle_api_data)
    
    app.on_startup.append(start_background_bot)
    web.run_app(app, port=8085)
