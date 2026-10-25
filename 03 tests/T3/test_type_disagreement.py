"""An inconclusive PDF pre-read must not become a category conflict."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from type_classifier import triple_type_disagreement


class TypeDisagreementTests(unittest.TestCase):
    def test_unknown_is_not_an_opposing_classification(self):
        evidence = {"attachment_original_name": "虚构平台-行程单.pdf",
                    "local_pdf_type": "Unknown"}
        self.assertFalse(triple_type_disagreement(evidence, "Itinerary"))

    def test_explicit_opposition_still_requires_review(self):
        evidence = {"attachment_original_name": "虚构平台-行程单.pdf",
                    "local_pdf_type": "Invoice"}
        self.assertTrue(triple_type_disagreement(evidence, "Itinerary"))


if __name__ == "__main__":
    unittest.main()
