#!/bin/bash


set -e


CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DARKGRAY='\033[1;30m'
NC='\033[0m'


INSTALL_DIR="$HOME/NexusTG"
REPO_URL="https://github.com/newfpv/NexusTGTest.git"
TOTAL_STEPS=6


wait_and_exit() {
    echo -e "\n${DARKGRAY}Press ENTER to close this window...${NC}"
    read -r
    exit 1
}


trap 'echo -e "\n${RED}❌ An unexpected error occurred. Exiting...${NC}"; wait_and_exit' ERR


draw_progress_bar() {
    local step=$1
    local text=$2
    local filled=$(( (step * 20) / TOTAL_STEPS ))
    local empty=$(( 20 - filled ))
    local percent=$(( (step * 100) / TOTAL_STEPS ))
    local bar_filled=$(printf "%${filled}s" | tr ' ' '█')
    local bar_empty=$(printf "%${empty}s" | tr ' ' '░')
    echo -e "\n${CYAN}[${bar_filled}${bar_empty}] ${percent}% | Step ${step}/${TOTAL_STEPS} - ${text}${NC}"
}

clear
echo -e "${CYAN}====================================================${NC}"
echo -e "${GREEN}  ✨ Welcome to the NexusTG (AI Twin) Installer ✨  ${NC}"
echo -e "${CYAN}====================================================${NC}"
echo -e "Sit back and relax. I'll do all the heavy lifting! 🚀"

draw_progress_bar 1 "Checking Git & Curl..."
if ! command -v git &> /dev/null || ! command -v curl &> /dev/null; then
    echo -e "${YELLOW}Tools not found. Installing via apt (sudo required)...${NC}"
    if command -v apt-get &> /dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq git curl > /dev/null 2>&1
        echo -e "${GREEN}✔ Tools installed successfully!${NC}"
    else
        echo -e "${RED}❌ Error: apt-get not found. Please install git and curl manually.${NC}"
        wait_and_exit
    fi
else
    echo -e "${GREEN}✔ Tools are ready!${NC}"
fi

draw_progress_bar 2 "Downloading project files..."
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git pull -q
    echo -e "${GREEN}✔ Project files updated in $INSTALL_DIR!${NC}"
else
    git clone -q "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    echo -e "${GREEN}✔ Project downloaded successfully!${NC}"
fi

draw_progress_bar 3 "Checking 'uv' (Turbo Python Manager)..."
if ! command -v uv &> /dev/null; then
    echo -e "${YELLOW}Installing uv...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh > /dev/null 2>&1
    
    # Обновляем пути "на лету"
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    
    echo -e "${GREEN}✔ uv installed successfully!${NC}"
else
    echo -e "${GREEN}✔ uv is ready!${NC}"
fi

draw_progress_bar 4 "Configuring your bot..."

LANG_FILES=(language_*.json)
SELECTED_LANG="language_EN.json"

if [ -e "${LANG_FILES[0]}" ]; then
    echo -e "\n${CYAN}🌍 Select Language:${NC}"
    for i in "${!LANG_FILES[@]}"; do
        CODE=$(echo "${LANG_FILES[$i]}" | sed -n 's/language_\(.*\)\.json/\1/p')
        echo "   $((i+1)). $CODE"
    done
    read -p "👉 Enter number (Press Enter for EN): " CHOICE
    
    if [[ "$CHOICE" =~ ^[0-9]+$ ]] && [ "$CHOICE" -gt 0 ] && [ "$CHOICE" -le "${#LANG_FILES[@]}" ]; then
        SELECTED_LANG="${LANG_FILES[$((CHOICE-1))]}"
    elif [[ " ${LANG_FILES[*]} " =~ " language_EN.json " ]]; then
        SELECTED_LANG="language_EN.json"
    else
        SELECTED_LANG="${LANG_FILES[0]}"
    fi
    echo -e "${GREEN}✅ Selected language: $SELECTED_LANG${NC}"
fi

ENV_PATH="$INSTALL_DIR/.env"
echo -e "\n${CYAN}🔑 Connecting your Telegram Bot${NC}"

echo -e "${DARKGRAY}====================================================${NC}"
echo -e "${GREEN}🤖 How to get your TG_BOT_TOKEN:${NC}"
echo -e "1. Open Telegram and search for @BotFather (with a blue tick)."
echo -e "2. Send the command: /newbot"
echo -e "3. Choose a name and a username for your bot."
echo -e "4. BotFather will give you a token (e.g. 1234567890:ABCdef...)."
echo -e "${DARKGRAY}====================================================\n${NC}"

set +e
while true; do
    read -p "👉 Paste your TG_BOT_TOKEN here: " RAW_TOKEN
    TG_BOT_TOKEN=$(echo "$RAW_TOKEN" | xargs)
    
    echo -e "${YELLOW}⏳ Verifying token with Telegram...${NC}"
    
    HTTP_STATUS=$(curl -s -o /tmp/tg_resp.json -w "%{http_code}" "https://api.telegram.org/bot$TG_BOT_TOKEN/getMe")
    
    if [ "$HTTP_STATUS" -eq 200 ]; then
        BOT_NAME=$(grep -o '"first_name":"[^"]*' /tmp/tg_resp.json | cut -d'"' -f4)
        BOT_USER=$(grep -o '"username":"[^"]*' /tmp/tg_resp.json | cut -d'"' -f4)
        echo -e "${GREEN}✅ Token is VALID! Connected to: $BOT_NAME (@$BOT_USER)${NC}"
        
        echo "TG_BOT_TOKEN=$TG_BOT_TOKEN" > "$ENV_PATH"
        echo "LANG_FILE=$SELECTED_LANG" >> "$ENV_PATH"
        rm -f /tmp/tg_resp.json
        break
    else
        echo -e "${RED}❌ Error! Telegram rejected this token. Please check it and try again.${NC}"
    fi
done
set -e
draw_progress_bar 5 "Building environment with 'uv'..."
echo -e "${YELLOW}⚡ Installing libraries (this will just take a few seconds)...${NC}"

uv venv
uv pip install -r pyproject.toml
draw_progress_bar 6 "Creating start script..."

START_SCRIPT="$INSTALL_DIR/start.sh"
cat << 'EOF' > "$START_SCRIPT"
#!/bin/bash
cd "$(dirname "$0")" || exit
echo -e "\033[0;36m========================================\033[0m"
echo -e "\033[0;32m  NexusTG Bot is Starting...\033[0m"
echo -e "\033[0;33m  Please do NOT close this window!\033[0m"
echo -e "\033[0;36m========================================\033[0m"
uv run main.py
echo -e "\n\033[1;30mPress ENTER to exit...\033[0m"
read -r
EOF

chmod +x "$START_SCRIPT"
echo -e "${GREEN}✔ Script 'start.sh' created successfully!${NC}"

echo -e "\n${CYAN}====================================================${NC}"
echo -e "${GREEN} 🎉 INSTALLATION COMPLETED SUCCESSFULLY! 🎉${NC}"
echo -e "${CYAN}====================================================\n${NC}"

echo -e "${RED}⚠️ IMPORTANT RULE:${NC}"
echo -e "${YELLOW}The bot only runs while the terminal window is open."
echo -e "If you close it, the bot will immediately shut down!\n${NC}"

echo -e "${CYAN}📌 HOW TO RUN THE BOT NOW:${NC}"
echo -e "1. Go to the bot folder: ${GREEN}cd ~/NexusTG${NC}"
echo -e "2. Run the start script: ${GREEN}./start.sh${NC}"
echo -e "3. Open Telegram and send /start to your bot.\n"

wait_and_exit
