from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models.services import Service
from app.schemas.services import ServiceCreate

NAME = "services"
PREFIX = f"/{NAME}"
router = APIRouter(prefix=PREFIX, tags=[NAME])


@router.post("")
def create_service(service: ServiceCreate, db: Session = Depends(get_db)):
    new_service = Service(**service.model_dump())
    db.add(new_service)
    db.commit()
    db.refresh(new_service)
    return new_service


@router.get("")
def get_services(service_id: int, db: Session = Depends(get_db)):
    services = db.query(Service).filter(Service.id == service_id).all()
    return services


@router.get("/{service_id}")
def get_service(service_id: int, db: Session = Depends(get_db)):
    service = db.query(Service).filter(Service.id == service_id).first()
    return service
