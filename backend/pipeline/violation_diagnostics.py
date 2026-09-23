from typing import Any, Dict, List, Optional

"""
Root-cause diagnosis for a rule FAIL/REVIEW result.

Every category here is backed by signal the pipeline already computes elsewhere in the
scan -- this module does not add new extraction capability, it interprets signal that
was previously computed and discarded (glare ratio, dot-matrix stamp detection, spatial
trigger-keyword presence) or already fully computed but unlabeled (readability scores,
per-rule format-validity flags) into a single root_cause + rectification pair.

GLARE_RATIO_THRESHOLD is the same 0.05 (5% of panel) used elsewhere as a "material"
glare-coverage cutoff; below that, glare is treated as cosmetic noise rather than a
plausible cause of a missing declaration.
"""

GLARE_RATIO_THRESHOLD = 0.05

ROOT_CAUSE_GENUINELY_ABSENT = "GENUINELY_ABSENT"
ROOT_CAUSE_PRINT_QUALITY_DEGRADED = "PRINT_QUALITY_DEGRADED"
ROOT_CAUSE_WRONG_PANEL_LIKELY = "WRONG_PANEL_LIKELY"
ROOT_CAUSE_NON_STANDARD_FORMAT = "NON_STANDARD_FORMAT"
ROOT_CAUSE_UNDERSIZED_TEXT = "UNDERSIZED_TEXT"


def _field_label(field_key: str) -> str:
    return field_key.replace("_", " ")


def diagnose_missing_field(
    field_key: str,
    legal_ref: str,
    spatial_block: Optional[List[str]],
    glare_ratio: float,
    dot_matrix_fired: bool,
) -> Dict[str, str]:
    """Diagnose a field with no extracted value at all.

    spatial_block is the raw spatial-cluster token list for this field's trigger keyword
    (e.g. spatial_data["mrp_spatial_block"]) when the extractor tracks one, or None when
    this field has no spatial trigger tracking (falls back to a plain "not located").
    An empty list means the trigger keyword genuinely was not found anywhere on the panel;
    a non-empty list means a trigger was found but the value still failed to parse.
    """
    label = _field_label(field_key)

    if glare_ratio >= GLARE_RATIO_THRESHOLD or dot_matrix_fired:
        cause_bits = []
        if glare_ratio >= GLARE_RATIO_THRESHOLD:
            cause_bits.append(f"{glare_ratio * 100:.1f}% of the scanned panel is covered by specular glare")
        if dot_matrix_fired:
            cause_bits.append("ink-jet dot-matrix stamp artifacts were detected on this panel")
        return {
            "root_cause": ROOT_CAUSE_PRINT_QUALITY_DEGRADED,
            "explanation": "; ".join(cause_bits) + ", which may be obscuring this declaration.",
            "rectification": (
                f"Re-photograph under diffuse, even lighting to avoid glare, or re-stamp the "
                f"{label} declaration at higher print resolution rather than post-production ink-jet stamping."
            ),
        }

    if spatial_block is not None and len(spatial_block) == 0:
        return {
            "root_cause": ROOT_CAUSE_GENUINELY_ABSENT,
            "explanation": f"No '{label}' trigger text was located anywhere on the scanned panel(s).",
            "rectification": f"Add the {label} declaration to the packaging as required under {legal_ref}.",
        }

    if spatial_block:
        # Trigger keyword found, but the value after it still failed to parse.
        return {
            "root_cause": ROOT_CAUSE_NON_STANDARD_FORMAT,
            "explanation": f"Text near the expected '{label}' location was found but could not be parsed into a valid value.",
            "rectification": f"Verify the {label} declaration is printed in a standard, clearly separated format per {legal_ref}.",
        }

    # No spatial tracking exists for this field (product name, net quantity, country of
    # origin, FSSAI) -- honest fallback rather than fabricating a signal we don't have.
    return {
        "root_cause": ROOT_CAUSE_GENUINELY_ABSENT,
        "explanation": f"'{label}' could not be located on the scanned panel(s).",
        "rectification": f"Add the {label} declaration to the packaging as required under {legal_ref}.",
    }


def diagnose_low_confidence_field(
    field_key: str,
    legal_ref: str,
    glare_ratio: float,
    dot_matrix_fired: bool,
) -> Dict[str, str]:
    """Diagnose a field that WAS found but is flagged REVIEW/FAIL for a format or
    confidence reason (incomplete address, unreadable value, prohibited unit, missing
    tax-inclusive phrase, etc.) -- i.e. extraction located it, it just isn't compliant."""
    label = _field_label(field_key)

    if glare_ratio >= GLARE_RATIO_THRESHOLD or dot_matrix_fired:
        cause_bits = []
        if glare_ratio >= GLARE_RATIO_THRESHOLD:
            cause_bits.append(f"{glare_ratio * 100:.1f}% glare coverage in this region")
        if dot_matrix_fired:
            cause_bits.append("dot-matrix stamp artifacts nearby")
        return {
            "root_cause": ROOT_CAUSE_PRINT_QUALITY_DEGRADED,
            "explanation": "; ".join(cause_bits) + " reduced confidence in the read value.",
            "rectification": f"Re-stamp or re-print the {label} declaration at higher resolution to improve legibility.",
        }

    return {
        "root_cause": ROOT_CAUSE_NON_STANDARD_FORMAT,
        "explanation": f"'{label}' was located but does not meet the required statutory format.",
        "rectification": f"Correct the printed format of the {label} declaration to comply with {legal_ref}.",
    }


def diagnose_readability(readability_data: Dict[str, Any]) -> Dict[str, str]:
    """Rule 7 diagnosis: split the readability warnings that already exist into the
    two distinct real causes they represent -- a genuinely small font (UNDERSIZED_TEXT,
    from the box-height measurement) vs. a blurry/low-contrast capture (PRINT_QUALITY_DEGRADED),
    instead of one flat 'low legibility' message for both."""
    warnings = readability_data.get("warnings", [])
    undersized = any("under 12px" in w or "minimum height" in w for w in warnings)
    degraded = any("Blur detected" in w or "Insufficient visual contrast" in w for w in warnings)

    if undersized and not degraded:
        return {
            "root_cause": ROOT_CAUSE_UNDERSIZED_TEXT,
            "explanation": "Detected declaration numerals fall below the minimum legible height.",
            "rectification": "Increase the printed font size of the small declarations to meet the Fifth Schedule minimum numeral height.",
        }
    if degraded:
        return {
            "root_cause": ROOT_CAUSE_PRINT_QUALITY_DEGRADED,
            "explanation": "The capture shows low sharpness or insufficient contrast against the packaging background.",
            "rectification": "Re-photograph in better, diffuse lighting, or increase print contrast between text and background.",
        }
    return {
        "root_cause": ROOT_CAUSE_UNDERSIZED_TEXT,
        "explanation": "Legibility concern flagged for this panel.",
        "rectification": "Review the flagged declarations for font size and contrast against Rule 7 & Fifth Schedule minimums.",
    }
