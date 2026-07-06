"""Dashboard API application (spec ch. 9).

FastAPI app that performs zero scientific reasoning (9.29): every route
reads structured output already produced by the other engines and organizes
it into human-readable JSON. The frontend (dashboard/frontend) is a static
single page consuming this API.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from elecous.bootstrap import ElecousStack, build_stack
from elecous.dashboard.api.routes import router
from elecous.infrastructure.config import active_config


def create_app(stack: ElecousStack | None = None) -> FastAPI:
    app = FastAPI(title="ELECOUS Dashboard", version="1.0.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.state.stack = stack or build_stack()
    app.include_router(router, prefix="/api")

    import pathlib

    frontend_dir = pathlib.Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    return app


def main() -> None:
    import uvicorn

    from elecous.scheduler.registration import register_all_schedules

    config = active_config()
    app = create_app()
    # Live Autonomy directive: the dashboard entry point is a full living
    # instance, not a passive viewer -- previously only `elecous run`
    # registered the 38 scheduled tasks, so a dashboard-only process
    # honestly reported "nothing running / none scheduled" forever.
    stack = app.state.stack
    register_all_schedules(stack)
    stack.scheduler.start()
    try:
        uvicorn.run(app, host=config.get("dashboard.host", "127.0.0.1"), port=int(config.get("dashboard.port", 8321)))
    finally:
        stack.scheduler.stop()


if __name__ == "__main__":
    main()
