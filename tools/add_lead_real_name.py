"""一次性迁移：为 customer_lead 增加 real_name 列（客源真实姓名，选填）。"""
import pymysql

HOST, PORT, USER, PASSWORD, DB = "127.0.0.1", 3306, "root", "xuanshiai", "xuanshiai"

conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB, charset="utf8mb4")
try:
    with conn.cursor() as cur:
        cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'customer_lead' AND COLUMN_NAME = 'real_name'", (DB,))
        exists = cur.fetchone()
        if exists:
            print("real_name 已存在，跳过")
        else:
            cur.execute("ALTER TABLE customer_lead ADD COLUMN real_name varchar(64) DEFAULT NULL "
                        "COMMENT '客源真实姓名（选填）' AFTER name")
            conn.commit()
            print("已添加 customer_lead.real_name 列")
        cur.execute("SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'customer_lead' AND COLUMN_NAME = 'real_name'", (DB,))
        print("校验:", cur.fetchone())
finally:
    conn.close()
