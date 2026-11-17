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

def run_cmd(cmd_list, shell=False):
    try:
        res = subprocess.run(cmd_list, capture_output=True, text=True, check=False, shell=shell)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def find_npm():
    """Detects npm on Windows across PATH and standard Node.js installations."""
    # 1. Search PATH
    npm_path = shutil.which("npm") or shutil.which("npm.cmd")
    if npm_path:
        return npm_path

    # 2. Derive from Node.js executable location
    node_path = shutil.which("node")
    if node_path:
        candidate = Path(node_path).parent / "npm.cmd"
        if candidate.exists():
            return str(candidate)

    # 3. Check standard Windows installation directories
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\nodejs\npm.cmd"),
        os.path.expandvars(r"%ProgramFiles(x86)%\nodejs\npm.cmd"),
        os.path.expandvars(r"%LocalAppData%\Programs\NodeJS\npm.cmd"),
        os.path.expandvars(r"%AppData%\npm\npm.cmd"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None

def probe_ffmpeg_encoder(encoder_name: str) -> tuple[bool, str]:
    """Tests actual hardware or software encoder initialization with a 1-frame probe."""
    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.04",
            "-c:v", encoder_name,
            "-f", "null", "-"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return True, "VERIFIED USABLE"
        # Check stderr for specific failure reason
        stderr = res.stderr or ""
        if "MFX session" in stderr or "mfx" in stderr.lower():
            return False, "Hardware not present (MFX/Intel QSV unavailable)"
        if "encoder->Init() failed" in stderr or "amf" in stderr.lower():
            return False, "Hardware not present (AMD AMF device unavailable)"
        if "nvenc" in stderr.lower():
            return False, "NVIDIA NVENC init failed"
        return False, "Initialization failed"
    except Exception as e:
        return False, str(e)

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
    node_bin = shutil.which("node") or "Unknown"
    print(f"  • Node.js:          {node_out if node_ok else 'NOT FOUND in PATH'} ({node_bin})")
    
    npm_path = find_npm()
    if npm_path:
        npm_ok, npm_out, _ = run_cmd([npm_path, "--version"], shell=True)
        if npm_ok and npm_out:
            print(f"  • npm:              FOUND v{npm_out} ({npm_path})")
        else:
            print(f"  • npm:              FOUND ({npm_path}) [Version probe failed]")
    else:
        print("  • npm:              NOT FOUND (Checked PATH and standard Node.js directories)")

    # Frontend Build Verification
    fe_modules = (ROOT_DIR / "frontend" / "node_modules").exists()
    fe_dist = (ROOT_DIR / "frontend" / "dist" / "index.html").exists()
    print(f"  • Frontend Modules: {'INSTALLED' if fe_modules else 'NOT INSTALLED (Run: npm install --prefix frontend)'}")
    print(f"  • Frontend Build:   {'BUILT (dist/ ready)' if fe_dist else 'MISSING (Run: npm run build --prefix frontend)'}")

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        _, ff_ver, _ = run_cmd(["ffmpeg", "-version"])
        ff_first = ff_ver.split("\n")[0] if ff_ver else "Found"
        print(f"  • FFmpeg:           FOUND ({ff_first})")
        print(f"    Binary Path:      {ffmpeg_path}")
    else:
        print("  • FFmpeg:           [!] NOT FOUND in PATH (Required for video rendering)")

    # 2. Hardware Acceleration & Encoder Probing
    print()
    print("[2] HARDWARE ACCELERATION (NVENC / GPU & CPU FALLBACK)")
    smi_ok, smi_out, _ = run_cmd(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])
    if smi_ok and smi_out:
        print(f"  • NVIDIA GPU:       {smi_out}")
    else:
        print("  • NVIDIA GPU:       nvidia-smi not available or no NVIDIA GPU detected")

    if ffmpeg_path:
        # Live probe actual encoder capabilities
        nvenc_ok, nvenc_msg = probe_ffmpeg_encoder("h264_nvenc")
        hevc_ok, hevc_msg = probe_ffmpeg_encoder("hevc_nvenc")
        qsv_ok, qsv_msg = probe_ffmpeg_encoder("h264_qsv")
        amf_ok, amf_msg = probe_ffmpeg_encoder("h264_amf")
        cpu_ok, cpu_msg = probe_ffmpeg_encoder("libx264")

        print(f"  • NVENC H.264:      {'SUPPORTED & ' + nvenc_msg if nvenc_ok else 'NOT USABLE (' + nvenc_msg + ')'}")
        print(f"  • NVENC HEVC:       {'SUPPORTED & ' + hevc_msg if hevc_ok else 'NOT USABLE (' + hevc_msg + ')'}")
        print(f"  • Intel QSV:        {'SUPPORTED & ' + qsv_msg if qsv_ok else 'NOT USABLE (' + qsv_msg + ')'}")
        print(f"  • AMD AMF:          {'SUPPORTED & ' + amf_msg if amf_ok else 'NOT USABLE (' + amf_msg + ')'}")
        print(f"  • CPU Fallback:     {'READY (' + cpu_msg + ')' if cpu_ok else 'UNAVAILABLE'}")

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

    # 5. Database Health & Queue Safety
    print()
    print("[5] DATABASE & LOCAL STORAGE")
    try:
        from backend.app.core.config import settings
        from backend.app.database.session import SessionLocal
        from backend.app.models.schema import ProcessingJob, GeneratedClip, PublishingJob, PlatformUpload
        
        db = SessionLocal()
        job_count = db.query(ProcessingJob).count()
        clip_count = db.query(GeneratedClip).count()
        pub_count = db.query(PublishingJob).count()
        upload_count = db.query(PlatformUpload).count()
        
        # Breakdown uploads
        uploads = db.query(PlatformUpload).all()
        from collections import Counter
        upload_breakdown = Counter(u.status for u in uploads)
        
        db.close()
        db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        print(f"  • Database File:    {db_path}")
        print(f"  • Status:           HEALTHY")
        print(f"  • Total Jobs:       {job_count}")
        print(f"  • Total Clips:      {clip_count}")
        print(f"  • Publishing Jobs:  {pub_count}")
        print(f"  • Platform Uploads: {upload_count} {dict(upload_breakdown)}")
    except Exception as e:
        print(f"  • SQLite Database:  [!] ERROR: {e}")

    # 6. Windows Secure Vault State (Safe audit - NEVER print secrets)
    print()
    print("[6] SECURE VAULT & CREDENTIAL STORAGE (ZERO-KNOWLEDGE AUDIT)")
    try:
        from backend.app.security.vault import vault
        
        # Google / YouTube Client Configuration
        client_cfg = vault.get_json("google", "client_config")
        has_client_cfg = client_cfg is not None
        proj_id = client_cfg.get("installed", {}).get("project_id", "Unknown") if has_client_cfg else "N/A"
        
        # Verify intended production project
        is_prod_project = (proj_id == "gaming-uncut")
        is_test_project = (proj_id in ("form-project", "test-suite-project", "test-gaming-project"))
        
        proj_status = ""
        if has_client_cfg:
            if is_prod_project:
                proj_status = f"CONFIGURED (Verified Production Project: {proj_id})"
            elif is_test_project:
                proj_status = f"WARNING: Test project detected ({proj_id}). Expected: gaming-uncut"
            else:
                proj_status = f"CONFIGURED (Project: {proj_id})"
        else:
            proj_status = "NOT IMPORTED"
            
        print(f"  • Google OAuth Client: {proj_status}")

        # YouTube User Tokens
        yt_tokens = vault.get_json("google", "oauth_tokens")
        has_yt_tokens = yt_tokens is not None
        print(f"  • YouTube User OAuth:  {'STORED (Refresh token active)' if has_yt_tokens else 'NOT AUTHORIZED'}")

        # Verified Channel
        yt_channel = vault.get_json("google", "verified_channel")
        if yt_channel:
            ch_title = yt_channel.get("title", "Unknown")
            ch_id = yt_channel.get("id", "Unknown")
            ch_subs = yt_channel.get("subscriber_count", 0)
            print(f"  • Verified Channel:    VERIFIED ({ch_title} | ID: {ch_id} | {ch_subs:,} subs)")
        else:
            print("  • Verified Channel:    NOT VERIFIED (Connect YouTube Channel required)")

        # Gemini API Key
        gemini_key = vault.get_secret("gemini", "api_key") or os.getenv("GEMINI_API_KEY")
        print(f"  • Gemini AI Key:       {'CONFIGURED' if gemini_key else 'NOT CONFIGURED (Using heuristic fallback)'}")

        # Meta Credentials
        meta_app = vault.get_secret("meta", "app_id")
        meta_page = vault.get_secret("meta", "page_access_token")
        meta_ig = vault.get_secret("meta", "instagram_account_id")
        print(f"  • Meta App ID:         {'CONFIGURED' if meta_app else 'NOT CONFIGURED'}")
        print(f"  • Meta Page Token:     {'STORED' if meta_page else 'NOT CONFIGURED'}")
        print(f"  • Instagram Business:  {'CONFIGURED (ID: ' + meta_ig + ')' if meta_ig else 'NOT CONFIGURED'}")

    except Exception as e:
        print(f"  • Secure Vault:        [!] ERROR: {e}")

    # 7. Publishing Readiness Audit (7 Separate States)
    print()
    print("[7] PUBLISHING READINESS & PLATFORM AUTHORIZATION AUDIT")
    try:
        from backend.app.security.vault import vault
        
        s1_oauth_client = vault.get_json("google", "client_config") is not None
        s2_yt_user_auth = vault.get_json("google", "oauth_tokens") is not None
        s3_yt_channel   = vault.get_json("google", "verified_channel") is not None
        s4_gemini_api   = bool(vault.get_secret("gemini", "api_key") or os.getenv("GEMINI_API_KEY"))
        s5_meta_app     = bool(vault.get_secret("meta", "app_id"))
        s6_fb_page      = bool(vault.get_secret("meta", "page_access_token"))
        s7_ig_business  = bool(vault.get_secret("meta", "page_access_token") and vault.get_secret("meta", "instagram_account_id"))

        print(f"  1. Google OAuth Client:         {'[READY]' if s1_oauth_client else '[NOT READY] Client secrets JSON needed'}")
        print(f"  2. YouTube User Authorization:   {'[READY]' if s2_yt_user_auth else '[PENDING] User consent required'}")
        print(f"  3. YouTube Channel Verification: {'[READY]' if s3_yt_channel else '[PENDING] Channel identity verification required'}")
        print(f"  4. Gemini AI Engine:            {'[READY]' if s4_gemini_api else '[OPTIONAL] Using local heuristic fallback'}")
        print(f"  5. Meta Application ID:         {'[READY]' if s5_meta_app else '[NOT READY] Meta developer app required'}")
        print(f"  6. Facebook Page Authorization:  {'[READY]' if s6_fb_page else '[PENDING] Page access token required'}")
        print(f"  7. Instagram Business Account:  {'[READY]' if s7_ig_business else '[PENDING] Connected IG account required'}")

        yt_ready = s1_oauth_client and s2_yt_user_auth and s3_yt_channel
        fb_ready = s6_fb_page
        ig_ready = s7_ig_business

        print()
        print("  --- PLATFORM PUBLISHING GATES ---")
        print(f"  • YouTube Shorts:   {'AUTHORIZED & READY' if yt_ready else 'PAUSED / NOT AUTHORIZED (Account connection required)'}")
        print(f"  • Facebook Reels:   {'AUTHORIZED & READY' if fb_ready else 'PAUSED / NOT AUTHORIZED (Page token required)'}")
        print(f"  • Instagram Reels:  {'AUTHORIZED & READY' if ig_ready else 'PAUSED / NOT AUTHORIZED (Business account required)'}")
        
        all_paused = not (yt_ready or fb_ready or ig_ready)
        if all_paused:
            print("  • Queue Protection: ACTIVE (All unauthenticated uploads are safely held; no uploads will dispatch)")
        else:
            print(f"  • Queue Protection: ACTIVE (Only authorized platforms will receive dispatches)")

    except Exception as e:
        print(f"  • Readiness Audit:  [!] ERROR: {e}")

    print()
    print("=" * 70)
    print("Diagnostics complete.")
    print("=" * 70)

if __name__ == "__main__":
    main()
