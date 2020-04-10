"""
To trace sqlalchemy queries, add instrumentation to the engine class or
instance you are using::

    from opentelemetry.instrumentation.sqlalchemy.engine import trace_engine
    from sqlalchemy import create_engine

    engine = create_engine('sqlite:///:memory:')
    trace_engine(engine, tracer, 'my-database')

    engine.connect().execute('select count(*) from users')
"""
# project
from ddtrace.ext import net as netx
from ddtrace.ext import sql as sqlx

# 3p
from sqlalchemy.event import listen

from opentelemetry import trace
from opentelemetry.instrumentation.sqlalchemy.version import __version__
from opentelemetry.trace.status import Status, StatusCanonicalCode


def trace_engine(engine, tracer=None, service=None):
    """
    Add tracing instrumentation to the given sqlalchemy engine or instance.

    :param sqlalchemy.Engine engine: a SQLAlchemy engine class or instance
    :param ddtrace.Tracer tracer: a tracer instance. will default to the global
    :param str service: the name of the service to trace.
    """
    tracer = tracer or trace.get_tracer(
        sqlx.normalize_vendor(engine.name), __version__
    )
    EngineTracer(tracer, service, engine)


# pylint: disable=unused-argument
def _wrap_create_engine(func, module, args, kwargs):
    """Trace the SQLAlchemy engine, creating an `EngineTracer`
    object that will listen to SQLAlchemy events. A PIN object
    is attached to the engine instance so that it can be
    used later.
    """
    # the service name is set to `None` so that the engine
    # name is used by default; users can update this setting
    # using the PIN object
    engine = func(*args, **kwargs)
    EngineTracer(
        trace.get_tracer(sqlx.normalize_vendor(engine.name), __version__),
        None,
        engine,
    )
    return engine


class EngineTracer:
    def __init__(self, tracer: trace.Tracer, service, engine):
        self.tracer = tracer
        self.engine = engine
        self.vendor = sqlx.normalize_vendor(engine.name)
        self.service = service or self.vendor
        self.name = "%s.query" % self.vendor
        # TODO: revisit, might be better done w/ context attach/detach
        self.current_span = None

        listen(engine, "before_cursor_execute", self._before_cur_exec)
        listen(engine, "after_cursor_execute", self._after_cur_exec)
        listen(engine, "dbapi_error", self._dbapi_error)

    # pylint: disable=unused-argument
    def _before_cur_exec(self, conn, cursor, statement, *args):
        self.current_span = self.tracer.start_span(self.name)
        with self.tracer.use_span(self.current_span, end_on_exit=False):
            self.current_span.set_attribute("service", self.vendor)
            self.current_span.set_attribute("resource", statement)

            if not _set_attributes_from_url(
                self.current_span, conn.engine.url
            ):
                _set_attributes_from_cursor(
                    self.current_span, self.vendor, cursor
                )

    # pylint: disable=unused-argument
    def _after_cur_exec(self, conn, cursor, statement, *args):
        if not self.current_span:
            return

        try:
            if cursor and cursor.rowcount >= 0:
                self.current_span.set_attribute(sqlx.ROWS, cursor.rowcount)
        finally:
            self.current_span.end()

    # pylint: disable=unused-argument
    def _dbapi_error(self, conn, cursor, statement, *args):
        if not self.current_span:
            return

        try:
            # span.set_traceback()
            self.current_span.set_status(
                Status(StatusCanonicalCode.UNKNOWN, str("something happened"))
            )
        finally:
            self.current_span.end()


def _set_attributes_from_url(span: trace.Span, url):
    """ set connection tags from the url. return true if successful. """
    if url.host:
        span.set_attribute(netx.TARGET_HOST, url.host)
    if url.port:
        span.set_attribute(netx.TARGET_PORT, url.port)
    if url.database:
        span.set_attribute(sqlx.DB, url.database)

    return bool(span.attributes.get(netx.TARGET_HOST, False))


def _set_attributes_from_cursor(span: trace.Span, vendor, cursor):
    """ attempt to set db connection tags by introspecting the cursor. """
    if "postgres" == vendor:
        if hasattr(cursor, "connection") and hasattr(cursor.connection, "dsn"):
            dsn = getattr(cursor.connection, "dsn", None)
            if dsn:
                data = sqlx.parse_pg_dsn(dsn)
                span.set_attribute(sqlx.DB, data.get("dbname"))
                span.set_attribute(netx.TARGET_HOST, data.get("host"))
                span.set_attribute(netx.TARGET_PORT, data.get("port"))