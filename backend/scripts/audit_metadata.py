from backend.app.database.session import SessionLocal
from backend.app.models.schema import GeneratedClip, ClipMetadata, ProcessingJob, SourceVideo

def audit():
    db = SessionLocal()
    clips = db.query(GeneratedClip).order_by(GeneratedClip.id.asc()).all()
    print(f"Total Generated Clips in DB: {len(clips)}")
    for c in clips:
        m = c.metadata_rel
        job = c.job
        src = job.source_video if job else None
        print(f"--- Clip #{c.id} (Job #{c.job_id}) ---")
        print(f"  Source Title: {src.title if src else 'None'}")
        print(f"  Source Path: {src.local_path if src else 'None'}")
        print(f"  Output Path: {c.output_path}")
        if m:
            print(f"  Metadata ID: {m.id}")
            print(f"  Title: {m.title}")
            print(f"  Description: {m.description}")
            print(f"  Hashtags: {m.hashtags}")
            print(f"  Detected Game: {m.detected_game} ({m.game_confidence})")
            print(f"  Analysis Provider: {m.analysis_provider}")
            print(f"  Analysis Status: {m.analysis_status}")
            print(f"  Fingerprint: {m.analysis_fingerprint}")
        else:
            print("  NO METADATA RECORD ATTACHED!")

if __name__ == "__main__":
    audit()
