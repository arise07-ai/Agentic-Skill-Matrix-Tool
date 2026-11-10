import logging
import re
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db
from .config import get_settings
from .deps import current_user, staff_user
from .agents import proposals, runtime as agent_runtime
from .routers import agents as agents_router
from .routers import auth as auth_router
from .routers import assignments as assignments_router, data, member, projects, roles as roles_router, uploads
from .services import auth, repo, standardization
from .ai import vectorstore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("skillmatrix")


def _warm_vectors() -> None:
    try:
        standardization.ensure_taxonomy_indexed()
        log.info("Taxonomy vector index ready")
        categories_changed = standardization.consume_category_sync_change()
        employee_count = db.one("SELECT COUNT(*) AS n FROM employees")["n"]
        indexed_parents = vectorstore.count_where("employees", {"entity_type": "employee_parent"})
        employee_vectors = vectorstore.count("employees")
        if categories_changed or indexed_parents != employee_count or (employee_count == 0 and employee_vectors > 0):
            indexed = repo.reindex_employees()
            log.info("Rebuilt entity-based employee vector index for %d employee(s)", indexed)
    except Exception:  # noqa: BLE001  never take the API down
        log.exception("Vector indexing failed; semantic retrieval is unavailable until it is fixed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()
    db.init_db()
    standardization.index()  # seeds the taxonomy table: local and fast
    log.info("Login: %s", auth.bootstrap_admin())
    n = agent_runtime.fail_orphaned_runs()
    if n:
        log.info("Marked %d interrupted agent run(s) as failed", n)
    n = proposals.release_interrupted_decisions()
    if n:
        log.info("Returned %d interrupted proposal decision(s) to the approval queue", n)
    # Vector indexing may call the embeddings API / Chroma and can be slow or hang (bad network, first-run
    # downloads). Run it in the background so the server starts listening immediately.
    threading.Thread(target=_warm_vectors, name="taxonomy-warmup", daemon=True).start()
    log.info("Skill Matrix API ready; vector indexing continues in the background")
    yield


app = FastAPI(title="Skill Matrix API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Every unexpected error is logged in full under a short reference. People see a plain message with that reference (so it
    can be found in the server log); only an admin also sees the error text, with key-like text masked."""
    ref = secrets.token_hex(3).upper()
    log.exception("Unhandled error [ref %s] on %s %s", ref, request.method, request.url.path)
    message = f"Something went wrong on our side. Reference: {ref}"
    try:  # the error may come from the database itself, so failing to identify the user just means "not an admin"
        viewer, _ = auth.session_from_token(request.cookies.get(auth.COOKIE))
    except Exception:  # noqa: BLE001
        viewer = None
    if viewer and viewer["role"] == "admin":
        text = re.sub(r"(Incorrect API key provided: )\S+", r"\1***", f"{type(exc).__name__}: {exc}")
        text = re.sub(r"(sk|tvly|lsv2)[-_][A-Za-z0-9_\-]{6,}", r"\1-***", text)
        message += f" ({text})"
    return JSONResponse(status_code=500, content={"detail": message})




@app.get("/api/health")
def health():
    return {"status": "ok"}


protected = [Depends(current_user)]  # every API route below needs a logged-in user
staff = [Depends(staff_user)]  # ... and these are closed to members (a member only has /api/me, their own learning plan and their own approvals)
app.include_router(auth_router.router)
app.include_router(uploads.router, dependencies=staff)
app.include_router(data.router, dependencies=staff)
app.include_router(projects.router, dependencies=protected)  # the /projects routes are staff-only inside; the learning routes check the employee
app.include_router(agents_router.router, dependencies=protected)  # the run/brief routes are staff-only inside
app.include_router(roles_router.router, dependencies=staff)
app.include_router(assignments_router.router, dependencies=staff)
app.include_router(member.router, dependencies=protected)
