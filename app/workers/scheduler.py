import logging
from apscheduler.schedulers.background import BackgroundScheduler
from app.services.monitor import check_service_by_id
from app.db.session import SessionLocal
from app.db.models.services import Service

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
    logger.info("Scheduled service %d (%s) every %ds", service.id, service.name, service.check_interval)


def unschedule_service(service_id: int) -> None:
    job_id = _job_id(service_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info("Unscheduled service %d", service_id)


def start_scheduler() -> None:
    db = SessionLocal()
    try:
        active_services = db.query(Service).filter(Service.is_active).all()
        for service in active_services:
            schedule_service(service)
        logger.info("Scheduler started with %d active service(s)", len(active_services))
    finally:
        db.close()

    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
