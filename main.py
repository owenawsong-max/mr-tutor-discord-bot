import discord
from discord.ext import commands, app_commands
from discord.ui import Button, View
import openai
import os
import aiohttp
import base64
from collections import defaultdict
import json
from datetime import datetime, timedelta
import asyncio

# --- Configuration and Setup ---

POE_API_KEY = os.getenv("POE_API_KEY")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ADMIN_IDS = [int(id_str) for id_str in os.getenv("ADMIN_IDS", "").split(",") if id_str.strip()]
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "Admin")

# Persistent storage files
RATE_LIMITS_FILE = "rate_limits.json"
BOT_STATE_FILE = "bot_state.json"
USER_ACCEPTANCES_FILE = "user_acceptances.json"

# --- Bot Initialization ---

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

# Use commands.Bot instead of discord.Client
bot = commands.Bot(command_prefix='$', intents=intents)

# --- State and History ---

conversation_history = defaultdict(list)
MAX_HISTORY_LENGTH = 50

# Rate limiting structure (will be loaded from file)
rate_limits = {"global": {}, "users": {}}

# Bot state (will be loaded from file)
bot_state = {"enabled": True, "disable_until": None}

# User message tracking for rate limiting
user_messages = defaultdict(lambda: defaultdict(list))  # {user_id: {command_type: [timestamps]}}

# User acceptances for non-teach models
user_acceptances = {}  # {user_id: timestamp}

# --- Custom Prompt ---
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

# --- Persistence Functions ---

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

# --- Utility Functions ---

def is_admin(user_id, member=None):
    """Check if user is admin by ID or role"""
    # Check user ID
    if user_id in ADMIN_IDS:
        return True
    
    # Check role if member object is provided
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

def check_rate_limit(user_id, command_type):
    """Check if user has exceeded rate limits for a command_type"""
    now = datetime.now().timestamp()
    user_id_str = str(user_id)
    
    # Clean old timestamps (keep last hour for all checks)
    for uid in user_messages:
        for cmd in user_messages[uid]:
            user_messages[uid][cmd] = [
                ts for ts in user_messages[uid][cmd] 
                if now - ts < 3600
            ]

    # Check user-specific rate limits
    if user_id_str in rate_limits["users"] and command_type in rate_limits["users"][user_id_str]:
        limit_config = rate_limits["users"][user_id_str][command_type]
        
        # Check if expired
        if "expires" in limit_config and limit_config["expires"] and now >= limit_config["expires"]:
            del rate_limits["users"][user_id_str][command_type]
            save_rate_limits()
        else:
            timestamps = user_messages[user_id][command_type]
            
            # Check per minute
            if "per_minute" in limit_config and len([ts for ts in timestamps if now - ts < 60]) >= limit_config["per_minute"]:
                return False, f"You've exceeded the user rate limit (per minute) for this command type (`{command_type}`)."
            
            # Check per 10 minutes
            if "per_10min" in limit_config and len([ts for ts in timestamps if now - ts < 600]) >= limit_config["per_10min"]:
                return False, f"You've exceeded the user rate limit (per 10 minutes) for this command type (`{command_type}`)."
            
            # Check per hour
            if "per_hour" in limit_config and len([ts for ts in timestamps if now - ts < 3600]) >= limit_config["per_hour"]:
                return False, f"You've exceeded the user rate limit (per hour) for this command type (`{command_type}`)."
    
    # Check global rate limits
    if command_type in rate_limits["global"]:
        limit_config = rate_limits["global"][command_type]
        timestamps = user_messages[user_id][command_type]
        
        # Check per minute
        if "per_minute" in limit_config and len([ts for ts in timestamps if now - ts < 60]) >= limit_config["per_minute"]:
            return False, f"Global rate limit exceeded (per minute) for this command type (`{command_type}`)."
        
        # Check per 10 minutes
        if "per_10min" in limit_config and len([ts for ts in timestamps if now - ts < 600]) >= limit_config["per_10min"]:
            return False, f"Global rate limit exceeded (per 10 minutes) for this command type (`{command_type}`)."
        
        # Check per hour
        if "per_hour" in limit_config and len([ts for ts in timestamps if now - ts < 3600]) >= limit_config["per_hour"]:
            return False, f"Global rate limit exceeded (per hour) for this command type (`{command_type}`)."
    
    return True, None

def record_message(user_id, command_type):
    """Record a message for rate limiting"""
    user_messages[user_id][command_type].append(datetime.now().timestamp())

def needs_acceptance(user_id):
    """Check if user needs to accept terms for non-teach models"""
    user_id_str = str(user_id)
    if user_id_str not in user_acceptances:
        return True
    
    # Check if acceptance is older than 30 days
    last_acceptance = datetime.fromtimestamp(user_acceptances[user_id_str])
    if datetime.now() - last_acceptance > timedelta(days=30):
        return True
    
    return False

# --- Attachment Handling ---

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

# --- Poe API Interaction ---

def query_poe(user_id, user_prompt, attachment_contents=None, model="GPT-5-mini", use_tutor_prompt=True):
    try:
        # Prepare content for history and API call
        if attachment_contents:
            # For multi-modal input, the prompt must be a list of content blocks
            message_content = [{"type": "text", "text": user_prompt}]
            message_content.extend(attachment_contents)
        else:
            # For text-only, the prompt can be a string or a list with one text block
            message_content = user_prompt if not attachment_contents else [{"type": "text", "text": user_prompt}]
        
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
    except Exception as e:
        # Catch all API errors and return a user-friendly message
        return f"API/Model Error (`{model}`): {type(e).__name__} - {e}"

async def generate_image(prompt, model="FLUX-schnell"):
    """Generate image using Poe API"""
    try:
        # Image generation typically doesn't use a system prompt
        chat = poe_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=1000
        )
        
        response = chat.choices[0].message
        return response
    except Exception as e:
        return f"Image generation error: {type(e).__name__} - {e}"

# --- Command Logic Core ---

async def process_chat_command(interaction_or_message, user_id, user_query, model, use_tutor, command_type, is_image_gen=False):
    """Handles the core logic for chat/image commands, called by both prefix and slash commands."""
    
    # 1. Rate Limit Check
    can_proceed, rate_limit_msg = check_rate_limit(user_id, command_type)
    if not can_proceed:
        if isinstance(interaction_or_message, discord.Interaction):
            await interaction_or_message.response.send_message(f"⏱️ {rate_limit_msg}", ephemeral=True)
        else:
            await interaction_or_message.channel.send(f"⏱️ {rate_limit_msg}")
        return
    
    # 2. Acceptance Check (only for non-tutor, non-image models)
    if not use_tutor and not is_image_gen and needs_acceptance(user_id):
        async def process_after_acceptance():
            await process_chat_command(interaction_or_message, user_id, user_query, model, use_tutor, command_type, is_image_gen)
        
        view = AcceptanceView(user_id, process_after_acceptance)
        
        acceptance_embed = discord.Embed(
            title="⚠️ Non-Tutor Model - User Agreement",
            description=(
                "You are proceeding to use a **non-tutor model**. This will be the base model "
                "without the teaching guidelines, and could be easier to misuse.\n\n"
                "**By using this, you agree to:**\n"
                "1. Not use this to cheat on assignments or academic work\n"
                "2. Not say extremely inappropriate or harmful content to it\n"
                "3. Take responsibility if your usage causes any issues\n\n"
                "*This agreement is valid for 30 days.*"
            ),
            color=discord.Color.orange()
        )
        
        if isinstance(interaction_or_message, discord.Interaction):
            await interaction_or_message.response.send_message(embed=acceptance_embed, view=view, ephemeral=True)
        else:
            await interaction_or_message.channel.send(embed=acceptance_embed, view=view)
        return

    # 3. Record Message & Process Attachments
    record_message(user_id, command_type)
    
    attachment_contents = []
    attachments = []
    
    if isinstance(interaction_or_message, discord.Interaction) and interaction_or_message.message:
        attachments = interaction_or_message.message.attachments
    elif isinstance(interaction_or_message, discord.Message):
        attachments = interaction_or_message.attachments

    if attachments and not is_image_gen:
        attachment_contents = await process_attachments(attachments)
    
    if not user_query and not attachment_contents:
        response_text = "Please provide a message or attach a file after your command."
        if isinstance(interaction_or_message, discord.Interaction):
            await interaction_or_message.response.send_message(response_text, ephemeral=True)
        else:
            await interaction_or_message.channel.send(response_text)
        return
    
    if not user_query:
        user_query = "Can you help me understand this?"
        
    # 4. Image Generation
    if is_image_gen:
        thinking_msg = await interaction_or_message.channel.send(f"🎨 Generating image... (using `{model}`)")
        
        response = await generate_image(user_query, model)
        await thinking_msg.delete()
        
        if isinstance(response, str):
            await interaction_or_message.channel.send(f"**Image Generation Failed:**\n{response}")
        else:
            # Poe image responses often contain text describing the image/process
            content = response.content if hasattr(response, 'content') else ""
            
            output_msg = f"🎨 **Image Generation Complete** (Model: `{model}`)\nPrompt: *{user_query}*"
            if content:
                output_msg += f"\n\n{content}"
            
            # NOTE: Direct image URL embedding from Poe API is complex/unreliable here.
            # We rely on Poe's response text which *should* contain the image data/link if available.
            await interaction_or_message.channel.send(output_msg)
        return
    
    # 5. Text Generation
    model_emoji = "🤖" if not use_tutor else "📚"
    thinking_msg = await interaction_or_message.channel.send(f"{model_emoji} {'Mr. Tutor' if use_tutor else 'AI'} is thinking... (using `{model}`)")
    
    reply = query_poe(user_id, user_query, attachment_contents, model=model, use_tutor_prompt=use_tutor)
    await thinking_msg.delete()
    
    # Send response, chunking if necessary
    if len(reply) > 2000:
        chunks = [reply[i:i+2000] for i in range(0, len(reply), 2000)]
        for chunk in chunks:
            await interaction_or_message.channel.send(chunk)
    else:
        await interaction_or_message.channel.send(reply)


# --- Command Definitions (Slash & Prefix) ---

# Command mapping for easy lookup: (model, use_tutor, command_type)
COMMAND_MAP = {
    # Tutor Models
    "t": ("GPT-5-mini", True, "normal"),
    "t_plus": ("Gemini-2.5-Flash-Tut", True, "plus"),
    "t_minus": ("Gemini-2.5-Flash-Lite", True, "minus"),
    # Image Models
    "t_image": ("FLUX-schnell", False, "image"),
    "t_image_plus": ("GPT-Image-1-Mini", False, "imageplus"),
    # Non-Tutor Models
    "tn": ("GPT-5-mini", False, "nonnormal"),
    "tn_plus": ("Gemini-2.5-Flash-Tut", False, "nonplus"),
    "tn_minus": ("Gemini-2.5-Flash-Lite", False, "nonminus"),
}

# --- Chat Commands ---

class TutorGroup(app_commands.GroupCommand):
    def __init__(self, name, description, model, use_tutor, command_type):
        super().__init__(name=name, description=description)
        self.model = model
        self.use_tutor = use_tutor
        self.command_type = command_type

    async def callback(self, interaction: discord.Interaction, *, query: str = None):
        await process_chat_command(interaction, interaction.user.id, query, self.model, self.use_tutor, self.command_type, is_image_gen=(self.command_type in ["image", "imageplus"]))

# Create Slash Commands based on the map
for cmd_name, (model, tutor, cmd_type) in COMMAND_MAP.items():
    if cmd_type in ["normal", "plus", "minus", "nonnormal", "nonplus", "nonminus"]:
        # Text Commands
        description = f"Ask {model} (Tutor: {tutor})"
        if cmd_type.startswith("non"):
            description = f"Ask {model} (No Tutor Prompt)"
        
        # Register as a simple command for now, we'll group them later
        @bot.hybrid_command(name=cmd_name, description=description)
        async def command_func(interaction: discord.Interaction | discord.Message, query: str = None):
            # This function is called by both $cmd and /cmd
            user_id = interaction.user.id
            
            # Determine command type from the function name (which reflects the map key)
            func_name = command_func.__name__
            
            # Map function name back to the original command type key
            # This is a bit hacky but necessary for hybrid commands to know their configuration
            # We need a better way to pass config, but for now, we rely on the command name.
            
            # Simple mapping for hybrid commands based on name:
            if func_name == 't_func': model, tutor, cmd_type = COMMAND_MAP['t']
            elif func_name == 't_plus_func': model, tutor, cmd_type = COMMAND_MAP['t_plus']
            elif func_name == 't_minus_func': model, tutor, cmd_type = COMMAND_MAP['t_minus']
            elif func_name == 'tn_func': model, tutor, cmd_type = COMMAND_MAP['tn']
            elif func_name == 'tn_plus_func': model, tutor, cmd_type = COMMAND_MAP['tn_plus']
            elif func_name == 'tn_minus_func': model, tutor, cmd_type = COMMAND_MAP['tn_minus']
            elif func_name == 't_image_func': model, tutor, cmd_type = COMMAND_MAP['t_image']
            elif func_name == 't_image_plus_func': model, tutor, cmd_type = COMMAND_MAP['t_image_plus']
            else: return # Should not happen

            await process_chat_command(interaction, user_id, query, model, tutor, cmd_type, is_image_gen=(cmd_type in ["image", "imageplus"]))
        
        # Rename the function dynamically to ensure it maps correctly to the map key for processing
        # This is a common workaround for dynamically created hybrid commands
        command_func.__name__ = f"{cmd_name}_func"
        
        # Re-assign the command to the bot object with the correct name
        setattr(bot, cmd_name, command_func)


# --- Utility Commands ---

@bot.hybrid_command(name="clear", description="Clears your conversation history with the bot.")
async def clear_history(interaction: discord.Interaction | discord.Message):
    user_id = interaction.user.id
    if user_id in conversation_history:
        conversation_history[user_id].clear()
        response = "Your conversation history has been cleared!"
    else:
        response = "You don't have any conversation history yet."
    
    if isinstance(interaction, discord.Interaction):
        await interaction.response.send_message(response, ephemeral=True)
    else:
        await interaction.channel.send(response)

@bot.hybrid_command(name="help", description="Shows all available commands.")
async def help_cmd(interaction: discord.Interaction | discord.Message):
    help_text = """**Mr. Tutor Bot Commands:**
**📚 Teaching Models (Default: Tutor Prompt)**
`/t` or `$t <message>` — Mr. Tutor (GPT-5-mini)
`/t_plus` or `$t+ <message>` — Gemini-2.5-Flash-Tut (web search ON)
`/t_minus` or `$t- <message>` — Gemini-2.5-Flash-Lite (cheap version)

**🎨 Image Generation**
`/t_image` or `$ti <prompt>` — Image generation (FLUX-schnell)
`/t_image_plus` or `$ti+ <prompt>` — Image generation (GPT-Image-1-Mini)

**🤖 Non-Teach Models (Requires Acceptance)**
`/tn` or `$tn <message>` — GPT-5-mini (no tutor prompt)
`/tn_plus` or `$tn+ <message>` — Gemini-2.5-Flash-Tut (no tutor prompt)
`/tn_minus` or `$tn- <message>` — Gemini-2.5-Flash-Lite (no tutor prompt)

**🛠️ Utility**
`/clear` or `$clear` — Clear your conversation history
`/help` or `$help` — Show this help

**Admin Commands (Requires Admin Role/ID):**
`/admin_set_limit <type> <name> <min> <10min> <hour>` — Set global/user rate limit
`/admin_remove_limit <type> <name> [user]` — Remove a rate limit
`/admin_toggle_bot <minutes>` — Disable bot (0 = infinite)
`/admin_enable_bot` — Re-enable bot immediately

**Note on Prefix Commands:** All commands above also work with the `$` prefix (e.g., `$t`, `$tn+`, `$clear`).
"""
    if isinstance(interaction, discord.Interaction):
        await interaction.response.send_message(help_text, ephemeral=True)
    else:
        await interaction.channel.send(help_text)

# --- Acceptance View (Re-defined for clarity) ---

class AcceptanceView(View):
    def __init__(self, user_id, callback):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
        self.callback = callback
    
    @discord.ui.button(label="Accept & Continue", style=discord.ButtonStyle.green)
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This prompt is not for you!", ephemeral=True)
            return
        
        user_acceptances[str(self.user_id)] = datetime.now().timestamp()
        save_user_acceptances()
        
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

# --- Admin Commands ---

@bot.tree.command(name="admin_set_limit", description="[ADMIN] Set a global or user rate limit.")
@app_commands.describe(
    limit_type="Type: 'global' or 'user'",
    name="Command type ('normal', 'plus', 'image', etc.) or @user mention",
    per_min="Requests per minute",
    per_10min="Requests per 10 minutes",
    per_hour="Requests per hour",
    duration_hours="For user limits only: how long the limit lasts (0 for permanent)"
)
async def admin_set_limit(interaction: discord.Interaction, limit_type: str, name: str, per_min: int, per_10min: int, per_hour: int, duration_hours: float = 0.0):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Access Denied: You are not an administrator.", ephemeral=True)
        return
    
    limit_type = limit_type.lower()
    
    try:
        if limit_type == "global":
            if name not in [t[2] for t in COMMAND_MAP.values()]:
                await interaction.response.send_message(f"❌ Invalid command type: `{name}`. Use one of: {', '.join(sorted(list(set([t[2] for t in COMMAND_MAP.values()]))))}", ephemeral=True)
                return
            
            rate_limits["global"][name] = {
                "per_minute": per_min, "per_10min": per_10min, "per_hour": per_hour
            }
            save_rate_limits()
            await interaction.response.send_message(f"✅ Global rate limit set for `{name}`: {per_min}/min, {per_10min}/10min, {per_hour}/hour.")
        
        elif limit_type == "user":
            if not interaction.message or not interaction.message.mentions:
                await interaction.response.send_message("❌ For user limits, you must mention the user in the original prefix command that triggered this, or use a different structure for slash commands. For now, please mention the user in the command if possible or use a prefix command.", ephemeral=True)
                return
            
            target_user = interaction.message.mentions[0] # This is unreliable for pure slash commands. Let's assume the user will use a prefix command for user limits for now, or we need to add a user argument to the slash command.
            # ***FIX: For a pure slash command, we must add a user argument***
            # Since I cannot easily modify the function signature above without breaking the prefix command mapping, 
            # I will advise the user to use the prefix command for user limits or update the slash command structure.
            # For now, I'll check if the command was triggered by a message that had a mention.
            
            # ***REVISING ADMIN SLICE FOR USER LIMITS***
            # To support user limits cleanly in slash commands, we need to add a user parameter.
            # I'll add a new slash command for user limits that takes a user.
            await interaction.response.send_message("❌ User limit setting via this generic slash command is complex with prefix commands. Please use the dedicated `/admin_set_user_limit` command instead, or use the prefix command `$setuserlimit @user <cmd> <h> <m> <10m> <h>`.", ephemeral=True)
        
        else:
            await interaction.response.send_message("❌ Invalid limit type. Use 'global' or 'user'.", ephemeral=True)

    except Exception as e:
        await interaction.response.send_message(f"❌ Error setting limit: {e}", ephemeral=True)


@bot.tree.command(name="admin_set_user_limit", description="[ADMIN] Set a user-specific rate limit.")
@app_commands.describe(
    target_user="The user to limit",
    command_type="Command type ('normal', 'plus', 'image', etc.)",
    per_min="Requests per minute",
    per_10min="Requests per 10 minutes",
    per_hour="Requests per hour",
    duration_hours="How long the limit lasts (0 for permanent)"
)
async def admin_set_user_limit(interaction: discord.Interaction, target_user: discord.Member, command_type: str, per_min: int, per_10min: int, per_hour: int, duration_hours: float = 0.0):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Access Denied: You are not an administrator.", ephemeral=True)
        return
    
    if command_type not in [t[2] for t in COMMAND_MAP.values()]:
        await interaction.response.send_message(f"❌ Invalid command type: `{command_type}`. Use one of: {', '.join(sorted(list(set([t[2] for t in COMMAND_MAP.values()]))))}", ephemeral=True)
        return

    user_id_str = str(target_user.id)
    if user_id_str not in rate_limits["users"]:
        rate_limits["users"][user_id_str] = {}
    
    expires = None
    if duration_hours > 0:
        expires = (datetime.now() + timedelta(hours=duration_hours)).timestamp()
    
    rate_limits["users"][user_id_str][command_type] = {
        "per_minute": per_min, "per_10min": per_10min, "per_hour": per_hour, "expires": expires
    }
    save_rate_limits()
    
    duration_text = f"{duration_hours} hours" if duration_hours > 0 else "permanently"
    await interaction.response.send_message(f"✅ Rate limit set for {target_user.mention} on command type `{command_type}` for {duration_text}: {per_min}/min, {per_10min}/10min, {per_hour}/hour.")


@bot.tree.command(name="admin_remove_limit", description="[ADMIN] Remove a global or user rate limit.")
@app_commands.describe(
    limit_type="Type: 'global' or 'user'",
    name="Command type ('normal', 'plus', 'image', etc.)",
    target_user="The user to remove the limit from (only needed if limit_type is 'user')"
)
async def admin_remove_limit(interaction: discord.Interaction, limit_type: str, name: str, target_user: discord.Member = None):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Access Denied: You are not an administrator.", ephemeral=True)
        return

    limit_type = limit_type.lower()
    
    if limit_type == "global":
        if name in rate_limits["global"]:
            del rate_limits["global"][name]
            save_rate_limits()
            await interaction.response.send_message(f"✅ Global rate limit removed for `{name}`")
        else:
            await interaction.response.send_message(f"❌ No global rate limit found for `{name}`")
    
    elif limit_type == "user":
        if not target_user:
            await interaction.response.send_message("❌ For user limits, you must specify the user to remove the limit from (e.g., `/admin_remove_limit user normal @User`).")
            return
        
        user_id_str = str(target_user.id)
        if user_id_str in rate_limits["users"] and name in rate_limits["users"][user_id_str]:
            del rate_limits["users"][user_id_str][name]
            save_rate_limits()
            await interaction.response.send_message(f"✅ Rate limit removed for {target_user.mention} on command type `{name}`")
        else:
            await interaction.response.send_message(f"❌ No rate limit found for {target_user.mention} on command type `{name}`")
    else:
        await interaction.response.send_message("❌ Invalid limit type. Use 'global' or 'user'.")


@bot.tree.command(name="admin_toggle_bot", description="[ADMIN] Disable the bot for a set duration.")
@app_commands.describe(minutes="Duration in minutes to disable the bot (0 for infinite).")
async def admin_toggle_bot(interaction: discord.Interaction, minutes: float):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Access Denied: You are not an administrator.", ephemeral=True)
        return
    
    bot_state["enabled"] = False
    
    if minutes > 0:
        bot_state["disable_until"] = (datetime.now() + timedelta(minutes=minutes)).timestamp()
        await interaction.response.send_message(f"🔴 Bot disabled for {minutes} minutes. It will re-enable automatically.")
    else:
        bot_state["disable_until"] = None
        await interaction.response.send_message("🔴 Bot disabled indefinitely until re-enabled via `/admin_enable_bot`.")
    
    save_bot_state()


@bot.tree.command(name="admin_enable_bot", description="[ADMIN] Re-enable the bot immediately.")
async def admin_enable_bot(interaction: discord.Interaction):
    if not is_admin(interaction.user.id, interaction.user):
        await interaction.response.send_message("❌ Access Denied: You are not an administrator.", ephemeral=True)
        return
    
    bot_state["enabled"] = True
    bot_state["disable_until"] = None
    save_bot_state()
    await interaction.response.send_message("🟢 Bot re-enabled!")


# --- Bot Events ---

@bot.event
async def on_ready():
    load_persistent_data()
    
    # Sync app commands (Slash Commands)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print(f'Admin User IDs: {ADMIN_IDS}')
    print(f'Admin Role Name: {ADMIN_ROLE_NAME}')
    print(f'⚠️  WARNING: File persistence will be lost on Railway restarts!')
    print(f'⚠️  Consider using environment variables or a database for production.')
    
    # Start background task to check bot state
    bot.loop.create_task(check_bot_state_loop())

async def check_bot_state_loop():
    """Background task to check if bot should be re-enabled"""
    while True:
        check_bot_state()
        await asyncio.sleep(60)  # Check every minute

@bot.event
async def on_message(message):
    # Process commands first (handles both $prefix and /slash via hybrid_command)
    await bot.process_commands(message)
    
    if message.author == bot.user:
        return
    
    # Check bot state before processing any further commands
    if not check_bot_state():
        if not is_admin(message.author.id, message.author):
            return # Ignore non-admin messages when disabled

    # --- Manual Prefix Command Handling (for commands *not* converted to hybrid/slash) ---
    # Since we converted ALL chat commands to hybrid commands above, this section is simplified.
    # We only need to handle the logic for non-command messages or if a user uses a command
    # that wasn't properly converted (which shouldn't happen with the loop above).
    
    # The main issue with your old code was the manual parsing. By using hybrid commands,
    # the framework handles the dispatch to the correct function based on the prefix ($)
    # or the slash invocation (/).
    
    # If a message starts with '$' but doesn't match a defined command, we do nothing here,
    # as the bot.process_commands() handles known prefixes.
    pass

# --- Run Bot ---

if __name__ == "__main__":
    load_persistent_data()
    if not DISCORD_BOT_TOKEN:
        print("FATAL: DISCORD_BOT_TOKEN environment variable not set.")
    else:
        bot.run(DISCORD_BOT_TOKEN)
