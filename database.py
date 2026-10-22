import os
import sqlite3
import threading
from dotenv import load_dotenv

load_dotenv()

class SQLiteDBWrapper:
    """SQLite için psycopg2 uyumlu DBWrapper (conn.execute ve row['col'] desteği)."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        cur = self.conn.cursor()
        # %s parametrelerini SQLite için ? karakterine çevir
        sqlite_query = query.replace('%s', '?')
        if params:
            cur.execute(sqlite_query, params)
        else:
            cur.execute(sqlite_query)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


# Supabase'e her istekte yeni bağlantı açmak ~0.7 sn sürüyor; açık bağlantılar havuzda saklanır.
_pool = None
_pool_lock = threading.Lock()
_borrowed = set()  # havuzdan alınıp henüz geri verilmemiş bağlantılar


def _get_pool(db_url):
    global _pool
    with _pool_lock:
        if _pool is None:
            from psycopg2.pool import ThreadedConnectionPool
            # psycopg2 en fazla minconn kadar bağlantıyı saklar; sync worker'a tek bağlantı yeter.
            # keepalive: uzun süre boşta kalan bağlantı sessizce kopmasın
            _pool = ThreadedConnectionPool(1, 4, db_url, keepalives=1, keepalives_idle=30,
                                           keepalives_interval=10, keepalives_count=3)
    return _pool


class PostgresDBWrapper:
    """PostgreSQL için psycopg2 DBWrapper. close() bağlantıyı havuza geri verir."""
    def __init__(self, conn, pool=None):
        self.conn = conn
        self.pool = pool
        self.used = False
        if pool is not None:
            _borrowed.add(self)

    def execute(self, query, params=None):
        import psycopg2
        from psycopg2.extras import DictCursor
        for attempt in range(5):
            try:
                cur = self.conn.cursor(cursor_factory=DictCursor)
                if params:
                    cur.execute(query, params)
                else:
                    cur.execute(query)
                break
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # Havuzdaki bağlantıyı sunucu kapatmış olabilir. Yalnızca ilk sorguda
                # yenisiyle tekrar dene; işlem ortasında denemek veriyi bozabilir.
                if self.pool is None or self.used or attempt == 4:
                    raise
                self.pool.putconn(self.conn, close=True)
                self.conn = self.pool.getconn()
        self.used = True
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        if self.conn is None:
            return
        conn, self.conn = self.conn, None
        _borrowed.discard(self)
        if self.pool is None:
            conn.close()
        else:
            # putconn yarım kalan işlemi geri alır, kopmuş bağlantıyı atar
            self.pool.putconn(conn, close=bool(conn.closed))


def release_leaked_connections():
    """İstek sonunda kapatılmamış bağlantıları havuza geri verir (hata/erken dönüş yolları).
    shortcut: gunicorn sync worker'da bir anda tek istek olduğunu varsayar; thread'li worker'a geçilirse istek başına izlenmeli."""
    for wrapper in list(_borrowed):
        wrapper.close()


def get_db_connection():
    db_url = os.getenv('DATABASE_URL')

    # Render veya canlı ortamda PostgreSQL varsa
    if db_url and db_url.startswith(('postgres://', 'postgresql://')):
        import psycopg2
        from psycopg2.pool import PoolError
        # Render bazen postgres:// verir, psycopg2 postgresql:// ister
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        pool = _get_pool(db_url)
        try:
            return PostgresDBWrapper(pool.getconn(), pool)
        except PoolError:
            # Havuz doluysa eski yol: tek seferlik bağlantı
            return PostgresDBWrapper(psycopg2.connect(db_url))
    else:
        # Yerel geliştirme ortamında SQLite kullan
        db_path = os.path.join(os.path.dirname(__file__), 'ozelders.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return SQLiteDBWrapper(conn)


def init_db():
    try:
        db_url = os.getenv('DATABASE_URL')
        is_postgres = bool(db_url and db_url.startswith(('postgres://', 'postgresql://')))
        
        conn = get_db_connection()
        if is_postgres:
            schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
            if os.path.exists(schema_path):
                with open(schema_path, 'r', encoding='utf-8') as f:
                    schema = f.read()
                    conn.execute(schema)
                    conn.commit()
        else:
            # SQLite için şema
            sqlite_schema = """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                grade_level TEXT NOT NULL,
                parent_contact TEXT,
                hourly_rate REAL NOT NULL,
                default_duration INTEGER DEFAULT 60,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id),
                lesson_date TEXT NOT NULL,
                duration_minutes INTEGER DEFAULT 60,
                status TEXT DEFAULT 'planlandi',
                topic TEXT,
                homework TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson_id INTEGER NOT NULL REFERENCES lessons(id),
                amount REAL NOT NULL,
                is_paid INTEGER DEFAULT 0,
                payment_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT,
                grade_level TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS quick_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS mistakes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id),
                lesson_id INTEGER REFERENCES lessons(id),
                mistake_type TEXT NOT NULL,
                topic TEXT,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
            cur = conn.conn.cursor()
            cur.executescript(sqlite_schema)
            conn.commit()
        
        conn.close()
    except Exception as e:
        print(f"init_db uyarısı: {e}")

if __name__ == '__main__':
    init_db()
    print("Veritabanı hazırlandı.")
