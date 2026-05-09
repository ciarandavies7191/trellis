from __future__ import annotations
import re
from abc import ABC, abstractmethod
from typing import Optional

from .models import ChunkMetadata


class SectionClassifier(ABC):
    @abstractmethod
    def classify(self, chunk: ChunkMetadata) -> Optional[str]: ...


class SynonymVocabulary(ABC):
    @abstractmethod
    def expand(self, term: str) -> list[str]: ...

    @abstractmethod
    def tokenize(self, text: str) -> list[str]: ...


class FieldTaxonomy(ABC):
    @abstractmethod
    def canonicalize(self, raw_label: str, section: Optional[str] = None) -> str: ...


class NullClassifier(SectionClassifier):
    def classify(self, chunk: ChunkMetadata) -> Optional[str]:
        return None


class KeywordSectionClassifier(SectionClassifier):
    def __init__(self, rules: dict[str, list[str]]) -> None:
        self._rules = rules

    def classify(self, chunk: ChunkMetadata) -> Optional[str]:
        haystack = " ".join(
            [chunk.text]
            + chunk.column_labels
            + chunk.row_labels
        ).lower()

        best_label: Optional[str] = None
        best_score = 0
        for label, keywords in self._rules.items():
            score = sum(1 for kw in keywords if kw.lower() in haystack)
            if score > best_score:
                best_score = score
                best_label = label

        return best_label if best_score > 0 else None


class WhitespaceSynonymVocabulary(SynonymVocabulary):
    def expand(self, term: str) -> list[str]:
        return [term]

    def tokenize(self, text: str) -> list[str]:
        return text.lower().split()


class DictSynonymVocabulary(SynonymVocabulary):
    def __init__(self, synonyms: dict[str, list[str]]) -> None:
        self._synonyms = synonyms

    def expand(self, term: str) -> list[str]:
        return self._synonyms.get(term, [term])

    def tokenize(self, text: str) -> list[str]:
        raw_tokens = re.split(r"[\s\W]+", text.lower())
        result: list[str] = []
        for token in raw_tokens:
            if not token:
                continue
            result.extend(self.expand(token))
        return result


class SlugifyTaxonomy(FieldTaxonomy):
    def canonicalize(self, raw_label: str, section: Optional[str] = None) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", raw_label.lower()).strip("_")
        return f"unknown.{slug}"


class DictFieldTaxonomy(FieldTaxonomy):
    def __init__(self, mappings: dict) -> None:
        self._mappings = mappings
        self._fallback = SlugifyTaxonomy()

    def canonicalize(self, raw_label: str, section: Optional[str] = None) -> str:
        key_with_section = (section, raw_label.lower())
        if key_with_section in self._mappings:
            return self._mappings[key_with_section]
        key_plain = raw_label.lower()
        if key_plain in self._mappings:
            return self._mappings[key_plain]
        return self._fallback.canonicalize(raw_label, section)


class RetrievalRegistry:
    def __init__(self) -> None:
        self._classifier: Optional[SectionClassifier] = None
        self._vocabulary: Optional[SynonymVocabulary] = None
        self._taxonomy: Optional[FieldTaxonomy] = None

    def register_classifier(self, classifier: SectionClassifier) -> None:
        self._classifier = classifier

    def register_vocabulary(self, vocabulary: SynonymVocabulary) -> None:
        self._vocabulary = vocabulary

    def register_taxonomy(self, taxonomy: FieldTaxonomy) -> None:
        self._taxonomy = taxonomy

    @property
    def classifier(self) -> SectionClassifier:
        return self._classifier or NullClassifier()

    @property
    def vocabulary(self) -> SynonymVocabulary:
        return self._vocabulary or WhitespaceSynonymVocabulary()

    @property
    def taxonomy(self) -> FieldTaxonomy:
        return self._taxonomy or SlugifyTaxonomy()
