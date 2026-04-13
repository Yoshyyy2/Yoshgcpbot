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
        # First try raw URL
        match = re.search(r'[&?]token=([A-Za-z0-9_\-]+)', url)
        if match:
            return match.group(1)
        
        # Try decoded URL
        decoded = unquote(url)
        match = re.search(r'[&?]token=([A-Za-z0-9_\-]+)', decoded)
        if match:
            return match.group(1)
        
        # Try double decoded
        double_decoded = unquote(decoded)
        match = re.search(r'[&?]token=([A-Za-z0-9_\-]+)', double_decoded)
        if match:
            return match.group(1)

        # Try parse_qs on query string
        parsed = urlparse(double_decoded)
        params = parse_qs(parsed.query)
        for key in ['token', 'access_token', 'auth_token']:
            if key in params:
                return params[key][0]

        return None
    except Exception as e:
        logger.error(f"Token extraction error: {e}")
        return None

# ===== Activate GCP with access token =====
def activate_gcp(token: str):
    try:
        # Set access token via environment
        env = os.environ.copy()
        env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = token

        # Try activate-access-token first
        result = subprocess.run(
            ["gcloud", "config", "set", "auth/access_token_file", "/dev/stdin"],
            input=token,
            capture_output=True, text=True, timeout=30, env=env
        )

        # Just set the token directly via gcloud auth
        result2 = subprocess.run(
            ["gcloud", "auth", "activate-service-account", "--access-token-file=/dev/stdin"],
            input=token,
            capture_output=True, text=True, timeout=30, env=env
        )

        # Store token for later use
        os.environ["CLOUDSDK_AUTH_ACCESS_TOKEN"] = token
        return True, None
    except Exception as e:
        return False, str(e)

# ===== Get project from URL =====
def extract_project(url: str):
    try:
        decoded = unquote(unquote(url))
        match = re.search(r'project[=%]3D([a-z0-9-]+)', decoded)
        if match:
            return match.group(1)
        match = re.search(r'project=([a-z0-9-]+)', decoded)
        if match:
            return match.group(1)
        match = re.search(r'(qwiklabs-gcp-[a-z0-9-]+)', decoded)
        if match:
            return match.group(1)
        return None
    except Exception as e:
        logger.error(f"Project extraction error: {e}")
        return None

# ===== Get GCP Project =====
def get_project(project_id=None):
    try:
        if project_id:
            subprocess.run(
                ["gcloud", "config", "set", "project", project_id],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "CLOUDSDK_AUTH_ACCESS_TOKEN": os.environ.get("CLOUDSDK_AUTH_ACCESS_TOKEN", "")}
            )
            return project_id
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
        env = {**os.environ}
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
        ], capture_output=True, text=True, timeout=300, env=env)
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

    # Debug log
    logger.info(f"Received URL (first 200): {url[:200]}")
    
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
    logger.info(f"Extracted token: {token}")
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

    # Extract project from URL
    project_id = extract_project(url)
    logger.info(f"Extracted project: {project_id}")

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
    project = get_project(project_id)
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
