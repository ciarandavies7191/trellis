from __future__ import annotations

import pytest

from trellis.retrieval.models import ChunkMetadata, ChunkType
from trellis.retrieval.plugins import (
    DictFieldTaxonomy,
    DictSynonymVocabulary,
    KeywordSectionClassifier,
    NullClassifier,
    RetrievalRegistry,
    SlugifyTaxonomy,
    WhitespaceSynonymVocabulary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(text: str, column_labels=None, row_labels=None) -> ChunkMetadata:
    return ChunkMetadata(
        chunk_id="test-id",
        document_id="doc1",
        tenant_id="tenant1",
        page=1,
        chunk_type=ChunkType.PROSE,
        section_label=None,
        period_labels=[],
        period_ends=[],
        row_labels=row_labels or [],
        column_labels=column_labels or [],
        extra={},
        text=text,
        embedding=None,
    )


# ---------------------------------------------------------------------------
# KeywordSectionClassifier
# ---------------------------------------------------------------------------


class TestKeywordSectionClassifier:
    def _make(self, rules):
        return KeywordSectionClassifier(rules)

    def test_returns_label_when_keyword_present(self):
        clf = self._make({"income_statement": ["revenue", "net income"]})
        chunk = _make_chunk("Total revenue 100")
        assert clf.classify(chunk) == "income_statement"

    def test_returns_none_when_no_match(self):
        clf = self._make({"income_statement": ["revenue", "net income"]})
        chunk = _make_chunk("This text has nothing relevant")
        assert clf.classify(chunk) is None

    def test_returns_highest_density_label_when_multiple_match(self):
        clf = self._make({
            "income_statement": ["revenue", "expenses"],
            "balance_sheet": ["assets", "liabilities", "equity", "cash"],
        })
        chunk = _make_chunk("assets liabilities equity cash revenue")
        assert clf.classify(chunk) == "balance_sheet"

    def test_checks_column_labels(self):
        clf = self._make({"balance_sheet": ["assets"]})
        chunk = _make_chunk("some text", column_labels=["Total Assets"])
        assert clf.classify(chunk) == "balance_sheet"

    def test_checks_row_labels(self):
        clf = self._make({"balance_sheet": ["liabilities"]})
        chunk = _make_chunk("some text", row_labels=["Total Liabilities"])
        assert clf.classify(chunk) == "balance_sheet"

    def test_case_insensitive(self):
        clf = self._make({"section": ["REVENUE"]})
        chunk = _make_chunk("total revenue for the year")
        assert clf.classify(chunk) == "section"


# ---------------------------------------------------------------------------
# DictSynonymVocabulary
# ---------------------------------------------------------------------------


class TestDictSynonymVocabulary:
    def _make(self, synonyms=None):
        return DictSynonymVocabulary(synonyms or {"rev": ["revenue", "sales"]})

    def test_expand_returns_synonyms_for_known_term(self):
        vocab = self._make()
        assert vocab.expand("rev") == ["revenue", "sales"]

    def test_expand_returns_term_wrapped_in_list_for_unknown(self):
        vocab = self._make()
        assert vocab.expand("unknown_word") == ["unknown_word"]

    def test_tokenize_expands_known_synonyms(self):
        vocab = self._make({"ebit": ["earnings_before_interest", "operating_income"]})
        tokens = vocab.tokenize("ebit margin")
        assert "earnings_before_interest" in tokens
        assert "operating_income" in tokens

    def test_tokenize_passes_unknown_tokens_through(self):
        vocab = self._make()
        tokens = vocab.tokenize("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_tokenize_lowercases(self):
        vocab = self._make()
        tokens = vocab.tokenize("Hello World")
        assert "hello" in tokens


# ---------------------------------------------------------------------------
# WhitespaceSynonymVocabulary
# ---------------------------------------------------------------------------


class TestWhitespaceSynonymVocabulary:
    def test_expand_returns_term(self):
        vocab = WhitespaceSynonymVocabulary()
        assert vocab.expand("anything") == ["anything"]

    def test_tokenize_splits_on_whitespace(self):
        vocab = WhitespaceSynonymVocabulary()
        assert vocab.tokenize("hello world") == ["hello", "world"]

    def test_tokenize_lowercases(self):
        vocab = WhitespaceSynonymVocabulary()
        assert vocab.tokenize("Hello WORLD") == ["hello", "world"]


# ---------------------------------------------------------------------------
# SlugifyTaxonomy
# ---------------------------------------------------------------------------


class TestSlugifyTaxonomy:
    def test_produces_unknown_prefix_with_slug(self):
        tax = SlugifyTaxonomy()
        result = tax.canonicalize("Net Income")
        assert result == "unknown.net_income"

    def test_handles_special_chars(self):
        tax = SlugifyTaxonomy()
        result = tax.canonicalize("Revenue (adj.)")
        assert result.startswith("unknown.")
        assert " " not in result

    def test_section_param_ignored(self):
        tax = SlugifyTaxonomy()
        assert tax.canonicalize("Cost", "income") == "unknown.cost"


# ---------------------------------------------------------------------------
# DictFieldTaxonomy
# ---------------------------------------------------------------------------


class TestDictFieldTaxonomy:
    def _make(self):
        mappings = {
            ("income_statement", "revenue"): "is.revenue",
            "net income": "is.net_income",
        }
        return DictFieldTaxonomy(mappings)

    def test_hits_exact_section_and_label_mapping(self):
        tax = self._make()
        assert tax.canonicalize("Revenue", "income_statement") == "is.revenue"

    def test_hits_plain_label_mapping_when_no_section_match(self):
        tax = self._make()
        assert tax.canonicalize("Net Income") == "is.net_income"

    def test_falls_back_to_slugify_for_unknown(self):
        tax = self._make()
        result = tax.canonicalize("Unrecognized Field")
        assert result.startswith("unknown.")

    def test_section_key_lookup_is_case_insensitive_on_label(self):
        tax = self._make()
        assert tax.canonicalize("NET INCOME") == "is.net_income"


# ---------------------------------------------------------------------------
# RetrievalRegistry
# ---------------------------------------------------------------------------


class TestRetrievalRegistry:
    def test_default_classifier_is_null_classifier(self):
        reg = RetrievalRegistry()
        assert isinstance(reg.classifier, NullClassifier)

    def test_default_vocabulary_is_whitespace(self):
        reg = RetrievalRegistry()
        assert isinstance(reg.vocabulary, WhitespaceSynonymVocabulary)

    def test_default_taxonomy_is_slugify(self):
        reg = RetrievalRegistry()
        assert isinstance(reg.taxonomy, SlugifyTaxonomy)

    def test_returns_registered_classifier(self):
        reg = RetrievalRegistry()
        clf = KeywordSectionClassifier({"a": ["b"]})
        reg.register_classifier(clf)
        assert reg.classifier is clf

    def test_returns_registered_vocabulary(self):
        reg = RetrievalRegistry()
        vocab = DictSynonymVocabulary({"x": ["y"]})
        reg.register_vocabulary(vocab)
        assert reg.vocabulary is vocab

    def test_returns_registered_taxonomy(self):
        reg = RetrievalRegistry()
        tax = DictFieldTaxonomy({"a": "b"})
        reg.register_taxonomy(tax)
        assert reg.taxonomy is tax
