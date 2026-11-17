"""
Gaming Uncut Automation — System & Security Diagnostics.
Safely inspects environment, hardware encoders, vault configurations, and git state.
SECURITY DIRECTIVE: Never prints or exposes secret tokens or passwords.
"""

import sys
import os
import shutil
import subprocess
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

def run_cmd(cmd_list):
    try:
        res = subprocess.run(cmd_list, capture_output=True, text=True, check=False)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def main():
    print("=" * 70)
    print("      GAMING UNCUT AUTOMATION — SYSTEM & SECURITY DIAGNOSTICS")
    print("                 NO FILTER. JUST GAMEPLAY.")
    print("=" * 70)
    print()

    # 1. Environment & Runtimes
    print("[1] RUNTIME & CLI TOOLS")
    print(f"  • Python:           {sys.version.split()[0]} ({sys.executable})")
    
    node_ok, node_out, _ = run_cmd(["node", "--version"])
    print(f"  • Node.js:          {node_out if node_ok else 'NOT FOUND in PATH'}")
    
    npm_ok, npm_out, _ = run_cmd(["npm", "--version"])
    print(f"  • npm:              {npm_out if npm_ok else 'NOT FOUND in PATH'}")

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        _, ff_ver, _ = run_cmd(["ffmpeg", "-version"])
        ff_first = ff_ver.split("\n")[0] if ff_ver else "Found"
        print(f"  • FFmpeg:           FOUND ({ff_first})")
        print(f"    Binary Path:      {ffmpeg_path}")
    else:
        print("  • FFmpeg:           [!] NOT FOUND in PATH (Required for video rendering)")

    # 2. Hardware Acceleration & NVENC
    print()
    print("[2] HARDWARE ACCELERATION (NVENC / GPU)")
    smi_ok, smi_out, _ = run_cmd(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])
    if smi_ok and smi_out:
        print(f"  • NVIDIA GPU:       {smi_out}")
    else:
        print("  • NVIDIA GPU:       nvidia-smi not available or no NVIDIA GPU detected")

    if ffmpeg_path:
        enc_ok, enc_out, _ = run_cmd(["ffmpeg", "-encoders"])
        has_nvenc = "h264_nvenc" in enc_out
        has_hevc_nvenc = "hevc_nvenc" in enc_out
        has_qsv = "h264_qsv" in enc_out
        has_amf = "h264_amf" in enc_out
        print(f"  • NVENC H.264:      {'SUPPORTED' if has_nvenc else 'Not available'}")
        print(f"  • NVENC HEVC:       {'SUPPORTED' if has_hevc_nvenc else 'Not available'}")
        print(f"  • Intel QSV:        {'SUPPORTED' if has_qsv else 'Not available'}")
        print(f"  • AMD AMF:          {'SUPPORTED' if has_amf else 'Not available'}")

    # 3. Git Status & Tracking
    print()
    print("[3] GIT REPOSITORY STATE")
    branch_ok, branch_name, _ = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    commit_ok, commit_sha, _ = run_cmd(["git", "rev-parse", "HEAD"])
    remote_ok, remote_url, _ = run_cmd(["git", "remote", "get-url", "origin"])
    status_ok, status_short, _ = run_cmd(["git", "status", "--short"])

    print(f"  • Active Branch:    {branch_name if branch_ok else 'Unknown'}")
    print(f"  • Commit SHA:       {commit_sha if commit_ok else 'Unknown'}")
    print(f"  • Remote Origin:    {remote_url if remote_ok else 'Not configured'}")
    clean_status = "Clean" if not status_short else f"{len(status_short.splitlines())} modified/untracked files"
    print(f"  • Working Tree:     {clean_status}")

    # 4. Network & Port 8765
    print()
    print("[4] NETWORK & PORT BINDING")
    ps_cmd = "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"
    port_ok, port_pid, _ = run_cmd(["powershell", "-NoProfile", "-Command", ps_cmd])
    if port_ok and port_pid.strip():
        pid = port_pid.strip()
        pname_ok, pname, _ = run_cmd(["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName"])
        print(f"  • Port 8765:        ACTIVE (LISTENING)")
        print(f"  • Process ID:       {pid} ({pname.strip() if pname_ok else 'Unknown'})")
    else:
        print("  • Port 8765:        IDLE (Not listening)")

    # 5. Database Health
    print()
    print("[5] DATABASE & LOCAL STORAGE")
    try:
        from backend.app.database.session import SessionLocal
        from backend.app.models.schema import ProcessingJob, GeneratedClip, PublishingJob
        db = SessionLocal()
        job_count = db.query(ProcessingJob).count()
        clip_count = db.query(GeneratedClip).count()
        pub_count = db.query(PublishingJob).count()
        db.close()
        print("  • SQLite Database:  HEALTHY (sqlite:///data/gaming_uncut.db)")
        print(f"  • Total Jobs:       {job_count}")
        print(f"  • Total Clips:      {clip_count}")
        print(f"  • Publishing Queue: {pub_count}")
    except Exception as e:
        print(f"  • SQLite Database:  [!] ERROR: {e}")

    # 6. Windows Secure Vault State (Safe audit - NEVER print secrets)
    print()
    print("[6] SECURE VAULT & CREDENTIAL STATE (ZERO-KNOWLEDGE AUDIT)")
    try:
        from backend.app.security.vault import vault
        
        # Google / YouTube
        client_cfg = vault.get_json("google", "client_config")
        has_client_cfg = client_cfg is not None
        proj_id = client_cfg.get("installed", {}).get("project_id", "Unknown") if has_client_cfg else "N/A"
        print(f"  • Google OAuth Client: {'CONFIGURED (Project: ' + proj_id + ')' if has_client_cfg else 'NOT IMPORTED'}")

        yt_tokens = vault.get_json("google", "oauth_tokens")
        has_yt_tokens = yt_tokens is not None
        print(f"  • YouTube User OAuth:  {'STORED (Refresh token active)' if has_yt_tokens else 'NOT AUTHORIZED'}")

        yt_channel = vault.get_json("google", "verified_channel")
        if yt_channel:
            ch_title = yt_channel.get("title", "Unknown")
            ch_id = yt_channel.get("id", "Unknown")
            ch_subs = yt_channel.get("subscriber_count", 0)
            print(f"  • Verified Channel:    VERIFIED ({ch_title} | ID: {ch_id} | {ch_subs:,} subs)")
        else:
            print("  • Verified Channel:    NOT VERIFIED (Connect YouTube Channel required)")

        # Gemini
        gemini_key = vault.get_secret("gemini", "api_key") or os.getenv("GEMINI_API_KEY")
        print(f"  • Gemini AI Key:       {'CONFIGURED' if gemini_key else 'NOT CONFIGURED (Using heuristic fallback)'}")

        # Meta
        meta_token = vault.get_secret("meta", "page_token")
        meta_app = vault.get_secret("meta", "app_id")
        meta_ig = vault.get_secret("meta", "ig_account_id")
        print(f"  • Meta App ID:         {'CONFIGURED' if meta_app else 'NOT CONFIGURED'}")
        print(f"  • Meta Page Token:     {'STORED' if meta_token else 'NOT CONFIGURED'}")
        print(f"  • Instagram Business:  {'CONFIGURED (' + meta_ig + ')' if meta_ig else 'NOT CONFIGURED'}")
        
    except Exception as e:
        print(f"  • Secure Vault:        [!] ERROR: {e}")

    print()
    print("=" * 70)
    print("Diagnostics complete.")
    print("=" * 70)

if __name__ == "__main__":
    main()
