import json
import sqlite3

from accounts.finalize import close, initialize_accounts
from intake.batches import accept
from intake.records import WeighingRecord
from records.export import PendingBatch, UnknownReceipt, read, reprint
from tests.fixtures import ProjectTestCase


class ArchiveTests(ProjectTestCase):
    def test_retry_after_failed_finalize(self):
        self.accept_route('old', records=(WeighingRecord('old-r', 'D', 4),))
        self.drain('old')
        old = close(self.accounts, 'old')
        self.accept_route()
        self.drain()
        self.accounts.execute('CREATE TABLE commit_parent (number INTEGER PRIMARY KEY)')
        self.accounts.execute('''
            CREATE TABLE commit_guard (
                number INTEGER REFERENCES commit_parent(number) DEFERRABLE INITIALLY DEFERRED
            )
        ''')
        self.accounts.execute('''
            CREATE TRIGGER fail_account_commit AFTER UPDATE OF receipt_id ON account_batches
            WHEN NEW.batch_id = 'A' AND NEW.receipt_id IS NOT NULL
            BEGIN
                INSERT INTO commit_guard VALUES (1);
            END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            close(self.accounts, 'A')
        self.assertFalse(self.accounts.in_transaction)
        self.reopen()
        self.assertEqual(read(self.accounts, batch_id='A'), PendingBatch('A'))
        self.assertEqual(read(self.accounts, receipt_id=old.receipt.receipt_id), old)
        with self.assertRaises(UnknownReceipt):
            read(self.accounts, receipt_id='R000002')
        self.assertEqual(self.accounts.execute('SELECT COUNT(*) FROM publications').fetchone(), (1,))
        self.assertEqual(self.accounts.execute('SELECT COUNT(*) FROM commit_guard').fetchone(), (0,))
        self.accounts.execute('DROP TRIGGER fail_account_commit')
        retried = close(self.accounts, 'A')
        self.assert_route_book(retried)
        self.assertEqual(retried.receipt.receipt_id, 'R000002')
        self.assertEqual(close(self.accounts, 'A'), retried)
        self.assertEqual(read(self.accounts, batch_id='A'), retried)
        self.assertEqual(self.accounts.execute('SELECT COUNT(*) FROM publications').fetchone(), (2,))

    def test_read_fixed_result_after_reopen(self):
        accepted = self.accept_route()
        self.assertEqual(read(self.accounts, batch_id='A'), PendingBatch('A'))
        self.assertEqual(reprint(self.accounts, batch_id='A'), PendingBatch('A'))
        with self.assertRaises(UnknownReceipt):
            read(self.accounts, receipt_id='R000001')
        self.drain()
        publication = close(self.accounts, 'A')
        receipt_id = publication.receipt.receipt_id
        printout = reprint(self.accounts, batch_id='A')
        self.assertEqual(publication.receipt.acceptance_id, accepted.acceptance_id)
        self.assert_route_book(publication)
        self.work.close()
        self.accounts.close()
        self.work_path.rename(self.folder / 'work-offline.sqlite')
        archive = self.open_database(self.accounts_path, initialize_accounts)
        self.assertEqual(read(archive, receipt_id=receipt_id), publication)
        self.assertEqual(read(archive, batch_id='A'), publication)
        self.assertEqual(reprint(archive, batch_id='A'), printout)
        self.assertEqual(reprint(archive, receipt_id=receipt_id), printout)
        printed_receipt, printed_account = map(json.loads, printout.splitlines())
        self.assertEqual(printed_receipt['receipt_id'], receipt_id)
        self.assertEqual(printed_receipt['original_grams'], 12)
        self.assertEqual(printed_account['category_totals'], {'D': 12})
        self.assertEqual([(row['path'], row['grams']) for row in printed_account['rows']], [
            (['S', 'L', 'D'], 5), (['S', 'R', 'D'], 7),
        ])
        self.assertFalse(self.work_path.exists())

    def test_replay_after_publication_preserves_fixed_account(self):
        accepted = self.accept_route()
        self.drain()
        original = close(self.accounts, 'A')
        self.reopen()
        self.assertEqual(accept(self.work, 'A', 'v1', (WeighingRecord('r', 'S', 12),)), accepted)
        self.drain()
        self.assertEqual(close(self.accounts, 'A'), original)
        self.assertEqual(read(self.accounts, receipt_id=original.receipt.receipt_id), original)
        self.assertEqual(self.accounts.execute('SELECT COUNT(*) FROM publications').fetchone(), (1,))
