#!/usr/bin/env python3

import logging
import subprocess
import re
import os
import tempfile
from urllib.parse import urlparse, parse_qs, unquote
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ===== Config =====
BOT_TOKEN = "8767032901:AAEG06KxLdAeVE7X1xm6pUTz8ezFdqqc1Ac"
VLESS_UUID = "8024e6ab-5da4-473c-9008-2b3c51f8d697"
REGION = "us-central1"
SERVICE = "vless"
IMAGE = "docker.io/yoshyyy/yoshvip:latest"
PORT = 8080

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== Extract token from Qwiklabs URL =====
def extract_token(url: str):
    try:
        # Decode URL if needed
        decoded = unquote(url)
        # Try to find token param
        parsed = urlparse(decoded)
        params = parse_qs(parsed.query)
        
        # Check common token param names
        for key in ['token', 'access_token', 'auth_token']:
            if key in params:
                return params[key][0]
        
        # Some URLs have token at the end after '&token='
        match = re.search(r'[&?]token=([A-Za-z0-9_\-]+)', decoded)
        if match:
            return match.group(1)
            
        return None
    except Exception as e:
        logger.error(f"Token extraction error: {e}")
        return None

# ===== Activate GCP with token =====
def activate_gcp(token: str):
    try:
        result = subprocess.run(
            ["gcloud", "auth", "activate-refresh-token", token],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.error(f"Auth error: {result.stderr}")
            return False, result.stderr
        return True, None
    except Exception as e:
        return False, str(e)

# ===== Get GCP Project =====
def get_project():
    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()
    except:
        return None

# ===== Get Project Number =====
def get_project_number(project: str):
    try:
        result = subprocess.run(
            ["gcloud", "projects", "describe", project, "--format=value(projectNumber)"],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()
    except:
        return None

# ===== Enable APIs =====
def enable_apis():
    try:
        result = subprocess.run(
            ["gcloud", "services", "enable", 
             "run.googleapis.com", 
             "cloudbuild.googleapis.com", 
             "--quiet"],
            capture_output=True, text=True, timeout=60
        )
        return result.returncode == 0
    except:
        return False

# ===== Deploy Cloud Run =====
def deploy_cloudrun():
    try:
        result = subprocess.run([
            "gcloud", "run", "deploy", SERVICE,
            f"--image={IMAGE}",
            "--platform=managed",
            f"--region={REGION}",
            "--memory=512Mi",
            "--cpu=1",
            "--timeout=3600",
            "--allow-unauthenticated",
            f"--port={PORT}",
            "--min-instances=1",
            "--quiet"
        ], capture_output=True, text=True, timeout=300)
        return result.returncode == 0, result.stderr
    except Exception as e:
        return False, str(e)

# ===== Build VLESS URI =====
def build_uri(host: str):
    return (
        f"vless://{VLESS_UUID}@vpn.googleapis.com:443"
        f"?path=%2Fvless_yosh&security=tls&encryption=none"
        f"&host={host}&type=ws#Yosh-VLESS-WS"
    )

# ===== /start command =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌐 *Yosh VIP — GCP Deploy Bot*\n\n"
        "Send me your *Qwiklabs SSO link* and I'll deploy your VLESS node automatically!\n\n"
        "📌 The link looks like:\n"
        "`https://www.skills.google/google_sso?...`\n\n"
        "Just paste it here and wait! 🚀",
        parse_mode="Markdown"
    )

# ===== Handle Qwiklabs URL =====
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user = update.message.from_user.first_name

    # Check if it looks like a Qwiklabs URL
    if "skills.google" not in url and "qwiklabs" not in url and "cloudshell" not in url:
        await update.message.reply_text(
            "❌ That doesn't look like a Qwiklabs link bro!\n\n"
            "Send the link that starts with:\n"
            "`https://www.skills.google/google_sso?...`",
            parse_mode="Markdown"
        )
        return

    msg = await update.message.reply_text(
        f"⚡ Got it {user}! Starting deployment...\n\n"
        "⠋ Extracting credentials..."
    )

    # Step 1: Extract token
    token = extract_token(url)
    if not token:
        await msg.edit_text(
            "❌ Could not extract token from your link.\n"
            "Make sure you send the full Qwiklabs SSO URL!"
        )
        return

    await msg.edit_text(
        "✓ Credentials extracted\n"
        "⠙ Authenticating with GCP..."
    )

    # Step 2: Activate GCP
    ok, err = activate_gcp(token)
    if not ok:
        await msg.edit_text(
            f"❌ GCP authentication failed!\n\n"
            f"Error: `{err[:200]}`",
            parse_mode="Markdown"
        )
        return

    await msg.edit_text(
        "✓ Credentials extracted\n"
        "✓ GCP authenticated\n"
        "⠹ Getting project info..."
    )

    # Step 3: Get project
    project = get_project()
    if not project:
        await msg.edit_text("❌ Could not get GCP project ID!")
        return

    project_number = get_project_number(project)

    await msg.edit_text(
        "✓ Credentials extracted\n"
        "✓ GCP authenticated\n"
        f"✓ Project: `{project}`\n"
        "⠸ Enabling APIs...",
        parse_mode="Markdown"
    )

    # Step 4: Enable APIs
    enable_apis()

    await msg.edit_text(
        "✓ Credentials extracted\n"
        "✓ GCP authenticated\n"
        f"✓ Project: `{project}`\n"
        "✓ APIs enabled\n"
        "⠼ Deploying to Cloud Run...",
        parse_mode="Markdown"
    )

    # Step 5: Deploy
    ok, err = deploy_cloudrun()
    if not ok:
        await msg.edit_text(
            f"❌ Deployment failed!\n\n`{err[:300]}`",
            parse_mode="Markdown"
        )
        return

    # Step 6: Build URI
    host = f"{SERVICE}-{project_number}.{REGION}.run.app"
    uri = build_uri(host)

    await msg.edit_text(
        f"✅ *Deploy Successful!*\n\n"
        f"☁️ Project: `{project}`\n"
        f"🌍 Region: `{REGION}`\n"
        f"🔌 Protocol: `VLESS WS`\n"
        f"🔗 URL: `https://{host}`\n\n"
        f"🔑 *Access Key:*\n"
        f"`{uri}`",
        parse_mode="Markdown"
    )

# ===== Main =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    logger.info("Yosh VIP Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
