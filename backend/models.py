from pydantic import BaseModel
from typing import List, Optional


class DocumentSummary(BaseModel):
    id: str
    filename: str
    file_type: str
    language: str
    source_type: Optional[str] = None
    source_confidence: Optional[float] = None
    source_manual: int = 0
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


class DashboardMetrics(BaseModel):
    documents: int
    pages: int
    lines: int
    auto_accepted_percent: float
    reviewed_percent: float
    average_confidence: Optional[float] = None
    pages_per_minute: Optional[float] = None
    tesseract_percent: float
    status_breakdown: List[dict]
    source_breakdown: List[dict]
    confidence_buckets: List[dict]


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


class SourceTypeUpdate(BaseModel):
    source_type: str


class LineVerification(BaseModel):
    reviewer: str
    text: str
    action: str = "edit"
