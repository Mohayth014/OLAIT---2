from typing import Dict, Any, List, Optional, Tuple
from backend.config import DEFAULT_RULES
from backend.pipeline.violation_diagnostics import (
    diagnose_missing_field, diagnose_low_confidence_field, diagnose_readability,
    ROOT_CAUSE_WRONG_PANEL_LIKELY,
)

# A missing-field pattern this broad is more likely the wrong panel having been
# scanned (e.g. front display panel instead of the back information panel) than
# several independent declarations all genuinely absent -- see the WRONG_PANEL_LIKELY
# override applied after the main rule loop below.
WRONG_PANEL_MIN_MISSING = 3
WRONG_PANEL_MIN_FRACTION = 0.5

class LegalMetrologyRuleEngine:
    def __init__(self, rules_config: List[Dict[str, Any]] = None):
        self.rules = rules_config or DEFAULT_RULES

    def evaluate(
        self,
        extracted_data: Dict[str, Any],
        readability_data: Dict[str, Any],
        spatial_data: Optional[Dict[str, List[str]]] = None,
        glare_ratio: float = 0.0,
        dot_matrix_fired: bool = False,
    ) -> Tuple[str, float, List[Dict[str, Any]], List[str], str]:
        """
        Evaluates extracted product declarations against Legal Metrology (Packaged Commodities) Rules, 2011.

        spatial_data / glare_ratio / dot_matrix_fired are optional root-cause signal (spatial
        trigger-keyword clusters, glare coverage ratio, dot-matrix stamp detection) used to turn a
        bare FAIL/REVIEW into a root_cause + rectification pair on each rule result. All three
        default to "no signal available" so callers that don't have them still get a valid result.

        Returns:
            - overall_status: 'COMPLIANT', 'NON_COMPLIANT', or 'REVIEW_REQUIRED'
            - overall_confidence: float (0.0 to 1.0)
            - rule_results: List of evaluated rule result objects
            - violations: List of statutory violation strings
            - summary: Human-readable executive summary
        """
        spatial_data = spatial_data or {}
        rule_results = []
        violations = []
        critical_failed = False
        major_failed_count = 0
        review_count = 0

        confidences = []

        for rule in self.rules:
            r_id = rule["rule_id"]
            title = rule["title"]
            legal_ref = rule["legal_reference"]
            field = rule["field"]
            severity = rule["severity"]
            is_required = rule.get("required", True)

            # 1. Rule 6(1)(a): Manufacturer / Packer Complete Name & Address
            diag = None
            if r_id == "LM_RULE_6_1_A":
                mfg = extracted_data.get("manufacturer", {})
                conf = mfg.get("confidence", 0.0)
                confidences.append(conf)

                if mfg.get("value"):
                    details = mfg.get("details") or {}
                    if details.get("is_complete_address"):
                        status = "PASS"
                        msg = f"Manufacturer identity and complete address verified ({mfg.get('value')})."
                    else:
                        status = "REVIEW"
                        msg = f"Manufacturer name found ({mfg.get('value')}), but complete postal address / PIN code could not be fully verified."
                        review_count += 1
                        diag = diagnose_low_confidence_field(field, legal_ref, glare_ratio, dot_matrix_fired)
                else:
                    status = "FAIL" if is_required else "REVIEW"
                    msg = "Mandatory declaration of Manufacturer / Packer / Importer name and address is missing or illegible."
                    diag = diagnose_missing_field(field, legal_ref, spatial_data.get("mfg_spatial_block"), glare_ratio, dot_matrix_fired)
                    if is_required:
                        critical_failed = True
                        violations.append(f"Violation of {legal_ref}: Missing mandatory Name and Address of Manufacturer/Packer/Importer.")

            # 2. Rule 6(1)(b): Generic Name / Product Identity
            elif r_id == "LM_RULE_6_1_B":
                pname = extracted_data.get("product_name", {})
                conf = pname.get("confidence", 0.0)
                confidences.append(conf)

                if pname.get("value") and conf >= 0.50:
                    status = "PASS"
                    msg = f"Product identity / generic commodity name declared: '{pname.get('value')}'."
                elif pname.get("value"):
                    status = "REVIEW"
                    msg = f"Product identity detected with moderate confidence: '{pname.get('value')}'."
                    review_count += 1
                    diag = diagnose_low_confidence_field(field, legal_ref, glare_ratio, dot_matrix_fired)
                else:
                    status = "FAIL" if is_required else "REVIEW"
                    msg = "Common or generic name of commodity not conspicuously found."
                    # No spatial trigger tracking exists for the generic product name field.
                    diag = diagnose_missing_field(field, legal_ref, None, glare_ratio, dot_matrix_fired)
                    if is_required:
                        critical_failed = True
                        violations.append(f"Violation of {legal_ref}: Absence of conspicuous generic commodity name.")

            # 3. Rule 6(1)(c): Net Quantity in the Standard Unit of Weight, Measure or Number
            elif r_id == "LM_RULE_6_1_C":
                qty = extracted_data.get("net_quantity", {})
                conf = qty.get("confidence", 0.0)
                confidences.append(conf)

                details = qty.get("details") or {}
                if details.get("needs_review"):
                    # A declaration is present on the pack but its value could not be read.
                    status = "REVIEW"
                    review_count += 1
                    msg = (
                        "A net-quantity declaration is present on the package, but its value "
                        f"could not be read with confidence ({qty.get('value')}). Officer verification required."
                    )
                    diag = diagnose_low_confidence_field(field, legal_ref, glare_ratio, dot_matrix_fired)
                elif qty.get("value"):
                    if details.get("is_prohibited_symbol"):
                        status = "FAIL"
                        critical_failed = True
                        msg = f"Prohibited non-standard unit symbol used: '{details.get('raw_unit')}'. Law mandates the standard symbol (e.g. 'g', 'kg', 'ml', 'l')."
                        violations.append(f"Violation of {legal_ref}: Prohibited unit '{details.get('raw_unit')}' used instead of the standard unit of weight/measure.")
                        # The value WAS read successfully -- this is a pure format defect,
                        # never a print-quality one, regardless of glare/dot-matrix signal.
                        diag = {
                            "root_cause": "NON_STANDARD_FORMAT",
                            "rectification": f"Replace the non-standard unit '{details.get('raw_unit')}' with the standard SI symbol (e.g. 'g', 'kg', 'ml', 'l') per {legal_ref}.",
                        }
                    else:
                        status = "PASS"
                        unit_note = " (number of articles)" if details.get("count_commodity") else ""
                        recovered = " [value bound from an adjacent label region]" if details.get("cross_line_binding") else ""
                        msg = f"Net quantity declared in the standard unit of weight, measure or number: {qty.get('value')}{unit_note}{recovered}."
                else:
                    status = "FAIL" if is_required else "REVIEW"
                    msg = "Mandatory declaration of net quantity (standard unit of weight, measure or number) is missing or undetectable."
                    # No spatial trigger tracking exists for net quantity.
                    diag = diagnose_missing_field(field, legal_ref, None, glare_ratio, dot_matrix_fired)
                    if is_required:
                        critical_failed = True
                        violations.append(f"Violation of {legal_ref}: Missing declaration of net quantity.")

            # 4. Rule 6(1)(d): Month & Year of Manufacture / Packing
            elif r_id == "LM_RULE_6_1_D":
                mdate = extracted_data.get("mfg_date", {})
                bdate = extracted_data.get("best_before", {})
                conf = max(mdate.get("confidence", 0.0), bdate.get("confidence", 0.0))
                confidences.append(conf)
                b_details = bdate.get("details") or {}

                if mdate.get("value"):
                    # The actual printed month & year of manufacture / packing.
                    status = "PASS"
                    msg = f"Month & year of manufacture / packing declared on pack: {mdate.get('value')}."
                elif bdate.get("value") and b_details.get("is_date"):
                    # Only a 'Best Before / Use By' calendar date was read — not the mfg date.
                    status = "REVIEW"
                    review_count += 1
                    msg = (
                        f"Only a 'Best Before / Use By' date ({bdate.get('value')}) was detected. "
                        f"Rule 6(1)(d) requires the month & year of manufacture / packing — confirm it is on the pack."
                    )
                    diag = diagnose_low_confidence_field(field, legal_ref, glare_ratio, dot_matrix_fired)
                elif bdate.get("value"):
                    # A relative shelf-life period ("24 months from manufacture") is not a date.
                    status = "REVIEW"
                    review_count += 1
                    msg = (
                        f"A shelf-life period ('{bdate.get('value')}') was detected but not the month & year of "
                        f"manufacture / packing required under Rule 6(1)(d)."
                    )
                    diag = diagnose_low_confidence_field(field, legal_ref, glare_ratio, dot_matrix_fired)
                else:
                    status = "FAIL" if is_required else "REVIEW"
                    msg = "Month and year of manufacture or packing not clearly identified."
                    diag = diagnose_missing_field(field, legal_ref, spatial_data.get("date_spatial_block"), glare_ratio, dot_matrix_fired)
                    if is_required:
                        major_failed_count += 1
                        violations.append(f"Violation of {legal_ref}: Month and year of manufacture/packing not declared.")

            # 5. Rule 6(1)(e): Maximum Retail Price (MRP) & Taxes
            elif r_id == "LM_RULE_6_1_E":
                mrp = extracted_data.get("mrp", {})
                conf = mrp.get("confidence", 0.0)
                confidences.append(conf)

                if mrp.get("value"):
                    details = mrp.get("details") or {}
                    if details.get("inclusive_of_taxes"):
                        status = "PASS"
                        msg = f"MRP declared in Indian currency with tax inclusion: {mrp.get('value')} (inclusive of all taxes)."
                    else:
                        status = "FAIL"
                        critical_failed = True
                        msg = f"MRP declared as {mrp.get('value')}, but mandatory statutory statement 'inclusive of all taxes' was not detected."
                        violations.append(f"Violation of {legal_ref}: MRP declared without mandatory 'inclusive of all taxes' phrase.")
                        # The MRP figure itself WAS read -- only the tax-inclusion phrase is
                        # absent, a pure format defect rather than a print-quality one.
                        diag = {
                            "root_cause": "NON_STANDARD_FORMAT",
                            "rectification": f"Add the mandatory 'inclusive of all taxes' phrase immediately next to the MRP declaration, per {legal_ref}.",
                        }
                else:
                    status = "FAIL" if is_required else "REVIEW"
                    msg = "Maximum Retail Price (MRP) declaration is missing or obscured."
                    diag = diagnose_missing_field(field, legal_ref, spatial_data.get("mrp_spatial_block"), glare_ratio, dot_matrix_fired)
                    if is_required:
                        critical_failed = True
                        violations.append(f"Violation of {legal_ref}: Maximum Retail Price (MRP) is not declared.")

            # 6. Rule 6(1)(f): Consumer Care Details
            elif r_id == "LM_RULE_6_1_F":
                care = extracted_data.get("consumer_care", {})
                conf = care.get("confidence", 0.0)
                confidences.append(conf)

                if care.get("value") and care.get("is_valid"):
                    status = "PASS"
                    msg = f"Consumer grievance contact details declared: {care.get('value')}."
                elif care.get("value"):
                    status = "REVIEW"
                    msg = "Consumer care contact section detected but telephone number or email address could not be fully resolved."
                    review_count += 1
                    diag = diagnose_low_confidence_field(field, legal_ref, glare_ratio, dot_matrix_fired)
                else:
                    status = "FAIL" if is_required else "REVIEW"
                    msg = "Consumer grievance cell details (telephone/email) missing."
                    diag = diagnose_missing_field(field, legal_ref, spatial_data.get("care_spatial_block"), glare_ratio, dot_matrix_fired)
                    if is_required:
                        major_failed_count += 1
                        violations.append(f"Violation of {legal_ref}: Consumer care phone/email not provided.")

            # 7. Rule 6(10): Country of Origin
            elif r_id == "LM_RULE_6_10":
                origin = extracted_data.get("country_of_origin", {})
                conf = origin.get("confidence", 0.0)
                confidences.append(conf)

                if origin.get("value"):
                    status = "PASS"
                    msg = f"Country of origin declared: {origin.get('value')}."
                else:
                    status = "PASS" if not is_required else "REVIEW"
                    msg = "Country of origin declaration optional or assumed domestic unless imported."

            # 8. Rule 7: Readability & Contrast
            elif r_id == "LM_RULE_7_READABILITY":
                is_legible = readability_data.get("is_legible", True)
                conf = 0.90 if is_legible else 0.40
                confidences.append(conf)

                if is_legible:
                    status = "PASS"
                    msg = f"Declarations satisfy visual contrast (score: {readability_data.get('contrast_score')}) and sharpness standards."
                else:
                    status = "REVIEW"
                    reasons = " | ".join(readability_data.get("warnings", ["Low legibility"]))
                    msg = f"Potential readability concern: {reasons}"
                    review_count += 1
                    diag = diagnose_readability(readability_data)

            else:
                status = "PASS"
                conf = 0.8
                msg = "Rule evaluated."

            rule_results.append({
                "rule_id": r_id,
                "title": title,
                "legal_reference": legal_ref,
                "field": field,
                "status": status,
                "severity": severity,
                "message": msg,
                "confidence": round(conf, 2),
                "root_cause": diag["root_cause"] if diag else None,
                "rectification": diag["rectification"] if diag else "",
            })

        # Wrong-panel override: several declarations being simultaneously and genuinely
        # absent is far more often the wrong panel having been scanned than several
        # independent statutory omissions -- re-tag those results accordingly rather
        # than reporting each as its own unrelated GENUINELY_ABSENT finding.
        genuinely_absent_idxs = [
            i for i, r in enumerate(rule_results)
            if r["status"] == "FAIL" and r.get("root_cause") == "GENUINELY_ABSENT"
        ]
        wrong_panel_likely = len(rule_results) > 0 and len(genuinely_absent_idxs) >= max(
            WRONG_PANEL_MIN_MISSING, int(len(rule_results) * WRONG_PANEL_MIN_FRACTION)
        )
        if wrong_panel_likely:
            missing_titles = [rule_results[i]["title"] for i in genuinely_absent_idxs]
            for i in genuinely_absent_idxs:
                rule_results[i]["root_cause"] = ROOT_CAUSE_WRONG_PANEL_LIKELY
                rule_results[i]["rectification"] = (
                    f"{len(genuinely_absent_idxs)} mandatory declarations are absent simultaneously -- this may be "
                    "the front/display panel rather than the back information panel. Re-scan the back panel "
                    "where Legal Metrology declarations are printed."
                )

        # Calculate overall status
        if critical_failed:
            overall_status = "NON_COMPLIANT"
            summary = f"NON-COMPLIANT: Package violates mandatory statutory provisions under Legal Metrology Rules, 2011. {len(violations)} violation(s) identified."
        elif major_failed_count >= 2:
            overall_status = "NON_COMPLIANT"
            summary = f"NON-COMPLIANT: Multiple major declarations missing ({len(violations)} violations)."
        elif review_count > 0 or major_failed_count > 0:
            overall_status = "REVIEW_REQUIRED"
            summary = "REVIEW REQUIRED: Declarations partially detected with ambiguities or low contrast. Requires enforcement officer verification."
        else:
            overall_status = "COMPLIANT"
            summary = "COMPLIANT: All mandatory declarations verified under Legal Metrology (Packaged Commodities) Rules, 2011."

        if wrong_panel_likely:
            summary += (
                f" NOTE: {len(genuinely_absent_idxs)} declarations ({', '.join(missing_titles)}) are absent "
                "simultaneously -- verify the correct panel (front vs. back) was scanned before treating this as a genuine violation."
            )

        overall_confidence = float(round(sum(confidences) / len(confidences) if confidences else 0.0, 3))

        return overall_status, overall_confidence, rule_results, violations, summary
