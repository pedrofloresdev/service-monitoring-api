import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.db.models.services import Service
from app.db.session import SessionLocal
from app.services.monitor import check_service_by_id

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _job_id(service_id: int) -> str:
    return f"service_{service_id}"


def schedule_service(service: Service) -> None:
    scheduler.add_job(
        check_service_by_id,
        trigger="interval",
        seconds=service.check_interval,
        id=_job_id(service.id),
        args=[service.id],
        replace_existing=True,
    )
    logger.info(
        "Scheduled service %d (%s) every %ds", service.id, service.name, service.check_interval
    )


def unschedule_service(service_id: int) -> None:
    job_id = _job_id(service_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info("Unscheduled service %d", service_id)


def schedule_first_check(service_id: int) -> None:
    """One-off immediate check, exempt from the lateness cutoff (spec 003a RF-8).

    misfire_grace_time=None means "run no matter how late", so it is never skipped while it
    waits for a free worker. Interval jobs keep the scheduler defaults (RNF-1).
    """
    scheduler.add_job(
        check_service_by_id,
        trigger="date",
        run_date=datetime.now(UTC),
        id=f"first_check_{service_id}",
        args=[service_id],
        misfire_grace_time=None,
        replace_existing=True,
    )


def start_scheduler(first_check_ids: Sequence[int] = ()) -> None:
    db = SessionLocal()
    try:
        active_services = db.query(Service).filter(Service.is_active).all()
        for service in active_services:
            schedule_service(service)
        logger.info("Scheduler started with %d active service(s)", len(active_services))
    finally:
        db.close()

    for service_id in first_check_ids:
        schedule_first_check(service_id)
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
