def require_grams(value):
    if type(value) is not int or value < 0:
        raise ValueError('重量须为非负整数克')
    return value


def require_conserved_split(incoming, outgoing):
    require_grams(incoming)
    if sum(require_grams(grams) for grams in outgoing) != incoming:
        raise ValueError('拆分克数与流入克数不相等')


def split_weights(incoming, edges):
    weights = tuple(edge.grams for _, edge in edges)
    require_conserved_split(incoming, weights)
    return weights
