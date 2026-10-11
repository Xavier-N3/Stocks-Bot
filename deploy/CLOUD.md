# Running 24/7 in the cloud (free)

## Can Cloudflare run it?

**Not the bots themselves.** Cloudflare Workers/Pages run short JavaScript functions (milliseconds of CPU per
request, no always-on processes, no numpy/pandas), so they can't host 9 agents that loop forever, train neural
networks and keep a 100+ MB database. Cloudflare Containers can run it, but they're a paid feature and sleep when idle.

**What Cloudflare *is* great for here:** a free **Cloudflare Tunnel** that safely puts the Studio dashboard on the
internet (no open ports), optionally behind a Cloudflare Access login.

## Recommended setup (all free)

| Piece | Service | Cost |
|---|---|---|
| The bots + Studio, always on | **Oracle Cloud "Always Free"** Ampere VM (up to 4 CPU / 24 GB RAM) | free forever (card needed only to verify identity) |
| Code | **GitHub** private repo | free |
| Viewing the Studio from anywhere | **Tailscale** (private, simplest) or **Cloudflare Tunnel** (public URL + password) | free |

Other options if Oracle has no free capacity in your region: Google Cloud e2-micro (free, but only 1 GB RAM -
tight), or a cheap VPS (Hetzner ~$4-5/mo, DigitalOcean $6/mo). Avoid "free web app" hosts (Render, Railway, Hugging
Face Spaces): they sleep when idle or wipe the disk, which would stop trading and erase the learning.

> **Run it in ONE place only.** If the cloud server is trading, stop it on your PC (`./stop.ps1` and
> `./uninstall_autostart.ps1`) - two copies would trade the same account against each other.

## Step by step

1. **Push the code to GitHub** (on your PC, once): create an empty *private* repo at https://github.com/new
   (no README), then in this folder run:
   `git remote add origin https://github.com/<you>/<repo>.git` and `git push -u origin main`.
   Your keys (`.env`), data and logs are excluded by `.gitignore` and never uploaded.
2. **Create the server:** cloud.oracle.com -> sign up -> *Create a VM instance* -> Image **Ubuntu 24.04**, Shape
   **Ampere VM.Standard.A1.Flex** (2-4 OCPU, 12-24 GB). Download the SSH key it offers.
3. **SSH in** (`ssh -i key.pem ubuntu@<server-ip>`) and run:
   ```
   git clone https://github.com/<you>/<repo>.git ~/stocks-bot      # private repo: use a GitHub token as password
   cd ~/stocks-bot && bash deploy/setup_server.sh
   nano .env        # paste the same keys as your PC's .env (including NOTIFY_NTFY_TOPIC for phone alerts), add STUDIO_PASSWORD=something-long
   bash deploy/setup_server.sh                                      # run again to finish + start the service
   ```
   Optional: copy your PC's `data/market.db` to the server (`scp`) to keep the trained brains and history;
   otherwise it re-downloads data and re-trains in ~10 minutes.
4. **Updates:** push to GitHub, then on the server `bash deploy/update.sh`. Or turn on auto-deploy
   (`.github/workflows/deploy.yml` explains the 3 settings) so every push updates the server by itself.

## Reaching the Studio

**Option A - Tailscale (private, easiest):** install Tailscale on the server (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`)
and on your phone/PC, sign in with the same account. In the server's `.env` set `STUDIO_HOST=0.0.0.0`, restart,
then open `http://<server-tailscale-name>:8765`. Only your own devices can reach it.

**Option B - Cloudflare Tunnel (public link):** set `STUDIO_PASSWORD` in `.env` first. Then on the server:
```
curl -L -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel --url http://localhost:8765        # quick test: prints a https://....trycloudflare.com link
```
For a permanent address, add a domain to Cloudflare (free plan; the domain itself ~$10/yr), then in the Cloudflare
dashboard: *Zero Trust -> Networks -> Tunnels -> Create tunnel*, run the install command it shows on the server,
and add a public hostname (e.g. `studio.yourdomain.com` -> `http://localhost:8765`). Add a *Zero Trust -> Access*
application for that hostname with your email for a proper login screen.
