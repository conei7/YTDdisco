# YTDdisco

## Managed startup

AutoMonitor can pass `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, and
`DISCORD_AUTHORIZED_USERS` (comma-separated user IDs) through the environment.
The existing command-line and `YTDdisco.config` startup methods remain supported.
`requirements.txt` includes the bot's imports as well as the download dependencies.

## Required download dependencies

Install the current yt-dlp default dependencies before starting the bot:

```powershell
python -m pip install -r requirements.txt
```

Niconico uses AES-encrypted HLS. `pycryptodomex` is required so yt-dlp can
decrypt it natively instead of delegating the stream to ffmpeg. When this bot
is managed by AutoMonitor, it can also be installed with:

```text
/upgrade library:pycryptodomex
```
