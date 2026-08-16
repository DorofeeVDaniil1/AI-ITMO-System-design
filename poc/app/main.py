from __future__ import annotations

from fastapi import FastAPI

from poc.app.models import AccessVerifyRequest, AccessVerifyResponse
from poc.app.pipeline import MODEL_VERSION, process_event

app = FastAPI(title="Face Gate PoC", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model_version": MODEL_VERSION}


@app.post("/v1/access/verify", response_model=AccessVerifyResponse)
def verify_access(body: AccessVerifyRequest) -> AccessVerifyResponse:
    return process_event(body)
