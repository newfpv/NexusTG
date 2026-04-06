import os
import json
import logging
from dotenv import load_dotenv

load_dotenv(override=True)

# Get file name from .env, default is language.json
LANG_FILE = os.getenv("LANG_FILE", "language.json")

translations = {}
missing_keys = set()  # Storage for missing keys to avoid spamming the logs

def load_language():
    global translations
    if os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, 'r', encoding='utf-8') as f:
                translations = json.load(f)
            logging.info(f"🌐 Language pack loaded: {LANG_FILE}")
        except Exception as e:
            logging.error(f"❌ Error loading {LANG_FILE}: {e}")
    else:
        logging.warning(f"⚠️ Translation file {LANG_FILE} not found. Using keys instead of text.")

def _(key: str, **kwargs) -> str:
    """
    Gets string by key. If kwargs are passed, formats the string.
    Example: _("welcome_msg", name="Paul")
    """
    # Check if the key exists in the dictionary
    if key not in translations:
        if key not in missing_keys:
            logging.warning(f"⚠️ Missing translation! Key '{key}' not found in {LANG_FILE}")
            missing_keys.add(key)
        text = key  # If key is missing, return the key itself (so the bot doesn't crash)
    else:
        text = translations[key]
        
    if kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError as e:
            logging.error(f"Error formatting key '{key}': missing argument {e}")
            
    return text

# Load dictionary immediately upon module import
load_language()