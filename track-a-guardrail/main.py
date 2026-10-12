from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .approvals import router as approvals_router
from .audit import router as audit_router
from .auth import router as auth_router
from .permissions import router as permissions_router
from .registry import router as registry_router
from .tracing import router as tracing_router
from .analytics import router as analytics_router
from .dashboard import router as dashboard_router


app = FastAPI(
    title="Agent Sandbox — Guardrail Service",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(registry_router)
app.include_router(auth_router)
app.include_router(permissions_router)
app.include_router(approvals_router)
app.include_router(audit_router)
app.include_router(tracing_router)
app.include_router(analytics_router)
app.include_router(dashboard_router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "track-a-guardrail.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )