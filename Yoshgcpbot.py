#!/usr/bin/env python3

import logging
import asyncio
import re
import os
import json
import subprocess
import tempfile
from urllib.parse import unquote
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import httpx

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

# ===== Extract from URL =====
def extract_token(url: str):
    match = re.search(r'[&?]token=([A-Za-z0-9_\-]+)', url)
    return match.group(1) if match else None

def extract_project(url: str):
    match = re.search(r'(qwiklabs-gcp-[a-z0-9-]+)', url)
    return match.group(1) if match else None

def extract_email(url: str):
    decoded = unquote(unquote(url))
    match = re.search(r'Email[=%]3D([^&%\s]+)', decoded)
    if match:
        return unquote(match.group(1))
    match = re.search(r'student-\d+-[a-z0-9]+@qwiklabs\.net', decoded)
    return match.group(0) if match else None

# ===== Exchange Qwiklabs token for Google access token =====
async def get_google_token(qwiklabs_token: str, email: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Try to exchange via Google OAuth
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": qwiklabs_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                }
            )
            logger.info(f"Token exchange response: {resp.status_code} {resp.text[:200]}")
            if resp.status_code == 200:
                return resp.json().get("access_token")
    except Exception as e:
        logger.error(f"Token exchange error: {e}")

    # Try using token directly as bearer
    return qwiklabs_token

# ===== Enable APIs via REST =====
async def enable_apis_rest(access_token: str, project: str):
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            for api in ["run.googleapis.com", "cloudbuild.googleapis.com"]:
                resp = await client.post(
                    f"https://serviceusage.googleapis.com/v1/projects/{project}/services/{api}:enable",
                    headers=headers,
                    json={}
                )
                logger.info(f"Enable {api}: {resp.status_code}")
        return True
    except Exception as e:
        logger.error(f"Enable APIs error: {e}")
        return False

# ===== Deploy via Cloud Run REST API =====
async def deploy_cloudrun_rest(access_token: str, project: str):
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Get project number
            resp = await client.get(
                f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}",
                headers=headers
            )
            logger.info(f"Get project: {resp.status_code} {resp.text[:200]}")
            if resp.status_code != 200:
                return False, f"Cannot access project: {resp.text[:200]}"
            
            project_number = resp.json().get("projectNumber")
            logger.info(f"Project number: {project_number}")

            # Deploy Cloud Run service
            service_url = f"https://run.googleapis.com/v1/namespaces/{project}/services"
            body = {
                "apiVersion": "serving.knative.dev/v1",
                "kind": "Service",
                "metadata": {
                    "name": SERVICE,
                    "namespace": project,
                    "annotations": {
                        "run.googleapis.com/ingress": "all",
                        "run.googleapis.com/launch-stage": "BETA"
                    }
                },
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "autoscaling.knative.dev/minScale": "1",
                                "run.googleapis.com/cpu-throttling": "false"
                            }
                        },
                        "spec": {
                            "containerConcurrency": 1000,
                            "timeoutSeconds": 3600,
                            "containers": [{
                                "image": IMAGE,
                                "ports": [{"containerPort": PORT}],
                                "resources": {
                                    "limits": {
                                        "cpu": "1",
                                        "memory": "512Mi"
                                    }
                                }
                            }]
                        }
                    }
                }
            }

            # Try create first, then update if exists
            resp = await client.post(
                f"https://{REGION}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/{project}/services",
                headers=headers,
                json=body
            )
            logger.info(f"Deploy response: {resp.status_code} {resp.text[:300]}")

            if resp.status_code in [200, 201]:
                host = f"{SERVICE}-{project_number}.{REGION}.run.app"
                return True, host
            elif resp.status_code == 409:
                # Service exists, update it
                resp2 = await client.put(
                    f"https://{REGION}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/{project}/services/{SERVICE}",
                    headers=headers,
                    json=body
                )
                logger.info(f"Update response: {resp2.status_code} {resp2.text[:300]}")
                if resp2.status_code in [200, 201]:
                    host = f"{SERVICE}-{project_number}.{REGION}.run.app"
                    return True, host

            return False, f"Deploy failed: {resp.text[:300]}"

    except Exception as e:
        logger.error(f"Deploy error: {e}")
        return False, str(e)

# ===== Allow unauthenticated =====
async def allow_unauthenticated(access_token: str, project: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            resp = await client.post(
                f"https://{REGION}-run.googleapis.com/v1/projects/{project}/locations/{REGION}/services/{SERVICE}:setIamPolicy",
                headers=headers,
                json={
                    "policy": {
                        "bindings": [{
                            "role": "roles/run.invoker",
                            "members": ["allUsers"]
                        }]
                    }
                }
            )
            logger.info(f"IAM policy: {resp.status_code}")
    except Exception as e:
        logger.error(f"IAM error: {e}")

# ===== Build URI =====
def build_uri(host: str):
    return (
        f"vless://{VLESS_UUID}@vpn.googleapis.com:443"
        f"?path=%2Fvless_yosh&security=tls&encryption=none"
        f"&host={host}&type=ws#Yosh-VLESS-WS"
    )

# ===== /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌐 *Yosh VIP — GCP Deploy Bot*\n\n"
        "Send me your *Qwiklabs SSO link* and I'll deploy your VLESS node automatically!\n\n"
        "📌 The link looks like:\n"
        "`https://www.skills.google/google_sso?...`\n\n"
        "⏱ Deploy takes about *2 minutes*. Just paste and wait! 🚀",
        parse_mode="Markdown"
    )

# ===== Handle URL =====
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user = update.message.from_user.first_name

    if "skills.google" not in url and "qwiklabs" not in url:
        await update.message.reply_text(
            "❌ That doesn't look like a Qwiklabs link!\n\n"
            "Send the link that starts with:\n"
            "`https://www.skills.google/google_sso?...`",
            parse_mode="Markdown"
        )
        return

    token = extract_token(url)
    project = extract_project(url)
    email = extract_email(url)

    logger.info(f"Token: {token[:20] if token else None}")
    logger.info(f"Project: {project}")
    logger.info(f"Email: {email}")

    if not project:
        await update.message.reply_text("❌ Could not find project ID in the link!")
        return

    if not token:
        await update.message.reply_text("❌ Could not extract token from the link!")
        return

    msg = await update.message.reply_text(
        f"⚡ Got it {user}!\n"
        f"📋 Project: `{project}`\n\n"
        f"🔐 Authenticating with GCP...",
        parse_mode="Markdown"
    )

    try:
        # Get access token
        access_token = await get_google_token(token, email)
        logger.info(f"Access token: {access_token[:20] if access_token else None}")

        await msg.edit_text(
            f"📋 Project: `{project}`\n\n"
            f"✓ Token acquired\n"
            f"🔧 Enabling APIs...",
            parse_mode="Markdown"
        )

        # Enable APIs
        await enable_apis_rest(access_token, project)

        await msg.edit_text(
            f"📋 Project: `{project}`\n\n"
            f"✓ Token acquired\n"
            f"✓ APIs enabled\n"
            f"🚀 Deploying Cloud Run...",
            parse_mode="Markdown"
        )

        # Deploy
        ok, result = await deploy_cloudrun_rest(access_token, project)

        if ok:
            host = result
            # Allow unauthenticated access
            await allow_unauthenticated(access_token, project)
            uri = build_uri(host)
            await msg.edit_text(
                f"✅ *Deploy Successful!*\n\n"
                f"📋 Project: `{project}`\n"
                f"🌍 Region: `{REGION}`\n"
                f"🔌 Protocol: `VLESS WS`\n"
                f"🔗 URL: `https://{host}`\n\n"
                f"🔑 *Access Key:*\n"
                f"`{uri}`",
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text(
                f"❌ Deploy failed!\n\n`{result[:300]}`",
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Error: {e}")
        await msg.edit_text(f"❌ Error: `{str(e)[:200]}`", parse_mode="Markdown")

# ===== Main =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    logger.info("Yosh VIP Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
