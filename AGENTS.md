# Agent instructions

## Install pstack first

Work in this repo runs through the pstack plugin. Before anything else, check that its skills are available to you: `pstack:poteto-mode` in Claude Code, `poteto-mode` in Cursor.

If they are missing, stop. Do not read, edit or run anything. Tell the user to install pstack, then start a new session:

- Claude Code: trust this folder and accept the plugin prompt (`.claude/settings.json` asks for it), or run `/plugin marketplace add cursor/plugins` and then `/plugin install pstack@cursor-plugins`.
- Cursor: `/add-plugin pstack`.

With pstack installed, run every task through `/pstack:poteto-mode` in Claude Code or `/poteto-mode` in Cursor.

## Working in this repo

Read `.claude/skills/clip-studio/SKILL.md` before you change code.
