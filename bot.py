import asyncio
import os
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Railway HTTP server and Discord OAuth2 login.
import html
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

HTTP_SERVER = None
OAUTH_STATES = {}
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1566050081801633833")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = (
    os.getenv("DISCORD_REDIRECT_URI")
    or os.getenv("DISCORD_REDIRECT_URL")
    or "https://toga-lyric-production.up.railway.app/discord/callback"
).strip()


async def send_http_response(writer, status, body, content_type="text/html; charset=utf-8", headers=None):
    body = body.encode("utf-8") if isinstance(body, str) else body
    response_headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Connection": "close",
        **(headers or {}),
    }
    head = f"HTTP/1.1 {status}\r\n" + "".join(
        f"{key}: {value}\r\n" for key, value in response_headers.items()
    ) + "\r\n"
    writer.write(head.encode("latin-1") + body)
    await writer.drain()


async def handle_oauth_request(path, writer):
    if path == "/":
        await send_http_response(
            writer,
            "200 OK",
            '<!doctype html><html><head><title>Toga Lyric</title></head>'
            '<body><h1>Toga Lyric</h1><p>Discord voice bot is online.</p>'
            '<a href="/login">Login with Discord</a></body></html>',
        )
        return

    if path == "/health" or path == "/healthz":
        await send_http_response(writer, "200 OK", "Toga Lyric is online", "text/plain; charset=utf-8")
        return

    if path == "/login":
        if not DISCORD_CLIENT_ID:
            await send_http_response(writer, "500 Internal Server Error", "Missing DISCORD_CLIENT_ID")
            return
        state = secrets.token_urlsafe(32)
        OAUTH_STATES[state] = time.time() + 600
        # Prune expired states so the in-memory state table stays small.
        now = time.time()
        for old_state, expires in list(OAUTH_STATES.items()):
            if expires < now:
                OAUTH_STATES.pop(old_state, None)
        query = urlencode({
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "response_type": "code",
            "scope": "identify",
            "state": state,
        })
        await send_http_response(
            writer,
            "302 Found",
            "",
            headers={"Location": f"https://discord.com/oauth2/authorize?{query}"},
        )
        return

    if path == "/discord/callback":
        if not DISCORD_CLIENT_SECRET:
            await send_http_response(
                writer,
                "500 Internal Server Error",
                "Toga Lyric is missing the DISCORD_CLIENT_SECRET Railway variable.",
            )
            return
        query = parse_qs(urlsplit(path).query)
        # The query string is parsed by the caller and passed separately below.
        await send_http_response(writer, "400 Bad Request", "Missing OAuth callback parameters.")
        return

    await send_http_response(writer, "404 Not Found", "Not found", "text/plain; charset=utf-8")


async def handle_http_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        request = await asyncio.wait_for(reader.readline(), timeout=5)
        parts = request.decode("latin-1", errors="replace").strip().split()
        target = parts[1] if len(parts) >= 2 else "/"
        parsed = urlsplit(target)
        # Consume request headers before responding.
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if line in (b"\r\n", b"\n", b""):
                break

        if parsed.path == "/discord/callback":
            params = parse_qs(parsed.query)
            error = params.get("error", [None])[0]
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            expires = OAUTH_STATES.pop(state, None) if state else None

            if error:
                await send_http_response(writer, "400 Bad Request", f"Discord authorization failed: {html.escape(error)}")
            elif code and params.get("guild_id", [None])[0] and not state:
                # Discord bot-install authorization returns guild_id/permissions and
                # does not use the dashboard's CSRF state. The bot is installed by
                # Discord before this redirect; do not exchange this code as a user login.
                guild_id = html.escape(params.get("guild_id", [""])[0])
                await send_http_response(
                    writer,
                    "200 OK",
                    "<!doctype html><html><head><title>Toga Lyric</title></head>"
                    "<body><h1>Bot authorization completed</h1>"
                    f"<p>Toga Lyric was authorized for server {guild_id}.</p>"
                    "<p>You can return to Discord now.</p></body></html>",
                )
            elif not code or not expires or expires < time.time():
                await send_http_response(writer, "400 Bad Request", "Invalid or expired login state. Please start again at /login.")
            elif not DISCORD_CLIENT_SECRET:
                await send_http_response(writer, "500 Internal Server Error", "Missing DISCORD_CLIENT_SECRET Railway variable.")
            else:
                import aiohttp
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
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                        ) as token_response:
                            token_data = await token_response.json()
                        if token_response.status != 200:
                            raise ValueError(token_data.get("error_description", "Discord token exchange failed"))
                        async with session.get(
                            "https://discord.com/api/users/@me",
                            headers={"Authorization": f"Bearer {token_data['access_token']}"},
                        ) as user_response:
                            user_data = await user_response.json()
                        if user_response.status != 200:
                            raise ValueError("Could not retrieve the Discord account")
                    username = html.escape(user_data.get("global_name") or user_data.get("username") or "Discord user")
                    user_id = html.escape(str(user_data.get("id", "")))
                    await send_http_response(
                        writer,
                        "200 OK",
                        f"<!doctype html><html><head><title>Discord login</title></head>"
                        f"<body><h1>Discord login successful</h1><p>Signed in as {username}</p>"
                        f"<p>Discord user ID: {user_id}</p></body></html>",
                    )
                except Exception as exc:
                    print(f"Discord OAuth callback failed: {type(exc).__name__}: {exc}")
                    await send_http_response(writer, "502 Bad Gateway", "Discord login failed. Check Railway logs and OAuth settings.")
        else:
            await handle_oauth_request(parsed.path, writer)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def start_health_server():
    global HTTP_SERVER
    if HTTP_SERVER is not None:
        return
    port = int(os.getenv("PORT", "8080"))
    HTTP_SERVER = await asyncio.start_server(
        handle_http_request,
        host="0.0.0.0",
        port=port,
    )
    print(f"HTTP server listening on 0.0.0.0:{port}; OAuth callback: {DISCORD_REDIRECT_URI}")

async def connect_to_member_channel(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        raise ValueError("Use this command in a server")
    state = interaction.user.voice
    if not state or not state.channel:
        raise ValueError("Join a voice channel first")
    vc = interaction.guild.voice_client
    if vc and vc.is_connected():
        if vc.channel != state.channel:
            await vc.move_to(state.channel)
    else:
        vc = await state.channel.connect()
    return vc


def separate_vocals(source: Path, output_dir: Path) -> Path:
    """Run Demucs locally and return the isolated vocal stem."""
    import subprocess

    result = subprocess.run(
        [
            "python", "-m", "demucs",
            "--name", "htdemucs",
            "--two-stems", "vocals",
            "--shifts", "0",
            "--overlap", "0.1",
            "--segment", "5",
            "--device", "cpu",
            "--out", str(output_dir),
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "No diagnostic output").strip()
        print(f"Demucs failed with exit code {result.returncode}: {details[-5000:]}")
        raise RuntimeError(
            f"Vocal separation failed (exit {result.returncode}). "
            "Check Railway logs for the Demucs error."
        )
    vocal_path = output_dir / "htdemucs" / source.stem / "vocals.wav"
    if not vocal_path.is_file():
        raise FileNotFoundError("Demucs did not create the vocals stem")
    return vocal_path


@bot.event
async def on_ready():
    await start_health_server()
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}; synced {len(synced)} slash commands")
    except Exception as exc:
        print(f"Slash command sync failed: {exc}")


@bot.tree.command(name="join", description="Join your current voice channel")
async def join(interaction: discord.Interaction):
    try:
        await connect_to_member_channel(interaction)
        await interaction.response.send_message("Joined your voice channel")
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(
            f"Could not join voice: {type(exc).__name__}", ephemeral=True
        )


@bot.tree.command(name="play", description="Separate and play vocals from an audio file")
@app_commands.describe(audio="Upload an audio file you have permission to use")
async def play(interaction: discord.Interaction, audio: discord.Attachment):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server", ephemeral=True)
        return
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("Join a voice channel first", ephemeral=True)
        return
    if audio.size > MAX_UPLOAD_BYTES:
        await interaction.response.send_message("That file is too large (50 MB max)", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    try:
        voice_client = await connect_to_member_channel(interaction)
        suffix = Path(audio.filename).suffix or ".audio"
        with tempfile.TemporaryDirectory(prefix="vocalbot-") as temp:
            root = Path(temp)
            source = root / f"input{suffix}"
            stems = root / "stems"
            stems.mkdir()
            source.write_bytes(await audio.read())

            await interaction.followup.send(
                "Separating vocals now. This can take a few minutes on Railway."
            )
            await asyncio.to_thread(separate_vocals, source, stems)
            vocal_file = stems / "htdemucs" / source.stem / "vocals.wav"

            if voice_client.is_playing():
                voice_client.stop()
            loop = asyncio.get_running_loop()
            done = loop.create_future()

            def after_playback(error):
                def finish():
                    if done.done():
                        return
                    if error:
                        done.set_exception(error)
                    else:
                        done.set_result(None)
                loop.call_soon_threadsafe(finish)

            audio_source = discord.FFmpegPCMAudio(str(vocal_file))
            voice_client.play(audio_source, after=after_playback)
            await interaction.followup.send("Playing the isolated vocals in your voice channel")
            try:
                await done
            finally:
                audio_source.cleanup()
    except Exception as exc:
        await interaction.followup.send(
            f"Could not process or play that audio: {type(exc).__name__}"
        )


@bot.tree.command(name="stop", description="Stop vocal playback")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("Stopped playback")
    else:
        await interaction.response.send_message("Nothing is playing", ephemeral=True)


@bot.tree.command(name="leave", description="Disconnect the bot from voice")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_connected():
        await vc.disconnect()
        await interaction.response.send_message("Left the voice channel")
    else:
        await interaction.response.send_message("I am not in a voice channel", ephemeral=True)


if not TOKEN:
    raise RuntimeError("Set the DISCORD_TOKEN environment variable")

bot.run(TOKEN)
