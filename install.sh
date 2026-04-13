#!/bin/bash

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DARKGRAY='\033[1;30m'
NC='\033[0m'

INSTALL_DIR="$HOME/NexusTG"
REPO_URL="https://github.com/newfpv/NexusTG.git"
TOTAL_STEPS=5

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

draw_progress_bar 3 "Checking Docker & Docker Compose..."
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Installing Docker...${NC}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io docker-compose-v2 > /dev/null 2>&1
    sudo systemctl enable --now docker > /dev/null 2>&1
    sudo usermod -aG docker $USER
    echo -e "${GREEN}✔ Docker installed successfully!${NC}"
else
    echo -e "${GREEN}✔ Docker is ready!${NC}"
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

draw_progress_bar 5 "Starting bot in Docker..."
echo -e "${YELLOW}⚡ Building and launching container (this might take a minute)...${NC}"

# Запускаем докер
sudo docker compose up -d --build

echo -e "\n${CYAN}====================================================${NC}"
echo -e "${GREEN} 🎉 INSTALLATION COMPLETED SUCCESSFULLY! 🎉${NC}"
echo -e "${CYAN}====================================================\n${NC}"

echo -e "${GREEN}📌 The bot is now running in the background via Docker.${NC}"
echo -e "${DARKGRAY}You can safely close this terminal window.\n${NC}"

echo -e "${CYAN}🛠️  HOW TO MANAGE YOUR BOT NOW:${NC}"
echo -e "1. ${YELLOW}View Logs:${NC}      cd ~/NexusTG && sudo docker compose logs -f"
echo -e "2. ${YELLOW}Restart Bot:${NC}    cd ~/NexusTG && sudo docker compose restart"
echo -e "3. ${YELLOW}Stop Bot:${NC}       cd ~/NexusTG && sudo docker compose down\n"
echo -e "👉 Open Telegram and send /start to your bot."

echo -e "\n${DARKGRAY}Press ENTER to exit...${NC}"
read -r