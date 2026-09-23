import pytest
from backend.pipeline.rules_engine import LegalMetrologyRuleEngine
from backend.config import DEFAULT_RULES

def test_fully_compliant_product():
    extracted = {
        "product_name": {"value": "Wheat Flour (Atta)", "confidence": 0.95, "is_valid": True},
        "brand": {"value": "Aashirvaad", "confidence": 0.95, "is_valid": True},
        "category": {"value": "Packaged Food", "confidence": 0.90, "is_valid": True},
        "net_quantity": {
            "value": "5 kg", "confidence": 0.95, "is_valid": True,
            "details": {"is_prohibited_symbol": False, "standard_unit": "kg"}
        },
        "mrp": {
            "value": "₹ 240.00", "confidence": 0.95, "is_valid": True,
            "details": {"inclusive_of_taxes": True}
        },
        "mfg_date": {"value": "08/2026", "confidence": 0.90, "is_valid": True},
        "best_before": {"value": "Best before 6 months", "confidence": 0.90, "is_valid": True},
        "manufacturer": {
            "value": "ITC Limited, Kolkata, West Bengal 700071",
            "confidence": 0.90, "is_valid": True,
            "details": {"is_complete_address": True}
        },
        "consumer_care": {"value": "1800-345-0010 | care@itc.in", "confidence": 0.95, "is_valid": True},
        "country_of_origin": {"value": "Made in India", "confidence": 0.95, "is_valid": True}
    }
    readability = {"is_legible": True, "contrast_score": 45.0, "blur_score": 120.0, "warnings": []}

    engine = LegalMetrologyRuleEngine(DEFAULT_RULES)
    status, conf, rule_results, violations, summary = engine.evaluate(extracted, readability)

    assert status == "COMPLIANT"
    assert len(violations) == 0
    assert conf > 0.85

def test_missing_mrp_triggers_violation():
    extracted = {
        "product_name": {"value": "Biscuits", "confidence": 0.9, "is_valid": True},
        "net_quantity": {"value": "100 g", "confidence": 0.9, "is_valid": True, "details": {"is_prohibited_symbol": False}},
        "mrp": {"value": None, "confidence": 0.0, "is_valid": False}, # Missing MRP
        "mfg_date": {"value": "05/2026", "confidence": 0.9, "is_valid": True},
        "manufacturer": {"value": "Britannia Industries Ltd, Bangalore 560001", "confidence": 0.9, "is_valid": True, "details": {"is_complete_address": True}},
        "consumer_care": {"value": "1800-425-4449", "confidence": 0.9, "is_valid": True}
    }
    readability = {"is_legible": True, "contrast_score": 50.0, "blur_score": 100.0, "warnings": []}

    engine = LegalMetrologyRuleEngine(DEFAULT_RULES)
    status, conf, rule_results, violations, summary = engine.evaluate(extracted, readability)

    assert status == "NON_COMPLIANT"
    assert any("MRP" in v for v in violations)

def test_prohibited_unit_symbol_violation():
    extracted = {
        "product_name": {"value": "Chilli Powder", "confidence": 0.9, "is_valid": True},
        "net_quantity": {
            "value": "200 gms", "confidence": 0.9, "is_valid": False,
            "details": {"is_prohibited_symbol": True, "raw_unit": "gms"}
        }, # Prohibited 'gms' instead of standard 'g'
        "mrp": {"value": "₹ 50.00", "confidence": 0.9, "is_valid": True, "details": {"inclusive_of_taxes": True}},
        "mfg_date": {"value": "07/2026", "confidence": 0.9, "is_valid": True},
        "manufacturer": {"value": "Everest Spices Ltd, Mumbai 400001", "confidence": 0.9, "is_valid": True, "details": {"is_complete_address": True}},
        "consumer_care": {"value": "care@everest.com", "confidence": 0.9, "is_valid": True}
    }
    readability = {"is_legible": True, "contrast_score": 40.0, "blur_score": 80.0, "warnings": []}

    engine = LegalMetrologyRuleEngine(DEFAULT_RULES)
    status, conf, rule_results, violations, summary = engine.evaluate(extracted, readability)

    assert status == "NON_COMPLIANT"
    assert any("Prohibited unit 'gms'" in v for v in violations)
