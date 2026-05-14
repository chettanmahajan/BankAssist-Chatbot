# AWS EC2 Deployment Guide: BankAssist Chatbot

This guide provides the exact steps to deploy your chatbot to AWS. Following these steps will ensure your interviewer can access the site anytime, and the application will survive instance restarts.

## 1. AWS Infrastructure Setup

### Launch EC2 Instance
1.  **Region**: Choose a region close to your target audience (e.g., `ap-south-1` for Mumbai).
2.  **AMI**: Choose **Ubuntu 24.04 LTS** (64-bit x86).
3.  **Instance Type**: 
    - Recommended: `t3.small` (2GB RAM).
    - Free Tier: `t3.micro` (1GB RAM). *Note: If using t3.micro, you MUST follow the Swap File step below.*
4.  **Key Pair**: Create or select a `.pem` file to SSH into the instance.
5.  **Elastic IP**:
    - Go to **Network & Security > Elastic IPs**.
    - Click **Allocate Elastic IP address**.
    - Once allocated, click **Actions > Associate Elastic IP address** and link it to your EC2 instance. This ensures your IP never changes.

### Security Group (Firewall)
Edit your instance's Security Group to allow these ports:
- **SSH (22)**: From "My IP" (for you to login).
- **HTTP (80)**: From "Anywhere (0.0.0.0/0)" (for the interviewer).
- **Custom TCP (8501)**: From "Anywhere" (Optional: only if you want direct Streamlit access).

---

## 2. Server Preparation

Connect to your server:
```bash
ssh -i your-key.pem ubuntu@your-elastic-ip
```

### Update and Install Dependencies
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git nginx redis-server
```

### Provision Swap File (Crucial for 1GB RAM instances)
RAG models (even small ones) need more than 1GB RAM to load. A 2GB swap file prevents "Out of Memory" crashes.
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 3. Project Setup

### Clone and Install
```bash
git clone https://github.com/chettanmahajan/BankAssist-Chatbot.git
cd BankAssist-Chatbot
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
pip install -r frontend/requirements.txt
```

### Configuration (.env)
Create the `.env` file and add your credentials:
```bash
nano .env
```
Paste the following (updating your API key):
```env
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
VECTORSTORE_DIR=./Faiss_db
DATA_DIR=./data
API_URL=http://localhost:8000
REDIS_HOST=localhost
REDIS_PORT=6379
```

### Build the Search Index
```bash
python3 -m backend.build_index --clean
```

---

## 4. Persistence with systemd

We will create two services to keep the backend and frontend running forever.

### Backend Service
Create file: `sudo nano /etc/systemd/system/banking-backend.service`
```ini
[Unit]
Description=Banking Chatbot Backend (FastAPI)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/BankAssist-Chatbot
Environment="PATH=/home/ubuntu/BankAssist-Chatbot/venv/bin"
ExecStart=/home/ubuntu/BankAssist-Chatbot/venv/bin/python3 -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

### Frontend Service
Create file: `sudo nano /etc/systemd/system/banking-frontend.service`
```ini
[Unit]
Description=Banking Chatbot Frontend (Streamlit)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/BankAssist-Chatbot
Environment="PATH=/home/ubuntu/BankAssist-Chatbot/venv/bin"
ExecStart=/home/ubuntu/BankAssist-Chatbot/venv/bin/streamlit run frontend/streamlit_app.py --server.port 8501 --server.address 0.0.0.0
Restart=always

[Install]
WantedBy=multi-user.target
```

### Enable and Start Services
```bash
sudo systemctl daemon-reload
sudo systemctl enable banking-backend banking-frontend
sudo systemctl start banking-backend banking-frontend
```

---

## 5. Nginx Reverse Proxy (The Final Touch)

This makes your app accessible on `http://your-ip` without adding `:8501`.

1. Remove default config: `sudo rm /etc/nginx/sites-enabled/default`
2. Create new config: `sudo nano /etc/nginx/sites-available/banking`
3. Paste this:
```nginx
server {
    listen 80;
    server_name _;

    # Streamlit Frontend
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    # FastAPI Backend (Optional: for direct API access via /api/)
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_buffering off;
        proxy_set_header Host $host;
    }
}
```
4. Enable and restart Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/banking /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## 6. Testing the Reboot
To verify that everything works after a stop/start:
1.  Run `sudo reboot`.
2.  Wait 2 minutes.
3.  Visit your Elastic IP in the browser.
4.  The app should load automatically!

---

## Troubleshooting
- **Check Backend Logs**: `journalctl -u banking-backend -f`
- **Check Frontend Logs**: `journalctl -u banking-frontend -f`
- **Verify Redis**: `systemctl status redis-server`
