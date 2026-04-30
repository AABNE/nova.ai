import discord
from ollama import Client
from datetime import datetime
import asyncio
import random
import sys
import os
import urllib.parse
import urllib.request
import io
import time
import json
from concurrent.futures import ThreadPoolExecutor

# ====== loaded from environment variables ======
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")  # default if not set
CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "nova")
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "aussieaviationbne")
# ===============================================

# check all required vars are set
if not DISCORD_TOKEN:
    print("[ERROR] DISCORD_TOKEN environment variable not set!")
    sys.exit(1)
if not OLLAMA_API_KEY:
    print("[ERROR] OLLAMA_API_KEY environment variable not set!")
    sys.exit(1)

intents = discord.Intents.all()
intents.dm_messages = True
intents.guild_messages = True
intents.message_content = True

discord_client = discord.Client(intents=intents)
ollama_client = Client(
    host="https://ollama.com",
    headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"}
)
executor = ThreadPoolExecutor(max_workers=4)
seen_messages = set()

conversation_history = {}
processing_users = set()

SAVE_DIR = "conversations"
os.makedirs(SAVE_DIR, exist_ok=True)

def log(user, message, who):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t}] {who} ({user}): {message}")

def generate_error_code():
    return "ERR-" + str(random.randint(1000, 9999))

def generate_image_url(prompt):
    encoded = urllib.parse.quote(prompt)
    return f"https://image.pollinations.ai/prompt/{encoded}"

def download_image(image_url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                image_url,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return io.BytesIO(r.read())
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(5)
                continue
            raise e

def ask_ollama(messages):
    response = ollama_client.chat(
        model=OLLAMA_MODEL,
        messages=messages
    )
    return response.message.content.strip()

def save_conversation(user_id, username, history):
    filepath = os.path.join(SAVE_DIR, f"{user_id}.json")
    data = {
        "user_id": user_id,
        "username": username,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "history": history
    }
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

def export_conversation(user_id, username, history):
    lines = []
    lines.append("Nova Conversation Export")
    lines.append(f"User: {username}")
    lines.append(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 50)
    lines.append("")
    for msg in history:
        role = username if msg["role"] == "user" else "Nova"
        lines.append(f"[{role}]: {msg['content']}")
        lines.append("")
    return "\n".join(lines)

async def restart():
    print("[SYSTEM] Restarting bot...")
    await discord_client.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def send_once(channel, content=None, file=None):
    if content and len(content) > 1990:
        content = content[:1990] + "..."
    if file:
        await channel.send(file=file)
    else:
        await channel.send(content)

async def ban_user(message, target_username, reason="No reason provided"):
    if message.author.name.lower() != OWNER_USERNAME:
        await send_once(message.channel, "🚫 You don't have permission to use ban commands.")
        return
    if isinstance(message.channel, discord.DMChannel):
        await send_once(message.channel, "❌ Ban command only works in a server, not in DMs.")
        return
    guild = message.guild
    target = None
    for member in guild.members:
        if member.name.lower() == target_username.lower():
            target = member
            break
    if not target:
        await send_once(message.channel, f"❌ Couldn't find user **{target_username}** in this server.")
        return
    if target.top_role >= guild.me.top_role:
        await send_once(message.channel, f"❌ I can't ban **{target.name}** — they have a higher or equal role to me.")
        return
    try:
        await target.ban(reason=f"Banned by AussieAviationBNE via Nova. Reason: {reason}")
        await send_once(message.channel, f"✅ **{target.name}** has been banned. Reason: {reason}")
        log("NOVA", f"[BAN] {target.name} banned by {message.author.name}. Reason: {reason}", "BOT")
    except discord.Forbidden:
        await send_once(message.channel, "❌ I don't have permission to ban members.")
    except Exception as e:
        await send_once(message.channel, f"❌ Failed to ban: {str(e)}")

async def unban_user(message, target_username):
    if message.author.name.lower() != OWNER_USERNAME:
        await send_once(message.channel, "🚫 You don't have permission to use unban commands.")
        return
    if isinstance(message.channel, discord.DMChannel):
        await send_once(message.channel, "❌ Unban command only works in a server, not in DMs.")
        return
    guild = message.guild
    try:
        banned_users = [entry async for entry in guild.bans()]
        target = None
        for ban_entry in banned_users:
            if ban_entry.user.name.lower() == target_username.lower():
                target = ban_entry.user
                break

        if not target:
            await send_once(message.channel, f"❌ Couldn't find **{target_username}** in the ban list.")
            return

        nova_channel = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        invite = None
        if nova_channel:
            invite = await nova_channel.create_invite(
                max_uses=1,
                max_age=86400,
                unique=True,
                reason=f"One time invite for unbanned user {target.name}"
            )

        await guild.unban(target)
        log("NOVA", f"[UNBAN] {target.name} unbanned by {message.author.name}", "BOT")

        if invite:
            try:
                dm = await target.create_dm()
                await dm.send(
                    f"👋 Hey **{target.name}**! You have been unbanned from **{guild.name}**.\n"
                    f"Here is your one time invite link — it only works once and expires in 24 hours:\n"
                    f"{invite.url}"
                )
                await send_once(message.channel,
                    f"✅ **{target.name}** has been unbanned and sent a one time invite via DM.\n"
                    f"Backup link: {invite.url}")
            except Exception:
                await send_once(message.channel,
                    f"✅ **{target.name}** has been unbanned.\n"
                    f"Their DMs are closed — send them this invite manually:\n{invite.url}")
        else:
            await send_once(message.channel, f"✅ **{target.name}** has been unbanned.")

    except discord.Forbidden:
        await send_once(message.channel, "❌ I don't have permission to unban members.")
    except Exception as e:
        await send_once(message.channel, f"❌ Failed to unban: {str(e)}")

def build_system_prompt(display_name):
    return f"""You are Nova, a jack of all trades AI assistant living in Discord.

IMPORTANT FACTS ABOUT YOU:
- Your name is Nova
- You were created by AussieAviationBNE
- The person you are talking to RIGHT NOW is called {display_name}
- {display_name} is NOT your creator, they are just a user
- Your creator AussieAviationBNE is a separate person
- If asked who made you, say AussieAviationBNE made you
- Never say you were made by Meta, Groq, OpenAI, Ollama, or anyone else
- Never confuse {display_name} with AussieAviationBNE
- Use {display_name}'s name naturally sometimes but don't overdo it
- Do NOT introduce yourself every single message, only on the first message
- AussieAviationBNE can ban and unban users by saying "ban username reason" or "unban username"

Based on what the user asks, start your reply with the correct tag. Everything goes in ONE single reply, never multiple.

Tags:
IMAGE: <detailed image prompt>
→ user wants any image, drawing, picture, artwork, meme, logo or visual

CODE: <language>
<code>
END_CODE
<short explanation>
→ user wants code, a script, help debugging, or anything programming

MATH: <step by step solution>
→ user gives a math problem or equation

MUSIC: <list of songs with artists>
→ user wants song recommendations or playlist ideas

ROAST: <funny roast, not mean>
→ user asks to roast someone or something

JOKE: <joke>
→ user wants a joke or something funny

RECIPE: <ingredients and steps>
→ user asks how to cook something

TRANSLATE: <translated text with explanation>
→ user wants something translated

ADVICE: <genuine helpful advice>
→ user needs life advice or help deciding something

STORY: <engaging story>
→ user wants a story or creative writing

For anything else just reply normally, no tag needed."""

@discord_client.event
async def on_ready():
    print(f"Bot is online as {discord_client.user}")
    print("=" * 50)

@discord_client.event
async def on_message(message):
    print(f"[DEBUG] Message from {message.author} in {type(message.channel).__name__}: {message.content}")

    if message.author.bot:
        return

    if message.author.system:
        return

    if message.id in seen_messages:
        return
    seen_messages.add(message.id)

    if len(seen_messages) > 1000:
        seen_messages.clear()

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_nova_channel = hasattr(message.channel, 'name') and message.channel.name.lower() == CHANNEL_NAME
    if not is_dm and not is_nova_channel:
        return

    user_text = message.content.strip()
    if not user_text and not message.attachments:
        return

    user_id = message.author.id
    username = message.author.name
    display_name = message.author.display_name

    if user_id in processing_users:
        return
    processing_users.add(user_id)

    try:
        if message.attachments:
            for attachment in message.attachments:
                if attachment.filename.endswith(".json"):
                    try:
                        file_data = await attachment.read()
                        data = json.loads(file_data.decode("utf-8"))
                        conversation_history[user_id] = data["history"]
                        await send_once(message.channel,
                            f"✅ Welcome back **{display_name}**! Loaded your previous conversation — let's pick up where we left off!")
                    except Exception:
                        await send_once(message.channel,
                            f"❌ Couldn't load that file. Make sure it's the `.json` file Nova sent you.")
            return

        if not user_text:
            return

        log(username, user_text, "USER")

        if user_id not in conversation_history:
            conversation_history[user_id] = []

        lower_text = user_text.lower()

        # wipe command
        if user_text == "novadothewipe":
            if username.lower() != OWNER_USERNAME:
                await send_once(message.channel, "🚫 You don't have permission to wipe.")
                return
            if isinstance(message.channel, discord.DMChannel):
                await send_once(message.channel, "❌ Wipe only works in a server.")
                return
            try:
                guild = message.guild
                total_deleted = 0
                for channel in guild.text_channels:
                    try:
                        deleted = await channel.purge(limit=None)
                        total_deleted += len(deleted)
                        log("NOVA", f"[WIPE] {len(deleted)} messages deleted in #{channel.name}", "BOT")
                    except discord.Forbidden:
                        log("NOVA", f"[WIPE] No permission in #{channel.name}", "BOT")
                    except Exception as e:
                        log("NOVA", f"[WIPE] Error in #{channel.name}: {str(e)}", "BOT")

                meme_url = "https://image.pollinations.ai/prompt/funny%20meme%20chaos%20explosion%20wipe%20everything%20gone%20humorous"

                def get_meme():
                    req = urllib.request.Request(meme_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        return io.BytesIO(r.read())

                loop = asyncio.get_event_loop()
                meme_data = await loop.run_in_executor(executor, get_meme)
                meme_file = discord.File(meme_data, filename="wipe.png")
                nova_channel = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
                if nova_channel:
                    confirm = await nova_channel.send(
                        f"🧹 Wiped **{total_deleted}** messages across **{len(guild.text_channels)}** channels. Say less 😈",
                        file=meme_file
                    )
                    await asyncio.sleep(5)
                    await confirm.delete()
                log("NOVA", f"[WIPE] Total {total_deleted} messages deleted by {username}", "BOT")
            except Exception as e:
                await send_once(message.channel, f"❌ Wipe failed: {str(e)}")
            return

        # ban command
        if lower_text.startswith("ban ") and username.lower() == OWNER_USERNAME:
            parts = user_text[4:].strip().split(" ", 1)
            target = parts[0].lstrip("@")
            reason = parts[1] if len(parts) > 1 else "No reason provided"
            await ban_user(message, target, reason)
            return

        # unban command
        if lower_text.startswith("unban ") and username.lower() == OWNER_USERNAME:
            target = user_text[6:].strip().lstrip("@")
            await unban_user(message, target)
            return

        # new conversation
        new_convo_phrases = [
            "new conversation", "new convo", "start over", "reset",
            "clear history", "fresh start", "start fresh", "new chat"
        ]
        if any(phrase in lower_text for phrase in new_convo_phrases):
            if conversation_history[user_id]:
                export_text = export_conversation(user_id, display_name, conversation_history[user_id])
                export_bytes = io.BytesIO(export_text.encode("utf-8"))
                export_file = discord.File(export_bytes, filename=f"nova_conversation_{username}.txt")
                json_data = json.dumps({
                    "user_id": user_id,
                    "username": username,
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "history": conversation_history[user_id]
                }, indent=2).encode("utf-8")
                json_bytes = io.BytesIO(json_data)
                json_file = discord.File(json_bytes, filename=f"nova_conversation_{username}.json")
                await message.channel.send(
                    f"💾 Here's your conversation log **{display_name}**! "
                    f"Upload the `.json` file anytime to continue where you left off.",
                    files=[export_file, json_file]
                )
                conversation_history[user_id] = []
                await send_once(message.channel, f"✨ Fresh start! What's on your mind **{display_name}**?")
            else:
                await send_once(message.channel, f"✨ No history yet **{display_name}**! Let's start one!")
            return

        conversation_history[user_id].append({
            "role": "user",
            "content": user_text
        })

        save_conversation(user_id, username, conversation_history[user_id])

        async with message.channel.typing():
            loop = asyncio.get_event_loop()
            reply = await loop.run_in_executor(executor, ask_ollama, [
                {"role": "system", "content": build_system_prompt(display_name)},
                *conversation_history[user_id]
            ])

        replied = False

        if reply.startswith("IMAGE:"):
            image_prompt = reply[6:].split("\n")[0].strip()
            image_url = generate_image_url(image_prompt)
            log("NOVA", f"[IMAGE] {image_prompt}", "BOT")
            async with message.channel.typing():
                loop = asyncio.get_event_loop()
                image_data = await loop.run_in_executor(executor, download_image, image_url)
            await send_once(message.channel, file=discord.File(image_data, filename="nova.png"))
            conversation_history[user_id].append({"role": "assistant", "content": f"Generated image of: {image_prompt}"})
            replied = True

        elif reply.startswith("CODE:"):
            lines = reply.split("\n")
            language = lines[0][5:].strip()
            code_lines = []
            explanation_lines = []
            in_code = True
            for line in lines[1:]:
                if line.strip() == "END_CODE":
                    in_code = False
                    continue
                if in_code:
                    code_lines.append(line)
                else:
                    explanation_lines.append(line)
            code = "\n".join(code_lines)
            explanation = "\n".join(explanation_lines).strip()
            final = f"```{language}\n{code}\n```"
            if explanation:
                final += f"\n{explanation}"
            log("NOVA", f"[CODE] {language}", "BOT")
            await send_once(message.channel, final)
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        elif reply.startswith("MATH:"):
            content = reply[5:].strip()
            log("NOVA", "[MATH]", "BOT")
            await send_once(message.channel, f"🧮 **Math Solution**\n{content}")
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        elif reply.startswith("MUSIC:"):
            content = reply[6:].strip()
            log("NOVA", "[MUSIC]", "BOT")
            await send_once(message.channel, f"🎵 **Nova's Playlist**\n{content}")
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        elif reply.startswith("ROAST:"):
            content = reply[6:].strip()
            log("NOVA", "[ROAST]", "BOT")
            await send_once(message.channel, f"🔥 **Nova roasts...**\n{content}")
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        elif reply.startswith("JOKE:"):
            content = reply[5:].strip()
            log("NOVA", "[JOKE]", "BOT")
            await send_once(message.channel, f"😂 {content}")
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        elif reply.startswith("RECIPE:"):
            content = reply[7:].strip()
            log("NOVA", "[RECIPE]", "BOT")
            await send_once(message.channel, f"🍳 **Recipe**\n{content}")
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        elif reply.startswith("TRANSLATE:"):
            content = reply[10:].strip()
            log("NOVA", "[TRANSLATE]", "BOT")
            await send_once(message.channel, f"🌍 **Translation**\n{content}")
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        elif reply.startswith("ADVICE:"):
            content = reply[7:].strip()
            log("NOVA", "[ADVICE]", "BOT")
            await send_once(message.channel, f"💡 **Nova's Advice**\n{content}")
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        elif reply.startswith("STORY:"):
            content = reply[6:].strip()
            log("NOVA", "[STORY]", "BOT")
            await send_once(message.channel, f"📖 **Nova's Story**\n{content}")
            conversation_history[user_id].append({"role": "assistant", "content": reply})
            replied = True

        if not replied:
            log("NOVA", reply, "BOT")
            await send_once(message.channel, reply)
            conversation_history[user_id].append({"role": "assistant", "content": reply})

        save_conversation(user_id, username, conversation_history[user_id])
        print("-" * 50)

    except Exception as e:
        error_code = generate_error_code()
        log("SYSTEM", f"Error: {str(e)} | Code: {error_code}", "ERROR")
        await send_once(message.channel,
            f"⚠️ **OVERLOADED** — Nova has crashed\n"
            f"```Error Code: {error_code}\nDetails: {str(e)}```"
            f"Restarting... please wait a moment"
        )
        await asyncio.sleep(2)
        await restart()

    finally:
        processing_users.discard(user_id)

discord_client.run(DISCORD_TOKEN)