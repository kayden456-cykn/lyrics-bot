# Vocal-only Discord Bot

A Discord bot that separates the vocal stem from an audio file using Demucs and plays that stem in a server voice channel. Designed for Railway deployment.

## Commands

- `/play audio:<file>` — upload an audio file you have permission to use; the bot separates and plays its vocals.
- `/stop` — stop playback.
- `/leave` — disconnect from voice.

## Railway deployment

1. Push this repository to GitHub and create a Railway service from the repository.
2. Railway detects the `Dockerfile` and builds the image.
3. Add the `DISCORD_TOKEN` environment variable in Railway.
4. Deploy the service. No web server or port is required for this worker.
5. Invite the Discord application with bot and application-command scopes, plus Connect, Speak, and Use Voice Activity permissions.

## Notes

- The bot currently accepts audio uploads, not YouTube URLs. Only upload audio you own or have permission to process.
- Demucs is computationally intensive. Vocal separation may take several minutes and can require substantial RAM/CPU; Railway resource limits may affect performance.
- Discord voice requires outbound UDP connectivity. Confirm voice networking is available in your Railway deployment region.
- The bot uses temporary files and deletes them after playback completes.
