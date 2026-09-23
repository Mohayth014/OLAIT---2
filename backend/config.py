import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = BASE_DIR / "FOOD" / "FOOD"
DATABASE_DIR = BASE_DIR / "backend" / "database"
DATABASE_PATH = DATABASE_DIR / "compliance.db"
THUMBNAIL_DIR = DATABASE_DIR / "thumbnails"
REPORTS_DIR = BASE_DIR / "backend" / "reporting" / "generated_reports"
FRONTEND_DIR = BASE_DIR / "frontend"
MOBILE_DIR = BASE_DIR / "frontend-mobile"

# Ensure runtime directories exist
DATABASE_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# AI Models Configuration
CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"
OCR_LANGS = ["en"]
OCR_MAX_DIMENSION = 1280  # Optimized resolution: 28% faster inference with superior CLAHE text clarity

# Confidence Thresholds
CONFIDENCE_HIGH = 0.70
CONFIDENCE_REVIEW = 0.45

# Target Commodity Categories for CLIP Zero-Shot Classification
COMMODITY_CATEGORIES = [
    "packaged instant noodles or pasta",
    "soap shampoo cosmetics or personal care",
    "packaged soft drink fruit juice or beverage",
    "cooking oil ghee or mustard oil",
    "packaged potato chips namkeen or savoury snacks",
    "dairy milk paneer butter curd or cheese",
    "packaged spices masala or condiments",
    "atta flour rice pulses or grains packet",
    "tea coffee or malt health drink",
    "packaged chocolate candy or confectionery",
    "bakery biscuits cookies or rusk",
    "sauce ketchup mayonnaise or jam bottle",
    "household cleaning detergent or disinfectant"
]

# Legal Metrology Default Rules Configuration
DEFAULT_RULES = [
    {
        "rule_id": "LM_RULE_6_1_A",
        "title": "Name & Address of Manufacturer / Packer / Importer",
        "legal_reference": "Rule 6(1)(a), Legal Metrology (Packaged Commodities) Rules, 2011",
        "field": "manufacturer_address",
        "required": True,
        "severity": "CRITICAL",
        "description": "Every package shall bear the complete name and definite address of manufacturer/packer/importer including state/PIN code."
    },
    {
        "rule_id": "LM_RULE_6_1_B",
        "title": "Generic Name / Commodity Identity",
        "legal_reference": "Rule 6(1)(b), Legal Metrology (Packaged Commodities) Rules, 2011",
        "field": "product_name",
        "required": True,
        "severity": "CRITICAL",
        "description": "The common or generic names of the commodity contained in the package shall be prominently displayed."
    },
    {
        "rule_id": "LM_RULE_6_1_C",
        "title": "Net Quantity in Standard Unit of Weight, Measure or Number",
        "legal_reference": "Rule 6(1)(c), Legal Metrology (Packaged Commodities) Rules, 2011",
        "field": "net_quantity",
        "required": True,
        "severity": "CRITICAL",
        "description": "Net quantity shall be declared in the standard unit of weight or measure (g, kg, ml, l, m, cm) or, where the commodity is sold by number, as the number of articles. Non-standard units (gms, kilos, ltr) are prohibited."
    },
    {
        "rule_id": "LM_RULE_6_1_D",
        "title": "Month & Year of Manufacture / Packing / Import",
        "legal_reference": "Rule 6(1)(d), Legal Metrology (Packaged Commodities) Rules, 2011",
        "field": "mfg_date",
        "required": True,
        "severity": "MAJOR",
        "description": "The month and year in which the commodity is manufactured or packed or imported shall be clearly indicated."
    },
    {
        "rule_id": "LM_RULE_6_1_E",
        "title": "Maximum Retail Price (MRP) with Tax Inclusion",
        "legal_reference": "Rule 6(1)(e), Legal Metrology (Packaged Commodities) Rules, 2011",
        "field": "mrp",
        "required": True,
        "severity": "CRITICAL",
        "description": "Maximum Retail Price (MRP) shall be declared in Indian Rupees (₹ or Rs.) followed by 'inclusive of all taxes' or 'incl. of all taxes'."
    },
    {
        "rule_id": "LM_RULE_6_1_F",
        "title": "Consumer Care Details (Phone & Email)",
        "legal_reference": "Rule 6(1)(f), Legal Metrology (Packaged Commodities) Rules, 2011",
        "field": "consumer_care",
        "required": True,
        "severity": "MAJOR",
        "description": "Name, address, telephone number or email address of the person or office that can be contacted for consumer grievances."
    },
    {
        "rule_id": "LM_RULE_6_10",
        "title": "Country of Origin Declaration",
        "legal_reference": "Rule 6(10), Legal Metrology (Packaged Commodities) Rules, 2011",
        "field": "country_of_origin",
        "required": False,
        "severity": "MAJOR",
        "description": "Country of origin must be stated for all imported packages or manufactured goods."
    },
    {
        "rule_id": "LM_RULE_7_READABILITY",
        "title": "Legibility, Contrast & Minimum Size Requirements",
        "legal_reference": "Rule 7 & Fifth Schedule, Legal Metrology Rules, 2011",
        "field": "readability",
        "required": True,
        "severity": "MAJOR",
        "description": "Declarations must be conspicuous, clearly legible, distinct in contrast to the background, and meet minimum numeral height."
    }
]
