# anonymous user

A Discord bot for anonymous two-way DMs with private conversation dashboards.

## Commands

- \`/dm user_id message\` — send an anonymous message and open a private dashboard.
- \`/inbox\` — browse conversations and open a private dashboard.
- \`/dmclose conversation_id\` — close a conversation.

## Replies

The recipient replies directly to the bot's DM. Replies are stored and visible only to the person who started the conversation inside their private ephemeral dashboard. Use **Refresh** or \`/inbox\` to check for new replies. Replies are not forwarded to the sender's DMs.

The recipient sees the bot account as the sender, not the initiating user's Discord account. Delivery can fail if the recipient has DMs disabled or Discord blocks the DM.

## Setup

1. Create a Discord application and bot named **anonymous user** in the Discord Developer Portal.
2. Enable the Message Content Intent.
3. Invite it with the \`bot\` and \`applications.commands\` scopes and permission to send messages and embeds in DMs.
4. Set \`DISCORD_TOKEN\` in Railway.
5. Deploy this repository using the included Dockerfile.

## Storage

Conversation data is stored in SQLite. Set \`DATABASE_PATH\` to a path on a mounted Railway volume (for example \`/data/anonymous_user.sqlite3\`) to keep conversations across redeploys. Without a persistent volume, local SQLite data can be lost when Railway replaces the container.

## Safety

Use this bot only for consensual, non-harassing communication. Recipients can block the bot through Discord's privacy controls. The bot does not reveal the sender's Discord account to the recipient, but Discord and server administrators can still identify the bot account.
