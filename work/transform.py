from dataclasses import dataclass

from rules.weights import split_weights
from work.lineage import RouteOccurrence, SourceError, derive_child_occurrence, validate_lineage


@dataclass(frozen=True, slots=True)
class Expansion:
    children: tuple[RouteOccurrence, ...]


@dataclass(frozen=True, slots=True)
class TerminalResult:
    occurrence: RouteOccurrence
    category: str

    def to_data(self):
        return {'occurrence': self.occurrence.to_data(), 'category': self.category}

    @classmethod
    def from_data(cls, data):
        return cls(RouteOccurrence.from_data(data['occurrence']), data['category'])


def process_occurrence(occurrence, header):
    validate_lineage(occurrence, header)
    node = occurrence.path[-1]
    outgoing = header.graph.outgoing_edges(node)
    if outgoing:
        weights = split_weights(occurrence.grams, outgoing)
        return Expansion(tuple(
            derive_child_occurrence(occurrence, index, edge, grams)
            for (index, edge), grams in zip(outgoing, weights, strict=True)
        ))
    category = header.graph.terminal_category(node)
    if category is None:
        raise SourceError('待办既无出边也无终点类别')
    return TerminalResult(occurrence, category)


def validate_terminal_result(result, header):
    validate_lineage(result.occurrence, header)
    category = header.graph.terminal_category(result.occurrence.path[-1])
    if category is None or result.category != category:
        raise SourceError('结果类别与冻结终点不符')
