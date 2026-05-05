from app import app
from models import db, Experiment, MassSpecSample, MassSpecAcquisition

with app.app_context():
    samples = MassSpecSample.query.all()
    updated = 0
    for sample in samples:
        first_acq = (
            MassSpecAcquisition.query
            .filter_by(sample_id=sample.id)
            .order_by(MassSpecAcquisition.date.asc(), MassSpecAcquisition.filename.asc())
            .first()
        )
        if first_acq and first_acq.user_initials:
            sample.user_initials = first_acq.user_initials
            updated += 1
    db.session.commit()
    print(f"Updated {updated} of {len(samples)} samples.")

    experiments = Experiment.query.all()
    updated = 0
    for experiment in experiments:
        first_sample = (
            MassSpecSample.query
            .filter_by(experiment_id=experiment.id)
            .order_by(MassSpecSample.id.asc())
            .first()
        )
        if first_sample and first_sample.user_initials:
            experiment.user_initials = first_sample.user_initials
            updated += 1
    db.session.commit()
    print(f"Updated {updated} of {len(experiments)} experiments.")
