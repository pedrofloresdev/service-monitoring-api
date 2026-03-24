from apscheduler.schedulers.background import BackgroundScheduler
from app.services.monitor import check_all_services
from app.core.config import SCHEDULER_INTERVAL_SECONDS

scheduler = BackgroundScheduler()


def start_scheduler():
    scheduler.add_job(check_all_services, "interval", seconds=SCHEDULER_INTERVAL_SECONDS)
    scheduler.start()
