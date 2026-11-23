from dataclasses import dataclass


def require_name(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field}须为非空文本')
    value.encode('utf-8')
    return value


@dataclass(frozen=True, slots=True)
class RouteEdge:
    source: str
    target: str
    grams: int


@dataclass(frozen=True, slots=True)
class RouteGraph:
    nodes: tuple[str, ...]
    edges: tuple[RouteEdge, ...]
    terminals: tuple[tuple[str, str], ...]

    def outgoing_edges(self, node):
        return tuple((index, edge) for index, edge in enumerate(self.edges) if edge.source == node)

    def terminal_category(self, node):
        for terminal, category in self.terminals:
            if terminal == node:
                return category
        return None

    def to_data(self):
        return {
            'nodes': self.nodes,
            'edges': [(edge.source, edge.target, edge.grams) for edge in self.edges],
            'terminals': self.terminals,
        }

    @classmethod
    def from_data(cls, data):
        return cls(
            tuple(data['nodes']),
            tuple(RouteEdge(*edge) for edge in data['edges']),
            tuple(tuple(terminal) for terminal in data['terminals']),
        )
