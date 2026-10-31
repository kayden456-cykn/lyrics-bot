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

    subprocess.run(
        [
            "python", "-m", "demucs",
            "--name", "htdemucs",
            "--two-stems", "vocals",
            "--out", str(output_dir),
            str(source),
        ],
        check=True,
        timeout=900,
    )
    vocal_path = output_dir / "htdemucs" / source.stem / "vocals.wav"
    if not vocal_path.is_file():
        raise FileNotFoundError("Demucs did not create the vocals stem")
    return vocal_path


@bot.event
async def on_ready():
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
