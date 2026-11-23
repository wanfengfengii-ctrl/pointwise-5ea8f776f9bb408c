import json
import sqlite3
from contextlib import contextmanager


def open_port(path):
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute('PRAGMA foreign_keys = ON')
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def transaction(connection):
    """Own one local write transaction, including errors raised by commit."""
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def initialize_port(connection, role, initializers):
    with transaction(connection):
        connection.execute('''
            CREATE TABLE IF NOT EXISTS port_identity (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                role TEXT NOT NULL
            )
        ''')
        connection.execute('INSERT OR IGNORE INTO port_identity VALUES (1, ?)', (role,))
        stored_role, = connection.execute('SELECT role FROM port_identity').fetchone()
        if stored_role != role:
            raise ValueError('加工端和封账端须使用独立数据库')
        for initialize in initializers:
            initialize()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))
