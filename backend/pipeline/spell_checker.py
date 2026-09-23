import re
from typing import Optional, List, Tuple
try:
    from symspellpy import SymSpell, Verbosity
    HAS_SYMSPELL = True
except ImportError:
    HAS_SYMSPELL = False

class PackagingSpellChecker:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PackagingSpellChecker, cls).__new__(cls)
            cls._instance._init_dictionaries()
        return cls._instance

    def _init_dictionaries(self):
        self.has_symspell = HAS_SYMSPELL
        if self.has_symspell:
            self.sym_spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
            # Seed domain dictionary with Indian FMCG brands and statutory Legal Metrology terms
            domain_terms = [
                # Top Brands
                "dove", "lux", "lifebuoy", "pears", "dettol", "santoor", "medimix",
                "maggi", "yippee", "knorr", "chings", "top ramen", "wai wai",
                "lays", "kurkure", "bingo", "balaji", "haldiram", "bikaji", "prataap",
                "britannia", "parle", "sunfeast", "oreo", "good day", "marie gold", "monaco",
                "amul", "mother dairy", "nandini", "nestle", "kwality walls",
                "fortune", "saffola", "dhara", "gemini", "sundrop", "engine", "emami",
                "tata", "aashirvaad", "pillsbury", "catch", "mdh", "everest", "badshah",
                "coca cola", "pepsi", "thums up", "sprite", "limca", "fanta", "frooti", "maaza",
                "real", "tropicana", "red bull", "sting", "bournvita", "horlicks", "boost",
                # Statutory Phrases
                "manufactured", "marketed", "packed", "importer", "hindustan", "unilever",
                "limited", "private", "enterprise", "consumer", "feedback", "helpline",
                "inclusive", "taxes", "quantity", "maximum", "retail", "price",
                "weight", "grams", "kilograms", "millilitres", "litres", "origin"
            ]
            for term in domain_terms:
                self.sym_spell.create_dictionary_entry(term, 1000)
            print("[SpellChecker] SymSpell packaging dictionary initialized with domain terms.")

    def correct_word(self, word: str) -> str:
        """Corrects a single token within edit distance 2."""
        if not self.has_symspell or not word or len(word) <= 2:
            return word

        clean = word.lower().strip(",.:;!?()[]")
        suggestions = self.sym_spell.lookup(clean, Verbosity.CLOSEST, max_edit_distance=2)
        if suggestions and suggestions[0].distance <= 2:
            # Preserve capitalization
            match = suggestions[0].term
            if word.isupper():
                return match.upper()
            elif word.istitle():
                return match.title()
            return match
        return word

    def correct_packaging_text(self, text: str) -> str:
        """
        Applies fast regex optical confusion corrections + SymSpell fuzzy correction.
        Runs in sub-millisecond time.
        """
        if not text:
            return text

        # 1. Packaging Header Corruptions (M/N dot-matrix confusion)
        t = re.sub(r'\bMET\s+CONTENTS\b', 'NET CONTENTS', text, flags=re.IGNORECASE)
        t = re.sub(r'\bWHEM\s+PACKED\b', 'WHEN PACKED', t, flags=re.IGNORECASE)

        # 2. Currency symbol normalization: standardise Rs., Rs, or dot-matrix {/? to ₹
        t = re.sub(r'\bRs\.?\s*', '₹ ', t, flags=re.IGNORECASE)
        t = re.sub(r'\b[TFr]\s*(?=\d)', '₹ ', t)
        # Dot-matrix ink-jet currency prefixes: e.g. * {523 /-, {523, ? 523
        t = re.sub(r'(?:^|(?<=\s))[*#]?\s*[\{\?]\s*(?=[1-9]\d*)', '₹ ', t)
        t = re.sub(r'\bMRP\s*[\?\{\*]\s*', 'MRP ₹ ', t, flags=re.IGNORECASE)

        # 3. OCR numeric confusion (O/o -> 0) in numbers and currency contexts
        def fix_price_zeros(match):
            return match.group(0).replace('O', '0').replace('o', '0')

        t = re.sub(r'[₹]\s*[0-9oO]+(?:\.[0-9oO]+)?', fix_price_zeros, t)
        t = re.sub(r'\b[0-9oO]+\.[0-9oO]+\b', fix_price_zeros, t)
        t = re.sub(r'(?<=\d)[oO](?=\d)', '0', t)
        t = re.sub(r'(?<=\d)[oO]\b', '0', t)

        # 4. Dot-matrix Gram / Weight confusion (digit 9 -> g)
        # Fix space in two-digit numbers e.g. '0 5/28' -> '05/28'
        t = re.sub(r'\b(0)\s+([1-9][\/\-]\d{2,4})', r'\1\2', t)
        # Fix unit price '/9' -> '/g' only after decimal prices e.g. '1.05/9' -> '1.05/g'
        t = re.sub(r'(\d+\.\d{1,2})\s*\/\s*9\b', r'\1/g', t)
        # Fix weight e.g. '125 9' -> '125 g' (requires space before 9 so survey numbers like 159/B are preserved)
        t = re.sub(r'(\d+(?:\.\d+)?)\s+9(?=\s|\+|\/|\-|$|\b)', r'\1 g', t)
        # Catch non-standard symbol 'gms'
        t = re.sub(r'(\d+)\s*gms\b', r'\1 gms', t, flags=re.IGNORECASE)

        # 5. Dot-matrix Date stamps (# = MFD, @ = USE BEFORE)
        t = re.sub(r'#\s*(\d{1,2}[\/\-]\d{2,4})', r'MFD: \1', t)
        t = re.sub(r'@\s*(\d{1,2}[\/\-]\d{2,4})', r'USE BEFORE: \1', t)

        # 6. Legal Metrology statutory phrase standardizations
        t = re.sub(r'incl\.?\s*of\s*all\s*taxes?', 'inclusive of all taxes', t, flags=re.IGNORECASE)
        t = re.sub(r'cust(?:omer)?\s*care', 'consumer care', t, flags=re.IGNORECASE)
        t = re.sub(r'toll\s*free\s*no\.?', 'toll free:', t, flags=re.IGNORECASE)

        return t

    # Patterns that only ever show up from post-production ink-jet/dot-matrix stamping
    # (batch-code, MRP, and date stamps applied after the main print run) -- distinct
    # from generic OCR noise like O/0 confusion, so a hit here is a real print-quality
    # signal rather than an artifact of the scan itself.
    _DOT_MATRIX_ARTIFACT_PATTERNS = [
        r'(?:^|(?<=\s))[*#]?\s*[\{\?]\s*(?=[1-9]\d*)',   # {523, ?523 currency prefix
        r'\bMRP\s*[\?\{\*]\s*',                          # MRP{199, MRP?199
        r'#\s*\d{1,2}[\/\-]\d{2,4}',                     # # 01/26 -> MFD stamp
        r'@\s*\d{1,2}[\/\-]\d{2,4}',                     # @ 05/28 -> best-before stamp
        r'\d+(?:\.\d+)?\s+9(?=\s|\+|\/|\-|$|\b)',        # '125 9' ink-jet 'g' misread as '9'
    ]

    def has_dot_matrix_artifacts(self, text: str) -> bool:
        """True if the RAW (pre-correction) text shows tell-tale ink-jet dot-matrix stamp
        artifacts. Call this before correct_packaging_text() -- correction erases the
        evidence this looks for."""
        if not text:
            return False
        return any(re.search(p, text) for p in self._DOT_MATRIX_ARTIFACT_PATTERNS)

# Singleton helper
_spell_checker_instance = None

def get_spell_checker() -> PackagingSpellChecker:
    global _spell_checker_instance
    if _spell_checker_instance is None:
        _spell_checker_instance = PackagingSpellChecker()
    return _spell_checker_instance
