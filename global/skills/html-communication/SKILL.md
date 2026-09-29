---
name: html-communication
description: Use when the user asks to communicate through an HTML document, or says "HTML" with no other context.
---

# HTML communication

Use this skill when the user wants a plan, spec, write-up, findings, summary, report, comparison, or set of UI mocks presented as a readable HTML page. It is for documents about the work. HTML that ships as part of a product is ordinary code, so build that without this skill.

## Document

Create one self-contained HTML file, capped at 512 KB. Write it outside the repository at `/tmp/html/<short-name>.html`, and keep editing that same file across revisions.

- Write it like a spec, not a landing page. Make it dense and scannable, with no hero section, decorative chrome, or marketing voice.
- Default to a true black (`#000`) background and white primary text. Use dark gray only for secondary surfaces and accents.
- Make it readable on a phone, with a responsive viewport and no fixed-width layout.
- Use semantic HTML, inline CSS, inline SVG, and HTTPS or data-URL images. Keep scripts, stylesheets, and fonts inside the file.
- Add an inline script only when interactivity actually helps, and keep the page readable with JavaScript turned off.
- The uploaded page is public, so leave out secrets, tokens, and absolute paths on the machine. Refer to code by its path in the repository.

## UI mocks

When the user asks for variants:

- Render real styled variants rather than describing them.
- Label them `A`, `B`, `C`, and so on, and give each one a single line on the trade-off it makes.
- Lay them out for direct comparison.
- End with your recommendation, which can combine variants, such as "C + A".

## Publish

The user has given standing permission to upload every document this skill creates or updates. Upload it every time, including in auto mode, without asking first and without stopping at the local file.

1. Write the HTML file.
2. Upload it with the file-upload skill.
3. Reply with the returned URL and the local path. For UI mocks, also list each variant by letter with its trade-off line, so the user can pick from the chat.

The file host never overwrites a file, so each upload gets a new URL. After a revision, upload again and send the new URL.

Skip opening the page in a browser to check it unless the user asks.
