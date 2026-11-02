import os

import asyncio
import html
import secrets
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1566050081801633833")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = (
    os.getenv("DISCORD_REDIRECT_URL")
    or os.getenv("DISCORD_REDIRECT_URI")
    or "https://toga-lyric-production.up.railway.app/discord/callback"
)
OAUTH_STATES = {}
HTTP_RUNNER = None
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DATABASE_PATH", "anonymous_user.sqlite3")
SEND_COOLDOWNS = {}
LIVE_DASHBOARDS = {}
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, recipient_id TEXT NOT NULL,
            recipient_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
            sender TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_owner ON conversations(owner_id, updated_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_recipient ON conversations(recipient_id, status, updated_at)")


def add_message(conversation_id, sender, content):
    timestamp = now()
    with db() as con:
        con.execute(
            "INSERT INTO messages(conversation_id, sender, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, sender, content[:4000], timestamp))
        con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (timestamp, conversation_id))


def get_conversation(conversation_id, owner_id):
    with db() as con:
        return con.execute(
            "SELECT * FROM conversations WHERE id=? AND owner_id=?",
            (conversation_id, str(owner_id))).fetchone()


def get_messages(conversation_id):
    with db() as con:
        return con.execute(
            "SELECT sender, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 12",
            (conversation_id,)).fetchall()[::-1]


def dashboard_embed(conversation):
    embed = discord.Embed(
        title="Anonymous User | Conversation",
        description=(
            f"**Recipient:** <@{conversation['recipient_id']}> ({conversation['recipient_id']})\n"
            f"**Status:** {conversation['status'].title()}\n"
            f"**Conversation ID:** {conversation['id']}"
        ),
        color=discord.Color.dark_red(),
        timestamp=datetime.now(timezone.utc))
    messages = get_messages(conversation["id"])
    if messages:
        lines = []
        for item in messages:
            label = "You" if item["sender"] == "owner" else "Recipient"
            lines.append(f"**{label}:** {item['content'][:650]}")
        embed.add_field(name="Messages", value="\n\n".join(lines)[:3900], inline=False)
    else:
        embed.add_field(name="Messages", value="No replies yet. Use Refresh to check again.", inline=False)
    embed.set_footer(text="Only you can see this dashboard")
    return embed


class ConversationDashboard(discord.ui.View):
    def __init__(self, owner_id, conversation_id):
        super().__init__(timeout=900)
        self.owner_id = str(owner_id)
        self.conversation_id = conversation_id

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("This dashboard belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh(self, interaction, button):
        conversation = get_conversation(self.conversation_id, self.owner_id)
        if not conversation:
            await interaction.response.send_message("Conversation not found.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=dashboard_embed(conversation), view=self)

    @discord.ui.button(label="Close conversation", style=discord.ButtonStyle.danger)
    async def close(self, interaction, button):
        with db() as con:
            con.execute(
                "UPDATE conversations SET status='closed', updated_at=? WHERE id=? AND owner_id=?",
                (now(), self.conversation_id, self.owner_id))
        conversation = get_conversation(self.conversation_id, self.owner_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=dashboard_embed(conversation), view=self)


class InboxSelect(discord.ui.Select):
    def __init__(self, owner_id, conversations):
        self.owner_id = str(owner_id)
        options = [
            discord.SelectOption(
                label=("To " + row["recipient_name"])[:100],
                description=(row["status"].title() + " • " + row["updated_at"][:16]),
                value=row["id"])
            for row in conversations[:25]]
        super().__init__(placeholder="Choose a conversation", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        conversation = get_conversation(self.values[0], self.owner_id)
        if not conversation:
            await interaction.response.send_message("Conversation not found.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=dashboard_embed(conversation),
            view=ConversationDashboard(self.owner_id, conversation["id"]),
            ephemeral=True)
        try:
            LIVE_DASHBOARDS[conversation["id"]] = await interaction.original_response()
        except discord.HTTPException:
            pass


class InboxView(discord.ui.View):
    def __init__(self, owner_id, conversations):
        super().__init__(timeout=300)
        self.add_item(InboxSelect(owner_id, conversations))


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)
    print(f"Slash command error in {getattr(interaction.command, 'name', 'unknown')}: {type(original).__name__}: {original}", flush=True)
    try:
        message = "The command hit an error. Check the bot logs for details."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


async def oauth_home(request):
    return web.Response(
        text='<h1>Toga Productions</h1><p>Connect your Discord account to continue.</p><a href="/login">Continue with Discord</a>',
        content_type="text/html",
    )


async def oauth_login(request):
    state = secrets.token_urlsafe(32)
    OAUTH_STATES[state] = time.time() + 600
    for key, expiry in list(OAUTH_STATES.items()):
        if expiry < time.time():
            OAUTH_STATES.pop(key, None)
    query = urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    })
    raise web.HTTPFound("https://discord.com/oauth2/authorize?" + query)


async def oauth_callback(request):
    if not DISCORD_CLIENT_SECRET:
        return web.Response(text="Missing DISCORD_CLIENT_SECRET on Railway.", status=500)
    if request.query.get("error"):
        return web.Response(text="Discord authorization was cancelled.", status=400)
    code = request.query.get("code")
    state = request.query.get("state")
    expiry = OAUTH_STATES.pop(state, None) if state else None
    if not code or not expiry or expiry < time.time():
        return web.Response(text="Login expired or invalid. Start again at /login.", status=400)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": DISCORD_REDIRECT_URI,
                },
            ) as response:
                token_data = await response.json()
                if response.status != 200:
                    print(f"OAuth token exchange failed: {response.status} {token_data}", flush=True)
                    return web.Response(text="Discord token exchange failed. Check OAuth settings.", status=502)
            async with session.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": "Bearer " + token_data["access_token"]},
            ) as response:
                user_data = await response.json()
                if response.status != 200:
                    return web.Response(text="Could not verify the Discord account.", status=502)
        username = html.escape(user_data.get("global_name") or user_data.get("username") or "Discord user")
        user_id = html.escape(str(user_data.get("id", "")))
        return web.Response(
            text=f"<h1>Discord login successful</h1><p>Signed in as {username}</p><p>Account ID: {user_id}</p>",
            content_type="text/html",
        )
    except Exception as exc:
        print(f"OAuth callback failed: {type(exc).__name__}: {exc}", flush=True)
        return web.Response(text="Discord login failed. Check Railway logs.", status=502)


async def start_oauth_server():
    global HTTP_RUNNER
    if HTTP_RUNNER is not None:
        return
    app = web.Application()
    app.router.add_get("/", oauth_home)
    app.router.add_get("/login", oauth_login)
    app.router.add_get("/discord/callback", oauth_callback)
    app.router.add_get("/health", lambda request: web.Response(text="ok"))
    HTTP_RUNNER = web.AppRunner(app)
    await HTTP_RUNNER.setup()
    site = web.TCPSite(HTTP_RUNNER, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()
    print(f"OAuth server listening; callback={DISCORD_REDIRECT_URI}", flush=True)


@bot.event
async def on_ready():
    await start_oauth_server()
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}; synced {len(synced)} slash commands")
    except Exception as exc:
        print(f"Slash command sync failed: {type(exc).__name__}: {exc}")


@bot.event
async def on_message(message):
    if message.author.bot or message.guild is not None:
        return

    with db() as con:
        conversation = con.execute(
            """SELECT * FROM conversations
               WHERE recipient_id=? AND status='open'
               ORDER BY updated_at DESC LIMIT 1""",
            (str(message.author.id),)).fetchone()

    if not conversation:
        await message.channel.send("There isn't an open anonymous conversation for you right now.")
        return

    content = message.content.strip()
    if content.lower() in {"stop", "unsubscribe", "cancel"}:
        with db() as con:
            con.execute("UPDATE conversations SET status='closed', updated_at=? WHERE id=?", (now(), conversation["id"]))
        await message.channel.send("This anonymous conversation is now closed. You will not receive more messages from it.")
        return
    if message.attachments:
        content = "\n".join([content] + [f"Attachment: {item.url}" for item in message.attachments]).strip()
    if not content:
        await message.channel.send("Please send text or an attachment as your reply.")
        return

    add_message(conversation["id"], "recipient", content)
    dashboard_message = LIVE_DASHBOARDS.get(conversation["id"])
    if dashboard_message:
        try:
            updated_conversation = get_conversation(conversation["id"], conversation["owner_id"])
            await dashboard_message.edit(embed=dashboard_embed(updated_conversation))
        except (discord.NotFound, discord.HTTPException):
            LIVE_DASHBOARDS.pop(conversation["id"], None)
    await message.channel.send(
        "Your reply was saved privately. The sender dashboard updates automatically while active; otherwise use /inbox.")


@bot.tree.command(name="dm", description="Send an anonymous DM and open its dashboard")
@app_commands.describe(user_id="The recipient's Discord user ID", message="The anonymous message to send")
async def dm(interaction: discord.Interaction, user_id: str, message: str):
    cooldown_key = str(interaction.user.id)
    remaining = 30 - (time.time() - SEND_COOLDOWNS.get(cooldown_key, 0))
    if remaining > 0:
        await interaction.response.send_message(f"Please wait {int(remaining) + 1} seconds before sending another anonymous DM.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        recipient_id = int(user_id.strip())
        if recipient_id <= 0 or recipient_id == bot.user.id:
            raise ValueError
    except (ValueError, AttributeError):
        await interaction.followup.send("Enter a valid Discord user ID.", ephemeral=True)
        return

    if recipient_id == interaction.user.id:
        await interaction.followup.send("You cannot message yourself.", ephemeral=True)
        return
    if not message.strip():
        await interaction.followup.send("Your message cannot be empty.", ephemeral=True)
        return

    try:
        recipient = await bot.fetch_user(recipient_id)
        conversation_id = uuid.uuid4().hex[:10].upper()
        outgoing = discord.Embed(
            title="Anonymous message",
            description=message.strip()[:1800],
            color=discord.Color.dark_red())
        outgoing.set_footer(text="Reply to this DM to respond anonymously")
        await recipient.send(embed=outgoing)
    except discord.Forbidden:
        await interaction.followup.send(
            "I couldn't DM that user. They may have DMs disabled or may not share a server with me.",
            ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.followup.send("Discord couldn't deliver that message. Try again later.", ephemeral=True)
        return

    SEND_COOLDOWNS[cooldown_key] = time.time()
    timestamp = now()
    with db() as con:
        con.execute(
            """INSERT INTO conversations
               (id, owner_id, recipient_id, recipient_name, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?)""",
            (conversation_id, str(interaction.user.id), str(recipient_id), str(recipient), timestamp, timestamp))
    add_message(conversation_id, "owner", message.strip())
    conversation = get_conversation(conversation_id, interaction.user.id)
    dashboard_message = await interaction.followup.send(
        content="**Anonymous message sent.** This dashboard is only visible to you.",
        embed=dashboard_embed(conversation),
        view=ConversationDashboard(interaction.user.id, conversation_id),
        ephemeral=True,
        wait=True)
    LIVE_DASHBOARDS[conversation_id] = dashboard_message


@bot.tree.command(name="inbox", description="View your anonymous conversations")
async def inbox(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    with db() as con:
        conversations = con.execute(
            "SELECT * FROM conversations WHERE owner_id=? ORDER BY updated_at DESC LIMIT 25",
            (str(interaction.user.id),)).fetchall()

    if not conversations:
        await interaction.followup.send(
            "Your anonymous inbox is empty. Use /dm to start a conversation.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Anonymous User | Inbox",
        description="Select a conversation to open its private dashboard.",
        color=discord.Color.dark_red())
    for row in conversations[:10]:
        embed.add_field(
            name="To " + row["recipient_name"] + " • " + row["status"].title(),
            value="ID: " + row["id"] + " • Updated: " + row["updated_at"][:16],
            inline=False)
    embed.set_footer(text="Only you can see this inbox")
    await interaction.followup.send(
        embed=embed, view=InboxView(interaction.user.id, conversations), ephemeral=True)


@bot.tree.command(name="dmclose", description="Close one of your anonymous conversations")
@app_commands.describe(conversation_id="The ID shown in your conversation dashboard")
async def dmclose(interaction: discord.Interaction, conversation_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    conversation_id = conversation_id.strip().upper()
    conversation = get_conversation(conversation_id, interaction.user.id)
    if not conversation:
        await interaction.followup.send("Conversation not found in your inbox.", ephemeral=True)
        return

    with db() as con:
        con.execute(
            "UPDATE conversations SET status='closed', updated_at=? WHERE id=? AND owner_id=?",
            (now(), conversation_id, str(interaction.user.id)))
    updated = get_conversation(conversation_id, interaction.user.id)
    await interaction.followup.send(
        "Conversation closed.", embed=dashboard_embed(updated), ephemeral=True)


if not TOKEN:
    raise RuntimeError("Set the DISCORD_TOKEN environment variable")

init_db()
bot.run(TOKEN)
