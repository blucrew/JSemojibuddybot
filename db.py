import sqlite3
import time
import json

class DBManager:
    def __init__(self, db_path="buddy.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        # Streamers table
        c.execute('''CREATE TABLE IF NOT EXISTS streamers
                     (channel_id TEXT PRIMARY KEY, access_token TEXT, refresh_token TEXT, 
                      config TEXT, streamer_username TEXT)''')
        # Viewers table (long-term data)
        c.execute('''CREATE TABLE IF NOT EXISTS viewers
                     (channel_id TEXT, username TEXT, emoji TEXT, color TEXT, 
                      is_subscriber INTEGER, last_seen REAL,
                      PRIMARY KEY (channel_id, username))''')
        # Active Viewers table (session data)
        c.execute('''CREATE TABLE IF NOT EXISTS active_viewers
                     (channel_id TEXT, username TEXT,
                      PRIMARY KEY (channel_id, username))''')
        # Events table (for polling)
        c.execute('''CREATE TABLE IF NOT EXISTS events
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT, 
                      type TEXT, data TEXT, timestamp REAL)''')
        conn.commit()
        conn.close()

    def update_streamer(self, channel_id, access_token, refresh_token):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        # Check if exists to preserve config
        c.execute("SELECT config FROM streamers WHERE channel_id=?", (channel_id,))
        row = c.fetchone()
        if row:
            c.execute("UPDATE streamers SET access_token=?, refresh_token=? WHERE channel_id=?",
                      (access_token, refresh_token, channel_id))
        else:
            default_config = json.dumps({
                "collection_name": "My Emoji Army",
                "viewer_timeout_minutes": 45,
                "font_family": "Nunito",
                "bg_color": "#00000000", # Transparent
                "text_color": "#ffffff",
                "streamer_own_emoji": "👑",
                "subscriber_emoji": "⭐",
                "default_emoji": "🙂"
            })
            c.execute("INSERT INTO streamers (channel_id, access_token, refresh_token, config) VALUES (?, ?, ?, ?)",
                      (channel_id, access_token, refresh_token, default_config))
        conn.commit()
        conn.close()

    def update_streamer_tokens(self, channel_id, access_token, refresh_token):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE streamers SET access_token=?, refresh_token=? WHERE channel_id=?",
                  (access_token, refresh_token, channel_id))
        conn.commit()
        conn.close()

    def update_config(self, channel_id, new_config_dict):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT config FROM streamers WHERE channel_id=?", (channel_id,))
        row = c.fetchone()
        if row:
            current_config = json.loads(row[0])
            current_config.update(new_config_dict)
            c.execute("UPDATE streamers SET config=? WHERE channel_id=?", (json.dumps(current_config), channel_id))
        conn.commit()
        conn.close()

    def get_streamer(self, channel_id):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM streamers WHERE channel_id=?", (channel_id,))
        row = c.fetchone()
        conn.close()
        if row:
            # Row mapping: 0=id, 1=access, 2=refresh, 3=config, 4=username
            data = json.loads(row[3])
            data['channel_id'] = row[0]
            data['access_token'] = row[1]
            if len(row) > 4:
                data['streamer_username'] = row[4]
            return data
        return None

    # THIS IS THE METHOD THAT WAS MISSING
    def get_all_streamers(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row # Helper to get dict-like access
        c = conn.cursor()
        c.execute("SELECT * FROM streamers")
        rows = c.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            results.append({
                'channel_id': row['channel_id'],
                'access_token': row['access_token'],
                'refresh_token': row['refresh_token']
            })
        return results

    def add_active_viewer(self, channel_id, username):
        self.update_viewer(channel_id, username) # Ensure they exist in the main table first
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO active_viewers (channel_id, username) VALUES (?, ?)", (channel_id, username))
        conn.commit()
        conn.close()

    def remove_active_viewer(self, channel_id, username):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM active_viewers WHERE channel_id=? AND username=?", (channel_id, username))
        conn.commit()
        conn.close()

    def clear_active_viewers(self, channel_id):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM active_viewers WHERE channel_id=?", (channel_id,))
        conn.commit()
        conn.close()

    def clear_all_active_viewers(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM active_viewers")
        conn.commit()
        conn.close()

    def remove_timed_out_viewers(self, channel_id, timeout_minutes):
        cutoff = time.time() - (timeout_minutes * 60)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''DELETE FROM active_viewers
                     WHERE channel_id=? AND username IN (
                         SELECT username FROM viewers
                         WHERE channel_id=? AND last_seen < ?
                     )''', (channel_id, channel_id, cutoff))
        removed = c.rowcount
        conn.commit()
        conn.close()
        return removed

    def get_active_viewers(self, channel_id):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # JOIN active_viewers with viewers to get all data
        c.execute('''SELECT v.* FROM viewers v
                     INNER JOIN active_viewers av 
                     ON v.channel_id = av.channel_id AND v.username = av.username
                     WHERE v.channel_id = ?''', (channel_id,))
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def update_viewer(self, channel_id, username, emoji=None, color=None, is_subscriber=None):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        now = time.time()
        
        # Check existence
        c.execute("SELECT * FROM viewers WHERE channel_id=? AND username=?", (channel_id, username))
        row = c.fetchone()
        
        if row:
            # Update existing
            # row: 0=cid, 1=user, 2=emoji, 3=color, 4=is_sub, 5=last_seen
            new_emoji = emoji if emoji is not None else row[2]
            new_color = color if color is not None else row[3]
            new_sub = int(is_subscriber) if is_subscriber is not None else row[4]
            
            c.execute('''UPDATE viewers SET emoji=?, color=?, is_subscriber=?, last_seen=? 
                         WHERE channel_id=? AND username=?''',
                      (new_emoji, new_color, new_sub, now, channel_id, username))
        else:
            # Insert new
            c.execute('''INSERT INTO viewers (channel_id, username, emoji, color, is_subscriber, last_seen)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (channel_id, username, emoji, color, int(is_subscriber or 0), now))
        conn.commit()
        conn.close()

    def get_viewers(self, channel_id):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM viewers WHERE channel_id=?", (channel_id,))
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def log_event(self, channel_id, type, data):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT INTO events (channel_id, type, data, timestamp) VALUES (?, ?, ?, ?)",
                  (channel_id, type, data, time.time()))
        # Cleanup old events
        c.execute("DELETE FROM events WHERE timestamp < ?", (time.time() - 10,))
        conn.commit()
        conn.close()

    def get_events(self, channel_id, since_timestamp):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM events WHERE channel_id=? AND timestamp > ?", (channel_id, since_timestamp))
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]
