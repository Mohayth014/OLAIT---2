import re
from typing import List, Dict, Any, Optional

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "Puducherry", "Chandigarh", "Daman", "Diu"
]

COMMON_BRANDS = [
    "Dove", "Lux", "Lifebuoy", "Pears", "Dettol", "Hamam", "Santoor", "Medimix",
    "Maggi", "Yippee", "Knorr", "Chings", "Top Ramen", "Wai Wai",
    "Lays", "Kurkure", "Bingo", "Balaji", "Haldirams", "Bikaji", "Prataap",
    "Britannia", "Parle", "Sunfeast", "Oreo", "Good Day", "Marie Gold", "Monaco",
    "Amul", "Mother Dairy", "Nandini", "Nestle", "Kwality Wall's",
    "Fortune", "Saffola", "Dhara", "Gemini", "Sundrop", "Engine", "Emami",
    "Tata", "Aashirvaad", "Pillsbury", "Catch", "MDH", "Everest", "Badshah",
    "Coca-Cola", "Pepsi", "Thums Up", "Sprite", "Limca", "Fanta", "Frooti", "Maaza",
    "Real", "Tropicana", "Red Bull", "Sting", "Monster", "Bournvita", "Horlicks", "Boost"
]

from backend.pipeline.spatial_layout import get_spatial_layout_extractor
from backend.pipeline.spell_checker import get_spell_checker

def extract_structured_information(
    ocr_boxes: List[Dict[str, Any]],
    full_text: str,
    clip_categories: List[Dict[str, Any]],
    img_width: int = 1280,
    img_height: int = 1280
) -> Dict[str, Any]:
    """
    Parses OCR detections and text fragments into Legal Metrology structured fields
    using hybrid NLP, 2D Spatial LayoutLM clusters, and packaging domain lexicons.
    """
    # Optical dot-matrix normalization and artifact correction
    spell_checker = get_spell_checker()
    full_text = spell_checker.correct_packaging_text(full_text)
    text_lower = full_text.lower()
    lines = [spell_checker.correct_packaging_text(b["text"].strip()) for b in ocr_boxes if len(b.get("text", "").strip()) > 0]

    # 2D Spatial Clustering (LayoutLMv3 grid representation)
    spatial_extractor = get_spatial_layout_extractor()
    spatial_data = spatial_extractor.extract_spatial_entities(ocr_boxes, img_width, img_height)

    extracted = {
        "product_name": _extract_product_name(lines, clip_categories),
        "brand": _extract_brand(lines, full_text),
        "category": _extract_category(clip_categories),
        "net_quantity": None, # computed below
        "mrp": None,          # computed below
        "mfg_date": _extract_mfg_date(lines, full_text, spatial_data.get("date_spatial_block", [])),
        "best_before": _extract_best_before(lines, full_text, spatial_data.get("date_spatial_block", [])),
        "manufacturer": _extract_manufacturer(lines, full_text, spatial_data.get("mfg_spatial_block", [])),
        "consumer_care": _extract_consumer_care(lines, full_text, spatial_data.get("care_spatial_block", [])),
        "fssai_license": _extract_fssai(lines, full_text),
        "country_of_origin": _extract_country_of_origin(lines, full_text),
        "unit_sale_price": None # computed below
    }

    net_qty_data = _extract_net_quantity(lines, full_text)
    mrp_data = _extract_mrp(lines, full_text, spatial_data.get("mrp_spatial_block", []))
    usp_data = _extract_unit_sale_price(lines, full_text, mrp_data, net_qty_data)

    extracted["net_quantity"] = net_qty_data
    extracted["mrp"] = mrp_data
    extracted["unit_sale_price"] = usp_data

    return extracted

def _extract_category(clip_categories: List[Dict[str, Any]]) -> Dict[str, Any]:
    if clip_categories and len(clip_categories) > 0:
        top_cat = clip_categories[0]
        return {
            "value": top_cat["label"].title(),
            "confidence": round(top_cat["confidence"], 2),
            "all_categories": [
                {"label": c["label"].title(), "confidence": round(c["confidence"], 2)}
                for c in clip_categories[:3]
            ]
        }
    return {"value": "General Packaged Commodity", "confidence": 0.50, "all_categories": []}

def _extract_brand(lines: List[str], full_text: str) -> Dict[str, Any]:
    text_lower = full_text.lower()

    # 1. Registered trademark or Brand marker: e.g. "DOVE IS A REGISTERED TRADEMARK" or "Brand: X" or "Trade Mark: X"
    tm_match = re.search(
        r'\b([A-Za-z0-9]{2,20})\b\s+(?:is\s+a\s+registered\s+(?:trade\s*mark|trademark)|registered\s+(?:trade\s*mark|trademark)|®|™)',
        full_text,
        re.IGNORECASE
    )
    if tm_match:
        cand = tm_match.group(1).strip()
        if len(cand) >= 2 and not any(k in cand.lower() for k in ["product", "packaging", "company", "limited"]):
            return {
                "value": cand.title(),
                "confidence": 0.96,
                "detected_text": tm_match.group(0),
                "is_valid": True,
                "details": {"source": "trademark_declaration"}
            }

    brand_header = re.search(r'(?:brand|trade\s*mark|tm)[\s:\.\-]+([A-Za-z0-9\s]{2,20})\b', full_text, re.IGNORECASE)
    if brand_header:
        cand = brand_header.group(1).strip()
        return {
            "value": cand.title(),
            "confidence": 0.94,
            "detected_text": brand_header.group(0),
            "is_valid": True,
            "details": {"source": "brand_header"}
        }

    # 2. Known domain brands lexicon (accelerator, dynamic matching)
    for brand in COMMON_BRANDS:
        if re.search(rf'\b{re.escape(brand.lower())}\b', text_lower):
            return {
                "value": brand,
                "confidence": 0.92,
                "detected_text": brand,
                "is_valid": True,
                "details": {"source": "domain_lexicon"}
            }

    # 3. Dynamic prominence heuristic: Top prominent title token on label
    for line in lines[:4]:
        clean = line.strip()
        if 2 <= len(clean) <= 25 and not any(k in clean.lower() for k in ["mrp", "net", "batch", "date", "exp", "contents", "mfg", "pkd", "packed", "ingredients"]):
            if clean.isupper() or clean.istitle() or len(clean.split()) <= 3:
                return {
                    "value": clean.title(),
                    "confidence": 0.70,
                    "detected_text": clean,
                    "is_valid": True,
                    "details": {"source": "prominent_title_heuristic"}
                }

    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False}

def _extract_product_name(lines: List[str], clip_categories: List[Dict[str, Any]]) -> Dict[str, Any]:
    # 0. Explicit "Product : <name>" / "Product Name : <name>" declaration — most reliable.
    for line in lines:
        pm = re.search(r'\bproduct(?:\s*name)?\s*[:\-–]\s*([A-Za-z][A-Za-z /&\'-]{2,40})', line, re.IGNORECASE)
        if pm:
            name = re.sub(r'\s*[\(\[].*$', '', pm.group(1)).strip(" -/&")
            name = re.sub(r'\s+', ' ', name).title()
            if len(name) >= 3 and not any(k in name.lower() for k in ["mrp", "batch", "mfg", "net qty", "date", "address", "information"]):
                return {
                    "value": name,
                    "confidence": 0.95,
                    "detected_text": line,
                    "is_valid": True,
                    "details": {"source": "product_label_declaration"},
                }

    # Dynamic commodity detection matching commodity descriptors anywhere on packaging
    commodity_patterns = [
        r'\b([A-Za-z\s]{3,30}(?:bar|soap|shampoo|oil|ghee|chips|namkeen|noodles|vermicelli|vermicelly|sevai|semiya|semia|poha|sabudana|pasta|macaroni|biscuit|biscuits|cookie|cookies|rusk|tea|coffee|juice|drink|beverage|water|atta|maida|flour|rice|masala|spice|spices|detergent|powder|cleaner|cream|lotion|sauce|ketchup|milk|paneer|cheese|chocolate|candy|honey)\b)'
    ]
    # Leading filler / instruction-verb tokens to peel off a captured phrase so a
    # cooking line like "Rinse the rice thoroughly" yields the generic name "Rice".
    _lead_filler = re.compile(
        r'^(?:the|a|an|of|for|with|and|to|your|our|new|pre|cooking|cook|rinse|soak|wash|'
        r'drain|boil|simmer|serve|add|measure|place|bring|reduce|heat|stir|knead|saute|'
        r'method|step\s*\d*|gently|thoroughly|completely|hot|cold|water|cup|cups|'
        r'meet|enjoy|try|buy|store|keep|shake|drained|cooked|boiled|soaked|prepared|'
        r'remaining|fresh|premium|pure|natural|real)\s+',
        re.IGNORECASE,
    )
    _trailing_filler = re.compile(
        r'\s+(?:thoroughly|completely|gently|well|hot|cold|first|now|today|daily|'
        r'and|to|for|with|in|on)$',
        re.IGNORECASE,
    )
    # A commodity noun sitting inside the ingredients declaration (e.g. "Wheat Flour"
    # inside "Malt [Barley, Wheat Flour, Wheat, Millet], Milk Solids, Sugar, ...") is a
    # component of the recipe, not the product's own generic name -- do not let it win
    # just because it happens to match a commodity keyword. Detect that context from
    # the neighbouring OCR fragments (ingredient lists are dense runs of short,
    # comma/percentage-heavy tokens like these) rather than the matched line alone.
    _ingredient_ctx = re.compile(
        r'\bingredient|\ballergen\b|\bcontains\b.{0,25}(?:wheat|milk|soy|nuts?|gluten|egg)|'
        r'\bmalt\b|\bgluten\b|\bsolids?\b|\bregulators?\b|\bvitamins?\b|\bminerals?\b|'
        r'\bacidity\b|\bpreservative|\bstabili[sz]er|\bemulsifier',
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        line_l = line.lower()
        for pat in commodity_patterns:
            m = re.search(pat, line_l, re.IGNORECASE)
            if not m:
                continue
            window = " ".join(lines[max(0, i - 3):i] + lines[i + 1:i + 4])
            if _ingredient_ctx.search(window) or _ingredient_ctx.search(line):
                continue
            matched_phrase = m.group(1).strip()
            prev = None
            while matched_phrase and matched_phrase != prev:
                prev = matched_phrase
                matched_phrase = _lead_filler.sub("", matched_phrase).strip()
                matched_phrase = _trailing_filler.sub("", matched_phrase).strip()
            matched_phrase = matched_phrase.title()
            if len(matched_phrase) >= 3 and not any(k in matched_phrase.lower() for k in ["mrp", "batch", "mfg", "net", "date", "step"]):
                return {
                    "value": matched_phrase,
                    "confidence": 0.90,
                    "detected_text": line,
                    "is_valid": True,
                    "details": {"source": "packaging_descriptor"}
                }

    # Dynamic CLIP Visual AI Classification (generalizes to any commodity category without hardcoding)
    if clip_categories and len(clip_categories) > 0:
        top_cat = clip_categories[0]
        cleaned_cat = top_cat["label"].split(" or ")[0].title()
        conf = top_cat["confidence"]
        return {
            "value": cleaned_cat,
            "confidence": round(conf * 0.92, 2),
            "detected_text": f"CLIP Visual Identity: {cleaned_cat}",
            "is_valid": True,
            "details": {"source": "clip_zero_shot_vision", "confidence": conf}
        }

    return {"value": "Packaged Commodity", "confidence": 0.40, "detected_text": None, "is_valid": False}

def _extract_net_quantity(lines: List[str], full_text: str) -> Dict[str, Any]:
    # 1. Multi-Pack / Combo Quantity Check (e.g., 4 UNITS X 125 g, 4 x 125g, UNITS X 125 g)
    multi_match = re.search(
        r'(?:(\d+)\s*(?:units?|u|n|pcs)?\s*[xX*]\s*)?(\d+(?:\.\d+)?)\s*(kg|kilograms?|g|grams?|gms?|gm|ml|millilitres?|l|litres?)\b',
        full_text,
        re.IGNORECASE
    )
    if multi_match:
        multiplier_str = multi_match.group(1)
        per_unit_str = multi_match.group(2)
        unit_raw = multi_match.group(3).lower()

        # Standardize SI unit
        if unit_raw in ["g", "gm", "gms", "gram", "grams"]:
            standard_unit = "g"
        elif unit_raw in ["kg", "kilogram", "kilograms"]:
            standard_unit = "kg"
        elif unit_raw in ["ml", "millilitre", "millilitres"]:
            standard_unit = "ml"
        elif unit_raw in ["l", "litre", "litres"]:
            standard_unit = "l"
        else:
            standard_unit = unit_raw

        is_prohibited_symbol = unit_raw in ["gm", "gms", "kilo", "kilos", "ltr"]

        if multiplier_str:
            multiplier = int(multiplier_str)
            total_amount = multiplier * float(per_unit_str)
            display_val = f"{total_amount:.0f} {standard_unit}" if total_amount.is_integer() else f"{total_amount} {standard_unit}"
            return {
                "value": display_val,
                "confidence": 0.94,
                "detected_text": multi_match.group(0),
                "is_valid": not is_prohibited_symbol,
                "details": {
                    "number": total_amount,
                    "multi_pack": True,
                    "units": multiplier,
                    "per_unit": float(per_unit_str),
                    "standard_unit": standard_unit,
                    "is_prohibited_symbol": is_prohibited_symbol,
                    "prohibited_symbol_reason": "Rule 6(1)(c) mandates standard symbol 'g' or 'kg'. 'gms' is prohibited." if is_prohibited_symbol else None
                }
            }

    _price_ctx = re.compile(
        r'₹|\brs\.?\b|\bmrp\b|\bm\.?r\.?p\b|\bsp\b|/-|per\s*(?:kg|kilo|g|gm|gram|ml|l|litre|100|pc|piece|unit|no)\b'
        r'|\bper\b\s*\d|%|\brda\b|kcal|k\s*j\b|energy|protein|carbohydrate|sugar|sodium|fat\b|fibre|fiber|magnesium'
        r'|histidine|leucine|lysine|valine|serving',
        re.IGNORECASE,
    )

    # 2a. Trigger-anchored binding: a "NET WT / NET VOL / NET CONTENTS / NET QUANTITY"
    #     label whose value landed in a different OCR box (e.g. "NET VOL." | "1 L", or
    #     "NET QUANTITY:" | "Ikg" where OCR read the leading '1' as I/l/|). Bind the
    #     trigger to the nearest following value. Runs BEFORE the generic scan so a
    #     stray price fragment can never win over the real declaration.
    # OCR-tolerant: "quantity" often reads "quanticy"/"quantlty"/"quantiy"; "wt" as "wl".
    _nq_trigger = re.compile(r'net\s*(?:vol(?:ume)?|w[tl]|weight|contents?|q[uü]\w{1,7}|qty)\b', re.IGNORECASE)
    _nq_unit_map = {
        "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
        "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
        "ml": "ml", "millilitre": "ml", "millilitres": "ml",
        "l": "l", "il": "l", "ltr": "l", "litre": "l", "litres": "l",
    }
    _nq_range = {"g": (1.0, 5000.0), "kg": (0.02, 60.0), "ml": (1.0, 5000.0), "l": (0.05, 30.0)}
    deferred_review = None  # "declaration present, value illegible" — only used if nothing else matches
    for i, line in enumerate(lines):
        if not _nq_trigger.search(line):
            continue
        window = " ".join(lines[i:i + 4])
        # OCR digit confusion: a leading "1" glued to a unit reads as I / l / | ("Ikg").
        window_fixed = re.sub(r'(?<![A-Za-z0-9])[Il|](\s?)(kgs?|gms?|g|ml|litres?|ltr|l)\b', r'1\1\2', window)
        bind = re.search(
            r'(\d+(?:\.\d+)?)\s*[a-z]{0,2}?\.?\s*'
            r'(kgs?|kilograms?|gms?|grams?|g|millilitres?|ml|litres?|ltr|l)\b',
            window_fixed, re.IGNORECASE
        )
        if bind:
            raw_u = bind.group(2).lower()
            std = _nq_unit_map.get(raw_u)
            if std and _nq_range[std][0] <= float(bind.group(1)) <= _nq_range[std][1]:
                is_proh = raw_u in ("gms", "ltr", "litres", "kilos")
                return {
                    "value": f"{bind.group(1)} {std}",
                    "confidence": 0.86,
                    "detected_text": f"{line.strip()} -> {bind.group(0).strip()}",
                    "is_valid": not is_proh,
                    "details": {
                        "number": float(bind.group(1)),
                        "raw_unit": raw_u,
                        "standard_unit": std,
                        "is_prohibited_symbol": is_proh,
                        "prohibited_symbol_reason": (
                            "Rule 6(1)(c) mandates the standard symbol ('g', 'kg', 'ml', 'l')."
                            if is_proh else None
                        ),
                        "cross_line_binding": True,
                    },
                }
        # If it reads like a count commodity ("... 36 NUMBER PATCHES"), let the count
        # block below handle it rather than flagging for review.
        if re.search(
            r'\d+\s*(?:x\s*)?(?:numbers?|nos?|pcs?|pieces?|patch(?:es)?|sheets?|tablets?|'
            r'caps?(?:ules?)?|wipes?|pads?|sachets?|strips?|pills?|units?|count)\b',
            window, re.IGNORECASE
        ):
            continue
        # Trigger + a bare standard unit of weight/measure, but no legible number:
        # remember it as a fallback, but let the generic + count scans try first
        # (OCR may have the real value on another line, e.g. a Paddle-only "1 L" box).
        if deferred_review is None and re.search(
            r'(?:^|\s)(?:kgs?|gms?|g|ml|millilitres?|litres?|ltr|l)(?:\s|$|\.|,|;)', window, re.IGNORECASE
        ):
            deferred_review = {
                "value": "Net-quantity declaration present (value not legible)",
                "confidence": 0.3,
                "detected_text": window[:100].strip(),
                "is_valid": False,
                "details": {
                    "number": None,
                    "standard_unit": None,
                    "is_prohibited_symbol": False,
                    "prohibited_symbol_reason": None,
                    "needs_review": True,
                },
            }

    # 2b. Standard Single Net Quantity Regex (generic scan, no trigger required)
    qty_regex = re.compile(
        r'(?:net\s*(?:contents|quantity|qty|weight|wt|vol|volume)?[:\.\s]*)?'
        r'(\d+(?:\.\d+)?)\s*(kg|kilograms?|kilos?|g|grams?|gms?|gm|ml|millilitres?|l|ltr|litres?|n|units?|u|pcs|pieces)\b',
        re.IGNORECASE
    )

    for line in lines:
        match = qty_regex.search(line)
        if not match:
            continue
        num_str, unit_raw = match.groups()
        unit_norm = unit_raw.lower()

        # A price / unit-price / nutrition line is never the net-quantity declaration
        # ("SP ₹ 152.00 per kg", "₹ 152.0U", "520 mg", "76 g" carbohydrate row, ...).
        if _price_ctx.search(line):
            continue
        # Bare count symbol 'n'/'u' from a token like "152.0U": counts are whole
        # numbers and need supporting context, otherwise it is OCR noise off a price.
        if unit_norm in ("n", "u") and (
            "." in num_str
            or not re.search(r'net\s*(?:qty|quantity|contents?|pack)|\bnos?\b|\bnumbers?\b', line, re.IGNORECASE)
        ):
            continue

        is_prohibited_symbol = unit_norm in ["gm", "gms", "kilo", "kilos", "ltr", "litres"]

        standard_unit = unit_norm
        if unit_norm in ["g", "gm", "gms", "gram", "grams"]:
            standard_unit = "g"
        elif unit_norm in ["kg", "kilo", "kilos", "kilogram"]:
            standard_unit = "kg"
        elif unit_norm in ["ml", "millilitre", "millilitres"]:
            standard_unit = "ml"
        elif unit_norm in ["l", "ltr", "litre", "litres"]:
            standard_unit = "l"
        elif unit_norm in ["n", "u", "unit", "units", "pcs", "pieces"]:
            standard_unit = "N"

        return {
            "value": f"{num_str} {standard_unit}",
            "confidence": 0.92,
            "detected_text": match.group(0),
            "is_valid": not is_prohibited_symbol,
            "details": {
                "number": float(num_str),
                "raw_unit": unit_raw,
                "standard_unit": standard_unit,
                "is_prohibited_symbol": is_prohibited_symbol,
                "prohibited_symbol_reason": "Rule 6(1)(c) mandates standard symbol 'g' or 'kg'. 'gms'/'kilos' is prohibited." if is_prohibited_symbol else None
            }
        }

    # 3. Count-based commodities sold by number (e.g. "36 NUMBER PATCHES", "24 PIECES",
    #    "12 SHEETS", "10 N"). Legal Metrology permits declaring net quantity by number
    #    for such articles; the unit is standardised to the statutory symbol 'N'.
    _count_noun = (
        r'patch(?:es)?|pieces?|sheets?|tablets?|caps(?:ules?)?|wipes?|pads?|'
        r'sachets?|strips?|pills?|tea\s*bags?|bags?|rolls?|napkins?|units?'
    )
    _count_unit = r'numbers?|nos?\.?|pcs?\.?|units?|count|' + _count_noun + r'|u|n'
    count_regex = re.compile(
        r'(?:net\s*(?:contents?|quantity|qty|weight|wt|pack)?[:\.\s]*)?'
        r'(\d{1,4})\s*(?:x\s*)?(' + _count_unit + r')\b'
        r'(?:\s+(' + _count_noun + r'))?',
        re.IGNORECASE
    )

    def _count_line_priority(ln: str) -> int:
        low = ln.lower()
        if re.search(r'net\s*(?:quantity|contents?|qty|weight|wt)', low):
            return 0
        if re.search(r'\d\s*(?:numbers?\b|nos?\b|n\b)', low):   # "36 NUMBER", "10 NOS"
            return 1
        if re.search(r'\bsize\b|\+|dimension', low):            # composition / size lines
            return 3
        return 2

    # Explicit "net quantity" lines first, then count markers, then any line, then the
    # joined text so a split "NET QUANTITY:" / "36 NUMBER PATCHES" still binds.
    for line in sorted(lines, key=_count_line_priority) + [full_text]:
        cm = count_regex.search(line)
        if not cm:
            continue
        num_str = cm.group(1)
        unit_word = cm.group(2).lower().strip(".")
        noun = (cm.group(3) or "").strip()
        line_has_trigger = bool(re.search(r'net\s*(?:quantity|contents?|qty|weight|wt)', line, re.IGNORECASE))
        # Bare "n" / "u" is only trustworthy beside a net-quantity trigger or an article
        # noun; multi-letter count words ("number", "patches", ...) stand on their own.
        if unit_word in ("n", "u") and not (line_has_trigger or noun):
            continue
        generic = {"n", "u", "number", "numbers", "no", "nos", "pc", "pcs", "count", "unit", "units"}
        display_noun = noun or ("" if unit_word in generic else unit_word)
        display_val = f"{num_str} N" + (f" ({display_noun.title()})" if display_noun else "")
        return {
            "value": display_val,
            "confidence": 0.9,
            "detected_text": cm.group(0).strip(),
            "is_valid": True,
            "details": {
                "number": float(num_str),
                "raw_unit": noun or unit_word,
                "standard_unit": "N",
                "count_commodity": True,
                "is_prohibited_symbol": False,
                "prohibited_symbol_reason": None
            }
        }

    if deferred_review is not None:
        return deferred_review
    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False, "details": None}

def _tax_inclusive_declared(text: Optional[str]) -> bool:
    """
    True when the packaging carries the statutory 'inclusive of all taxes' MRP
    declaration. Tolerant of OCR noise (dropped 'I' / 'V', joined words such as
    'NCLUSIEOFALL TAXES'). Returns False when the tax wording is explicitly
    *exclusive* ('taxes extra', 'excluding taxes', ...), which must stay non-compliant.
    """
    if not text:
        return False
    t = text.lower()

    # 1. Canonical phrase on clean OCR -> conclusively inclusive
    if re.search(r'incl(?:usive|\.)?[\s.,]*(?:of)?[\s.,]*(?:all)?[\s.,]*taxe?s', t):
        return True

    # 2. Explicitly exclusive / negated tax wording -> not inclusive
    if re.search(r'tax(?:es)?\s*(?:are\s*)?(?:extra|additional|as\s*applicable|not\s*incl)', t):
        return False
    if re.search(r'(?:without|excl(?:uding|usive\s*of)?|plus|\+)\s*[a-z\s]{0,12}tax(?:es)?', t):
        return False
    if re.search(r'\bnot\b[a-z\s]{0,20}\btax(?:es)?\b', t):
        return False

    # 3. OCR-tolerant: collapse punctuation, then look for an 'incl...'-ish stem that
    #    runs (optionally through 'of all') into 'tax(es)'.
    compact = re.sub(r'[^a-z]+', ' ', t).strip()
    if re.search(r'\bi?nclu?s[a-z]*\s*(?:of\s*)?(?:all\s*)?taxe?s\b', compact):
        return True
    if re.search(r'\bi?nclu\w*\b(?:\s+\w+){0,3}\s+taxe?s\b', compact):
        return True
    if re.search(r'\btaxe?s\b(?:\s+\w+){0,3}\s+i?nclu\w*\b', compact):
        return True
    # 4. Heavily-mangled dot-matrix / curved-surface OCR of "MRP (inclusive of all
    #    taxes)": the parenthetical survives only as garble, but an "MRP" marker
    #    followed within a short span by a bracket and a fuzzy "taxes" ("...axes",
    #    "...axesl", "yaxes") is, on a retail pack, always that statutory phrase.
    if re.search(r'm\W?r\W?p\b.{0,12}[\(\[].{0,24}(?:t|y)?axe[sf]?', t):
        return True
    if re.search(r'\ba[l1i]{1,2}[a-z]{0,4}axe[sf]', compact):   # "all ... axes" run-on
        return True
    # 5. "incl. of all taxes" reduced to e.g. "iircl: or all (axes)" — an incl-ish stem
    #    within a few chars of "all", within a few chars of a fuzzy "(taxes)".
    if re.search(r'i+n?r?c?lu?[a-z]*.{0,10}\ball\b.{0,8}\(?\s*(?:of\s*)?t?axe[sf]', t):
        return True
    return False

def _extract_mrp(lines: List[str], full_text: str, spatial_block: List[str] = None) -> Dict[str, Any]:
    effective_lines = (spatial_block + lines) if spatial_block else lines

    # Check for mandatory statutory tax statement across the full packaging text
    # (OCR-tolerant: handles noise like "NCLUSIEOFALL TAXES" for "INCLUSIVE OF ALL TAXES").
    has_taxes_declared = _tax_inclusive_declared(full_text)

    # An "MRP" marker, tolerant of OCR noise ("MR? Ps.", "M.R.P", "MRP Rs").
    _mrp_marker = re.compile(r'\bmrp\b|m[\W_]{0,2}r[\W_]{0,2}[p?][\W_]|maximum\s*retail|retail\s*price', re.IGNORECASE)

    # Word-boundary line trigger for step 1 below. A bare substring check for "rs"
    # false-positives on any word that merely contains those two letters in sequence
    # ("Regulators", "years", "confirms", ...) -- e.g. an ingredient-list fragment like
    # "Acidity Regulators [INS 501(ii)]" would otherwise be mistaken for a price line
    # and its INS additive code (501) read as the MRP amount.
    _mrp_line_trigger = re.compile(r'₹|\bmrp\b|\brs\.?\b|\binr\b|maximum\s*retail|retail\s*price', re.IGNORECASE)

    # Regex for price pattern
    mrp_regex = re.compile(
        r'(?:mrp|maximum\s*retail\s*price)?[\s:\.]*(?:₹|rs\.?|inr)?[\s]*(\d+(?:\.\d{1,2})?)'
        r'(?:[\s\w\(\)\.\,\/]*(incl(?:usive)?\s*(?:of)?\s*(?:all)?\s*taxes?))?',
        re.IGNORECASE
    )

    # 0. MRP marker whose amount landed in a separate OCR box (label sticker: "MRP Rs."
    #    and "86.00" often come out as non-adjacent boxes). Scan a window both ways.
    for idx, line in enumerate(effective_lines):
        if not _mrp_marker.search(line):
            continue
        if re.search(r'(?:₹|rs\.?|inr)?\s*\d+\.\d{2}\b', line) and re.search(r'\d\.\d{2}', line):
            break  # amount is on the marker line itself — let the main loop handle it
        window = effective_lines[max(0, idx - 5):idx] + effective_lines[idx + 1:idx + 8]
        prices = []
        for w in window:
            wl = w.lower()
            if re.search(r'kcal|kj\b|mg\b|mcg\b|per\s*100|vitamin|sodium|protein|fat\b|sugar|carbohyd|calcium|iron|zinc', wl):
                continue
            for pm in re.finditer(r'(?<![\d.])(\d{1,4}\.\d{2})(?!\d)', w):
                prices.append(float(pm.group(1)))
            for pm in re.finditer(r'(?<![\d.])([1-9]\d{1,4})\s*/\-', w):
                prices.append(float(pm.group(1)))
        uniq = sorted({p for p in prices if 2.0 <= p <= 50000})
        if len(uniq) == 1:
            amt = uniq[0]
            return {
                "value": f"₹ {amt:.2f}",
                "confidence": 0.85,
                "detected_text": f"{line.strip()} -> {amt:.2f}",
                "is_valid": has_taxes_declared,
                "details": {
                    "amount": amt,
                    "currency": "INR (₹)",
                    "inclusive_of_taxes": has_taxes_declared,
                    "tax_declaration_found": has_taxes_declared,
                    "cross_line_binding": True,
                },
            }

    # 1. Line-by-Line inspection with Cross-Line "See Below / See Crimp" Spatial Linking
    for idx, line in enumerate(effective_lines):
        if _mrp_line_trigger.search(line):
            # Standard single-line price extraction
            match = mrp_regex.search(line)
            if match and match.group(1):
                try:
                    amount = float(match.group(1))
                    if 0.5 <= amount <= 50000:
                        tax_in_line = _tax_inclusive_declared(line)
                        tax_ok = tax_in_line or has_taxes_declared
                        return {
                            "value": f"₹ {amount:.2f}",
                            "confidence": 0.94,
                            "detected_text": line,
                            "is_valid": tax_ok,
                            "details": {
                                "amount": amount,
                                "currency": "INR (₹)",
                                "inclusive_of_taxes": tax_ok,
                                "tax_declaration_found": tax_ok
                            }
                        }
                except ValueError:
                    pass

            # 2. Cross-Line "See Below / See Crimp / Dot-Matrix Stamp" Spatial Linker
            # If the pre-printed header says "MRP ... SEE BELOW" or "SEE CRIMP",
            # check the immediately downward lines for factory ink-jet stamped price
            for next_idx in range(idx + 1, min(idx + 5, len(effective_lines))):
                next_line = effective_lines[next_idx]
                next_lower = next_line.lower()

                # Skip lines that clearly represent net quantity, weights, dates, or manufacturer survey details
                if any(q in next_lower for q in ["net", "qty", "quantity", "weight", "mfd", "batch", "lot", "survey", "plot", "exp", "use before"]):
                    continue
                if re.search(r'\b\d+\s*(?:g|gm|gms|kg|ml|l|ltr)\b', next_lower) and not re.search(r'(?:\/|\bper\b)\s*(?:g|gm|kg|ml|l)', next_lower):
                    continue

                # Stamped price pattern: currency prefix (₹, Rs, {, ?) OR standalone price with /- e.g. "523 /-" or "523.00"
                stamp_match = re.search(r'(?:₹|rs\.?|inr|[\{\?\*])\s*([1-9]\d{0,4}(?:\.\d{1,2})?)', next_line, re.IGNORECASE)
                if not stamp_match:
                    stamp_match = re.search(r'\b([1-9]\d{0,4}(?:\.\d{2})?)\s*(?:\/|\/\-)', next_line)
                if not stamp_match:
                    stamp_match = re.search(r'^\s*([1-9]\d{1,4}(?:\.\d{2})?)\s*$', next_line)

                if stamp_match and stamp_match.group(1):
                    try:
                        amt = float(stamp_match.group(1))
                        if 1.0 <= amt <= 50000:
                            return {
                                "value": f"₹ {amt:.2f}",
                                "confidence": 0.92,
                                "detected_text": f"{line} -> {next_line}",
                                "is_valid": has_taxes_declared,
                                "details": {
                                    "amount": amt,
                                    "currency": "INR (₹)",
                                    "inclusive_of_taxes": has_taxes_declared,
                                    "tax_declaration_found": has_taxes_declared,
                                    "cross_line_binding": True
                                }
                            }
                    except ValueError:
                        pass

    # 3. Fallback: Standalone ink-jet currency stamp in full_text
    stamp_fallback = re.search(r'(?:₹|rs\.?)\s*([1-9]\d{0,4}(?:\.\d{1,2})?)\s*(?:\/|\/\-|\b)', full_text, re.IGNORECASE)
    if stamp_fallback:
        try:
            amt = float(stamp_fallback.group(1))
            if 1.0 <= amt <= 50000:
                return {
                    "value": f"₹ {amt:.2f}",
                    "confidence": 0.86,
                    "detected_text": stamp_fallback.group(0),
                    "is_valid": has_taxes_declared,
                    "details": {
                        "amount": amt,
                        "currency": "INR (₹)",
                        "inclusive_of_taxes": has_taxes_declared,
                        "tax_declaration_found": has_taxes_declared
                    }
                }
        except ValueError:
            pass

    # 4. Last resort: the pack clearly carries an MRP label, but the amount is an
    #    ink-jet stamp OCR'd into its own box with no currency mark ("220/-", "₹220"
    #    read as "220"). Accept a single plausible stamped price in that case.
    if _mrp_marker.search(full_text) or re.search(r'maximum\s*retail\s*price', full_text, re.IGNORECASE):
        # Strongest signal: a "<amount>/-" ink-jet stamp. Only if there is none, fall
        # back to a lone numeric line (noisier — needs to be unambiguous).
        dash = sorted({float(s) for s in re.findall(r'(?<![\d.])([1-9]\d{1,4})\s*/\-', full_text)
                       if 5.0 <= float(s) <= 50000})
        lone = sorted({float(s) for s in re.findall(r'(?:^|\n)\s*([1-9]\d{1,4})(?:\.\d{2})?\s*(?:$|\n)', full_text)
                       if 5.0 <= float(s) <= 50000})
        amt = dash[0] if len(dash) == 1 else (lone[0] if (not dash and len(lone) == 1) else None)
        if amt is not None:
            return {
                "value": f"₹ {amt:.2f}",
                "confidence": 0.78,
                "detected_text": f"MRP stamp: {amt:.0f}",
                "is_valid": has_taxes_declared,
                "details": {
                    "amount": amt,
                    "currency": "INR (₹)",
                    "inclusive_of_taxes": has_taxes_declared,
                    "tax_declaration_found": has_taxes_declared,
                    "stamp_fallback": True,
                },
            }

    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False, "details": None}

_DATE_TOKEN = re.compile(
    r'(\b\d{1,2}[\/\-.]\d{1,2}[\/\-.](?:20\d{2}|\d{2})\b(?![\d\-/.])|'
    r'\b(?:0?[1-9]|1[0-2])[\/\-](?:20\d{2}|\d{2})\b(?![\d\-/.])|'
    r'\b(?:\d{1,2}\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[\s,\'/-]*(?:19|20)?\d{2}\b)',
    re.IGNORECASE,
)
# Strict full calendar date with an unambiguous 20xx year — used for the two-date heuristic.
_FULL_DATE = re.compile(r'\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](20\d{2})\b(?![\d\-/.])')
# "MFG"/"MFD"/"PKD" as a *date* label — not the "Manufactured BY" attribution phrase.
_MFG_TRIGGER = re.compile(
    r'\b(?:mfg|mfd|manufactured|packed|pkd|pkg|date\s*of\s*(?:mfg|manufacture|packing|pkg))\b'
    r'(?!\s*[:.&\-]?\s*(?:by|and)\b)',
    re.IGNORECASE,
)
_EXPIRY_TRIGGER = re.compile(r'use\s*by|use\s*before|best\s*before|expiry|exp\b|\bbb\b|consume\s*before', re.IGNORECASE)
_CONTACT_LINE = re.compile(r'toll\s*free|1800|helpline|\btel\b|\bph\b|phone|\+\s*91|call\s*us|@', re.IGNORECASE)


def _extract_mfg_date(lines: List[str], full_text: str, date_spatial_block: List[str] = None) -> Dict[str, Any]:
    combined_text = (" ".join(date_spatial_block) + " " + full_text) if date_spatial_block else full_text
    scan_lines = (date_spatial_block or []) + lines

    # 1. Two-date heuristic (most reliable for Indian food stickers where OCR scatters
    #    the "Mfd." / "Use By" labels far from their values): with exactly two full
    #    dd/mm/20yy dates >6 months apart, the earlier one is the manufacture date.
    dated = _distinct_full_dates(full_text)
    if len(dated) == 2 and 180 < (dated[-1][0] - dated[0][0]).days < 366 * 6:
        raw = dated[0][1]
        return {
            "value": raw.upper(),
            "confidence": 0.85,
            "detected_text": f"earliest printed date: {raw}",
            "is_valid": True,
            "details": {"type": "Manufacturing / Packing Date", "verbatim": True, "source": "two_date_heuristic"},
        }

    # 2. Trigger-anchored binding: "Mfd." / "MFG" label whose date landed in a
    #    separate OCR box ("Mfd:" | "03/07/2026"). Return the date exactly as printed.
    for i, line in enumerate(scan_lines):
        if not _MFG_TRIGGER.search(line) or _EXPIRY_TRIGGER.search(line):
            continue
        ordered = [line] + [scan_lines[j] for j in range(i + 1, min(i + 6, len(scan_lines)))] \
                         + [scan_lines[j] for j in range(max(0, i - 3), i)]
        for cand in ordered:
            if _EXPIRY_TRIGGER.search(cand) or _CONTACT_LINE.search(cand) or re.search(r'batch|b\.?\s*no|lot\s*no|fssai|survey\s*no|d\.?\s*no', cand, re.IGNORECASE):
                continue
            dm = _DATE_TOKEN.search(cand)
            if dm:
                val = re.sub(r'\s+', ' ', dm.group(1)).strip().upper()
                return {
                    "value": val,
                    "confidence": 0.92,
                    "detected_text": f"{line.strip()} -> {val}",
                    "is_valid": True,
                    "details": {"type": "Manufacturing / Packing Date", "verbatim": True},
                }

    # 2. Trigger + date adjacent in the flat text
    date_patterns = [
        r'#\s*(\d{1,2}[\/\-]\d{2,4})',
        r'(?:mfg|mfd|packed|pkd|pkg|date\s*of\s*mfg|date\s*of\s*packing)[\s:\.\-]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'(?:mfg|mfd|packed|pkd|pkg|date\s*of\s*mfg|date\s*of\s*packing)[\s:\.\-]*(\d{1,2}[\/\-]\d{2,4})',
        r'(?:mfg|mfd|pkd|packed|manufactured)(?!\s*[:.&\-]?\s*by\b)[\s:\.\-]*((?:\d{1,2}\s+)?[A-Za-z]{3,9}[\s,]+\d{2,4})',
        r'(?:mfg|mfd|pkd|packed|pkg)(?!\s*[:.&\-]?\s*by\b)[\s\w\.\,\:\-]{0,25}\b(0[1-9]|1[0-2])[\/\-](\d{2,4})\b',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, combined_text, re.IGNORECASE)
        if match:
            if match.lastindex and match.lastindex >= 2:
                date_str = f"{match.group(1)}/{match.group(2)}"
            else:
                date_str = match.group(1) if match.groups() else match.group(0)
            return {
                "value": date_str.upper(),
                "confidence": 0.90,
                "detected_text": match.group(0),
                "is_valid": True,
                "details": {"type": "Manufacturing / Packing Date"}
            }

    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False}


def _distinct_full_dates(text: str):
    """All distinct dd/mm/20yy dates in `text`, as (date, raw_string) sorted ascending."""
    import datetime as _dt
    seen = {}
    for m in _FULL_DATE.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= d <= 31 and 1 <= mo <= 12 and 2000 <= y <= 2100):
            # tolerate mm/dd order
            d, mo = mo, d
            if not (1 <= d <= 31 and 1 <= mo <= 12):
                continue
        try:
            key = _dt.date(y, mo, d)
        except ValueError:
            continue
        seen.setdefault(key, m.group(0))
    return sorted(seen.items())


def _extract_best_before(lines: List[str], full_text: str, date_spatial_block: List[str] = None) -> Dict[str, Any]:
    combined_text = (" ".join(date_spatial_block) + " " + full_text) if date_spatial_block else full_text
    scan_lines = (date_spatial_block or []) + lines

    # 1. Trigger-anchored binding: "Use By" / "Best Before" label whose date landed in
    #    a separate OCR box ("Use By" | "02/07/2028"). Return the date exactly as printed.
    for i, line in enumerate(scan_lines):
        if not _EXPIRY_TRIGGER.search(line):
            continue
        ordered = [line] + [scan_lines[j] for j in range(i + 1, min(i + 6, len(scan_lines)))] \
                         + [scan_lines[j] for j in range(max(0, i - 3), i)]
        for cand in ordered:
            if _CONTACT_LINE.search(cand) or re.search(r'\bmfd\b|\bmfg\b|manufactured|batch|b\.?\s*no|lot\s*no|fssai|survey\s*no|d\.?\s*no', cand, re.IGNORECASE):
                continue
            dm = _DATE_TOKEN.search(cand)
            if dm:
                val = re.sub(r'\s+', ' ', dm.group(1)).strip().upper()
                return {
                    "value": val,
                    "confidence": 0.9,
                    "detected_text": f"{line.strip()} -> {val}",
                    "is_valid": True,
                    "details": {"period_or_date": val, "is_date": True, "verbatim": True},
                }

    bb_patterns = [
        r'@\s*(\d{1,2}[\/\-]\d{2,4})',
        r'(?:use\s*before|use\s*by|best\s*before|exp(?:iry)?\s*date)[\s:\.\-]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'(?:use\s*before|use\s*by|best\s*before|exp(?:iry)?\s*date)[\s:\.\-]*(\d{1,2}[\/\-]\d{2,4})',
        r'(?:use\s*before|use\s*by|best\s*before|exp(?:iry)?)[\s:\.\-]*((?:\d{1,2}\s+)?[A-Za-z]{3,9}[\s,]+\d{2,4})',
    ]
    for pattern in bb_patterns:
        match = re.search(pattern, combined_text, re.IGNORECASE)
        if match:
            val_str = match.group(1) if match.groups() else match.group(0)
            return {
                "value": val_str.upper(),
                "confidence": 0.89,
                "detected_text": match.group(0),
                "is_valid": True,
                "details": {"period_or_date": val_str, "is_date": True}
            }

    # Two-date heuristic mirror: with exactly two full 20xx dates >6 months apart, the
    # later one is the 'Use By' / expiry date.
    dated = _distinct_full_dates(full_text)
    if len(dated) >= 2 and (dated[-1][0] - dated[0][0]).days > 180:
        raw = dated[-1][1]
        return {
            "value": raw.upper(),
            "confidence": 0.8,
            "detected_text": f"latest printed date: {raw}",
            "is_valid": True,
            "details": {"period_or_date": raw, "is_date": True, "verbatim": True, "source": "two_date_heuristic"},
        }

    # Relative shelf-life period ("best before 9 months from manufacture") — lowest
    # priority, and flagged as a period (not a date) so Rule 6(1)(d) does not treat it
    # as the month & year of manufacture.
    rel = re.search(
        r'(?:use\s*before|use\s*by|best\s*before|exp(?:iry)?)[\s\w]{0,20}?'
        r'((?:\d{1,2}|one|two|three|four|five|six|nine|ten|twelve|twenty[\s-]?four|eighteen)\s*'
        r'(?:months?|days?|weeks?|years?))',
        combined_text, re.IGNORECASE,
    )
    if rel:
        val_str = re.sub(r'\s+', ' ', rel.group(1)).strip()
        return {
            "value": val_str,
            "confidence": 0.7,
            "detected_text": rel.group(0),
            "is_valid": True,
            "details": {"period_or_date": val_str, "is_date": False, "is_relative_period": True}
        }
    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False}

def _extract_manufacturer(lines: List[str], full_text: str, spatial_block: List[str] = None) -> Dict[str, Any]:
    # Attribution phrases only. Bare "mfd:"/"mfg:" are NOT triggers — they usually
    # precede an ink-jet manufacturing DATE ("MFD: 28/01/24"), not the packer name.
    triggers = [
        "manufactured by", "manufactured &", "manufactured and",
        "mfg by", "mfg. by", "mfg: by", "mfg:by", "mfg.by",
        "mfd by", "mfd. by", "mfd: by",
        "packed by", "pkd by", "packed & marketed", "packed and marketed",
        "marketed by", "marketed &", "marketed and", "mktd by", "mktd. by", "mktd: by",
        "imported by", "importer :", "brand owner",
    ]
    _legal_suffix = re.compile(
        r'(?:Pvt\.?\s*Ltd\.?|Private\s*Limited|Limited|Ltd\.?|LLP|Industries|Enterprises|'
        r'Foods|Beverages|Consumer\s*(?:Care|Products)|Mills|Agro|Exports?)',
        re.IGNORECASE,
    )
    _addr_structure = re.compile(
        r'reg[dt]\.?\s*of+i?c?e?|registered\s*off|corp\.?\s*off|head\s*off|works\s*:|'
        r'plot\s*no|survey\s*no|khasra|gala\s*no|\bgate\b|\broad\b|\bstreet\b|\bnagar\b|'
        r'\bestate\b|industrial|\bsector\b|\bphase\b|\bvillage\b|\btaluka\b|\bdist\b|'
        r'\bp\.?o\.?\b|highway|\bmarg\b|\blane\b',
        re.IGNORECASE,
    )

    def _looks_like_mfg(block_text: str) -> bool:
        low = block_text.lower()
        return bool(
            any(tr in low for tr in triggers)
            or _legal_suffix.search(block_text)
            or re.search(r'\b[1-9][0-9]{2}[\s\-]?[0-9]{3}\b', block_text)
        )

    candidate_blocks = []
    if spatial_block and _looks_like_mfg(" ".join(spatial_block)):
        candidate_blocks.append(spatial_block)

    for idx, line in enumerate(lines):
        line_l = line.lower()
        if any(tr in line_l for tr in triggers):
            block = [line]
            for next_idx in range(idx + 1, min(idx + 6, len(lines))):
                block.append(lines[next_idx])
            candidate_blocks.append(block)

    # Always keep the whole-label block as a fallback candidate — a trigger block can
    # be a false hit (a date stamp) while the real "... Limited, <PIN>" sits elsewhere.
    fallback_block = lines
    candidate_blocks.append(fallback_block)

    best_candidate = None
    best_score = -1

    for block in candidate_blocks:
        is_fallback = block is fallback_block
        block_text = " ".join(block)
        low = block_text.lower()
        pin = None
        pin_match = re.search(r'\b[1-9][0-9]{2}[\s\-]?[0-9]{3}\b', block_text)
        if pin_match:
            pin = re.sub(r'[\s\-]', '', pin_match.group(0))

        state = None
        for s in INDIAN_STATES:
            if re.search(rf'\b{re.escape(s)}\b', block_text, re.IGNORECASE):
                state = s
                break
        # OCR frequently mangles "Delhi - 110 006" to "Del-H0006"; recognise it.
        if not state and re.search(r'\bdel[\s\-]?h', block_text, re.IGNORECASE):
            state = "Delhi"

        corp = None
        # A run of Capitalised words (allowing ALL-CAPS acronyms) ending in a legal suffix,
        # so noise words before it ("Drain Water completelya KRBL Limited") are dropped.
        corp_match = re.search(
            r'((?:[A-Z][A-Za-z0-9&.\-]*\s+){0,4}[A-Z][A-Za-z0-9&.\-]*\s+'
            r'(?:Pvt\.?\s*Ltd\.?|Private\s*Limited|Limited|Ltd\.?|LLP|Industries|Enterprises|Foods|Beverages))',
            block_text,
        )
        if not corp_match:
            corp_match = re.search(
                r'([A-Z][A-Za-z0-9\s\,\.&\-]{2,40}(?:Pvt\.?\s*Ltd\.?|Private\s*Limited|Limited|Ltd\.?|LLP|Industries|Enterprises|Foods|Beverages|Consumer))',
                block_text, re.IGNORECASE,
            )
        has_legal_name = bool(corp_match)
        if corp_match:
            corp = corp_match.group(1).strip()
        else:
            corp = block[0]

        corp = re.sub(r'^(?:mfg|mfd|mktd|packed|manufactured|marketed)\b[\s&:\.\-]*(?:and)?\s*(?:by)?\s*[:\.\-]*\s*', '', corp, flags=re.IGNORECASE).strip()
        corp = re.sub(r'[\s,:=\-]+$', '', corp).strip()
        # A "company name" that is really a date / batch stamp ("28/01/24", "B.0 6") or
        # otherwise has no word in it is not a name.
        if not re.search(r'[A-Za-z]{3,}', corp) or re.match(r'^\W*\d[\d/\-.\s]*$', corp):
            corp = None

        has_addr = bool(_addr_structure.search(block_text))
        has_country = bool(state) or bool(re.search(r'\bindia\b|\|ndua\b|\bndia\b', low))
        has_trigger = any(tr in low for tr in triggers)

        # Complete statutory address: an explicit PIN or state, OR a named legal entity
        # sitting inside a recognisable registered-address structure within a country.
        is_complete = bool(pin) or bool(state) or (has_legal_name and has_addr and has_country)

        # A block is only a real manufacturer candidate if it carries an attribution
        # trigger, a named legal entity, or a PIN/state — never a bare stray line
        # (e.g. an ink-jet date "28/01/24" that happened to sort first).
        has_signal = bool(has_trigger or has_legal_name or pin or state)

        score = (
            (2 if pin else 0)
            + (1 if state else 0)
            + (1 if corp else 0)
            + (2 if has_legal_name else 0)
            + (1 if has_trigger else 0)
            + (1 if has_addr else 0)
            - (3 if is_fallback else 0)   # whole-label block: only wins if nothing better
        )
        if has_signal and score > best_score:
            best_score = score
            best_candidate = (corp, state, pin, block_text, is_complete)

    if best_candidate and best_score > 0:
        company_name, state_found, pin_code, combined_mfg_text, is_complete = best_candidate
        display_val = company_name
        if state_found or pin_code:
            display_val += f", {state_found or ''} {pin_code or ''}".strip()

        return {
            "value": display_val,
            "confidence": 0.92 if is_complete else 0.65,
            "detected_text": combined_mfg_text[:120],
            "is_valid": is_complete,
            "details": {
                "company_name": company_name,
                "state": state_found,
                "pin_code": pin_code,
                "is_complete_address": is_complete
            }
        }

    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False, "details": None}

def _extract_consumer_care(lines: List[str], full_text: str, spatial_block: List[str] = None) -> Dict[str, Any]:
    search_text = (" ".join(spatial_block) + " " + full_text) if spatial_block else full_text

    phone_pattern = re.compile(r'(?:toll\s*free|care|call|phone|tel|helpline)?[\s:\.]*(1800[\s\-\d]{6,14}|(?:\+?91[\s\-]?)?[6-9]\d{9}|\+91[\s\-\d]{10,13})', re.IGNORECASE)
    email_pattern = re.compile(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', re.IGNORECASE)

    # Digit runs belonging to an FSSAI / licence number must not be mistaken for a phone.
    license_digits = [re.sub(r'\D', '', x) for x in re.findall(r'\d(?:[\s\-]?\d){11,15}', search_text)]
    license_digits = [x for x in license_digits if len(x) >= 12]

    phone = None
    for pm in phone_pattern.finditer(search_text):
        cand = pm.group(1)
        digits = re.sub(r'\D', '', cand)
        if len(digits) >= 12:
            digits = digits[-10:]
        if any(digits and digits in lic for lic in license_digits):
            continue
        phone = cand.strip()
        break

    email_match = email_pattern.search(search_text)
    email = email_match.group(1) if email_match else None
    # OCR often corrupts the address but keeps the "<word>@<word>...com" shape intact.
    if not email:
        loose = re.search(r'([a-z0-9._%-]{3,}@[a-z0-9.-]{3,}(?:\s*\.?\s*(?:com|in|org|net))?)', search_text, re.IGNORECASE)
        if loose:
            email = loose.group(1).strip()

    if phone or email:
        val_str = " | ".join([p for p in [phone, email] if p])
        return {
            "value": val_str,
            "confidence": 0.94 if (phone and "@" not in (email or "")) or (email and "." in (email or "")) else 0.8,
            "detected_text": val_str,
            "is_valid": True,
            "details": {"phone": phone, "email": email}
        }

    if re.search(r'consumer\s*care|customer\s*care|customercare|cuskomer|reach\s*us|write\s*to\s*us|'
                 r'for\s*any\s*(?:comment|complaint|query|feedback|suggestion)|grievance|helpline|'
                 r'consumer\s*(?:complaint|grievance|feedback)|e-?mail\s*us|feedback|lever\s*care|'
                 r'care\s*line|care@|quer(?:y|ies)|toll\s*free', full_text, re.IGNORECASE):
        return {
            "value": "Consumer Care Mentioned",
            "confidence": 0.60,
            "detected_text": "Consumer care contact section detected",
            "is_valid": True,
            "details": {"phone": None, "email": None}
        }

    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False, "details": None}

def _extract_fssai(lines: List[str], full_text: str) -> Dict[str, Any]:
    fssai_match = re.search(r'(?:fssai|lic(?:ense)?\s*no\.?)[\s:\.]*(\b1\d{13}\b)', full_text, re.IGNORECASE)
    if not fssai_match:
        fssai_match = re.search(r'\b(1\d{13})\b', full_text)

    if fssai_match:
        lic_num = fssai_match.group(1)
        return {
            "value": f"FSSAI Lic. No. {lic_num}",
            "confidence": 0.95,
            "detected_text": fssai_match.group(0),
            "is_valid": True,
            "details": {"license_number": lic_num}
        }
    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False}

def _extract_country_of_origin(lines: List[str], full_text: str) -> Dict[str, Any]:
    # Dynamic country pattern: e.g. "Made in India", "Country of Origin: USA", "Product of Germany"
    origin_match = re.search(
        r'(?:country\s*of\s*origin|made\s*in|product\s*of|mfd\s*in)[\s:\.\-]*([A-Za-z]{3,25})\b',
        full_text,
        re.IGNORECASE
    )
    if origin_match:
        country = origin_match.group(1).strip().title()
        if country.lower() not in ["the", "this", "our", "all", "each", "pack"]:
            return {
                "value": f"Made in {country}",
                "confidence": 0.95,
                "detected_text": origin_match.group(0),
                "is_valid": True,
                "details": {"country": country}
            }

    if "made in india" in full_text.lower():
        return {
            "value": "Made in India",
            "confidence": 0.95,
            "detected_text": "MADE IN INDIA",
            "is_valid": True,
            "details": {"country": "India"}
        }

    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False}

def _extract_unit_sale_price(
    lines: List[str],
    full_text: str,
    mrp_data: Optional[Dict[str, Any]] = None,
    qty_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    # 1. Match printed USP on packaging (e.g. 'USP: ₹ 1.05 / g' or '₹ 1.05/g' or '₹ 0.50 per ml' or 'USP Rs 1.05/g')
    usp_explicit = re.search(
        r'(?:usp|unit\s*sale\s*price)[\s:\.]*(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d{1,2})?)\s*(?:\/|\bper\b)\s*(?:100\s*g|100\s*ml|1\s*kg|1\s*g|1\s*l|g|kg|ml|l|unit|piece|u|n)\b',
        full_text,
        re.IGNORECASE
    )
    if usp_explicit:
        rate_str = usp_explicit.group(1)
        unit_str = usp_explicit.group(2).strip()
        return {
            "value": f"₹ {rate_str} / {unit_str}",
            "confidence": 0.94,
            "detected_text": usp_explicit.group(0),
            "is_valid": True,
            "details": {"rate": float(rate_str), "unit": unit_str, "source": "printed_declaration"}
        }

    # If without USP prefix, MUST have currency symbol and decimal rate e.g. "₹ 1.05 / g" or "₹ 1.05/g"
    usp_currency = re.search(
        r'(?:₹|rs\.?)\s*(\d+\.\d{1,2})\s*(?:\/|\bper\b)\s*(100\s*g|100\s*ml|1\s*kg|1\s*g|1\s*l|g|kg|ml|l|unit|piece|u|n)\b',
        full_text,
        re.IGNORECASE
    )
    if usp_currency:
        rate_str = usp_currency.group(1)
        unit_str = usp_currency.group(2).strip()
        return {
            "value": f"₹ {rate_str} / {unit_str}",
            "confidence": 0.92,
            "detected_text": usp_currency.group(0),
            "is_valid": True,
            "details": {"rate": float(rate_str), "unit": unit_str, "source": "printed_declaration"}
        }

    # 2. Automated Rule 6(10) Statutory USP Calculation Engine
    # Computes statutory unit sale price when MRP and Quantity are known
    if mrp_data and mrp_data.get("value") and qty_data and qty_data.get("value"):
        _qd = qty_data.get("details") or {}
        mrp_val = (mrp_data.get("details") or {}).get("amount")
        qty_num = _qd.get("number")
        qty_unit = (_qd.get("standard_unit") or "").lower()

        if mrp_val and qty_num and qty_num > 0:
            if qty_unit in ["g", "gm", "gram"]:
                rate = mrp_val / qty_num
                std_unit = "g"
            elif qty_unit in ["kg", "kilogram"]:
                rate = mrp_val / qty_num
                std_unit = "kg"
            elif qty_unit in ["ml", "millilitre"]:
                rate = mrp_val / qty_num
                std_unit = "ml"
            elif qty_unit in ["l", "litre"]:
                rate = mrp_val / qty_num
                std_unit = "l"
            elif qty_unit in ["n", "u"]:
                rate = mrp_val / qty_num
                std_unit = "N"
            else:
                rate = None

            if rate is not None:
                return {
                    "value": f"₹ {rate:.2f} / {std_unit}",
                    "confidence": 0.88,
                    "detected_text": f"Rule 6(10) Statutory USP: ₹ {mrp_val:.2f} ÷ {qty_num}{std_unit}",
                    "is_valid": True,
                    "details": {
                        "rate": round(rate, 2),
                        "unit": std_unit,
                        "source": "statutory_rule_6_10_calculation",
                        "is_computed": True
                    }
                }

    return {"value": None, "confidence": 0.0, "detected_text": None, "is_valid": False}

