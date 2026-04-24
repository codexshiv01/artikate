import psycopg2

conn = psycopg2.connect(
    dbname='neondb',
    user='neondb_owner',
    password='npg_zUVL6JC0nRAc',
    host='ep-long-lab-ammve2mr-pooler.c-5.us-east-1.aws.neon.tech',
    sslmode='require',
)
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = 'test_neondb'
    AND pid <> pg_backend_pid()
""")
print('Terminated sessions:', cur.fetchall())

cur.execute('DROP DATABASE IF EXISTS test_neondb')
print('Dropped test_neondb')

conn.close()
