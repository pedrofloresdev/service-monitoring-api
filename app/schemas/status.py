from pydantic import BaseModel


class ServiceStatusEntry(BaseModel):
    id: int
    name: str
    url: str
    status: str
    avg_response_time_ms: float
    uptime_last_24h: float


class SystemStatus(BaseModel):
    total_services: int
    up: int
    down: int
    services: list[ServiceStatusEntry]
