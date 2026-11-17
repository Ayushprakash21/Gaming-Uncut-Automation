# Gaming Uncut Automation

Autonomous local Windows desktop application for extracting high-excitement gaming moments, synthesizing 1080×1920 (9:16) vertical videos with animated captions and branding, and dispatching to **YouTube Shorts**, **Instagram Reels**, and **Facebook Reels** via official APIs.

---

## Features
- **Automated Footage Ingestion:** Watches OBS / NVIDIA Shadowplay recordings folder with write debounce. Also supports drag-and-drop.
- **AI Moment Detection:** Audio decibel spike clustering & speech excitement scoring to pinpoint 30s–60s clutch moments.
- **Hardware-Accelerated Compositor (NVIDIA NVENC):** Converts 16:9 gameplay into 1080×1920 60FPS vertical clips with blurred backdrop framing.
- **Branding & Captions:** Word-by-word highlighted karaoke subtitles rendered natively via Windows DirectWrite, plus top-zone "Gaming Uncut" watermark.
- **Official Publishing Integrations:**
  - **YouTube Shorts:** Official Google Data API v3 resumable chunked upload with automated `#Shorts` categorization.
  - **Instagram Reels:** Direct binary chunk upload via official Meta Graph API Resumable protocol (`rupload.facebook.com`) without external hosting costs.
  - **Facebook Reels:** Page Video Reels session upload and publishing.
- **Local Control Hub:** Modern dark-themed dashboard (`http://localhost:8765`) for previewing 9:16 video renders, editing hooks/titles, human approval, and monitoring live audit logs.
- **Secure Vault:** Uses Windows Credential Manager / DPAPI encryption. No tokens or secrets are hardcoded or committed to git.
- **Daily Target (10–15 clips/day):** Staggered scheduler with exponential backoff retries and quota monitoring.

---

## Quick Start (Windows)

### 1. Launch the Application
Simply double click `start.bat` or run:
```powershell
.\.venv\Scripts\python.exe run.py
```
This automatically initializes the SQLite WAL database, imports detected Google OAuth credentials into the vault, and opens the local dashboard at:
👉 `http://127.0.0.1:8765`

### 2. Configure Credentials
1. **YouTube Shorts:**
   - The desktop client secret `client_secrets.json` from your Downloads folder has already been securely imported into the local vault.
   - On your first upload or when triggering dispatch, a browser window will open for one-time Google Account authorization. The refresh token is securely stored.
2. **Meta Developer App (Instagram & Facebook):**
   - In the dashboard's **Vault & Settings** tab, input your Meta App ID, App Secret, and Long-Lived Page Access Token.

---

## API & Quota Guidelines
- **YouTube Data API Quotas:** Default Google Cloud quota is 10,000 units/day (1 video upload = 1,600 units, max ~6 uploads/day). To reach 10–15 clips/day, submit the official Google Cloud YouTube Quota Extension form requesting 50,000–100,000 daily units.
- **Meta Graph API:** For live public posting to Instagram & Facebook, your Meta App requires App Review for `instagram_content_publish` and `pages_manage_posts`. While in Development Mode, posts are published to your linked developer/tester accounts.
