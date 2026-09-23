from pydantic import BaseModel
from typing import List, Optional


class DocumentSummary(BaseModel):
    id: str
    filename: str
    file_type: str
    language: str
    source_type: Optional[str] = None
    source_confidence: Optional[float] = None
    status: str
    page_count: int
    verified_pages: int = 0
    error: Optional[str] = None
    created_at: str
    updated_at: str


class DocumentList(BaseModel):
    documents: List[DocumentSummary]


class Stats(BaseModel):
    documents: int
    pages: int
    pages_verified: int
    lines: int
    lines_needing_review: int
    avg_confidence: Optional[float] = None


class EngineStatus(BaseModel):
    name: str
    available: bool
    detail: str = ""


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    language: str
    engines: List[EngineStatus]
