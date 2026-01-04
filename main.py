import os
from keep_alive import start as start_keep_alive

if __name__ == '__main__':
    start_keep_alive()  # Start Flask server FIRST

import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import Button, View
import openai
import os
import aiohttp
import base64
from collections import defaultdict
import json
from datetime import datetime, timedelta
import asyncio

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)

POE_API_KEY = os.getenv("POE_API_KEY")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(",")
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "Admin")

# Persistent storage files
RATE_LIMITS_FILE = "rate_limits.json"
BOT_STATE_FILE = "bot_state.json"
USER_ACCEPTANCES_FILE = "user_acceptances.json"

# Separate conversation histories for tutor vs non-tutor models
tutor_conversation_history = defaultdict(list)
standard_conversation_history = defaultdict(list)
MAX_HISTORY_LENGTH = 50

rate_limits = {
    "global": {},
    "users": {}
}

bot_state = {
    "enabled": True,
    "disable_until": None
}

user_messages = defaultdict(lambda: defaultdict(list))
user_acceptances = {}

custom_prompt = """# Mr. Tutor – Core Guidelines
  
You are in a roleplay as **"Mr. Tutor"**!
Your role is to act like a proper teacher who helps learners with questions and problems.
You **never reveal the final answer directly**. Instead, you guide, question, and encourage the learner to discover the solution themselves.
  
---
  
## Teaching Philosophy
- Act as a mentor, not a solver.
- Encourage curiosity and independent thinking.
- Provide hints, scaffolding, and structured steps.
- Celebrate progress, not just correctness.
  
---
  
## Core Guidelines
1. **Never give the final answer outright.**
   - Instead, break the problem into smaller steps.
   - Offer hints, analogies, or guiding questions.
  
2. **Encourage active participation.**
   - Ask the learner what they think the next step could be.
   - Validate their reasoning and gently correct if needed.
  
3. **Use the Socratic method.**
   - Lead with questions that spark deeper thought.
   - Example: "What happens if we try to simplify this part first?"
  
4. **Provide structure.**
   - Outline clear steps or strategies without completing them.
   - Example: "Step 1 is to identify the variables. Step 2 is to check the relationship. What do you notice?"
  
5. **Adapt to the learner's level.**
   - Use simple language for beginners.
   - Add complexity for advanced learners.
  
6. **Encourage reflection.**
   - Ask learners to explain their reasoning.
   - Reinforce understanding by connecting concepts.
  
7. **Promote confidence.**
   - Highlight what the learner did correctly.
   - Frame mistakes as opportunities to learn.
  
---
  
## Example Behaviors
- Don't: "The answer is 42."
- Do: "What happens if you divide both sides by 7? What number do you get?"
  
- Don't: "Here's the full solution."
- Do: "Let's start with the first step. Can you identify the key variable here?"
  
---
  
## Goal
By following these guidelines, Mr. Tutor ensures that learners:
- Develop problem-solving skills.
- Gain confidence in their own reasoning.
- Learn how to learn, not just how to answer.
  
---
  """

poe_client = openai.OpenAI(
    api_key=POE_API_KEY,
    base_url="https://api.poe.com/v1",
)

# Command configurations - LONGER PREFIXES FIRST
COMMAND_CONFIGS = [
    # $ prefix versions (longer first)
    ("tutorplus", "Gemini-2.5-Flash-Tut", True, "plus"),
    ("tutorminus", "Gemini-2.5-Flash-Lite", True, "minus"),
    ("imageplus", "GPT-Image-1-Mini", False, "imageplus"),
    ("standardplus", "Gemini-2.5-Flash-Tut", False, "nonplus"),
    ("standardminus", "Gemini-2.5-Flash-Lite", False, "nonminus"),
    ("tutor", "GPT-5-mini", True, "normal"),
    ("image", "FLUX-schnell", False, "image"),
    ("standard", "GPT-5-mini", False, "nonnormal"),
    # Keep old $ shortcuts for backwards compatibility
    ("tut+", "Gemini-2.5-Flash-Tut", True, "plus"),
    ("tut-", "Gemini-2.5-Flash-Lite", True, "minus"),
    ("tut", "GPT-5-mini", True, "normal"),
    ("ti+", "GPT-Image-1-Mini", False, "imageplus"),
    ("ti", "FLUX-schnell", False, "image"),
    ("tn+", "Gemini-2.5-Flash-Tut", False, "nonplus"),
    ("tn-", "Gemini-2.5-Flash-Lite", False, "nonminus"),
    ("tn", "GPT-5-mini", False, "nonnormal"),
    ("t+", "Gemini-2.5-Flash-Tut", True, "plus"),
    ("t-", "Gemini-2.5-Flash-Lite", True, "minus"),
    ("t", "GPT-5-mini", True, "normal"),
]

# Helper functions
def load_json(filename, default):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

def load_persistent_data():
    global rate_limits, bot_state, user_acceptances
    rate_limits = load_json(RATE_LIMITS_FILE, {"global": {}, "users": {}})
    bot_state = load_json(BOT_STATE_FILE, {"enabled": True, "disable_until": None})
    user_acceptances = load_json(USER_ACCEPTANCES_FILE, {})

def save_rate_limits():
    save_json(RATE_LIMITS_FILE, rate_limits)

def save_bot_state():
    save_json(BOT_STATE_FILE, bot_state)

def save_user_acceptances():
    save_json(USER_ACCEPTANCES_FILE, user_acceptances)

def is_admin(user_id, member=None):
    """Check if user is admin by ID or role"""
    if str(user_id) in ADMIN_IDS and ADMIN_IDS[0] != "":
        return True

    if member and hasattr(member, 'roles'):
        for role in member.roles:
            if role.name == ADMIN_ROLE_NAME:
                return True

    return False

def check_bot_state():
    """Check if bot should be re-enabled"""
    if not bot_state["enabled"] and bot_state["disable_until"]:
        if datetime.now().timestamp() >= bot_state["disable_until"]:
            bot_state["enabled"] = True
            bot_state["disable_until"] = None
            save_bot_state()
    return bot_state["enabled"]

def check_rate_limit(user_id, command):
    """Check if user has exceeded rate limits for a command"""
    now = datetime.now().timestamp()

    if user_id in user_messages and command in user_messages[user_id]:
        user_messages[user_id][command] = [
            ts for ts in user_messages[user_id][command]
            if now - ts < 3600
        ]

    user_id_str = str(user_id)
    if user_id_str in rate_limits["users"] and command in rate_limits["users"][user_id_str]:
        limit_config = rate_limits["users"][user_id_str][command]

        if "expires" in limit_config and limit_config["expires"] and now >= limit_config["expires"]:
            del rate_limits["users"][user_id_str][command]
            save_rate_limits()
        else:
            timestamps = user_messages[user_id][command]

            if "per_minute" in limit_config:
                recent_1min = [ts for ts in timestamps if now - ts < 60]
                if len(recent_1min) >= limit_config["per_minute"]:
                    return False, "You've exceeded the rate limit (per minute) for this command."

            if "per_10min" in limit_config:
                recent_10min = [ts for ts in timestamps if now - ts < 600]
                if len(recent_10min) >= limit_config["per_10min"]:
                    return False, "You've exceeded the rate limit (per 10 minutes) for this command."

            if "per_hour" in limit_config:
                recent_hour = [ts for ts in timestamps if now - ts < 3600]
                if len(recent_hour) >= limit_config["per_hour"]:
                    return False, "You've exceeded the rate limit (per hour) for this command."

    if command in rate_limits["global"]:
        limit_config = rate_limits["global"][command]
        timestamps = user_messages[user_id][command]

        if "per_minute" in limit_config:
            recent_1min = [ts for ts in timestamps if now - ts < 60]
            if len(recent_1min) >= limit_config["per_minute"]:
                return False, "Global rate limit exceeded (per minute) for this command."

        if "per_10min" in limit_config:
            recent_10min = [ts for ts in timestamps if now - ts < 600]
            if len(recent_10min) >= limit_config["per_10min"]:
                return False, "Global rate limit exceeded (per 10 minutes) for this command."

        if "per_hour" in limit_config:
            recent_hour = [ts for ts in timestamps if now - ts < 3600]
            if len(recent_hour) >= limit_config["per_hour"]:
                return False, "Global rate limit exceeded (per hour) for this command."

    return True, None

def record_message(user_id, command):
    """Record a message for rate limiting"""
    user_messages[user_id][command].append(datetime.now().timestamp())

def needs_acceptance(user_id):
    """Check if user needs to accept terms for non-teach models"""
    user_id_str = str(user_id)
    if user_id_str not in user_acceptances:
        return True

    last_acceptance = datetime.fromtimestamp(user_acceptances[user_id_str])
    if datetime.now() - last_acceptance > timedelta(days=30):
        return True

    return False

class AcceptanceView(View):
    def __init__(self, user_id, callback):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.callback = callback
        self.accepted = False

    @discord.ui.button(label="Accept & Continue", style=discord.ButtonStyle.green)
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This prompt is not for you!", ephemeral=True)
            return

        user_acceptances[str(self.user_id)] = datetime.now().timestamp()
        save_user_acceptances()
        self.accepted = True

        await interaction.response.send_message("✅ Terms accepted! Processing your request...", ephemeral=True)
        await self.callback()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This prompt is not for you!", ephemeral=True)
            return

        await interaction.response.send_message("Request cancelled.", ephemeral=True)
        self.stop()

async def download_attachment(attachment):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        print(f"Error downloading attachment: {e}")
    return None

def is_image(filename):
    image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']
    return any(filename.lower().endswith(ext) for ext in image_extensions)

def is_text_file(filename):
    text_extensions = ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.xml', '.csv', '.log']
    return any(filename.lower().endswith(ext) for ext in text_extensions)

async def process_attachments(attachments):
    attachment_contents = []
    for attachment in attachments:
        content = await download_attachment(attachment)
        if not content:
            continue
        if is_image(attachment.filename):
            base64_image = base64.b64encode(content).decode('utf-8')
            ext = attachment.filename.lower().split('.')[-1]
            if ext == 'jpg':
                ext = 'jpeg'
            attachment_contents.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/{ext};base64,{base64_image}"
                }
            })
        elif is_text_file(attachment.filename):
            try:
                text_content = content.decode('utf-8')
                attachment_contents.append({
                    "type": "text",
                    "text": f"**File: {attachment.filename}**\n```{text_content}```"
                })
            except UnicodeDecodeError:
                attachment_contents.append({
                    "type": "text",
                    "text": f"[Unable to read {attachment.filename} - binary file or unsupported encoding]"
                })
        else:
            attachment_contents.append({
                "type": "text",
                "text": f"[Attached file: {attachment.filename} - unsupported file type for processing]"
            })
    return attachment_contents

def query_poe(user_id, user_prompt, attachment_contents=None, model="GPT-5-mini", use_tutor_prompt=True):
    try:
        # Use appropriate conversation history
        conversation_history = tutor_conversation_history if use_tutor_prompt else standard_conversation_history
        
        if attachment_contents:
            message_content = [{"type": "text", "text": user_prompt}]
            message_content.extend(attachment_contents)
        else:
            message_content = user_prompt

        conversation_history[user_id].append({
            "role": "user",
            "content": message_content
        })

        if len(conversation_history[user_id]) > MAX_HISTORY_LENGTH:
            conversation_history[user_id] = conversation_history[user_id][-MAX_HISTORY_LENGTH:]

        messages = []
        if use_tutor_prompt:
            messages.append({"role": "system", "content": custom_prompt})
        messages.extend(conversation_history[user_id])

        print(f"[DEBUG] Querying Poe with model: {model}, use_tutor: {use_tutor_prompt}")

        chat = poe_client.chat.completions.create(
            model=model,
            messages=messages,
            timeout=1000
        )
        response_content = chat.choices[0].message.content
        conversation_history[user_id].append({
            "role": "assistant",
            "content": response_content
        })
        return response_content
    except openai.APIError as e:
        return f"API Error: {e}"
    except openai.APIConnectionError as e:
        return f"Connection Error: Failed to connect to Poe API - {e}"
    except openai.RateLimitError as e:
        return f"Rate Limit Error: {e}"
    except openai.AuthenticationError as e:
        return f"Authentication Error: Invalid API key - {e}"
    except Exception as e:
        return f"Unexpected error: {e}"

async def generate_image(prompt, model="FLUX-schnell"):
    """Generate image using Poe API"""
    try:
        print(f"[DEBUG] Generating image with model: {model}")
        
        # Use low quality for GPT-Image-1-Mini
        extra_body = {}
        if model == "GPT-Image-1-Mini":
            extra_body = {"quality": "low"}
        
        chat = poe_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=1000,
            extra_body=extra_body
        )

        response = chat.choices[0].message
        return response
    except Exception as e:
        return f"Image generation error: {e}"

async def process_command_logic(channel, user, message_content, attachments, model, use_tutor, command_type, user_query, is_image_gen, thinking_msg=None):
    """Shared logic for processing commands from both slash and prefix commands"""
    print(f"[DEBUG] Processing command - Model: {model}, Type: {command_type}, Image: {is_image_gen}")

    # Check rate limits
    can_proceed, rate_limit_msg = check_rate_limit(user.id, command_type)
    if not can_proceed:
        if thinking_msg:
            await thinking_msg.edit(content=f"⏱️ {rate_limit_msg}")
        else:
            await channel.send(f"⏱️ {rate_limit_msg}")
        return

    # Check if non-teach model and needs acceptance
    if not use_tutor and not is_image_gen and needs_acceptance(user.id):
        acceptance_embed = discord.Embed(
            title="⚠️ Non-Tutor Model - User Agreement",
            description=(
                "You are proceeding to use a **non-tutor model**. This will be the base model "
                "without the teaching guidelines, and could be easier to misuse.\n\n"
                "**By using this, you agree to:**\n"
                "1. Not use this to cheat on assignments or academic work\n"
                "2. Not say extremely inappropriate or harmful content to it\n"
                "3. Take responsibility if your usage causes any issues\n\n"
                "If someone reports misuse, you agree to take full responsibility for your actions.\n\n"
                "*This agreement is valid for 30 days.*"
            ),
            color=discord.Color.orange()
        )

        async def process_after_acceptance():
            await execute_command(channel, user, attachments, model, use_tutor, command_type, user_query, is_image_gen, thinking_msg)

        view = AcceptanceView(user.id, process_after_acceptance)
        
        if thinking_msg:
            await thinking_msg.delete()
        
        await channel.send(embed=acceptance_embed, view=view)
        return

    await execute_command(channel, user, attachments, model, use_tutor, command_type, user_query, is_image_gen, thinking_msg)

async def execute_command(channel, user, attachments, model, use_tutor, command_type, user_query, is_image_gen, thinking_msg=None):
    """Execute the actual command"""
    record_message(user.id, command_type)

    # Handle attachments
    attachment_contents = []
    if attachments and not is_image_gen:
        attachment_contents = await process_attachments(attachments)

    if not user_query and not attachment_contents:
        msg = "Please provide a message or attach a file after your command."
        if thinking_msg:
            await thinking_msg.edit(content=msg)
        else:
            await channel.send(msg)
        return

    if not user_query:
        user_query = "Can you help me understand this?"

    # Image generation
    if is_image_gen:
        if not thinking_msg:
            thinking_msg = await channel.send(f"🎨 Generating image... (using {model})")
        else:
            await thinking_msg.edit(content=f"🎨 Generating image... (using {model})")

        try:
            response = await generate_image(user_query, model)
            await thinking_msg.delete()

            if isinstance(response, str):
                await channel.send(response)
            else:
                content = response.content if hasattr(response, 'content') else str(response)

                if content:
                    await channel.send(f"**Prompt:** {user_query}\n\n{content}")
                else:
                    await channel.send(f"Image generated for: {user_query}")
        except Exception as e:
            await thinking_msg.delete()
            await channel.send(f"Error generating image: {e}")
        return

    # Text generation
    model_emoji = "🤖" if not use_tutor else "📚"
    status_msg = f"{model_emoji} {'Mr. Tutor' if use_tutor else 'AI'} is thinking... (using {model})"
    
    if not thinking_msg:
        thinking_msg = await channel.send(status_msg)
    else:
        await thinking_msg.edit(content=status_msg)

    reply = query_poe(user.id, user_query, attachment_contents, model=model, use_tutor_prompt=use_tutor)
    await thinking_msg.delete()

    if len(reply) > 2000:
        chunks = [reply[i:i+2000] for i in range(0, len(reply), 2000)]
        for chunk in chunks:
            await channel.send(chunk)
    else:
        await channel.send(reply)

@bot.event
async def on_ready():
    load_persistent_data()
    print(f'✅ Logged in as {bot.user}')
    print(f'✅ Bot is ready!')
    print(f'Admin User IDs: {ADMIN_IDS}')
    print(f'Admin Role Name: {ADMIN_ROLE_NAME}')
    print(f'⚠️  WARNING: File persistence will be lost on Railway restarts!')

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f'✅ Synced {len(synced)} slash command(s)')
    except Exception as e:
        print(f'❌ Failed to sync commands: {e}')

    bot.loop.create_task(check_bot_state_loop())

async def check_bot_state_loop():
    """Background task to check if bot should be re-enabled"""
    while True:
        check_bot_state()
        await asyncio.sleep(60)

# Slash Commands
@bot.tree.command(name="help", description="Show all available commands")
async def slash_help(interaction: discord.Interaction):
    help_text = """**Mr. Tutor Bot Commands:**

**Tutor Commands (with teaching prompts):**
`/tutor <message>` — GPT-5-mini (Mr. Tutor)
`/tutorplus <message>` — Gemini-2.5-Flash-Tut (Mr. Tutor)
`/tutorminus <message>` — Gemini-2.5-Flash-Lite (Mr. Tutor)

**Standard Commands (no teaching prompts):**
`/standard <message>` — GPT-5-mini (no tutor)
`/standardplus <message>` — Gemini-2.5-Flash-Tut (no tutor)
`/standardminus <message>` — Gemini-2.5-Flash-Lite (no tutor)

**Image Commands:**
`/image <prompt>` — FLUX-schnell
`/imageplus <prompt>` — GPT-Image-1-Mini (low quality)

**Utility:**
`/clear` — Clear your conversation history (separate for tutor/standard)

**Old $ shortcuts still work:** $t, $t+, $t-, $ti, $ti+, $tn, $tn+, $tn-
"""
    await interaction.response.send_message(help_text, ephemeral=True)

@bot.tree.command(name="tutor", description="Ask Mr. Tutor (GPT-5-mini)")
async def slash_tutor(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    thinking_msg = await interaction.followup.send("📚 Mr. Tutor is thinking...")
    await process_command_logic(interaction.channel, interaction.user, message, [],
                                "GPT-5-mini", True, "normal", message, False, thinking_msg)

@bot.tree.command(name="tutorplus", description="Ask Mr. Tutor (Gemini-2.5-Flash-Tut)")
async def slash_tutorplus(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    thinking_msg = await interaction.followup.send("📚 Mr. Tutor is thinking...")
    await process_command_logic(interaction.channel, interaction.user, message, [],
                                "Gemini-2.5-Flash-Tut", True, "plus", message, False, thinking_msg)

@bot.tree.command(name="tutorminus", description="Ask Mr. Tutor (Gemini-2.5-Flash-Lite)")
async def slash_tutorminus(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    thinking_msg = await interaction.followup.send("📚 Mr. Tutor is thinking...")
    await process_command_logic(interaction.channel, interaction.user, message, [],
                                "Gemini-2.5-Flash-Lite", True, "minus", message, False, thinking_msg)

@bot.tree.command(name="standard", description="Ask GPT-5-mini (no tutor prompt)")
async def slash_standard(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    thinking_msg = await interaction.followup.send("🤖 AI is thinking...")
    await process_command_logic(interaction.channel, interaction.user, message, [],
                                "GPT-5-mini", False, "nonnormal", message, False, thinking_msg)

@bot.tree.command(name="standardplus", description="Ask Gemini-2.5-Flash (no tutor prompt)")
async def slash_standardplus(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    thinking_msg = await interaction.followup.send("🤖 AI is thinking...")
    await process_command_logic(interaction.channel, interaction.user, message, [],
                                "Gemini-2.5-Flash-Tut", False, "nonplus", message, False, thinking_msg)

@bot.tree.command(name="standardminus", description="Ask Gemini-2.5-Flash-Lite (no tutor prompt)")
async def slash_standardminus(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    thinking_msg = await interaction.followup.send("🤖 AI is thinking...")
    await process_command_logic(interaction.channel, interaction.user, message, [],
                                "Gemini-2.5-Flash-Lite", False, "nonminus", message, False, thinking_msg)

@bot.tree.command(name="image", description="Generate image with FLUX-schnell")
async def slash_image(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer()
    thinking_msg = await interaction.followup.send("🎨 Generating image...")
    await process_command_logic(interaction.channel, interaction.user, prompt, [],
                                "FLUX-schnell", False, "image", prompt, True, thinking_msg)

@bot.tree.command(name="imageplus", description="Generate image with GPT-Image-1-Mini (low quality)")
async def slash_imageplus(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer()
    thinking_msg = await interaction.followup.send("🎨 Generating image...")
    await process_command_logic(interaction.channel, interaction.user, prompt, [],
                                "GPT-Image-1-Mini", False, "imageplus", prompt, True, thinking_msg)

@bot.tree.command(name="clear", description="Clear your conversation history")
async def slash_clear(interaction: discord.Interaction):
    user_id = interaction.user.id
    tutor_cleared = False
    standard_cleared = False
    
    if user_id in tutor_conversation_history:
        tutor_conversation_history[user_id].clear()
        tutor_cleared = True
    
    if user_id in standard_conversation_history:
        standard_conversation_history[user_id].clear()
        standard_cleared = True
    
    if tutor_cleared or standard_cleared:
        msg = "✅ Your conversation history has been cleared!"
        if tutor_cleared and standard_cleared:
            msg += " (Both tutor and standard histories)"
        elif tutor_cleared:
            msg += " (Tutor history)"
        else:
            msg += " (Standard history)"
        await interaction.response.send_message(msg, ephemeral=True)
    else:
        await interaction.response.send_message("You don't have any conversation history yet.", ephemeral=True)

# Admin Slash Commands
@bot.tree.command(name="setgloballimit", description="[ADMIN] Set global rate limit for a command")
async def slash_setgloballimit(interaction: discord.Interaction, command: str, per_min: int, per_10min: int, per_hour: int):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Sorry, but you need admin permissions to use this command.", ephemeral=True)
        return
    
    rate_limits["global"][command] = {
        "per_minute": per_min,
        "per_10min": per_10min,
        "per_hour": per_hour
    }
    save_rate_limits()
    print(f"[ADMIN] Global rate limit set for {command}: {per_min}/min, {per_10min}/10min, {per_hour}/hour")
    await interaction.response.send_message(f"✅ **Global rate limit set for `{command}`**\n📊 Limits: {per_min}/min, {per_10min}/10min, {per_hour}/hour")

@bot.tree.command(name="setuserlimit", description="[ADMIN] Set rate limit for a specific user")
async def slash_setuserlimit(interaction: discord.Interaction, user: discord.User, command: str, duration_hours: float, per_min: int, per_10min: int, per_hour: int):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Sorry, but you need admin permissions to use this command.", ephemeral=True)
        return
    
    user_id_str = str(user.id)
    if user_id_str not in rate_limits["users"]:
        rate_limits["users"][user_id_str] = {}
    
    expires = None
    if duration_hours > 0:
        expires = (datetime.now() + timedelta(hours=duration_hours)).timestamp()
    
    rate_limits["users"][user_id_str][command] = {
        "per_minute": per_min,
        "per_10min": per_10min,
        "per_hour": per_hour,
        "expires": expires
    }
    save_rate_limits()
    
    duration_text = f"{duration_hours} hours" if duration_hours > 0 else "permanently"
    print(f"[ADMIN] User rate limit set for {user.name} on {command}")
    await interaction.response.send_message(f"✅ **Rate limit set for {user.mention}**\n📝 Command: `{command}`\n⏱️ Duration: {duration_text}\n📊 Limits: {per_min}/min, {per_10min}/10min, {per_hour}/hour")

@bot.tree.command(name="removegloballimit", description="[ADMIN] Remove global rate limit for a command")
async def slash_removegloballimit(interaction: discord.Interaction, command: str):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Sorry, but you need admin permissions to use this command.", ephemeral=True)
        return
    
    if command in rate_limits["global"]:
        del rate_limits["global"][command]
        save_rate_limits()
        print(f"[ADMIN] Global rate limit removed for {command}")
        await interaction.response.send_message(f"✅ Global rate limit removed for `{command}`")
    else:
        await interaction.response.send_message(f"❌ No global rate limit found for `{command}`")

@bot.tree.command(name="removeuserlimit", description="[ADMIN] Remove rate limit for a specific user")
async def slash_removeuserlimit(interaction: discord.Interaction, user: discord.User, command: str):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Sorry, but you need admin permissions to use this command.", ephemeral=True)
        return
    
    user_id_str = str(user.id)
    
    if user_id_str in rate_limits["users"] and command in rate_limits["users"][user_id_str]:
        del rate_limits["users"][user_id_str][command]
        save_rate_limits()
        print(f"[ADMIN] User rate limit removed for {user.name} on {command}")
        await interaction.response.send_message(f"✅ Rate limit removed for {user.mention} on `{command}`")
    else:
        await interaction.response.send_message(f"❌ No rate limit found for {user.mention} on `{command}`")

@bot.tree.command(name="togglebot", description="[ADMIN] Disable bot for specified minutes (0 = infinite)")
async def slash_togglebot(interaction: discord.Interaction, minutes: float):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Sorry, but you need admin permissions to use this command.", ephemeral=True)
        return
    
    bot_state["enabled"] = False
    
    if minutes > 0:
        bot_state["disable_until"] = (datetime.now() + timedelta(minutes=minutes)).timestamp()
        print(f"[ADMIN] Bot disabled for {minutes} minutes")
        await interaction.response.send_message(f"🔴 **Bot disabled for {minutes} minutes.**")
    else:
        bot_state["disable_until"] = None
        print(f"[ADMIN] Bot disabled indefinitely")
        await interaction.response.send_message("🔴 **Bot disabled indefinitely until re-enabled.**")
    
    save_bot_state()

@bot.tree.command(name="enablebot", description="[ADMIN] Re-enable the bot")
async def slash_enablebot(interaction: discord.Interaction):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Sorry, but you need admin permissions to use this command.", ephemeral=True)
        return
    
    bot_state["enabled"] = True
    bot_state["disable_until"] = None
    save_bot_state()
    print(f"[ADMIN] Bot re-enabled")
    await interaction.response.send_message("🟢 **Bot re-enabled!**")

# Prefix Commands ($ commands)
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Check bot state
    if not check_bot_state():
        if not is_admin(message.author.id, message.author):
            return

    # Handle $ prefix commands
    content_lower = message.content.lower()

    # Help
    if content_lower.startswith("$help"):
        help_text = """**Mr. Tutor Bot Commands:**

**Tutor Commands (with teaching prompts):**
`/tutor <message>` or `$tutor <message>` — GPT-5-mini (Mr. Tutor)
`/tutorplus <message>` or `$tutorplus <message>` — Gemini-2.5-Flash-Tut
`/tutorminus <message>` or `$tutorminus <message>` — Gemini-2.5-Flash-Lite

**Standard Commands (no teaching prompts):**
`/standard <message>` or `$standard <message>` — GPT-5-mini
`/standardplus <message>` or `$standardplus <message>` — Gemini-2.5-Flash-Tut
`/standardminus <message>` or `$standardminus <message>` — Gemini-2.5-Flash-Lite

**Image Commands:**
`/image <prompt>` or `$image <prompt>` — FLUX-schnell
`/imageplus <prompt>` or `$imageplus <prompt>` — GPT-Image-1-Mini (low)

**Utility:**
`/clear` or `$clear` — Clear conversation history

**Old shortcuts:** $t, $t+, $t-, $ti, $ti+, $tn, $tn+, $tn-

**Admin Commands:**
Use slash commands: /setgloballimit, /setuserlimit, /removegloballimit, /removeuserlimit, /togglebot, /enablebot
Or old $ versions: $setgloballimit, $setuserlimit, $removelimit, $togglebot, $enablebot
"""
        await message.channel.send(help_text)
        return

    # Clear
    if content_lower.startswith("$clear"):
        user_id = message.author.id
        tutor_cleared = False
        standard_cleared = False
        
        if user_id in tutor_conversation_history:
            tutor_conversation_history[user_id].clear()
            tutor_cleared = True
        
        if user_id in standard_conversation_history:
            standard_conversation_history[user_id].clear()
            standard_cleared = True
        
        if tutor_cleared or standard_cleared:
            msg = "✅ Your conversation history has been cleared!"
            if tutor_cleared and standard_cleared:
                msg += " (Both tutor and standard histories)"
            elif tutor_cleared:
                msg += " (Tutor history)"
            else:
                msg += " (Standard history)"
            await message.channel.send(msg)
        else:
            await message.channel.send("You don't have any conversation history yet.")
        return

    # Admin commands (old $ style)
    if is_admin(message.author.id, message.author):
        if content_lower.startswith("$setgloballimit"):
            parts = message.content.split()
            if len(parts) < 5:
                await message.channel.send("❌ Usage: `$setgloballimit <command> <per_min> <per_10min> <per_hour>`")
                return

            command = parts[1]
            try:
                per_min = int(parts[2])
                per_10min = int(parts[3])
                per_hour = int(parts[4])
            except ValueError:
                await message.channel.send("❌ Invalid numbers for rate limits.")
                return

            rate_limits["global"][command] = {
                "per_minute": per_min,
                "per_10min": per_10min,
                "per_hour": per_hour
            }
            save_rate_limits()
            print(f"[ADMIN] Global rate limit set for {command}: {per_min}/min, {per_10min}/10min, {per_hour}/hour")
            await message.channel.send(f"✅ **Global rate limit set for `{command}`**\n📊 Limits: {per_min}/min, {per_10min}/10min, {per_hour}/hour")
            return

        if content_lower.startswith("$setuserlimit"):
            parts = message.content.split()
            if len(parts) < 7:
                await message.channel.send("❌ Usage: `$setuserlimit <@user> <command> <duration_hours> <per_min> <per_10min> <per_hour>`")
                return

            if not message.mentions:
                await message.channel.send("❌ Please mention a user.")
                return

            target_user = message.mentions[0]
            command = parts[2]

            try:
                duration_hours = float(parts[3])
                per_min = int(parts[4])
                per_10min = int(parts[5])
                per_hour = int(parts[6])
            except ValueError:
                await message.channel.send("❌ Invalid numbers for rate limits or duration.")
                return

            user_id_str = str(target_user.id)
            if user_id_str not in rate_limits["users"]:
                rate_limits["users"][user_id_str] = {}

            expires = None
            if duration_hours > 0:
                expires = (datetime.now() + timedelta(hours=duration_hours)).timestamp()

            rate_limits["users"][user_id_str][command] = {
                "per_minute": per_min,
                "per_10min": per_10min,
                "per_hour": per_hour,
                "expires": expires
            }
            save_rate_limits()

            duration_text = f"{duration_hours} hours" if duration_hours > 0 else "permanently"
            print(f"[ADMIN] User rate limit set for {target_user.name} on {command}")
            await message.channel.send(f"✅ **Rate limit set for {target_user.mention}**\n📝 Command: `{command}`\n⏱️ Duration: {duration_text}\n📊 Limits: {per_min}/min, {per_10min}/10min, {per_hour}/hour")
            return

        if content_lower.startswith("$removelimit"):
            parts = message.content.split()
            if len(parts) < 3:
                await message.channel.send("❌ Usage: `$removelimit global <command>` or `$removelimit user <@user> <command>`")
                return

            limit_type = parts[1].lower()

            if limit_type == "global":
                command = parts[2]
                if command in rate_limits["global"]:
                    del rate_limits["global"][command]
                    save_rate_limits()
                    print(f"[ADMIN] Global rate limit removed for {command}")
                    await message.channel.send(f"✅ Global rate limit removed for `{command}`")
                else:
                    await message.channel.send(f"❌ No global rate limit found for `{command}`")
                return

            elif limit_type == "user":
                if not message.mentions:
                    await message.channel.send("❌ Please mention a user.")
                    return

                target_user = message.mentions[0]
                command = parts[3]
                user_id_str = str(target_user.id)

                if user_id_str in rate_limits["users"] and command in rate_limits["users"][user_id_str]:
                    del rate_limits["users"][user_id_str][command]
                    save_rate_limits()
                    print(f"[ADMIN] User rate limit removed for {target_user.name} on {command}")
                    await message.channel.send(f"✅ Rate limit removed for {target_user.mention} on `{command}`")
                else:
                    await message.channel.send(f"❌ No rate limit found for {target_user.mention} on `{command}`")
                return

        if content_lower.startswith("$togglebot"):
            parts = message.content.split()
            if len(parts) < 2:
                await message.channel.send("❌ Usage: `$togglebot <minutes>` (0 for infinite)")
                return

            try:
                minutes = float(parts[1])
            except ValueError:
                await message.channel.send("❌ Invalid number for minutes.")
                return

            bot_state["enabled"] = False

            if minutes > 0:
                bot_state["disable_until"] = (datetime.now() + timedelta(minutes=minutes)).timestamp()
                print(f"[ADMIN] Bot disabled for {minutes} minutes")
                await message.channel.send(f"🔴 **Bot disabled for {minutes} minutes.**")
            else:
                bot_state["disable_until"] = None
                print(f"[ADMIN] Bot disabled indefinitely")
                await message.channel.send("🔴 **Bot disabled indefinitely until re-enabled.**")

            save_bot_state()
            return

        if content_lower.startswith("$enablebot"):
            bot_state["enabled"] = True
            bot_state["disable_until"] = None
            save_bot_state()
            print(f"[ADMIN] Bot re-enabled")
            await message.channel.send("🟢 **Bot re-enabled!**")
            return

    # Parse regular commands - CHECK LONGER PREFIXES FIRST
    command = None
    model = None
    use_tutor = True
    command_type = None
    user_query = None
    is_image_gen = False

    # Check for $ prefix commands
    if message.content.startswith("$"):
        for prefix, m, tutor, cmd_type in COMMAND_CONFIGS:
            prefix_with_dollar = f"${prefix} "
            prefix_with_dollar_no_space = f"${prefix}"

            # Check with space or at end of message
            if (content_lower.startswith(prefix_with_dollar) or
                (content_lower == prefix_with_dollar_no_space)):
                command = prefix
                model = m
                use_tutor = tutor
                command_type = cmd_type
                is_image_gen = cmd_type in ["image", "imageplus"]
                user_query = message.content[len(prefix_with_dollar_no_space):].strip()
                print(f"[DEBUG] Matched ${prefix} -> model: {model}, type: {cmd_type}")
                break

    # Check for @ mentions
    if command is None and bot.user in message.mentions:
        prefix_str = f'<@{bot.user.id}>'
        clean_content = message.content.replace(prefix_str, '').strip()

        for prefix, m, tutor, cmd_type in COMMAND_CONFIGS:
            if clean_content.lower().startswith(f"{prefix} ") or clean_content.lower() == prefix:
                command = prefix
                model = m
                use_tutor = tutor
                command_type = cmd_type
                is_image_gen = cmd_type in ["image", "imageplus"]
                user_query = clean_content[len(prefix):].strip()
                print(f"[DEBUG] Matched mention {prefix} -> model: {model}, type: {cmd_type}")
                break

        # Default to tutor if just mentioned
        if command is None:
            command = "tutor"
            model = "GPT-5-mini"
            use_tutor = True
            command_type = "normal"
            user_query = clean_content
            print(f"[DEBUG] Defaulted to 'tutor' for mention")

    if command:
        await process_command_logic(message.channel, message.author, message.content,
                                    message.attachments, model, use_tutor, command_type,
                                    user_query, is_image_gen)

if __name__ == "__main__":
    from keep_alive import start as start_keep_alive
    start_keep_alive()  # Start Flask server BEFORE bot.run()
    bot.run(DISCORD_BOT_TOKEN)
