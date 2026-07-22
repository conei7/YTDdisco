# YTDdisco

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
