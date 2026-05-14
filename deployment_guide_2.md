# AWS EC2 Deployment Guide v2 — BankAssist Chatbot

This is the recommended deployment path. It accounts for OOM risk during `pip install`, Streamlit's first-run prompt under systemd, and uses `.env.example` as the source of truth for env vars.

---

## 1. Launch the EC2 Instance

| Setting | Value |
| --- | --- |
| Region | Closest to your users (e.g. `ap-south-1` Mumbai) |
| AMI | **Ubuntu 24.04 LTS** (64-bit x86) — ships Python 3.12, matches the pinned wheels |
| Instance type | **t3.small (2GB RAM) recommended.** t3.micro (1GB) works only with the swap file step below. |
| Storage | 16 GB gp3 (torch + HF models eat ~3 GB) |
| Key pair | Create or pick an existing `.pem` |

### Allocate an Elastic IP
**Network & Security → Elastic IPs → Allocate → Associate with your instance.** This keeps the public IP stable across stops/starts so your demo link doesn't break.

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

---

## 6. Smoke Test (Manual)

Before wiring systemd, confirm everything actually runs:

```bash
# Terminal 1
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

In another SSH session:
```bash
curl http://127.0.0.1:8000/health
# expect: {"status":"ok","version":"1.0.0","vectorstore_loaded":true,"docs_indexed":853}
```

Stop with Ctrl+C, then continue to systemd.

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

> `--server.headless true` is the fix v1 missed — without it, Streamlit blocks on a "What's your email?" prompt under systemd and the service never goes ready.

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable banking-backend banking-frontend
sudo systemctl start banking-backend banking-frontend

# Verify
sudo systemctl status banking-backend --no-pager
sudo systemctl status banking-frontend --no-pager
curl http://127.0.0.1:8000/health
curl -I http://127.0.0.1:8501
```

Both `status` outputs should say `active (running)`.

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

## Troubleshooting

| Symptom | Diagnosis | Fix |
| --- | --- | --- |
| `pip install` exits with "Killed" | OOM on t3.micro | Add swap (Section 2) and use `--no-cache-dir` |
| Backend service won't start, log says `ModuleNotFoundError` | venv not activated when installing | `source venv/bin/activate` then re-install requirements |
| Backend `/health` returns `vectorstore_loaded: false` | FAISS index missing | `python -m backend.build_index --clean` |
| Frontend says "API unreachable" | Backend down, or `API_URL` wrong in `.env` | `sudo systemctl status banking-backend`; ensure `API_URL=http://localhost:8000` in `.env` |
| Streamlit service "active" but page won't load | Headless flag missing — service is blocked on email prompt | Confirm `--server.headless true` is in the unit, then `sudo systemctl daemon-reload && sudo systemctl restart banking-frontend` |
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

---

## Differences vs `deployment_guide.md` (v1)

1. **`pip install --no-cache-dir`** — prevents OOM on small instances.
2. **`--server.headless true` and `--browser.gatherUsageStats false`** on the Streamlit unit — avoids the first-run prompt that silently hangs systemd.
3. **`.env` setup uses `cp .env.example .env`** — gets all tunables, not just the subset in v1.
4. **Nginx adds `X-Real-IP`, `X-Forwarded-For`, `proxy_read_timeout 86400`** — proper headers for Streamlit + long-lived SSE from the backend.
5. **Manual smoke test step** before wiring systemd — catches config errors early instead of debugging through `journalctl`.
6. **`After=banking-backend.service`** on the frontend unit — frontend boots after backend.
