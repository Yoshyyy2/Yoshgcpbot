#!/usr/bin/env python3

import logging
import asyncio
import re
import os
from urllib.parse import unquote
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

# ===== Config =====
BOT_TOKEN = "8767032901:AAEG06KxLdAeVE7X1xm6pUTz8ezFdqqc1Ac"
VLESS_UUID = "8024e6ab-5da4-473c-9008-2b3c51f8d697"
REGION = "us-central1"
SERVICE = "vless"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== Extract project from URL =====
def extract_project(url: str):
    match = re.search(r'(qwiklabs-gcp-[a-z0-9-]+)', url)
    return match.group(1) if match else None

# ===== Deploy via Playwright =====
async def deploy_via_browser(url: str, project: str, status_cb):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        # Use fresh incognito context - no saved cookies!
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            storage_state=None,  # No saved state
            no_viewport=False,
        )
        # Clear all cookies and storage to simulate private/incognito
        await context.clear_cookies()
        page = await context.new_page()

        try:
            # Step 1: Open Qwiklabs URL
            await status_cb("🌐 Opening Qwiklabs link...")
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(3)

            # Step 2: Wait for GCP Console to load
            await status_cb("⏳ Waiting for GCP Console...")
            await page.wait_for_url("**/console.cloud.google.com/**", timeout=60000)
            await asyncio.sleep(5)
            logger.info(f"Current URL: {page.url}")

            # Step 3: Open Cloud Shell
            await status_cb("🖥️ Opening Cloud Shell...")
            # Click the Cloud Shell button (top right icon)
            shell_btn = page.locator("button[aria-label='Activate Cloud Shell']")
            if await shell_btn.count() == 0:
                shell_btn = page.locator("[data-tooltip='Activate Cloud Shell']")
            if await shell_btn.count() == 0:
                shell_btn = page.locator("button.cloud-shell-button")
            await shell_btn.click(timeout=30000)
            await asyncio.sleep(8)

            # Step 4: Wait for terminal
            await status_cb("⌨️ Terminal ready, deploying...")
            terminal = page.locator(".cloudshell-terminal textarea").first
            if await terminal.count() == 0:
                terminal = page.locator("textarea.xterm-helper-textarea").first
            await terminal.wait_for(timeout=30000)
            await asyncio.sleep(3)

            # Step 5: Type deploy command
            deploy_cmd = f"bash <(curl -fsSL https://raw.githubusercontent.com/Yoshyyy2/Mygcp/main/yosh.sh)"
            await terminal.click()
            await asyncio.sleep(1)

            # Type command with auto answers
            full_cmd = (
                f"printf '1\\n1\\n1\\n512Mi\\n{SERVICE}\\n' | "
                f"bash <(curl -fsSL https://raw.githubusercontent.com/Yoshyyy2/Mygcp/main/yosh.sh)"
            )
            await page.keyboard.type(full_cmd)
            await page.keyboard.press("Enter")

            await status_cb("🚀 Deploying... (this takes ~3 minutes)")

            # Step 6: Wait for output and grab vless link
            await asyncio.sleep(180)  # Wait 3 minutes for deploy

            # Take screenshot for debugging
            await page.screenshot(path="/tmp/deploy_result.png")

            # Get terminal output
            terminal_text = await page.locator(".cloudshell-terminal").inner_text()
            logger.info(f"Terminal output: {terminal_text[-500:]}")

            # Extract vless link
            vless_match = re.search(r'vless://[^\s]+', terminal_text)
            if vless_match:
                return True, vless_match.group(0)

            # Try trojan too
            trojan_match = re.search(r'trojan://[^\s]+', terminal_text)
            if trojan_match:
                return True, trojan_match.group(0)

            return False, "Could not extract access key from output"

        except Exception as e:
            logger.error(f"Browser error: {e}")
            await page.screenshot(path="/tmp/error_screenshot.png")
            return False, str(e)
        finally:
            await browser.close()

# ===== /start command =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌐 *Yosh VIP — GCP Deploy Bot*\n\n"
        "Send me your *Qwiklabs SSO link* and I'll deploy your VLESS node automatically!\n\n"
        "📌 The link looks like:\n"
        "`https://www.skills.google/google_sso?...`\n\n"
        "⏱ Deploy takes about *3 minutes*. Just paste and wait! 🚀",
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

    project = extract_project(url)
    if not project:
        await update.message.reply_text("❌ Could not find project ID in the link!")
        return

    msg = await update.message.reply_text(
        f"⚡ Got it {user}!\n"
        f"📋 Project: `{project}`\n\n"
        f"🌐 Opening Qwiklabs link...",
        parse_mode="Markdown"
    )

    async def status_cb(text):
        try:
            await msg.edit_text(
                f"📋 Project: `{project}`\n\n{text}",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    try:
        ok, result = await deploy_via_browser(url, project, status_cb)
        if ok:
            host = f"{SERVICE}-{project}.{REGION}.run.app"
            await msg.edit_text(
                f"✅ *Deploy Successful!*\n\n"
                f"📋 Project: `{project}`\n"
                f"🌍 Region: `{REGION}`\n"
                f"🔌 Protocol: `VLESS WS`\n\n"
                f"🔑 *Access Key:*\n"
                f"`{result}`",
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text(
                f"❌ Deploy failed!\n\n`{result[:300]}`",
                parse_mode="Markdown"
            )
    except Exception as e:
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
