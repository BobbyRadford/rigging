---
name: file-upload
description: Upload a local file (screenshot, screen recording, log, archive, any file) and get a public URL. Use when Bobby says "upload it", "host this", "send me the file", or when a PR, issue, or chat reply needs an image or video link.
---

# File upload

Upload files to `https://file.bobbyradford.com` and return the permanent public URL from the response body. Authenticate with `FILE_HOST_TOKEN` from the environment. If it is unset, tell Bobby instead of guessing; do not try other hosts.

## Upload

```bash
curl -sS --fail-with-body -X PUT -T <path-to-file> \
  -H "X-Upload-Token: $FILE_HOST_TOKEN" \
  "https://file.bobbyradford.com/<filename>"
```

- Use only the file's basename for `<filename>`, such as `login-flow.mp4`. The server slugifies it and adds a random suffix, so names do not need to be unique.
- The response body is the permanent public URL. Use it exactly as returned.
- On HTTP 401 report that the token is wrong or unset. Do not retry.
- Never claim a file is hosted before the upload command succeeds.

## Use the URL in GitHub

- Embed images (`png`, `jpg`, `gif`, `webp`) as `![description](URL)`.
- Link videos (`mp4`, `mov`, `webm`) as `[📹 screen recording](URL)`. GitHub does not inline-play externally hosted video.
- When an inline preview helps and the clip is under about 30 seconds, also upload a GIF preview and embed it above the video link:

```bash
ffmpeg -i recording.mp4 -vf "fps=10,scale=800:-1" -loop 0 preview.gif
```

## Use the URL elsewhere

Slack and most chat clients unfurl image and mp4 URLs on their own; paste the bare URL. For logs and archives, link them with a short label describing what is inside.
