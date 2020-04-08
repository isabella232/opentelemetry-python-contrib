import os
import unittest

import pytest

from opentelemetry import trace
from sqlalchemy.exc import ProgrammingError

from .mixins import SQLAlchemyTestMixin


MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "port": int(os.getenv("TEST_MYSQL_PORT", 3306)),
    "user": os.getenv("TEST_MYSQL_USER", "test"),
    "password": os.getenv("TEST_MYSQL_PASSWORD", "test"),
    "database": os.getenv("TEST_MYSQL_DATABASE", "test"),
}


class MysqlConnectorTestCase(SQLAlchemyTestMixin, unittest.TestCase):
    """TestCase for mysql-connector engine"""

    VENDOR = "mysql"
    SQL_DB = "test"
    SERVICE = "mysql"
    ENGINE_ARGS = {"url": "mysql+mysqlconnector://%(user)s:%(password)s@%(host)s:%(port)s/%(database)s" % MYSQL_CONFIG}

    def setUp(self):
        super(MysqlConnectorTestCase, self).setUp()

    def tearDown(self):
        super(MysqlConnectorTestCase, self).tearDown()

    def check_meta(self, span):
        # check database connection tags
        self.assertEqual(span.attributes.get("out.host"), MYSQL_CONFIG["host"])
        # self.assertEqual(span.get_metric("out.port"), MYSQL_CONFIG["port"])

    def test_engine_execute_errors(self):
        # ensures that SQL errors are reported
        with pytest.raises(ProgrammingError):
            with self.connection() as conn:
                conn.execute("SELECT * FROM a_wrong_table").fetchall()

        traces = self.pop_traces()
        # trace composition
        self.assertEqual(len(traces), 1)
        span = traces[0]
        # span fields
        self.assertEqual(span.name, "{}.query".format(self.VENDOR))
        self.assertEqual(span.attributes.get("service"), self.SERVICE)
        self.assertEqual(span.attributes.get("resource"), "SELECT * FROM a_wrong_table")
        self.assertEqual(span.attributes.get("sql.db"), self.SQL_DB)
        self.assertIsNone(span.attributes.get("sql.rows"))  # or span.get_metric("sql.rows"))
        self.check_meta(span)
        self.assertTrue(span.end_time - span.start_time > 0)
        # check the error
        self.assertEqual(span.status.canonical_code, trace.status.StatusCanonicalCode.UNKNOWN)
        # TODO: error handling
        # self.assertEqual(span.attributes.get("error.type"), "mysql.connector.errors.ProgrammingError")
        # self.assertTrue("Table 'test.a_wrong_table' doesn't exist" in span.attributes.get("error.msg"))
        # self.assertTrue("Table 'test.a_wrong_table' doesn't exist" in span.attributes.get("error.stack"))