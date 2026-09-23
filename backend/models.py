from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class BoundingBox(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float

class OCRDetectedBox(BaseModel):
    text: str
    confidence: float
    bbox: List[List[float]] # [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    normalized_bbox: Optional[BoundingBox] = None
    field_tag: Optional[str] = None # e.g. 'mrp', 'net_quantity', 'manufacturer', 'date'
    panel: Optional[str] = "front" # 'front' or 'back'

class ExtractedField(BaseModel):
    value: Optional[str] = None
    confidence: float = 0.0
    detected_text: Optional[str] = None
    is_valid: bool = False
    details: Optional[Dict[str, Any]] = None

class ExtractedData(BaseModel):
    product_name: ExtractedField = Field(default_factory=ExtractedField)
    brand: ExtractedField = Field(default_factory=ExtractedField)
    category: ExtractedField = Field(default_factory=ExtractedField)
    net_quantity: ExtractedField = Field(default_factory=ExtractedField)
    mrp: ExtractedField = Field(default_factory=ExtractedField)
    mfg_date: ExtractedField = Field(default_factory=ExtractedField)
    best_before: ExtractedField = Field(default_factory=ExtractedField)
    manufacturer: ExtractedField = Field(default_factory=ExtractedField)
    consumer_care: ExtractedField = Field(default_factory=ExtractedField)
    fssai_license: ExtractedField = Field(default_factory=ExtractedField)
    country_of_origin: ExtractedField = Field(default_factory=ExtractedField)
    unit_sale_price: ExtractedField = Field(default_factory=ExtractedField)

class ReadabilityMetric(BaseModel):
    blur_score: float
    contrast_score: float
    is_sharp: bool
    is_contrast_sufficient: bool
    avg_font_height_px: float = 0.0
    min_font_height_px: float = 0.0
    max_font_height_px: float = 0.0
    estimated_text_coverage: float
    is_legible: bool = True
    warnings: List[str] = []

class RuleValidationResult(BaseModel):
    rule_id: str
    title: str
    legal_reference: str
    field: str
    status: str # 'PASS', 'FAIL', 'REVIEW'
    severity: str # 'CRITICAL', 'MAJOR', 'MINOR'
    message: str
    confidence: float
    # Root-Cause & Remediation: why this rule failed/needs review, and how to fix it --
    # None/"" on PASS. root_cause is one of GENUINELY_ABSENT, PRINT_QUALITY_DEGRADED,
    # WRONG_PANEL_LIKELY, NON_STANDARD_FORMAT, UNDERSIZED_TEXT (see violation_diagnostics.py).
    root_cause: Optional[str] = None
    rectification: str = ""

class SimilarProduct(BaseModel):
    filename: str
    name: str
    category: str
    similarity_score: float
    similarity_percentage: float = 0.0
    thumbnail_url: str

class InspectionResponse(BaseModel):
    inspection_id: str
    timestamp: str
    image_filename: str
    image_url: str
    thumbnail_url: str
    overall_status: str # 'COMPLIANT', 'NON_COMPLIANT', 'REVIEW_REQUIRED'
    overall_confidence: float
    summary: str
    extracted_data: ExtractedData
    rule_results: List[RuleValidationResult]
    violations: List[str]
    readability: ReadabilityMetric
    ocr_boxes: List[OCRDetectedBox]
    clip_categories: List[Dict[str, Any]]
    similar_products: List[SimilarProduct]
    officer_verification: Optional[Dict[str, Any]] = None
    back_image_url: Optional[str] = None
    is_dual_panel: Optional[bool] = False
    inspector_id: Optional[str] = None
    override_history: List[Dict[str, Any]] = []

class ManualReviewPayload(BaseModel):
    """The inspector's own routine human-in-the-loop call on their own scan
    (approve / rescan / hold), made right after scanning. NOT the same action
    as a manager's verdict override -- see OverridePayload."""
    officer_id: str = "ENF-OFFICER-001"
    officer_name: str = "Legal Metrology Inspector"
    decision: str # 'APPROVED', 'REJECTED_NON_COMPLIANT', 'PENDING'
    officer_notes: str = ""
    override_fields: Optional[Dict[str, str]] = None

class RuleConfigUpdate(BaseModel):
    rule_id: str
    enabled: bool
    severity: str
    required: bool

class RegisterPayload(BaseModel):
    """Officer self-registration. Role is never accepted from the client."""
    officer_name: str
    officer_id: str
    password: str
    jurisdiction: str

class LoginPayload(BaseModel):
    """Officers sign in with their name + service number (Officer ID) + password.
    `email` is kept as an accepted alias for `identifier` so existing clients work."""
    password: str
    identifier: Optional[str] = None    # Officer ID (e.g. 'LMO-001') or e-mail
    officer_name: Optional[str] = None  # verified against the registered name when supplied
    email: Optional[str] = None         # legacy alias for `identifier`

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    name: str
    role: str # 'inspector' | 'manager' -- displayed as Officer / Controller
    role_label: str = ""
    officer_id: Optional[str] = None
    jurisdiction: Optional[str] = None

class OverridePayload(BaseModel):
    """A manager changing an ALREADY-RECORDED verdict. Distinct action from
    ManualReviewPayload: different actor (manager, not the scanning inspector),
    different trigger (after the fact, not right after scanning), and it is
    logged as its own audit event (see database.overrides) rather than merged
    into the inspector's officer_review record."""
    new_status: str # 'COMPLIANT' | 'NON_COMPLIANT' | 'REVIEW_REQUIRED'
    reason: str # mandatory written justification; validated non-empty in the endpoint

class JurisdictionPayload(BaseModel):
    jurisdiction: str

class CreateSessionPayload(BaseModel):
    location: str
    inspection_type: str  # 'Routine' | 'Special' | 'Complaint-based' | 'Enforcement'
    notes: Optional[str] = ""

class AddProductPayload(BaseModel):
    product_name: str
    manufacturer: Optional[str] = ""
    total_quantity: int = 0
    sample_target: int = 0
    category: Optional[str] = ""
    location: Optional[str] = ""
    notes: Optional[str] = ""
