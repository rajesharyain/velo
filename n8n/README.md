# n8n automation (Velo → Instagram)

This folder documents running **n8n in Docker** next to the Velo API and importing a workflow that:

1. Calls **`POST /api/generate`** (Groq + Pexels + reel).
2. Builds an Instagram **caption** (hook, destinations, hashtags, CTA).
3. Creates and **publishes a Reel** via the **Instagram Graph API** (Meta).

> **Important:** Instagram’s servers must **fetch your video over HTTPS**. A local `http://127.0.0.1` URL will **not** work. Use **ngrok**, **Cloudflare Tunnel**, or deploy Velo behind HTTPS and set **`MEDIA_PUBLIC_BASE`** to that origin.

---

## 1. Start Velo (host machine)

Listen on all interfaces so Docker can reach you:

```bash
cd /path/to/velo
uvicorn travel_instagram.web_app:app --host 0.0.0.0 --port 8000
```

Expose it on the internet with HTTPS (example with ngrok):

```bash
ngrok http 8000
```

Note the HTTPS origin, e.g. `https://abc123.ngrok-free.app`.

---

## 2. Start n8n

From the project root:

```bash
docker compose up -d
```

Open **http://localhost:5678**, create an owner account, then continue.

---

## 3. n8n variables (or Docker env)

In n8n: **Settings → Variables** (or add under `environment:` in `docker-compose.yml` for the `n8n` service):

| Variable | Example | Purpose |
|----------|---------|---------|
| `VELO_API_URL` | `http://host.docker.internal:8000` | Velo API (Docker → Windows/Mac host). On Linux you may need your LAN IP instead of `host.docker.internal`. |
| `MEDIA_PUBLIC_BASE` | `https://abc123.ngrok-free.app` | **HTTPS** origin where `/media/...` is reachable (same host as Velo). No trailing slash. |
| `IG_USER_ID` | `17841...` | Instagram **Business** account id (from Meta developer tools). |
| `IG_ACCESS_TOKEN` | `EAAB...` | Long-lived user token with **`instagram_content_publish`** (and related permissions). |

`docker-compose.yml` includes commented examples you can uncomment after filling values.

---

## 4. Meta / Instagram setup (summary)

1. Create a **Meta app** (type: Business).
2. Add **Instagram Graph API** and **Instagram Login** (or use Facebook Login) per current Meta docs.
3. Link an **Instagram Business** or **Creator** account to a **Facebook Page**.
4. Generate a **long-lived access token** with permissions to publish (`instagram_content_publish`, etc.).
5. Use the **Instagram user id** (`ig-user-id`) from the Graph API explorer, not the @username.

Details change often; follow [Meta’s Instagram Platform documentation](https://developers.facebook.com/docs/instagram-platform).

---

## 5. Import the workflow

1. In n8n: **Workflows → Import from file**.
2. Choose **`n8n/workflows/velo-to-instagram.json`**.
3. Open **SetTheme** and change **`theme`** to your travel topic (or duplicate the trigger with **Schedule** / **Webhook** later).
4. If you use a custom **`VELO_API_URL`**, ensure it has **no trailing slash** or adjust the **VeloGenerate** URL field.

The **Code** node builds:

- **Caption:** hook + each destination (📍 + caption) + hashtags + CTA (tweak there for “viral” tone).
- **Reel:** uses **`outputs.reel_video_url`** from the API (relative `/media/...`) + **`MEDIA_PUBLIC_BASE`** for a full **HTTPS** URL.

---

## 6. Publishing gotchas

- **Reel processing:** Meta often needs **10–60 seconds** between **container create** and **publish**. If **IGPublish** fails with “not ready”, insert a **Wait** node (30–60 s) between **IGCreateContainer** and **IGPublish**, or poll the container `status` field per Meta docs.
- **Carousel feed posts** need a different Graph flow (multiple `children`); this workflow only publishes **one Reel**.
- **Rate limits** and **app review** may apply for production use.

---

## 7. Optional: music in the API request

Edit the **VeloGenerate** node **JSON body** to pass a track from `music/`:

```json
{{ JSON.stringify({ theme: $json.theme, music_track_id: "your-file.mp3" }) }}
```

Or `null` for automatic selection (see app docs).
