import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.database.session import SessionLocal, engine, Base
from backend.app.models.schema import (
    SourceVideo, SourceAuthorization, ProcessingJob, DetectedHighlight,
    GeneratedClip, ClipMetadata, PublishingJob, PlatformUpload
)

def test_database_models():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        # 1. Create source video
        video = SourceVideo(
            url="https://youtube.com/watch?v=sample123",
            title="Apex Legends Clutch Win",
            duration=120.0,
            width=1920,
            height=1080,
            fps=60.0,
            local_path="data/input/test_apex.mp4",
            source_type="youtube",
            author="GamerPro",
            status="ready"
        )
        db.add(video)
        db.flush()

        # 2. Authorization
        auth = SourceAuthorization(
            source_video_id=video.id,
            auth_type="creator_authorized",
            authorized_by="GamerPro"
        )
        db.add(auth)

        # 3. Processing job
        job = ProcessingJob(
            source_video_id=video.id,
            job_type="full_pipeline",
            status="completed",
            progress_pct=100
        )
        db.add(job)
        db.flush()

        # 4. Detected Highlight
        hl = DetectedHighlight(
            source_video_id=video.id,
            start_sec=10.0,
            end_sec=70.0,
            duration_sec=60.0,
            score=95.5,
            event_type="Clutch 1v3 Victory",
            explanation="Insane final circle squad wipe."
        )
        db.add(hl)
        db.flush()

        # 5. Generated Clip
        clip = GeneratedClip(
            job_id=job.id,
            highlight_id=hl.id,
            output_path="data/output/test_apex_clip.mp4",
            duration_sec=60.0,
            width=1080,
            height=1920,
            layout_template="blurred_bg",
            quality_score=92.0,
            validation_status="passed"
        )
        db.add(clip)
        db.flush()

        # 6. Clip Metadata
        meta = ClipMetadata(
            clip_id=clip.id,
            title="UNREAL 1v3 APEX CLUTCH #Shorts",
            topic="Apex Legends",
            hashtags="#ApexLegends #Shorts #GamingUncut"
        )
        db.add(meta)

        # 7. Publishing Job
        pub_job = PublishingJob(
            clip_id=clip.id,
            scheduled_time=video.created_at,
            status="pending",
            approval_status="auto_approved"
        )
        db.add(pub_job)
        db.flush()

        # 8. Platform Uploads
        for p in ["youtube", "instagram", "facebook"]:
            upload = PlatformUpload(
                publishing_job_id=pub_job.id,
                platform=p,
                status="pending"
            )
            db.add(upload)

        db.commit()

        # Query and assert
        queried = db.query(SourceVideo).filter_by(id=video.id).first()
        assert queried is not None
        assert len(queried.highlights) >= 1
        assert len(queried.processing_jobs) >= 1
        assert queried.highlights[0].score == 95.5
        assert queried.processing_jobs[0].clips[0].metadata_rel.topic == "Apex Legends"

        # Cleanup test records
        db.delete(queried)
        db.commit()

    print("TEST PASSED: Database schema relational integrity verified.")

if __name__ == "__main__":
    test_database_models()
