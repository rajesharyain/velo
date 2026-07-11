# Deploying Velo on Oracle Cloud Free Tier

Zero-cost, always-on hosting for Velo + n8n on Oracle's genuinely-free ARM VM.

---

## What you get (free forever)

| Resource | Amount |
|---|---|
| CPU | 4 ARM (Ampere A1) cores |
| RAM | 24 GB |
| Disk | 50 GB |
| Bandwidth | 10 TB/month |
| Public IP | 1 static IP |

---

## Step 1 — Create an Oracle Cloud account

1. Go to [cloud.oracle.com](https://cloud.oracle.com) → **Start for free**
2. Use a real address and credit card (identity verification only — you won't be charged)
3. Choose your home region carefully — **you cannot change it later**. Pick the closest one to your audience.

---

## Step 2 — Provision the VM

1. In the Oracle Cloud console, go to **Compute → Instances → Create Instance**
2. Set the following:

   - **Name**: `velo-server`
   - **Image**: Ubuntu 22.04 (Minimal)
   - **Shape**: Click **Change Shape** → select **Ampere** → `VM.Standard.A1.Flex`
     - OCPUs: `4`
     - RAM: `24 GB`
   - **Boot volume**: 50 GB (default)
   - **SSH keys**: Upload your public key (`~/.ssh/id_rsa.pub`) or generate a new pair and download it

3. Click **Create**. Wait ~2 minutes for the instance to reach **Running** state.
4. Copy the **Public IP address** — you'll use it throughout this guide.

---

## Step 3 — Open firewall ports

Oracle's default security list blocks everything except SSH. Open ports for Velo and n8n:

1. In the console go to **Networking → Virtual Cloud Networks → your VCN → Security Lists → Default Security List**
2. Click **Add Ingress Rules** and add these two rules:

   | Source CIDR | Protocol | Port | Description |
   |---|---|---|---|
   | `0.0.0.0/0` | TCP | `8000` | Velo web app |
   | `0.0.0.0/0` | TCP | `5678` | n8n UI |

3. Also run this on the VM after SSH-ing in (Ubuntu's own firewall must allow the ports too):

```bash
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 5678 -j ACCEPT
sudo netfilter-persistent save
```

---

## Step 4 — SSH into the VM

```bash
ssh -i ~/.ssh/id_rsa ubuntu@<YOUR_PUBLIC_IP>
```

---

## Step 5 — Install Docker

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sudo sh

# Add your user to the docker group (no sudo needed)
sudo usermod -aG docker ubuntu

# Install Docker Compose plugin
sudo apt-get install -y docker-compose-plugin

# Apply group change without logging out
newgrp docker

# Verify
docker --version
docker compose version
```

---

## Step 6 — Clone the repo

```bash
cd ~
git clone https://github.com/rajesharyain/velo.git
cd velo
```

---

## Step 7 — Create your `.env` file

```bash
cp .env.example .env
nano .env
```

Set at minimum:

```
GROQ_API_KEY=your_groq_api_key
PEXELS_API_KEY=your_pexels_api_key

# Instagram publishing
IG_USER_ID=your_instagram_business_account_id
IG_ACCESS_TOKEN=your_never_expiring_system_user_token
PUBLIC_APP_BASE_URL=http://<YOUR_PUBLIC_IP>:8000

# YouTube publishing
YOUTUBE_CLIENT_ID=your_google_oauth_client_id
YOUTUBE_CLIENT_SECRET=your_google_oauth_client_secret
YOUTUBE_REFRESH_TOKEN=your_refresh_token
```

Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## Step 8 — Update `docker-compose.yml` for production

The default `docker-compose.yml` uses `localhost` for n8n — on a remote server the webhook URL must point to your public IP so n8n can receive external triggers and so the Velo→n8n internal calls resolve correctly.

Open the file:

```bash
nano docker-compose.yml
```

Find the `n8n` service environment section and update these two lines:

```yaml
  n8n:
    environment:
      - N8N_HOST=<YOUR_PUBLIC_IP>          # ← replace localhost
      - WEBHOOK_URL=http://<YOUR_PUBLIC_IP>:5678/   # ← replace localhost
```

Save and exit.

---

## Step 9 — Start the stack

```bash
# Build the Velo image and start both containers
docker compose up -d --build

# Watch logs to confirm both services started
docker compose logs -f
```

Once you see `Uvicorn running on http://0.0.0.0:8000` and n8n's startup banner, the stack is live.

- **Velo**: `http://<YOUR_PUBLIC_IP>:8000`
- **n8n**: `http://<YOUR_PUBLIC_IP>:5678`

---

## Step 10 — Import the n8n workflow

1. Open n8n at `http://<YOUR_PUBLIC_IP>:5678`
2. Complete the initial n8n account setup
3. Go to **Workflows → Import from file**
4. Upload `n8n/workflows/excel-to-reels-instagram-publish.json`
5. Open the imported workflow and update the **VELO_API_URL** variable:
   - Go to **Settings → Variables** (or the gear icon)
   - Set `VELO_API_URL` = `http://velo:8000` (internal Docker network — keep this as-is)
   - Set `MEDIA_PUBLIC_BASE` = `http://<YOUR_PUBLIC_IP>:8000`
6. **Activate** the workflow (toggle in top-right)

The schedule trigger will fire automatically at 10 AM PDT and 10 PM PDT.
To trigger manually via webhook:

```bash
curl -X POST http://<YOUR_PUBLIC_IP>:5678/webhook/velo-daily-run
```

---

## Step 11 — Upload your music files

```bash
# From your local machine
scp -i ~/.ssh/id_rsa music/*.mp3 ubuntu@<YOUR_PUBLIC_IP>:~/velo/music/
```

Or place them directly on the server in `~/velo/music/`.

---

## Keeping it running

The containers restart automatically on reboot (`restart: unless-stopped`).

To update Velo after a code change:

```bash
cd ~/velo
git pull
docker compose build velo
docker compose up -d velo
```

To update n8n:

```bash
docker compose pull n8n
docker compose up -d n8n
```

---

## Optional — HTTPS with a free domain (required for Meta API)

Meta's Instagram API requires `PUBLIC_APP_BASE_URL` to be HTTPS. If you want Instagram publishing to work properly from a remote server, set up a free domain + Caddy reverse proxy:

### Get a free domain

- [duckdns.org](https://www.duckdns.org) — free `*.duckdns.org` subdomain, points to your public IP

### Install Caddy (auto-HTTPS)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy -y
```

### Create Caddyfile

```bash
sudo nano /etc/caddy/Caddyfile
```

```
your-name.duckdns.org {
    reverse_proxy localhost:8000
}

n8n.your-name.duckdns.org {
    reverse_proxy localhost:5678
}
```

```bash
sudo systemctl reload caddy
```

Caddy handles TLS certificates automatically via Let's Encrypt.

Then update `.env`:

```
PUBLIC_APP_BASE_URL=https://your-name.duckdns.org
```

And update `docker-compose.yml` n8n environment:

```yaml
- WEBHOOK_URL=https://n8n.your-name.duckdns.org/
```

Rebuild and restart:

```bash
docker compose up -d --build
```

---

## Troubleshooting

**Velo not reachable on port 8000**
- Check Oracle security list has ingress rule for port 8000
- Check iptables: `sudo iptables -L INPUT -n | grep 8000`

**n8n webhook not triggering**
- Confirm `WEBHOOK_URL` in docker-compose.yml matches your public IP/domain
- Check workflow is **activated** in n8n UI

**Instagram publish failing**
- Token expired → generate a new System User token in Meta Business Suite
- Update `.env` on the server, then `docker compose up -d velo`

**Out of disk space**
- Clean old Docker images: `docker image prune -f`
- Clean old output files: `rm -rf ~/velo/output/carousel/*/downloads/`

**Check running containers**
```bash
docker compose ps
docker compose logs velo --tail=50
docker compose logs n8n --tail=50
```
