from fastapi import FastAPI

from app.api.routers.servers import router as servers_router

app = FastAPI(
    title="OpenStack VM Lifecycle API",
    version="0.1.0",
    description=(
        "Protected routes need a Keystone token (Bearer in the Authorization header). "
        "Get a token with `openstack token issue -f value -c id` after sourcing your openrc. "
        "On /docs, click Authorize and paste the token value only (Swagger adds Bearer)."
    ),
)

# Mount API routers
app.include_router(servers_router)


@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
)
def health() -> dict[str, str]:
    # Deliberately does not touch OpenStack/auth dependencies.
    return {"status": "ok"}

