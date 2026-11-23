from rules.routes import RouteEdge, RouteGraph, require_name
from rules.weights import require_grams, split_weights


def validate_route_references(graph):
    if not isinstance(graph, RouteGraph) or any(
        type(values) is not tuple for values in (graph.nodes, graph.edges, graph.terminals)
    ):
        raise ValueError('路线须由节点、边及终点元组组成')
    nodes = set()
    for node in graph.nodes:
        require_name(node, '节点')
        if node in nodes:
            raise ValueError(f'重复节点 {node}')
        nodes.add(node)
    if not nodes:
        raise ValueError('路线至少需要一个节点')

    outgoing = {node: [] for node in graph.nodes}
    for index, edge in enumerate(graph.edges):
        if not isinstance(edge, RouteEdge):
            raise ValueError('无效路线边')
        require_name(edge.source, '边起点')
        require_name(edge.target, '边终点')
        if edge.source not in nodes or edge.target not in nodes:
            raise ValueError('路线边引用了未登记节点')
        require_grams(edge.grams)
        outgoing[edge.source].append((index, edge))

    terminals = set()
    for terminal in graph.terminals:
        if type(terminal) is not tuple or len(terminal) != 2:
            raise ValueError('终点须指定节点与类别')
        node, category = terminal
        require_name(node, '终点节点')
        require_name(category, '终点类别')
        if node not in nodes or node in terminals:
            raise ValueError('终点节点未知或重复')
        terminals.add(node)

    for node in graph.nodes:
        if (node in terminals) == bool(outgoing[node]):
            raise ValueError(f'节点 {node} 须有出边或终点类别，二者只能选一')
    return outgoing


def validate_acyclic(outgoing):
    completed = set()
    for start in outgoing:
        if start in completed:
            continue
        active = {start}
        stack = [(start, iter(outgoing[start]))]
        while stack:
            node, edges = stack[-1]
            next_edge = next(edges, None)
            if next_edge is None:
                active.remove(node)
                completed.add(node)
                stack.pop()
                continue
            _, edge = next_edge
            if edge.target in active:
                raise ValueError(f'路线存在返回 {edge.target} 的环')
            if edge.target not in completed:
                active.add(edge.target)
                stack.append((edge.target, iter(outgoing[edge.target])))


def validate_revision(graph):
    outgoing = validate_route_references(graph)
    validate_acyclic(outgoing)
    for edge in graph.edges:
        children = outgoing[edge.target]
        if children:
            split_weights(edge.grams, children)
