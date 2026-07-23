"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router
from backend.agent.graph import build_graph
from backend.agent.memory import (
    ensure_data_directories,
    ensure_user_memory_db,
    ensure_vectorstore,
)
from backend.config import settings
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepare SQLite memory, Chroma, and the LangGraph runtime."""

    ensure_data_directories()
    await ensure_user_memory_db(settings.sqlite_db_path)
    await ensure_vectorstore()

    async with AsyncSqliteSaver.from_conn_string(settings.sqlite_db_path) as checkpointer:
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        app.state.graph = build_graph(checkpointer)
        yield


app = FastAPI(title="Enterprise Knowledge Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.frontend_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=True,
    )
