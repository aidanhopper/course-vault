from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vault import __version__
from vault.api.router import api_v1_router
from vault.config import settings
from vault.db.session import init_db
from vault.workers.queue import job_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize SQLite database tables
    await init_db()
    # Start background job runner queue
    job_queue.start()
    yield
    # Shutdown: gracefully stop worker
    await job_queue.stop()


app = FastAPI(
    title="Course Resource Vault",
    description="A private, headless repository for educational knowledge capture, video streaming, and materials management.",
    version=__version__,
    lifespan=lifespan,
)

# Enable CORS for web study clients and local apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API V1 routes
app.include_router(api_v1_router)


@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "ok",
        "version": __version__,
        "media_dir": str(settings.MEDIA_DIR),
    }


@app.get("/", tags=["System"])
async def root():
    return {
        "name": "Course Resource Vault",
        "version": __version__,
        "docs_url": "/docs",
        "api_v1": "/api/v1",
    }


def run():
    """CLI entrypoint for running the server with uvicorn."""
    import uvicorn
    uvicorn.run(
        "vault.main:app",
        host=settings.VAULT_HOST,
        port=settings.VAULT_PORT,
        reload=False,
    )


if __name__ == "__main__":
    run()
