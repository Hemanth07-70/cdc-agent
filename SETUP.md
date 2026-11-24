# VIT CDC Email Agent — Setup Guide

## Step 1: Telegram Bot (5 minutes)

1. Open Telegram → search `@BotFather`
2. Send `/newbot` → give it a name (e.g., "VIT CDC Agent")
3. Copy the **bot token** → paste in `.env` as `TELEGRAM_BOT_TOKEN`
4. Start your bot (search it, click Start)
5. Get your Chat ID:
   - Go to `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - Send any message to your bot first
   - Copy the `"id"` number from the response
   - Paste in `.env` as `TELEGRAM_CHAT_ID`

---

## Step 2: NVIDIA NIM API Key (2 minutes)

1. Go to `https://build.nvidia.com`
2. Sign up / Log in
3. Click your profile → API Keys → Generate Key
4. Paste in `.env` as `NVIDIA_API_KEY`

---

## Step 3: Gmail API (10 minutes)

1. Go to `https://console.cloud.google.com`
2. Create a new project (e.g., "CDC Agent")
3. Enable **Gmail API**: APIs & Services → Enable APIs → search Gmail API → Enable
4. Create credentials: APIs & Services → Credentials → Create Credentials → OAuth client ID
   - Application type: **Desktop app**
   - Download the JSON file → rename to `credentials.json` → place in this folder
5. OAuth consent screen → Add your Gmail as test user

---

## Step 4: Local Test

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill env
cp .env.template .env
# Edit .env with your actual keys

# Run once to authenticate Gmail (opens browser)
python agent.py
```

First run opens browser for Gmail auth — approve it. After that it runs headlessly forever.

---

## Step 5: Deploy to Oracle Cloud (Free, 24/7)

### Create Oracle Free VM
1. Sign up at `https://cloud.oracle.com` (needs credit card for verification, not charged)
2. Compute → Instances → Create Instance
   - Shape: **VM.Standard.A1.Flex** (ARM, Always Free)
   - Image: Ubuntu 22.04
   - Add your SSH key (generate with `ssh-keygen` if needed)
3. Click Create → wait 2 minutes

### Upload and Run
```bash
# From your laptop — copy project to server
scp -r /Users/hemanthchowdary/cdc-agent ubuntu@<YOUR_SERVER_IP>:~/cdc-agent

# SSH into server
ssh ubuntu@<YOUR_SERVER_IP>

# Install Python
sudo apt update && sudo apt install python3-pip python3-venv -y

# Setup
cd ~/cdc-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Add your .env (create it on the server)
nano .env
# Paste your keys, save with Ctrl+X

# Copy credentials.json and token.json from your laptop
# (run agent.py locally once to generate token.json, then upload both)
```

### Run as a Service (auto-restart, survives reboots)
```bash
sudo nano /etc/systemd/system/cdc-agent.service
```

Paste this:
```ini
[Unit]
Description=VIT CDC Email Agent
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/cdc-agent
Environment=PATH=/home/ubuntu/cdc-agent/venv/bin
ExecStart=/home/ubuntu/cdc-agent/venv/bin/python agent.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable cdc-agent
sudo systemctl start cdc-agent

# Check it's running
sudo systemctl status cdc-agent

# Watch live logs
sudo journalctl -u cdc-agent -f
```

Done. It runs 24/7, restarts automatically if it crashes, survives server reboots.

---

## Notification Example

What you'll receive on Telegram:

```
🎓 VIT CDC PLACEMENT ALERT
✅ YOU ARE ELIGIBLE — REGISTER NOW!

🏢 Company: Bosch Global Software Technologies
📋 Category: Super Dream Internship
👔 Role: AI Intern
💰 CTC / Stipend: ₹40,000/month → 16 LPA FTE
📍 Location: Bengaluru
📅 Interview / Test: 7th & 8th Dec 2026
⏰ Registration Deadline: 22nd Nov 2026 10:00 am
🎓 Min CGPA Required: 7.0

⚡ Go register on NEO PAT portal immediately!
```
