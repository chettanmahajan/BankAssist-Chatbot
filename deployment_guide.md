# AWS EC2 Deployment Guide — BankAssist Chatbot

Step-by-step walkthrough for deploying BankAssist Chatbot to a fresh AWS EC2 instance. Covers OOM-safe `pip install`, Streamlit's first-run prompt under systemd, `.env.example` as the env-var source of truth, and the real-world gotchas hit during the first live run-through.

---

## 1. Launch the EC2 Instance

| Setting | Value |
| --- | --- |
| Region | Closest to your users (e.g. `ap-south-1` Mumbai) |
| AMI | **Ubuntu Server 24.04 LTS** (64-bit x86) — ships Python 3.12, matches the pinned `cp312` wheels |
| Instance type | **t3.small (2GB RAM) recommended.** t3.micro (1GB) works only with the swap file step below. |
| Storage | 16 GB gp3 (torch + HF models eat ~3 GB) |
| Key pair | Create or pick an existing `.pem` |

> **Why not Ubuntu 26.04 LTS** (even though it's also free-tier eligible)? It ships Python 3.13. Some packages — especially `faiss-cpu==1.13.2` — do not yet have `cp313` wheels for Linux. Pip would fall back to compiling from source and likely fail on small instances. **Stick with 24.04.**
>
> **Why not Ubuntu Pro?** It's the same Ubuntu with paid support add-ons. No benefit for this project, and not all variants are free-tier eligible.

### Network settings (Launch wizard)

| Field | Value |
| --- | --- |
| VPC | **Default VPC** (already has an Internet Gateway attached) |
| Subnet | **Default subnet** in your chosen AZ (already public — routes `0.0.0.0/0` to the IGW) |
| Auto-assign public IP | **Enable** — needed so you can SSH in immediately |

This is the standard setup. You don't need a custom VPC for a single-instance demo.

### Allocate an Elastic IP
After the instance launches and you confirm everything works, go to **Network & Security → Elastic IPs → Allocate → Associate** with your instance. The auto-assigned public IP changes every time you stop/start; an Elastic IP stays put so your demo URL never breaks.

### Security Group inbound rules
| Port | Source | Why |
| --- | --- | --- |
| 22 (SSH) | My IP | You only |
| 80 (HTTP) | 0.0.0.0/0 | Public access via Nginx |
| 8501 (TCP) | 0.0.0.0/0 *(optional)* | Direct Streamlit access — skip if you only want port 80 |

---

## 2. Connect and Prepare the Server

```bash
ssh -i your-key.pem ubuntu@your-elastic-ip
```

> **First-time `.pem` permission error on Linux/macOS:** `chmod 400 your-key.pem` before SSHing.
>
> **"REMOTE HOST IDENTIFICATION HAS CHANGED!"** — happens when you previously SSH'd to a different EC2 instance that had the same public IP/DNS (AWS recycles them). Reset the stored host key and reconnect:
>
> ```bash
> ssh-keygen -R your-elastic-ip
> ssh -i your-key.pem ubuntu@your-elastic-ip
> # type "yes" when it asks about the new fingerprint
> ```

Install system dependencies:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git nginx redis-server
```

### Swap file — REQUIRED on t3.micro, optional on t3.small

`pip install torch` peaks around 1.2 GB of memory. Without swap, pip gets killed silently on 1 GB instances.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h   # verify Swap row shows 2.0Gi
```

---

## 3. Clone and Install

```bash
cd /home/ubuntu
git clone https://github.com/chettanmahajan/BankAssist-Chatbot.git
cd BankAssist-Chatbot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
```

**Install with `--no-cache-dir`** — keeps the wheel cache from doubling memory use during torch install:

```bash
pip install --no-cache-dir -r backend/requirements.txt
pip install --no-cache-dir -r frontend/requirements.txt
```

> Expect 5–10 minutes. torch alone is ~800 MB. If the install dies with "Killed", you're on t3.micro without swap — go back and add it.

---

## 4. Configure Environment Variables

Use `.env.example` as the template (it has every variable the code reads):

```bash
cp .env.example .env
nano .env
```

**Required edit:** set `GROQ_API_KEY=gsk_your_real_key`. Everything else has sensible defaults, but you can tune retrieval / Redis / API URL here.

Save and exit (Ctrl+O, Enter, Ctrl+X).

---

## 5. Build the FAISS Vector Index

```bash
python -m backend.build_index --clean
```

This downloads the embedding model (`all-MiniLM-L6-v2`, ~90 MB) from Hugging Face on the **first run only**, then embeds the ~850 documents in `data/`. Takes 1–3 minutes on t3.small. Outputs:

```
Faiss_db/index.faiss
Faiss_db/index.pkl
```

If this hangs, the instance has no outbound internet — check route table and security group egress.

### Verifying the index was written

Don't type the file paths at the shell — they're binary data, not executables, and bash will return `Permission denied`:

```bash
# WRONG — bash tries to execute the file
Faiss_db/index.faiss
# bash: Faiss_db/index.faiss: Permission denied
```

Use `ls` to confirm both files exist:

```bash
ls -lh Faiss_db/
# expect:
# -rw-rw-r-- 1 ubuntu ubuntu  ~2-5M  index.faiss
# -rw-rw-r-- 1 ubuntu ubuntu  ~1-2M  index.pkl
```

---

## 6. Smoke Test (Manual)

Before wiring systemd, confirm everything actually runs.

> **You need TWO Git Bash / terminal windows for this step.** Uvicorn runs in the foreground and blocks the terminal — if you Ctrl+C it before testing, there's nothing for curl to hit. Don't try to do it in one window.

### Terminal 1 — start the backend

```bash
cd /home/ubuntu/BankAssist-Chatbot
source venv/bin/activate
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Leave this running. You'll see logs ending with `Uvicorn running on http://127.0.0.1:8000`.

### Terminal 2 — open a SECOND SSH session and test

Open a new Git Bash window and SSH in again:

```bash
ssh -i "Server.pem" ubuntu@your-elastic-ip
curl http://127.0.0.1:8000/health
# expect: {"status":"ok","version":"1.0.0","vectorstore_loaded":true,"docs_indexed":853}
```

### Cleanup

Once you see the JSON response:
1. Go back to **Terminal 1** and press **Ctrl+C** to stop uvicorn.
2. Close Terminal 2.
3. Continue to Section 7.

### Don't want a second terminal?

You can background uvicorn instead:
```bash
nohup python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1 &
sleep 10
curl http://127.0.0.1:8000/health
kill %1   # stops the backgrounded uvicorn
```
But the two-terminal flow is cleaner and matches how you'll debug systemd later.

---

## 7. systemd Services (Persistent)

### Backend

```bash
sudo nano /etc/systemd/system/banking-backend.service
```

```ini
[Unit]
Description=BankAssist Backend (FastAPI/Uvicorn)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/BankAssist-Chatbot
ExecStart=/home/ubuntu/BankAssist-Chatbot/venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> No `EnvironmentFile=` needed — `app.py` calls `load_dotenv()` and `WorkingDirectory` points at the folder holding `.env`.

### Frontend

```bash
sudo nano /etc/systemd/system/banking-frontend.service
```

```ini
[Unit]
Description=BankAssist Frontend (Streamlit)
After=network.target banking-backend.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/BankAssist-Chatbot
ExecStart=/home/ubuntu/BankAssist-Chatbot/venv/bin/streamlit run frontend/streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> `--server.headless true` is critical — without it, Streamlit blocks on a "What's your email?" prompt under systemd and the service never goes ready.

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable banking-backend banking-frontend
sudo systemctl start banking-backend banking-frontend
```

### IMPORTANT — wait before testing

The services will report `active (running)` within milliseconds, but **the apps inside take 10–20 seconds to actually bind to their ports.** During that window curl will fail with `Failed to connect ... after 0 ms: Couldn't connect to server` — this is NOT a failure, just bad timing.

The backend has to load:
- The embeddings model (`all-MiniLM-L6-v2`) into RAM
- The FAISS index from disk
- The Groq client

Wait, then verify:

```bash
sleep 20
sudo systemctl status banking-backend --no-pager
sudo systemctl status banking-frontend --no-pager
curl http://127.0.0.1:8000/health
curl -I http://127.0.0.1:8501
```

Both `status` outputs should say `active (running)`. The curls should return the health JSON and `HTTP/1.1 200 OK`.

### If curl still fails after 30 seconds

The app actually crashed during init. Check logs:

```bash
sudo journalctl -u banking-backend -n 50 --no-pager
sudo journalctl -u banking-frontend -n 50 --no-pager
ss -tlnp | grep -E '8000|8501'   # confirms whether anything is listening
```

Most common causes:
- `GROQ_API_KEY` not set in `.env` → backend logs say something about missing key
- `.env` not in `/home/ubuntu/BankAssist-Chatbot/` → `load_dotenv()` finds nothing
- Streamlit blocked on first-run prompt → confirm `--server.headless true` is in the unit file

---

## 8. Nginx Reverse Proxy

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo nano /etc/nginx/sites-available/banking
```

```nginx
server {
    listen 80 default_server;
    server_name _;

    # Streamlit needs WebSocket upgrade + a longer read timeout for streaming
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    # Optional: expose backend at /api/* (proxy_buffering off enables SSE streaming)
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400;
    }
}
```

Enable and reload:

```bash
sudo ln -sf /etc/nginx/sites-available/banking /etc/nginx/sites-enabled/banking
sudo nginx -t
sudo systemctl reload nginx
```

Open `http://your-elastic-ip` in your browser — the Streamlit UI should load.

---

## 9. Reboot Test

The whole point of systemd is surviving restarts:

```bash
sudo reboot
```

Wait ~90 seconds, then re-visit `http://your-elastic-ip`. App should come back without you touching anything.

---

## 10. Updating the App Later

```bash
cd /home/ubuntu/BankAssist-Chatbot
git pull
source venv/bin/activate
pip install --no-cache-dir -r backend/requirements.txt -r frontend/requirements.txt
# Only if you changed files in data/:
python -m backend.build_index --clean
sudo systemctl restart banking-backend banking-frontend
```

---

## Real-World Issues Encountered (from first deployment, 2026-05-14)

These are the actual hiccups that came up during a live run-through of this guide. None of them broke the deployment — they all had a quick fix — but knowing about them up front saves time.

### Issue 1 — `REMOTE HOST IDENTIFICATION HAS CHANGED!` on SSH

**What you see:**
```
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
...
Host key verification failed.
```

**Why:** You SSH'd to this public IP/DNS before (it pointed at a different instance you terminated). AWS recycled the address; the new instance has different host keys. SSH stores fingerprints in `~/.ssh/known_hosts` and refuses to connect when it sees a mismatch.

**Fix:**
```bash
ssh-keygen -R ec2-13-206-117-7.ap-south-1.compute.amazonaws.com   # use your actual hostname
ssh -i "Server.pem" ubuntu@ec2-13-206-117-7.ap-south-1.compute.amazonaws.com
# type "yes" when it asks about the new fingerprint
```

### Issue 2 — `Permission denied` when listing index files

**What you see:**
```
$ Faiss_db/index.faiss
-bash: Faiss_db/index.faiss: Permission denied
```

**Why:** You typed the file path as a command. Bash tried to execute the binary FAISS index — it isn't executable, so bash refused. The index built successfully; this isn't a build failure.

**Fix:** use `ls`, not the bare path:
```bash
ls -lh Faiss_db/
```

### Issue 3 — `curl: Failed to connect ... after 0 ms` right after `systemctl start`

**What you see:**
```
$ sudo systemctl start banking-backend banking-frontend
$ curl http://127.0.0.1:8000/health
curl: (7) Failed to connect to 127.0.0.1 port 8000 after 0 ms: Couldn't connect to server
```

But `systemctl status` shows both services `active (running)`.

**Why:** systemd reports the service "active" as soon as the Python process is spawned. But uvicorn and Streamlit need 10–20 seconds to load the FAISS index, embeddings model, and Groq client before they bind to their ports. During that window, no socket exists, so curl gets an instant connection refused.

**Fix:** wait, then retry:
```bash
sleep 20
curl http://127.0.0.1:8000/health
curl -I http://127.0.0.1:8501
```

To watch the app come up in real time, tail the logs in a second SSH window:
```bash
sudo journalctl -u banking-backend -f
# wait for: "INFO: Application startup complete." then "Uvicorn running on http://127.0.0.1:8000"
```

### Issue 4 — Smoke test confusion: Ctrl+C or new terminal?

**The question:** in Section 6, `python -m uvicorn ...` blocks the terminal. Should you press Ctrl+C and then curl?

**Answer:** No — Ctrl+C kills uvicorn before you can test. Open a **second Git Bash window**, SSH into the same instance, and run curl there. Only Ctrl+C the first terminal *after* the curl succeeds. See Section 6 for the exact flow.

---

## Troubleshooting

| Symptom | Diagnosis | Fix |
| --- | --- | --- |
| `REMOTE HOST IDENTIFICATION HAS CHANGED!` on SSH | Stale host key in `~/.ssh/known_hosts` from a previous instance | `ssh-keygen -R <hostname>` then SSH again, type "yes" |
| `Permission denied` when typing `Faiss_db/index.faiss` at the shell | You typed a data-file path as a command | Use `ls -lh Faiss_db/` to verify, not the bare path |
| `curl: Failed to connect ... after 0 ms` right after `systemctl start` | App still initializing — port not bound yet | `sleep 20` then retry; tail `journalctl -u banking-backend -f` to watch startup |
| `pip install` exits with "Killed" | OOM on t3.micro | Add swap (Section 2) and use `--no-cache-dir` |
| Backend service won't start, log says `ModuleNotFoundError` | venv not activated when installing | `source venv/bin/activate` then re-install requirements |
| Backend `/health` returns `vectorstore_loaded: false` | FAISS index missing | `python -m backend.build_index --clean` |
| Frontend says "API unreachable" | Backend down, or `API_URL` wrong in `.env` | `sudo systemctl status banking-backend`; ensure `API_URL=http://localhost:8000` in `.env` |
| Streamlit service "active" but page won't load | Headless flag missing — service blocked on email prompt | Confirm `--server.headless true` is in the unit, then `sudo systemctl daemon-reload && sudo systemctl restart banking-frontend` |
| Nginx returns 502 | Backend or frontend not listening on expected port | Check `ss -tlnp \| grep -E '8000\|8501'` |
| Redis warning in backend logs | Redis not running | `sudo systemctl start redis-server` — non-fatal, caching is optional |
| Hugging Face download hangs | No outbound internet from EC2 | Check route table has IGW route; security group egress is open by default |

### Useful commands

```bash
sudo journalctl -u banking-backend -f      # tail backend logs
sudo journalctl -u banking-frontend -f     # tail frontend logs
sudo journalctl -u nginx -n 50             # nginx last 50 lines
sudo systemctl restart banking-backend     # restart after .env change
free -h                                    # check memory + swap usage
```

