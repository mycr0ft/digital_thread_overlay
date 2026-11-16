"""digital_thread_overlay - evidence-backed assertions across artifacts no tool owns.

The three ideas, each in its own module:

    model       an assertion says what is claimed, on what evidence, against which revision
    baselines   a baseline is identified by content, so a move is invisible and an edit is not
    ledger      records are appended and never edited
    query       trace, impact and staleness - the three questions a thread exists to answer
    operations  review moves status, a passing check moves level
"""
from .ledger import Ledger
from .model import Assertion, InterfaceCheck, Level, ManualReview, Status, ToolRun

__all__ = ["Assertion", "InterfaceCheck", "Ledger", "Level", "ManualReview", "Status",
           "ToolRun"]
__version__ = "0.1.0"
