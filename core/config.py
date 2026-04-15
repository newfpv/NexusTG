import os
import json
import logging
import re
from dotenv import load_dotenv

load_dotenv(override=True)

class AFCFilter(logging.Filter):
    def filter(self, record):
        return "AFC is enabled" not in record.getMessage()

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M",
    force=True
)

logging.getLogger().addFilter(AFCFilter())
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
LANG_FILE = os.getenv("LANG_FILE", "language_RU.json")
DB_PATH = "sqlite+aiosqlite:///data/core_database.db"

os.makedirs("data", exist_ok=True)
os.makedirs("modules", exist_ok=True)

translations = {}
CORE_REQUIRED_KEYS = ["btn_back", "btn_cancel", "setup_guide", "test_progress"]

def _flatten_dict(d: dict) -> dict:
    flat = {}
    for k, v in d.items():
        if isinstance(v, dict):
            flat.update(_flatten_dict(v))
        else:
            flat[k] = v
    return flat

def load_language():
    global translations
    if os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, 'r', encoding='utf-8') as f:
                translations = _flatten_dict(json.load(f))
            logging.info(f"🌐 i18n: {LANG_FILE} loaded.")
        except Exception as e:
            logging.error(f"❌ i18n Error: {e}")

def _(i18n_key: str, **kwargs) -> str:
    if i18n_key not in translations:
        logging.warning(f"Missing i18n key: '{i18n_key}'")
        text = i18n_key
    else:
        text = translations[i18n_key]
        
    if kwargs:
        try: return text.format(**kwargs)
        except KeyError as e: 
            logging.warning(f"Missing format argument '{e.args[0]}' in i18n key: '{i18n_key}'")
    return text

def validate_i18n_keys():
    missing_keys = set()
    pattern = re.compile(r'_\(\s*["\']([a-zA-Z0-9_]+)["\']')
    dirs = ['.', 'core', 'modules']
    
    for d in dirs:
        if not os.path.exists(d): continue
        for root, _, files in os.walk(d):
            if 'venv' in root or '__pycache__' in root:
                continue
            for file in files:
                if file.endswith('.py'):
                    try:
                        with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                            content = f.read()
                            matches = pattern.findall(content)
                            for match in matches:
                                if match not in translations and match not in CORE_REQUIRED_KEYS:
                                    missing_keys.add(match)
                    except Exception:
                        pass
                        
    if missing_keys:
        logging.warning(f"⚠️ WARNING! Missing i18n keys found ({len(missing_keys)} items):")
        for k in sorted(list(missing_keys)):
            logging.warning(f" - {k}")
    else:
        logging.info("✅ i18n Scanner: All translation keys are present!")

load_language()
validate_i18n_keys()