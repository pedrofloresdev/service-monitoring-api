from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.status import get_services_status
from app.schemas.status import SystemStatus

NAME = "status"
PREFIX = f"/{NAME}"
router = APIRouter(prefix=PREFIX, tags=[NAME])


@router.get("", response_model=SystemStatus)
def status(db: Session = Depends(get_db)):
    return get_services_status(db)
