from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models.services import Service
from app.schemas.services import ServiceCreate, ServiceUpdate, ServiceResponse
from app.workers.scheduler import schedule_service, unschedule_service

NAME = "services"
PREFIX = f"/{NAME}"
router = APIRouter(prefix=PREFIX, tags=[NAME])


@router.post("", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(payload: ServiceCreate, db: Session = Depends(get_db)):
    existing = db.query(Service).filter(Service.url == str(payload.url)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A service with this URL already exists")

    new_service = Service(**payload.model_dump())
    db.add(new_service)
    db.commit()
    db.refresh(new_service)
    schedule_service(new_service)
    return new_service


@router.get("", response_model=list[ServiceResponse])
def list_services(active_only: bool = False, db: Session = Depends(get_db)):
    query = db.query(Service)
    if active_only:
        query = query.filter(Service.is_active)
    return query.order_by(Service.created_at.desc()).all()


@router.get("/{service_id}", response_model=ServiceResponse)
def get_service(service_id: int, db: Session = Depends(get_db)):
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


@router.patch("/{service_id}", response_model=ServiceResponse)
def update_service(service_id: int, payload: ServiceUpdate, db: Session = Depends(get_db)):
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    updates = payload.model_dump(exclude_unset=True)
    if "url" in updates:
        updates["url"] = str(updates["url"])

    for field, value in updates.items():
        setattr(service, field, value)

    db.commit()
    db.refresh(service)

    if service.is_active:
        schedule_service(service)
    else:
        unschedule_service(service.id)

    return service


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: int, db: Session = Depends(get_db)):
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    unschedule_service(service.id)
    db.delete(service)
    db.commit()
