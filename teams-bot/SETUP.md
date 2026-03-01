# Clambake Teams Bot — Setup Guide

## Architecture

```
Teams Client <-> Azure Bot Service (free) <-> Cloudflare Tunnel <-> teams-bot container (port 3978) <-> Postgres
```

## Prerequisites

- Azure account (free tier works)
- Cloudflare account with a domain
- Docker + docker compose

## Step 1: Entra ID App Registration

1. Go to [Azure Portal > Entra ID > App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
   - Name: `clambake-teams-bot`
   - Supported account types: **Single tenant**
   - Redirect URI: leave blank
3. After creation, note the **Application (client) ID** → this is `TEAMS_BOT_APP_ID`
4. Go to **Certificates & secrets** > **New client secret**
   - Description: `clambake-bot-secret`
   - Expiry: 24 months
   - Copy the **Value** → this is `TEAMS_BOT_APP_PASSWORD`

## Step 2: Azure Bot Resource

1. Go to [Azure Portal > Create a resource](https://portal.azure.com/#create/hub) > search "Azure Bot"
2. Click **Create**
   - Bot handle: `clambake-bot`
   - Pricing tier: **F0 (Free)**
   - Microsoft App ID: **Use existing app registration**
   - App ID: paste the `TEAMS_BOT_APP_ID` from Step 1
   - App tenant ID: your Azure tenant ID
3. After creation, go to the bot resource > **Configuration**
   - Messaging endpoint: `https://clambake-bot.YOUR_DOMAIN.com/api/messages`
4. Go to **Channels** > click **Microsoft Teams** > Save

## Step 3: Cloudflare Tunnel

1. Go to [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/) > **Networks** > **Tunnels**
2. Create a new tunnel (or add to existing)
   - Name: `clambake-bot`
3. Copy the **Tunnel Token** → this is `CLOUDFLARE_TUNNEL_TOKEN`
4. Add a public hostname:
   - Subdomain: `clambake-bot`
   - Domain: `YOUR_DOMAIN.com`
   - Service: `http://clambake-teams-bot:3978`

## Step 4: Configure Environment

Edit `F:/Docker/clambake/.env` and add:

```env
TEAMS_BOT_APP_ID=<from Step 1>
TEAMS_BOT_APP_PASSWORD=<from Step 1>
CLOUDFLARE_TUNNEL_TOKEN=<from Step 3>
```

## Step 5: Initialize Schema + Start

```bash
# Apply schema (adds conversation_references + notification_log tables)
clambake init

# Start the bot
cd F:/Docker/clambake
docker compose up -d teams-bot cloudflared
```

## Step 6: Install Bot in Teams

### Option A: Teams Developer Portal (recommended)
1. Go to [Teams Developer Portal](https://dev.teams.microsoft.com/apps)
2. **New app** > fill in basic info
3. Go to **App features** > **Bot**
4. Select **Enter a bot ID** > paste `TEAMS_BOT_APP_ID`
5. Check scopes: **Personal**, **Team**, **Group Chat**
6. **Publish** > **Publish to your org** (or download zip for sideloading)

### Option B: Manual app manifest
Create `manifest.json`:
```json
{
  "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.16/MicrosoftTeams.schema.json",
  "manifestVersion": "1.16",
  "version": "1.0.0",
  "id": "<TEAMS_BOT_APP_ID>",
  "developer": {
    "name": "Clambake",
    "websiteUrl": "https://github.com/IcemanT3/Clambake",
    "privacyUrl": "https://github.com/IcemanT3/Clambake",
    "termsOfUseUrl": "https://github.com/IcemanT3/Clambake"
  },
  "name": { "short": "Clambake", "full": "Clambake Agent Coordinator" },
  "description": {
    "short": "Monitor and control Clambake agents from Teams",
    "full": "Clambake Teams Bot provides real-time notifications and commands for multi-agent Claude Code orchestration."
  },
  "icons": { "color": "color.png", "outline": "outline.png" },
  "bots": [{
    "botId": "<TEAMS_BOT_APP_ID>",
    "scopes": ["personal", "team", "groupChat"],
    "commandLists": [{
      "scopes": ["personal", "team"],
      "commands": [
        { "title": "status", "description": "Show active agent instances" },
        { "title": "tasks", "description": "List active tasks" },
        { "title": "digest", "description": "Activity summary" },
        { "title": "recall", "description": "Search memory" },
        { "title": "send", "description": "Message an agent" },
        { "title": "help", "description": "Show commands" }
      ]
    }]
  }]
}
```

Zip `manifest.json` + two icon PNGs, upload via Teams > Apps > Upload a custom app.

## Verification

1. In Teams, message the bot: `/help` — should show command list
2. `/status` — should show active Clambake instances
3. Create a task via CLI, complete it — bot should post to channel
4. Send a blocker message — bot should DM you

## Troubleshooting

- **401 errors**: Check APP_ID and APP_PASSWORD match between Azure and .env
- **Bot doesn't respond**: Verify Cloudflare tunnel is running (`docker logs clambake-cloudflared`)
- **No proactive notifications**: Bot must have received at least one message first (to save conversation reference)
- **DB connection errors**: Verify `host.docker.internal:5433` is reachable from the container
