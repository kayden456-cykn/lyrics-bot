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


@bot.tree.command(name="play", description="Separate and play vocals from an audio file")
@app_commands.describe(audio="Upload an audio file you have permission to use")
async def play(interaction: discord.Interaction, audio: discord.Attachment):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server", ephemeral=True)
        return

    voice_state = interaction.user.voice
    if not voice_state or not voice_state.channel:
        await interaction.response.send_message("Join a voice channel first", ephemeral=True)
        return
    if audio.size > MAX_UPLOAD_BYTES:
        await interaction.response.send_message("That file is too large (50 MB max)", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    voice_client = interaction.guild.voice_client
    try:
        if voice_client and voice_client.is_connected():
            if voice_client.channel != voice_state.channel:
                await voice_client.move_to(voice_state.channel)
        else:
            voice_client = await voice_state.channel.connect()

        suffix = Path(audio.filename).suffix or ".audio"
        with tempfile.TemporaryDirectory(prefix="vocalbot-") as temp:
            root = Path(temp)
            source = root / f"input{suffix}"
            stems = root / "stems"
            stems.mkdir()
            source.write_bytes(await audio.read())

            await interaction.followup.send("Separating vocals now. This can take a few minutes on Railway.")
            await asyncio.to_thread(separate_vocals, source, stems)
            vocal_file = stems / "htdemucs" / source.stem / "vocals.wav"

            if voice_client.is_playing():
                voice_client.stop()
            done = asyncio.get_running_loop().create_future()

            def after_playback(error):
                if not done.done():
                    if error:
                        done.set_exception(error)
                    else:
                        done.set_result(None)

            audio_source = discord.FFmpegPCMAudio(str(vocal_file))
            voice_client.play(audio_source, after=after_playback)
            await interaction.followup.send("Playing the isolated vocals in your voice channel")
            # Keep the temporary audio alive until playback finishes.
            try:
                await done
            finally:
                audio_source.cleanup()
    except Exception as exc:
        await interaction.followup.send(f"Could not process or play that audio: {type(exc).__name__}")


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
