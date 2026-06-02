"""cb_corpus: official central-bank document corpus builder (BIS-63, scope A-F)."""
from .banks import BIS_63, Bank, get_bank
from .taxonomy import DocType, FULL_SCOPE
from .models import DocRecord
from .config import Config

__version__ = "0.1.0"
__all__ = ["BIS_63", "Bank", "get_bank", "DocType", "FULL_SCOPE",
           "DocRecord", "Config"]
