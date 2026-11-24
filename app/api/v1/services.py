from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.models.services import Service
from app.db.session import get_db
from app.schemas.services import ServiceCreate, ServiceResponse, ServiceUpdate
from app.services import service_writes
from app.workers.scheduler import schedule_service, unschedule_service

NAME = "services"
PREFIX = f"/{NAME}"
router = APIRouter(prefix=PREFIX, tags=[NAME])


@router.post("", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(payload: ServiceCreate, db: Session = Depends(get_db)) -> Service:
    try:
        service = service_writes.create_service(db, payload)
    except service_writes.DuplicateUrl:
        raise _duplicate() from None
    schedule_service(service)
    return service


@router.get("", response_model=list[ServiceResponse])
def list_services(active_only: bool = False, db: Session = Depends(get_db)) -> list[Service]:
    query = db.query(Service)
    if active_only:
        query = query.filter(Service.is_active)
    return query.order_by(Service.created_at.desc()).all()


@router.get("/{service_id}", response_model=ServiceResponse)
def get_service(service_id: int, db: Session = Depends(get_db)) -> Service:
    try:
        return service_writes.get_service_or_404(db, service_id)
    except service_writes.ServiceNotFound:
        raise _not_found() from None


@router.patch("/{service_id}", response_model=ServiceResponse)
def update_service(
    service_id: int, payload: ServiceUpdate, db: Session = Depends(get_db)
) -> Service:
    try:
        service = service_writes.update_service(db, service_id, payload)
    except service_writes.ServiceNotFound:
        raise _not_found() from None
    except service_writes.DuplicateUrl:
        raise _duplicate() from None
    # Only after a successful commit (spec 003a RF-16).
    if service.is_active:
        schedule_service(service)
    else:
        unschedule_service(service.id)
    return service


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: int, db: Session = Depends(get_db)) -> None:
    try:
        service_writes.delete_service(db, service_id)
    except service_writes.ServiceNotFound:
        raise _not_found() from None
    unschedule_service(service_id)  # after the commit, so a failed delete keeps its schedule


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")


def _duplicate() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail="A service with this URL already exists"
    )
