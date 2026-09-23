import sys
import os
sys.path.insert(0, os.path.abspath("."))
import json
from backend.pipeline.spell_checker import get_spell_checker
from backend.pipeline.extraction import extract_structured_information

raw_text = """DOVE BATHING BAR: MADE IN INDIA. MKTD: BY
HINDUSTAN UNILEVER LIMITED (HUL): DOVE
IS A REGISTERED TRADEMARK @ HUL 2025.
PACK OF UNITS: INDIVIDUAL UNITS NOT FOR SALE
For COMpLETE DECLARATION SEE INDIVIDUAL PACKS INSIDE_
wut dove.in; WWW.unilever com
'TESTED FOR DERMATOLOGICAL SAFETY AND FOUND SAFE FOR HUMAN SKIN_
~REFERS TO SODium COCOYL ISETHIONATE AND SOdium PALM KERNELATE_
MFG: BY LAKME LEVER PVT: LTD , (UNIT-I), SURVEY NO: 159/B,
VARSANA, BHIMASAR
PADANA ROAD, PO. PADANA,
GANDHIDHAM (KACHCHH) - 370 240, GUJARAT. M GC/1122.
LEVERCARE-QUERY / FEEDBACK, TOLL FREE: 1800-10-22-221,
PO BOX 14760, MUMBAI 400 099, LEVER CARE@UNILEVERCOM
'MRP ? (inclusive of all taxes); USP; #MFD, & @USE BEFORE: SEE BELOW !
FH * {523 /-,3 1.05/9
# 01/26
@ 0 5/28
MET CONTENTS WHEM PACKED
UNITS X 125 9 + 1259 FREE?"""

spell_checker = get_spell_checker()
normalized_text = spell_checker.correct_packaging_text(raw_text)
lines = [l.strip() for l in normalized_text.split('\n') if l.strip()]
dummy_boxes = [{'text': l, 'confidence': 0.9, 'normalized_bbox': {'x_min': 0.1, 'y_min': 0.04*i, 'x_max': 0.9, 'y_max': 0.04*i+0.03}} for i, l in enumerate(lines)]

res = extract_structured_information(dummy_boxes, normalized_text, [{'label': 'soap shampoo cosmetics or personal care', 'confidence': 0.92}])
import sys
sys.stdout.reconfigure(encoding='utf-8')
print("--- NORMALIZED TEXT ---")
print(normalized_text)
print("--- EXTRACTIONS ON DOVE PACKAGING ---")
for k, v in res.items():
    if isinstance(v, dict) and 'value' in v:
        val = str(v.get("value")).replace("\n", " ")
        print(f'{k:18s}: {val} (valid: {v.get("is_valid")})')
    else:
        print(f'{k:18s}: {v}')

