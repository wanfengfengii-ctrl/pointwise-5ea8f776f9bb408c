from work.lineage import make_root_occurrence


def make_root_occurrences(header):
    return tuple(make_root_occurrence(header, index) for index in range(len(header.records)))


def stage_root_work(journal, header):
    for occurrence in make_root_occurrences(header):
        journal.enqueue_root(occurrence)
    journal.stage_delivery(header.batch_id, 'header', '', header.to_data())
