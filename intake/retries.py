class BatchConflict(ValueError):
    pass


def compare_batch_request(existing, revision_id, records):
    if existing.revision_id != revision_id or existing.records != records:
        raise BatchConflict(f'批次 {existing.batch_id} 已绑定其他原单')
    return existing.acceptance_id
