from datetime import timedelta
from sqlalchemy import select
from app.models import CheckIn, CheckInRevision, utcnow


def load_revisions(db, user_id):
    # Two-year personal evaluation window, bounded revision count; assessments
    # and account identity are deliberately not predictive features.
    columns = [CheckIn.user_id, CheckIn.observation_date, CheckIn.timezone,
        CheckInRevision.revision, CheckInRevision.questionnaire_version, CheckInRevision.engine_version, CheckInRevision.recorded_at, CheckInRevision.retrospective,
        CheckInRevision.sleep_hours, CheckInRevision.academic_load, CheckInRevision.screen_hours,
        CheckInRevision.extracurricular_load, CheckInRevision.deadline_pressure, CheckInRevision.recovery, CheckInRevision.reported_strain]
    rows = db.execute(select(*columns).join(CheckInRevision, CheckInRevision.checkin_id == CheckIn.id)
        .where(CheckIn.user_id == user_id, CheckIn.observation_date >= utcnow().date()-timedelta(days=730))
        .order_by(CheckIn.observation_date, CheckInRevision.revision).limit(10001)).mappings().all()
    if len(rows) > 10000:
        raise ValueError('Personal revision window exceeds evaluation limit')
    return [dict(row) for row in rows]
