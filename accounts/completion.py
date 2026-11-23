from dataclasses import dataclass

from work.lineage import OccurrenceKey


@dataclass(frozen=True, slots=True)
class ClosureAssessment:
    ready: bool
    missing: tuple[OccurrenceKey, ...] = ()


def assess_completion(view):
    if view is None or view.completion is None:
        return ClosureAssessment(False)
    expected = set(view.completion.result_keys)
    received = {result.occurrence.key for result in view.results}
    missing = tuple(sorted(expected - received))
    return ClosureAssessment(
        ready=bool(expected) and received == expected and len(received) == len(view.results),
        missing=missing,
    )
