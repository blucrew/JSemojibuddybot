import os
from dotenv import load_dotenv

load_dotenv()

# --- AUTHENTICATION ---
# Set JOYSTICK_ACCESS_TOKEN in your .env file
ACCESS_TOKEN = os.getenv("JOYSTICK_ACCESS_TOKEN")

# --- THEME SETTINGS ---
# The default emoji if a user hasn't chosen one
DEFAULT_EMOJI = "🤖"

# The name of your collection (displayed at the top of the list)
COLLECTION_NAME = "The Robot Factory"

# Visual Style for the OBS List
DOCK_BG_COLOR = "#050011"      # Background of the whole box
HEADER_COLOR = "#00ffff"       # Color of the Title Text
TEXT_COLOR = "#ffffff"         # Default text color
FONT_FAMILY = "monospace"      # Font style

# --- PERMISSIONS ---
# If True, only subscribers can use !paint and !avatar
SUBS_ONLY_CUSTOMIZATION = True
