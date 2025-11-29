import discord
import openai
import os
import aiohttp
import base64
from collections import defaultdict

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

POE_API_KEY = os.getenv("POE_API_KEY")

conversation_history = defaultdict(list)
MAX_HISTORY_LENGTH = 50

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
                    "text": f"**File: {attachment.filename}**\n``````"
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

def query_poe(user_id, user_prompt, attachment_contents=None, model="GPT-5-mini"):
    try:
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
        messages = [{"role": "system", "content": custom_prompt}]
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

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    print(f'Bot is ready! Use $tut, $tutor, $tutplus, $tutorplus to chat. $clear/$help also work.')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.lower().startswith("$help"):
        help_text = """**Mr. Tutor Bot Commands:**
`$tut <message>` or `$tutor <message>` — Mr. Tutor (GPT-5-mini)
`$tutplus <message>` or `$tutorplus <message>` — Gemini-2.5-Flash-Tut (web search ON)
`$clear` — Clear **your own** memory
`$help` — Show this help message

Mentions work: `@BotName tutplus what time is it?`
Only you can clear your memory, not others.
"""
        await message.channel.send(help_text)
        return

    if message.content.lower().startswith("$clear"):
        user_id = message.author.id
        if user_id in conversation_history:
            conversation_history[user_id].clear()
            await message.channel.send("Your conversation history has been cleared (just for you)!")
        else:
            await message.channel.send("You don't have any conversation history yet.")
        return

    prefixes = ["$tutplus", "$tutorplus", "$tut", "$tutor"]
    plain_prefixes = [p.replace('$', '') for p in prefixes]

    command = None
    model = None
    user_query = None

    mentioned = client.user in message.mentions
    if mentioned:
        prefix = f'<@{client.user.id}>'
        clean_content = message.content.replace(prefix, '').strip()
        for p in plain_prefixes:
            plen = len(p)
            if clean_content.lower().startswith(p):
                command = "$" + p
                model = "Gemini-2.5-Flash-Tut" if "plus" in p else "GPT-5-mini"
                user_query = clean_content[plen:].strip()
                break
        if command is None:
            command = "$tut"
            model = "GPT-5-mini"
            user_query = clean_content

    for p in prefixes:
        plen = len(p)
        if message.content.lower().startswith(p):
            command = p
            model = "Gemini-2.5-Flash-Tut" if "plus" in p else "GPT-5-mini"
            user_query = message.content[plen:].strip()
            break

    if command:
        attachment_contents = []
        if message.attachments:
            attachment_contents = await process_attachments(message.attachments)
        if not user_query and not attachment_contents:
            await message.channel.send("Please provide a message or attach a file after your command.")
            return
        if not user_query:
            user_query = "Can you help me understand this?"

        model_emoji = "?" if model == "GPT-5-mini" else "*"
        thinking_msg = await message.channel.send(f"{model_emoji} Mr. Tutor is thinking... (using {model})")
        reply = query_poe(message.author.id, user_query, attachment_contents, model=model)
        await thinking_msg.delete()

        if len(reply) > 2000:
            chunks = [reply[i:i+2000] for i in range(0, len(reply), 2000)]
            for chunk in chunks:
                await message.channel.send(chunk)
        else:
            await message.channel.send(reply)

client.run(os.getenv("DISCORD_BOT_TOKEN"))
