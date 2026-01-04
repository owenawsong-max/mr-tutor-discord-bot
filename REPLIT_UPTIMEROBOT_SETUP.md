# Replit + UptimeRobot Setup Guide (ACTUALLY WORKS)

## ⚠️ IMPORTANT: Why Standard UptimeRobot Fails

You tried the basic UptimeRobot ping approach and it failed because:
- **Replit's 15-minute inactivity timeout** is aggressive
- **5-minute ping intervals** aren't frequent enough
- **No background process** means bot could crash without restart

This guide uses a **Flask keep-alive server** which:
✅ Starts automatically with the bot
✅ Runs in a background thread
✅ Prevents inactivity timeouts
✅ Works with UptimeRobot pings every 2 minutes

---

## Step 1: Prepare Your Code

### Edit main.py

Add these lines **at the TOP** of your `main.py` file (before any bot code):

```python
import os
from keep_alive import start as start_keep_alive

# Start the keep-alive server FIRST
if __name__ == '__main__':
    start_keep_alive()  # This starts the Flask server in background
```

Then at the **END** of main.py, make sure you have:

```python
bot.run(os.getenv('DISCORD_BOT_TOKEN'))
```

### What the keep_alive.py does:
1. Starts a Flask web server on port 8080
2. Runs in a daemon thread (doesn't block bot)
3. Returns "Bot is alive!" when pinged
4. Prevents Replit from putting your bot to sleep

---

## Step 2: Update requirements.txt

Add Flask to your dependencies. Edit `requirements.txt` and add:

```
flask
aiohttp
discord.py
openai
```

---

## Step 3: Set Up Replit

### 3.1 Create a New Repl
1. Go to https://replit.com
2. Click "+ Create Repl"
3. Select **Python**
4. Name it: `mr-tutor-discord-bot`
5. Click "Create Repl"

### 3.2 Import from GitHub
1. In Replit, click **Version Control > Import from GitHub**
2. Paste: `https://github.com/owenawsong/mr-tutor-discord-bot`
3. Click "Import"
4. Wait for files to load (1-2 minutes)

### 3.3 Install Dependencies
1. In Replit shell, run:
   ```bash
   pip install -r requirements.txt
   ```
2. Should see: `Successfully installed flask, discord.py, etc...`

### 3.4 Configure Environment Variables
1. Click the **"Secrets"** button (lock icon) on the left sidebar
2. Add these secrets:
   - **Key**: `DISCORD_BOT_TOKEN`  
     **Value**: Your actual Discord bot token
   - **Key**: `POE_API_KEY`  
     **Value**: Your Poe API key
   - **Key**: `ADMIN_IDS`  
     **Value**: Your Discord user ID (or comma-separated list)
3. Click "Add Secret" for each one

---

## Step 4: Test in Replit

1. Click the **"Run"** button (or press Ctrl+Enter)
2. Wait 10 seconds for startup
3. You should see output like:
   ```
   ✅ Keep-alive server started on port 8080
   ✅ Logged in as mr-tutor#1234
   ✅ Bot is ready!
   ```
4. If you see errors, check:
   - Is `keep_alive.py` in the repo?
   - Did you add `from keep_alive import start as start_keep_alive`?
   - Did you call `start_keep_alive()` before `bot.run()`?

5. Test your bot in Discord:
   - Send: `$help` (should get help menu)
   - Send: `$tut hello` (should get response)
   - If it works, stop the Repl (click stop button)

---

## Step 5: Get Repl URL

1. In Replit, look at the top-right where it shows the output
2. You should see a URL like: `https://mr-tutor-discord-bot.repl.co`
3. **Copy this URL** - you'll need it for UptimeRobot

If you don't see a URL:
- Make sure Flask is running (check console for keep-alive message)
- Wait 5 seconds after clicking Run

---

## Step 6: Set Up UptimeRobot

### 6.1 Create Account
1. Go to https://uptimerobot.com
2. Click "Sign Up"
3. Create account with email
4. Verify email (check your inbox)

### 6.2 Create Monitor
1. Click **"Add New Monitor"** or **"+ Add Monitor"**
2. Configure:
   - **Monitor Type**: `HTTP(s)`
   - **Friendly Name**: `Mr. Tutor Bot`
   - **URL**: Paste your Repl URL from Step 5 (e.g., `https://mr-tutor-discord-bot.repl.co`)
   - **Monitoring Interval**: `2 minutes` (MORE FREQUENT = BETTER)
3. Click **"Create Monitor"**

### 6.3 Verify It's Working
1. Go back to your Replit
2. Click "Run" again
3. In UptimeRobot, you should see:
   - Status: **"Up"** (green checkmark)
   - Response Time: ~500ms
4. If "Down" (red), check:
   - Your Repl is running (did you click Run?)
   - The URL is correct
   - Flask is starting (check console output)

---

## Step 7: Keep the Repl Running Forever

⚠️ **Important**: Replit closes Repls when inactive. To keep it running:

### Option A: Always-On (Recommended if affordable)
1. In Replit, click **"Deployments"** on left sidebar
2. Look for **"Always On"** option
3. This costs ~$7/month but guarantees 24/7 uptime

### Option B: Free Method (With UptimeRobot)
1. Keep Replit tab open in your browser
2. UptimeRobot pings every 2 minutes to keep it awake
3. **Limitation**: If you close browser/computer, bot may go offline
4. **Workaround**: Use Replit mobile app or keep browser running

### Option C: Better Free Method
1. Use **Render.com** instead (free tier has 15-min sleep but more reliable)
2. Or upgrade to **PythonAnywhere** ($5/month, 100% guaranteed 24/7)

---

## Troubleshooting

### Bot goes offline after 15 minutes
✅ **Solution**: UptimeRobot interval is too long
- In UptimeRobot dashboard, change interval to **2 minutes**
- Make sure monitor status shows **"Up" (green)**

### "Bot is offline" in Discord
✅ **Solution**: Flask server not starting
- Check Replit console for error messages
- Make sure `keep_alive.py` exists in repo
- Verify you added import statement at top of main.py

### Flask says "Address already in use"
✅ **Solution**: Port 8080 is taken
- In `keep_alive.py`, change `port=8080` to `port=8081`
- Update UptimeRobot URL to include `:8081` if needed

### UptimeRobot shows "Timeout"
✅ **Solution**: Flask not responding fast enough
- Make sure Flask starts before bot.run()
- Check internet connection
- Try increasing UptimeRobot timeout (if possible)

### Bot responds to some commands but not others
✅ **Solution**: Keep-alive is working, but bot logic has issue
- Not a hosting problem
- Check your command handlers in main.py
- Test locally first before deploying

---

## Monitoring & Maintenance

### Daily Check
1. Open Discord, test bot: `$help`
2. Visit UptimeRobot dashboard - should show **"Up"**
3. If down, Replit crashed - click Run again

### Weekly Check
1. Verify no errors in Replit console
2. Check UptimeRobot uptime % (should be near 100%)
3. Test all bot commands

### Monthly Check
1. Check for memory leaks (Replit console)
2. Review bot performance
3. Update bot code and redeploy

---

## Cost Breakdown

| Service | Cost | Notes |
|---------|------|-------|
| Replit (free) | $0 | May sleep after inactivity |
| UptimeRobot (free) | $0 | Keeps bot awake with pings |
| Discord Bot Token | $0 | Free from Discord |
| **Total** | **$0** | ✅ Completely free! |

**Optional Upgrades:**
- Replit Always-On: ~$7/month (100% guaranteed uptime)
- PythonAnywhere: $5/month (professional, 24/7 guaranteed)

---

## Next Steps

1. ✅ Add `keep_alive.py` to your repo (DONE)
2. ⏭️ Edit `main.py` to import and call `start_keep_alive()`
3. ⏭️ Update `requirements.txt` with flask
4. ⏭️ Deploy to Replit via GitHub import
5. ⏭️ Set up UptimeRobot monitoring
6. ⏭️ Test bot and verify it stays online
7. ⏭️ Enjoy your free, always-on Discord bot!

---

## Quick Reference

**Replit URL Pattern**: `https://<repl-name>.repl.co`

**UptimeRobot Check Interval**: 2 minutes (fast enough to prevent sleep)

**Flask Health Check**: Visits `/` on port 8080, returns "Bot is alive!"

**Bot Token Location**: Replit Secrets (lock icon)

**Flask Import in main.py**:
```python
from keep_alive import start as start_keep_alive
start_keep_alive()  # Call BEFORE bot.run()
```
