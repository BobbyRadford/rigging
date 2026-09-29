---
name: file-host-read
description: Use when the user shares a file.bobbyradford.com URL.
---

# File host read

Download the file with the shell and read the local copy. Web fetch tools pass the page through another model and hand back a summary, and a browser hides the source.

```bash
curl --fail --silent --show-error --location --max-time 30 \
  --output /tmp/<basename> '<url>'
```

Use the last path segment of the URL as `<basename>`. Read the downloaded file and continue the user's request from its contents. For an HTML document, read the markup itself, since that is what its author wrote.

If `curl` fails, report its status or network error and stop there.
