from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import (
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from adapters.github.client import GitHubAdapterError
from adapters.hermes.client import HermesAdapterError
from apps.api.schemas import (
    AccountCreateRequest,
    ApprovalDecisionRequest,
    FeedbackCreate,
    GitHubConnectionCreate,
    LoginRequest,
    PersonaConversationCreate,
    PersonaCreate,
    PersonaExportRequest,
    PersonaImportRequest,
    PersonaMemoryRelationCreate,
    PersonaMemoryReviewRequest,
    PersonaMemoryUpdateRequest,
    PersonaModelPolicyUpdateRequest,
    PersonaQuestionCreate,
    PreferenceReviewRequest,
    ProjectMaintenanceTaskCreate,
    ReauthenticationRequest,
    TaskCancellationRequest,
)
from core.bootstrap import Container, build_container
from core.retrieval.answering import CitationValidationError
from core.security.access import AccessContext
from core.security.authentication import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    InvalidCredentialsError,
    InvalidCsrfError,
    InvalidSessionError,
    RecentReauthenticationRequired,
    SessionGrant,
    SessionPrincipal,
)
from core.security.data_policy import ModelDataPolicyError
from core.services.github_connections import GitHubAppNotConfiguredError
from core.services.project_maintenance import (
    ProjectMaintenanceCommand,
    TaskConflictError,
    TaskExecutionFailed,
)
from core.version import VERSION


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    app = FastAPI(
        title="PersonaOS",
        version=VERSION,
        description=(
            "Evidence-driven digital employee and review-first persona memory "
            "with versioned edits, model data boundaries and auditable deletion."
        ),
    )
    app.state.container = container

    public_api_paths = {
        "/api/v1/auth/login",
        "/api/v1/auth/status",
    }

    def request_id(request: Request) -> str | None:
        request_id = (request.headers.get("X-Request-ID") or "").strip()
        if len(request_id) > 100 or any(
            character in request_id for character in "\r\n\t"
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Request-ID is invalid",
            )
        return request_id or None

    def current_principal(request: Request) -> SessionPrincipal:
        principal = getattr(request.state, "principal", None)
        if not isinstance(principal, SessionPrincipal):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        return principal

    def persona_access(request: Request) -> AccessContext:
        principal = current_principal(request)
        return AccessContext(
            owner_id=principal.account_id,
            actor_id=principal.account_id,
            actor_type="local_account",
            request_id=getattr(request.state, "request_id", None),
        )

    def require_account_path(request: Request, user_id: str) -> str:
        account_id = current_principal(request).account_id
        if user_id != account_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"UserRecord not found: {user_id}",
            )
        return account_id

    def require_recent(
        request: Request,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        container.authentication.require_recent(
            current_principal(request),
            request_id=getattr(request.state, "request_id", None),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    def set_session_cookie(response: Response, grant: SessionGrant) -> None:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=grant.raw_token,
            path="/",
            secure=container.settings.persona_cookie_secure,
            httponly=True,
            samesite="strict",
        )

    def clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            path="/",
            secure=container.settings.persona_cookie_secure,
            httponly=True,
            samesite="strict",
        )

    def validate_request_origin(request: Request) -> None:
        origin = (request.headers.get("Origin") or "").strip()
        if not origin:
            return
        parsed = urlsplit(origin)
        forwarded_proto = (
            request.headers.get("X-Forwarded-Proto") or request.url.scheme
        ).split(",", maxsplit=1)[0].strip()
        expected = f"{forwarded_proto}://{request.headers.get('Host', '')}"
        actual = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.netloc and not parsed.path
            else ""
        )
        if actual != expected:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="request origin is not allowed",
            )

    @app.middleware("http")
    async def authenticate_request(request: Request, call_next):
        try:
            request.state.request_id = request_id(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        path = request.url.path
        protected = path.startswith("/api/v1/") and path not in public_api_paths
        if protected:
            if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                try:
                    validate_request_origin(request)
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={
                            "detail": exc.detail,
                            "code": "origin_validation_failed",
                        },
                        headers={"Cache-Control": "no-store"},
                    )
            raw_token = request.cookies.get(SESSION_COOKIE_NAME)
            try:
                principal = container.authentication.authenticate(raw_token)
                if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                    container.authentication.verify_csrf(
                        principal,
                        request.headers.get(CSRF_HEADER_NAME),
                    )
            except InvalidSessionError:
                response = JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "detail": "authentication required",
                        "code": "authentication_required",
                    },
                )
                clear_session_cookie(response)
                response.headers["Cache-Control"] = "no-store"
                return response
            except InvalidCsrfError:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "detail": "CSRF validation failed",
                        "code": "csrf_validation_failed",
                    },
                    headers={"Cache-Control": "no-store"},
                )
            request.state.principal = principal
        response = await call_next(request)
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RecentReauthenticationRequired)
    async def recent_reauthentication_handler(
        _: Request,
        exc: RecentReauthenticationRequired,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            content={
                "detail": str(exc),
                "code": "reauthentication_required",
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(TaskExecutionFailed)
    async def task_failed_handler(
        _: Request,
        exc: TaskExecutionFailed,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "detail": str(exc.cause),
                "task_id": exc.task_id,
                "trace_url": f"/api/v1/tasks/{exc.task_id}",
            },
        )

    @app.exception_handler(ModelDataPolicyError)
    async def model_data_policy_handler(
        _: Request,
        exc: ModelDataPolicyError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": VERSION,
            "runtime": container.settings.runtime_name,
            "github_mode": "read_only",
            "github_auth": (
                "github_app"
                if container.github_connections.enabled
                else "token"
                if container.settings.github_token
                else "anonymous"
            ),
            "api_port": container.settings.api_port,
            "task_timeout_seconds": container.settings.worker_task_timeout_seconds,
            "persona_identity_mode": "trusted_local_accounts",
            "account_setup_required": container.authentication.setup_required(),
            "persona_blob_encryption": "AES-256-GCM",
            "persona_embedding_space_id": (
                container.memory_index.embedding_space_id
            ),
        }

    @app.get("/api/v1/auth/status")
    async def authentication_status() -> dict[str, Any]:
        return {
            "mode": "trusted_local_accounts",
            "setup_required": container.authentication.setup_required(),
            "cookie_secure": container.settings.persona_cookie_secure,
            "local_only": True,
        }

    @app.post("/api/v1/auth/login")
    async def login(
        payload: LoginRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        validate_request_origin(request)
        try:
            grant = container.authentication.login(
                username=payload.username,
                password=payload.password,
                current_raw_token=request.cookies.get(SESSION_COOKIE_NAME),
                request_id=getattr(request.state, "request_id", None),
                user_agent=request.headers.get("User-Agent"),
            )
        except (InvalidCredentialsError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid username or password",
            ) from exc
        set_session_cookie(response, grant)
        return {
            "account": grant.principal.account,
            "session": {
                "id": grant.principal.session_id,
                "idle_expires_at": grant.principal.session[
                    "idle_expires_at"
                ].isoformat(),
                "absolute_expires_at": grant.principal.session[
                    "absolute_expires_at"
                ].isoformat(),
                "reauthenticated_at": grant.principal.session[
                    "reauthenticated_at"
                ].isoformat(),
            },
            "csrf_token": grant.csrf_token,
            "reauthentication_window_seconds": (
                container.authentication.reauthentication_seconds
            ),
        }

    @app.get("/api/v1/auth/session")
    async def get_authenticated_session(request: Request) -> dict[str, Any]:
        principal = current_principal(request)
        return {
            "account": principal.account,
            "session": {
                "id": principal.session_id,
                "idle_expires_at": principal.session[
                    "idle_expires_at"
                ].isoformat(),
                "absolute_expires_at": principal.session[
                    "absolute_expires_at"
                ].isoformat(),
                "reauthenticated_at": principal.session[
                    "reauthenticated_at"
                ].isoformat(),
            },
            "csrf_token": container.authentication.csrf_token(principal),
            "reauthentication_window_seconds": (
                container.authentication.reauthentication_seconds
            ),
        }

    @app.post("/api/v1/auth/reauthenticate")
    async def reauthenticate(
        payload: ReauthenticationRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        try:
            grant = container.authentication.reauthenticate(
                principal=current_principal(request),
                password=payload.password,
                request_id=getattr(request.state, "request_id", None),
            )
        except InvalidCredentialsError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid username or password",
            ) from exc
        set_session_cookie(response, grant)
        return {
            "account": grant.principal.account,
            "session": {
                "id": grant.principal.session_id,
                "idle_expires_at": grant.principal.session[
                    "idle_expires_at"
                ].isoformat(),
                "absolute_expires_at": grant.principal.session[
                    "absolute_expires_at"
                ].isoformat(),
                "reauthenticated_at": grant.principal.session[
                    "reauthenticated_at"
                ].isoformat(),
            },
            "csrf_token": grant.csrf_token,
            "reauthentication_window_seconds": (
                container.authentication.reauthentication_seconds
            ),
        }

    @app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(request: Request, response: Response) -> Response:
        container.authentication.logout(
            principal=current_principal(request),
            request_id=getattr(request.state, "request_id", None),
        )
        clear_session_cookie(response)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @app.get("/api/v1/accounts")
    async def list_accounts(request: Request) -> list[dict[str, Any]]:
        try:
            return container.authentication.list_accounts(
                current_principal(request)
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/accounts",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_account(
        payload: AccountCreateRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.authentication.create_account(
                username=payload.username,
                display_name=payload.display_name,
                password=payload.password,
                role=payload.role,
                actor=current_principal(request),
                request_id=getattr(request.state, "request_id", None),
            )
        except RecentReauthenticationRequired:
            raise
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_persona(
        payload: PersonaCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.create(
                persona_access(request),
                display_name=payload.display_name,
                description=payload.description,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/personas")
    async def list_personas(
        request: Request,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        return container.personas.list(
            persona_access(request),
            include_inactive=include_inactive,
        )

    @app.get("/api/v1/personas/{persona_id}")
    async def get_persona(
        persona_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.get(
                persona_access(request),
                persona_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.patch("/api/v1/personas/{persona_id}/model-policy")
    async def update_persona_model_policy(
        persona_id: str,
        payload: PersonaModelPolicyUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        if "external" in payload.allowed_model_boundaries:
            require_recent(
                request,
                action="persona.model_policy_updated",
                resource_type="persona",
                resource_id=persona_id,
            )
        try:
            return container.personas.update_model_policy(
                persona_access(request),
                persona_id=persona_id,
                allowed_model_boundaries=payload.allowed_model_boundaries,
                external_data_acknowledged=(
                    payload.external_data_acknowledged
                ),
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/{persona_id}/documents",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def upload_persona_document(
        persona_id: str,
        request: Request,
        file: Annotated[UploadFile, File()],
        language: str | None = Query(default=None, max_length=40),
    ) -> dict[str, Any]:
        try:
            content = await file.read(
                container.settings.persona_max_upload_bytes + 1
            )
            return container.personas.upload_text(
                persona_access(request),
                persona_id=persona_id,
                filename=file.filename or "",
                media_type=file.content_type,
                content=content,
                language=language,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        finally:
            await file.close()

    @app.get("/api/v1/personas/{persona_id}/documents")
    async def list_persona_documents(
        persona_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_documents(
                persona_access(request),
                persona_id=persona_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/documents/{document_id}")
    async def get_persona_document(
        document_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.get_document(
                persona_access(request),
                document_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.delete("/api/v1/documents/{document_id}")
    async def delete_persona_document(
        document_id: str,
        request: Request,
        confirm: bool = False,
    ) -> dict[str, Any]:
        require_recent(
            request,
            action="document.deleted",
            resource_type="source_document",
            resource_id=document_id,
        )
        try:
            return container.personas.delete_document(
                persona_access(request),
                document_id=document_id,
                confirmed=confirm,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/personas/{persona_id}/memory-candidates")
    async def list_persona_memory_candidates(
        persona_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_memories(
                persona_access(request),
                persona_id=persona_id,
                status="candidate",
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/personas/{persona_id}/memories")
    async def list_persona_memories(
        persona_id: str,
        request: Request,
        memory_status: str = Query(default="confirmed", alias="status"),
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_memories(
                persona_access(request),
                persona_id=persona_id,
                status=memory_status,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/memories/{memory_id}")
    async def get_persona_memory(
        memory_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.get_memory(
                persona_access(request),
                memory_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.patch("/api/v1/memories/{memory_id}")
    async def update_persona_memory(
        memory_id: str,
        payload: PersonaMemoryUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.update_memory(
                persona_access(request),
                memory_id=memory_id,
                expected_version=payload.expected_version,
                content=payload.content,
                sensitivity=payload.sensitivity,
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.delete("/api/v1/memories/{memory_id}")
    async def delete_persona_memory(
        memory_id: str,
        request: Request,
        confirm: bool = False,
    ) -> dict[str, Any]:
        require_recent(
            request,
            action="memory.deleted",
            resource_type="persona_memory",
            resource_id=memory_id,
        )
        try:
            return container.personas.delete_memory(
                persona_access(request),
                memory_id=memory_id,
                confirmed=confirm,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/{persona_id}/memory-relations",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_persona_memory_relation(
        persona_id: str,
        payload: PersonaMemoryRelationCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.create_memory_relation(
                persona_access(request),
                persona_id=persona_id,
                from_memory_id=payload.from_memory_id,
                to_memory_id=payload.to_memory_id,
                relation=payload.relation,
                confidence=payload.confidence,
                evidence_memory_version_ids=(
                    payload.evidence_memory_version_ids
                ),
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/memories/{memory_id}/relations")
    async def list_persona_memory_relations(
        memory_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_memory_relations(
                persona_access(request),
                memory_id=memory_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.delete("/api/v1/memory-relations/{relation_id}")
    async def delete_persona_memory_relation(
        relation_id: str,
        request: Request,
        confirm: bool = False,
    ) -> dict[str, Any]:
        try:
            return container.personas.delete_memory_relation(
                persona_access(request),
                relation_id=relation_id,
                confirmed=confirm,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/memory-candidates/{memory_id}/review")
    async def review_persona_memory(
        memory_id: str,
        payload: PersonaMemoryReviewRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personas.review_memory(
                persona_access(request),
                memory_id,
                action=payload.action,
                edited_content=payload.edited_content,
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/personas/{persona_id}/audit-events")
    async def list_persona_audit_events(
        persona_id: str,
        request: Request,
        limit: int = Query(default=200, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        try:
            return container.personas.list_audit_events(
                persona_access(request),
                persona_id=persona_id,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/personas/{persona_id}/export")
    async def export_persona(
        persona_id: str,
        payload: PersonaExportRequest,
        request: Request,
    ) -> dict[str, Any]:
        if payload.include_raw_sources:
            require_recent(
                request,
                action="persona.exported_with_raw_sources",
                resource_type="persona",
                resource_id=persona_id,
            )
        try:
            return container.personas.export_persona(
                persona_access(request),
                persona_id=persona_id,
                include_raw_sources=payload.include_raw_sources,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/import",
        status_code=status.HTTP_201_CREATED,
    )
    async def import_persona(
        payload: PersonaImportRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_recent(
            request,
            action="persona.imported_with_raw_sources",
            resource_type="persona_export",
            resource_id=str(payload.export.get("persona", {}).get("id", "unknown")),
        )
        try:
            return container.personas.import_persona(
                persona_access(request),
                package=payload.model_dump(),
            )
        except FileExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/{persona_id}/conversations",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_persona_conversation(
        persona_id: str,
        payload: PersonaConversationCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.persona_qa.create_conversation(
                persona_access(request),
                persona_id=persona_id,
                title=payload.title,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/conversations/{conversation_id}/messages")
    async def list_persona_conversation_messages(
        conversation_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.persona_qa.list_messages(
                persona_access(request),
                conversation_id=conversation_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/conversations/{conversation_id}/messages",
        status_code=status.HTTP_201_CREATED,
    )
    async def ask_persona(
        conversation_id: str,
        payload: PersonaQuestionCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return await container.persona_qa.ask(
                persona_access(request),
                conversation_id=conversation_id,
                question=payload.content,
                top_k=payload.top_k,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except CitationValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="answer generator returned invalid citations",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/messages/{message_id}/citations")
    async def get_persona_answer_citations(
        message_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        try:
            return container.persona_qa.get_citations(
                persona_access(request),
                message_id=message_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/personas/{persona_id}/memories/reindex",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def reindex_persona_memories(
        persona_id: str,
        request: Request,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            max_length=200,
        ),
    ) -> dict[str, Any]:
        try:
            return container.memory_reindex.enqueue(
                persona_access(request),
                persona_id=persona_id,
                idempotency_key=idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/employees")
    async def list_employees() -> list[dict[str, Any]]:
        return container.store.list_employees()

    @app.get("/api/v1/skills")
    async def list_skills() -> list[dict[str, Any]]:
        return container.store.list_skills()

    @app.get("/api/v1/users/{user_id}/memory-sources")
    async def list_memory_sources(
        user_id: str,
        request: Request,
        source_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return container.store.list_memory_sources(
            user_id=require_account_path(request, user_id),
            source_type=source_type,
            limit=limit,
        )

    @app.get("/api/v1/users/{user_id}/preferences")
    async def list_preferences(
        user_id: str,
        request: Request,
        preference_status: str | None = Query(default=None, alias="status"),
        context: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            return container.store.list_preferences(
                user_id=require_account_path(request, user_id),
                status=preference_status,
                context=context,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/users/{user_id}/preferences/learn")
    async def learn_preferences(
        user_id: str,
        request: Request,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return container.personalization.learn(
                user_id=require_account_path(request, user_id),
                task_id=task_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/preferences/{preference_id}")
    async def get_preference(
        preference_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.store.get_preference_bundle(
                preference_id,
                user_id=current_principal(request).account_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/preferences/{preference_id}/review")
    async def review_preference(
        preference_id: str,
        payload: PreferenceReviewRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.store.review_preference(
                preference_id,
                user_id=current_principal(request).account_id,
                action=payload.action,
                reason=payload.reason,
                expires_at=payload.expires_at,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/runtime/status")
    async def runtime_status() -> dict[str, Any]:
        try:
            return await container.runtime.status()
        except HermesAdapterError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/github/connections",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_github_connection(
        payload: GitHubConnectionCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return await container.github_connections.connect(
                user_id=current_principal(request).account_id,
                installation_id=payload.installation_id,
                repository=payload.repository,
            )
        except GitHubAppNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except GitHubAdapterError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/github/connections")
    async def list_github_connections(
        request: Request,
        include_disconnected: bool = False,
    ) -> list[dict[str, Any]]:
        return container.github_connections.list(
            user_id=current_principal(request).account_id,
            include_disconnected=include_disconnected,
        )

    @app.delete("/api/v1/github/connections/{connection_id}")
    async def disconnect_github_connection(
        connection_id: str,
        request: Request,
    ) -> dict[str, Any]:
        require_recent(
            request,
            action="github.connection_disconnected",
            resource_type="github_connection",
            resource_id=connection_id,
        )
        try:
            return container.github_connections.disconnect(
                connection_id,
                user_id=current_principal(request).account_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/tasks")
    async def list_tasks(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        return container.store.list_tasks(
            user_id=current_principal(request).account_id,
            limit=limit,
        )

    @app.post(
        "/api/v1/tasks/project-maintenance",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_project_maintenance_task(
        payload: ProjectMaintenanceTaskCreate,
        request: Request,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            max_length=200,
        ),
    ) -> dict[str, Any]:
        try:
            return container.project_maintenance.enqueue(
                ProjectMaintenanceCommand(
                    repository=payload.repository,
                    github_connection_id=payload.github_connection_id,
                    employee_id=payload.employee_id,
                    user_id=current_principal(request).account_id,
                    workflow_name=payload.workflow_name,
                    max_items=payload.max_items,
                ),
                idempotency_key=idempotency_key,
            )
        except TaskConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except GitHubAppNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except (KeyError, PermissionError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/tasks/{task_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def retry_task(task_id: str, request: Request) -> dict[str, Any]:
        account_id = current_principal(request).account_id
        try:
            queue_job = container.store.retry_failed_queue_job(
                task_id,
                expected_user_id=account_id,
            )
            bundle = container.store.get_task_bundle(
                task_id,
                expected_user_id=account_id,
            )
            bundle["queue_submission"] = {
                "created": False,
                "idempotency_replayed": False,
                "requeued": True,
                "queue_job_id": queue_job["id"],
            }
            return bundle
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/tasks/{task_id}/cancel",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def cancel_task(
        task_id: str,
        payload: TaskCancellationRequest,
        request: Request,
    ) -> dict[str, Any]:
        account_id = current_principal(request).account_id
        try:
            cancellation = container.store.request_task_cancellation(
                task_id,
                requested_by=account_id,
                reason=payload.reason,
                expected_user_id=account_id,
            )
            bundle = container.store.get_task_bundle(
                task_id,
                expected_user_id=account_id,
            )
            bundle["cancellation"] = cancellation
            return bundle
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str, request: Request) -> dict[str, Any]:
        try:
            return container.store.get_task_bundle(
                task_id,
                expected_user_id=current_principal(request).account_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    @app.post("/api/v1/approvals/{approval_id}/decision")
    async def decide_approval(
        approval_id: str,
        payload: ApprovalDecisionRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.approvals.decide(
                approval_id,
                decision=payload.decision,
                edited_output=payload.edited_output,
                reason=payload.reason,
                expected_user_id=current_principal(request).account_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.post(
        "/api/v1/tasks/{task_id}/feedback",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_feedback(
        task_id: str,
        payload: FeedbackCreate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return container.personalization.add_feedback(
                task_id,
                comment=payload.comment,
                rating=payload.rating,
                expected_user_id=current_principal(request).account_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    def trusted_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})
        schemes["PersonaSession"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": SESSION_COOKIE_NAME,
            "description": "Opaque revocable local session cookie.",
        }
        schemes["CsrfToken"] = {
            "type": "apiKey",
            "in": "header",
            "name": CSRF_HEADER_NAME,
            "description": (
                "Session-bound token required with authenticated unsafe methods."
            ),
        }
        public_paths = {
            "/health",
            "/api/v1/auth/login",
            "/api/v1/auth/status",
        }
        unsafe_methods = {"post", "put", "patch", "delete"}
        for path, path_item in schema.get("paths", {}).items():
            if path in public_paths:
                continue
            for method, operation in path_item.items():
                if method not in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "options",
                    "head",
                } or not isinstance(operation, dict):
                    continue
                operation["security"] = [
                    (
                        {"PersonaSession": [], "CsrfToken": []}
                        if method in unsafe_methods
                        else {"PersonaSession": []}
                    )
                ]
        app.openapi_schema = schema
        return schema

    app.openapi = trusted_openapi
    return app


app = create_app()
