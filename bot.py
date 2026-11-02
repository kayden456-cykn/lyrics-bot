import os
import sqlite3
import uuid
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DATABASE_PATH", "anonymous_user.sqlite3")
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


class InboxView(discord.ui.View):
    def __init__(self, owner_id, conversations):
        super().__init__(timeout=300)
        self.add_item(InboxSelect(owner_id, conversations))


@bot.event
async def on_ready():
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
    if message.attachments:
        content = "\n".join([content] + [f"Attachment: {item.url}" for item in message.attachments]).strip()
    if not content:
        await message.channel.send("Please send text or an attachment as your reply.")
        return

    add_message(conversation["id"], "recipient", content)
    await message.channel.send(
        "Your reply was saved privately. The sender can view it in their anonymous dashboard.")


@bot.tree.command(name="dm", description="Send an anonymous DM and open its dashboard")
@app_commands.describe(user_id="The recipient's Discord user ID", message="The anonymous message to send")
async def dm(interaction: discord.Interaction, user_id: str, message: str):
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

    timestamp = now()
    with db() as con:
        con.execute(
            """INSERT INTO conversations
               (id, owner_id, recipient_id, recipient_name, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?)""",
            (conversation_id, str(interaction.user.id), str(recipient_id), str(recipient), timestamp, timestamp))
    add_message(conversation_id, "owner", message.strip())
    conversation = get_conversation(conversation_id, interaction.user.id)
    await interaction.followup.send(
        content="**Anonymous message sent.** This dashboard is only visible to you.",
        embed=dashboard_embed(conversation),
        view=ConversationDashboard(interaction.user.id, conversation_id),
        ephemeral=True)


@bot.tree.command(name="inbox", description="View your anonymous conversations")
async def inbox(interaction: discord.Interaction):
    with db() as con:
        conversations = con.execute(
            "SELECT * FROM conversations WHERE owner_id=? ORDER BY updated_at DESC LIMIT 25",
            (str(interaction.user.id),)).fetchall()

    if not conversations:
        await interaction.response.send_message(
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
    await interaction.response.send_message(
        embed=embed, view=InboxView(interaction.user.id, conversations), ephemeral=True)


@bot.tree.command(name="dmclose", description="Close one of your anonymous conversations")
@app_commands.describe(conversation_id="The ID shown in your conversation dashboard")
async def dmclose(interaction: discord.Interaction, conversation_id: str):
    conversation_id = conversation_id.strip().upper()
    conversation = get_conversation(conversation_id, interaction.user.id)
    if not conversation:
        await interaction.response.send_message("Conversation not found in your inbox.", ephemeral=True)
        return

    with db() as con:
        con.execute(
            "UPDATE conversations SET status='closed', updated_at=? WHERE id=? AND owner_id=?",
            (now(), conversation_id, str(interaction.user.id)))
    updated = get_conversation(conversation_id, interaction.user.id)
    await interaction.response.send_message(
        "Conversation closed.", embed=dashboard_embed(updated), ephemeral=True)


if not TOKEN:
    raise RuntimeError("Set the DISCORD_TOKEN environment variable")

init_db()
bot.run(TOKEN)
