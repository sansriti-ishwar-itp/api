from fastapi import FastAPI

from app.api.routers.servers import router as servers_router

app = FastAPI(
    title="OpenStack VM Lifecycle API",
    version="0.1.0",
)

app.include_router(servers_router)

