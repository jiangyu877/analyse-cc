# -*- coding: utf-8 -*-
"""导入更多用户 + 海量消费数据"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.db import query, execute, _get_conn, _commit
import random, hashlib
from datetime import datetime, timedelta
import numpy as np

random.seed(42)
np.random.seed(42)

batch_rows = []
BATCH = 500
total_inserted = 0


def flush():
    global batch_rows, total_inserted
    if not batch_rows:
        return
    conn = _get_conn()
    cur = conn.cursor()
    sql = """INSERT INTO spending_record
        (user_id, spend_date, amount, payment_method, category_id, merchant_id, cu_id, remarks)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"""
    try:
        for r in batch_rows:
            cur.execute(sql, r)
        _commit()
        total_inserted += len(batch_rows)
        print(f"     [批量写入 {len(batch_rows)} 条, 累计 {total_inserted:,}]")
    except Exception as e:
        try:
            conn.rollback()
        except:
            pass
        raise e
    finally:
        cur.close()
        batch_rows.clear()


# ========== 1. 获取角色ID ==========
roles = {r['name']: r['id'] for r in query("SELECT id, name FROM roles")}
print("角色映射:", roles)

# ========== 2. 现状 ==========
print("\n" + "=" * 55)
print("当前状态")
print("=" * 55)
users_now = query("""
    SELECT u.id, u.username, u.full_name, r.name as role
    FROM users u LEFT JOIN roles r ON u.role_id = r.id
    ORDER BY u.id
""")
for u in users_now:
    cnt = query("SELECT COUNT(*) as c FROM spending_record WHERE user_id=%s", (u['id'],))
    print(f"  UID{u['id']:<3} {u['username']:<12} [{u['role']:<8}] {cnt[0]['c']:>5} 条")

total_recs = query("SELECT COUNT(*) as c FROM spending_record")
print(f"\n总记录: {total_recs[0]['c']:,} 条")

# ========== 3. 创建新用户 ==========
new_users = [
    ("david",   "王大伟", "user"),
    ("emma",    "李梦",   "user"),
    ("frank",   "陈建国", "user"),
    ("grace",   "林小美", "analyst"),
    ("henry",   "钱志强", "user"),
    ("iris",    "周思雨", "user"),
    ("jack",    "赵磊",   "analyst"),
    ("kate",    "吴婷婷", "user"),
    ("leo",     "郑伟",   "user"),
    ("mia",     "冯思涵", "user"),
    ("nick",    "陈小明", "user"),
    ("olivia",  "褚心怡", "analyst"),
    ("peter",   "卫建国", "user"),
    ("queen",   "蒋丽华", "user"),
    ("ryan",    "韩涛",   "user"),
    ("sophia",  "沈宛如", "user"),
    ("tom",     "杨光",   "user"),
    ("una",     "秦雨菲", "analyst"),
    ("victor",  "尤志明", "user"),
    ("wendy",   "许晴",   "user"),
]

try:
    import bcrypt
    pw = bcrypt.hashpw("123456".encode(), bcrypt.gensalt()).decode()
except ImportError:
    pw = hashlib.sha256("123456".encode()).hexdigest()

print("\n创建用户...")
actual_created = []
for uname, fullname, role_name in new_users:
    existing = query("SELECT id FROM users WHERE username=%s", (uname,))
    if existing:
        print(f"  跳过: {uname}")
        uid = existing[0]['id']
    else:
        rid = roles[role_name]
        execute(
            "INSERT INTO users (username, password_hash, full_name, role_id, status) VALUES (%s,%s,%s,%s,1)",
            (uname, pw, fullname, rid)
        )
        uid = query("SELECT id FROM users WHERE username=%s", (uname,))[0]['id']
        print(f"  + {uname} ({fullname}) [{role_name}] uid={uid}")
    actual_created.append((uname, uid))

# ========== 4. 配置数据 ==========
categories = query("SELECT category_id, parent_category FROM spending_category")
cat_groups = {}
for c in categories:
    parent = c.get('parent_category') or '其他'
    cat_groups.setdefault(parent, []).append(c['category_id'])
cat_order = list(cat_groups.keys())
all_cat_ids = [c['category_id'] for c in categories]

merchant_ids = [m['merchant_id'] for m in query("SELECT merchant_id FROM merchant")]
region_ids = [r['cu_id'] for r in query("SELECT cu_id FROM consumer_unit")]
payments = ['微信支付', '支付宝', '银行卡', '现金']

print(f"\n{len(all_cat_ids)} 分类 | {len(merchant_ids)} 商户 | {len(region_ids)} 地域")

# ========== 5. 用户消费画像 ==========
# cats order: 餐饮美食, 休闲娱乐, 购物消费, 教育类, 社交人情, 医疗健康, 住房, 交通出行
profiles = {
    "david":   {"day": 180, "std": 120, "we": 0.3,  "bill": 8000,
                "w": [0.35, 0.15, 0.25, 0.05, 0.05, 0.05, 0.05, 0.05]},
    "emma":    {"day": 250, "std": 180, "we": 0.6,  "bill": 6000,
                "w": [0.20, 0.15, 0.30, 0.10, 0.10, 0.05, 0.05, 0.05]},
    "frank":   {"day": 100, "std": 80,  "we": 0.2,  "bill": 3500,
                "w": [0.15, 0.15, 0.10, 0.05, 0.05, 0.25, 0.15, 0.10]},
    "grace":   {"day": 140, "std": 90,  "we": 0.4,  "bill": 5000,
                "w": [0.15, 0.15, 0.20, 0.15, 0.10, 0.10, 0.10, 0.05]},
    "henry":   {"day": 60,  "std": 50,  "we": 0.5,  "bill": 1500,
                "w": [0.40, 0.25, 0.15, 0.05, 0.05, 0.03, 0.03, 0.04]},
    "iris":    {"day": 300, "std": 200, "we": 0.2,  "bill": 9000,
                "w": [0.30, 0.15, 0.20, 0.10, 0.05, 0.10, 0.05, 0.05]},
    "jack":    {"day": 350, "std": 300, "we": 0.1,  "bill": 7000,
                "w": [0.20, 0.20, 0.15, 0.10, 0.15, 0.05, 0.10, 0.05]},
    "kate":    {"day": 90,  "std": 60,  "we": 0.8,  "bill": 3500,
                "w": [0.25, 0.15, 0.20, 0.15, 0.10, 0.05, 0.05, 0.05]},
    "leo":     {"day": 50,  "std": 30,  "we": 0.1,  "bill": 2500,
                "w": [0.35, 0.10, 0.10, 0.05, 0.03, 0.05, 0.22, 0.10]},
    "mia":     {"day": 120, "std": 100, "we": 0.5,  "bill": 4500,
                "w": [0.20, 0.25, 0.20, 0.10, 0.10, 0.05, 0.05, 0.05]},
    "nick":    {"day": 110, "std": 70,  "we": 0.4,  "bill": 4000,
                "w": [0.25, 0.15, 0.20, 0.08, 0.08, 0.12, 0.07, 0.05]},
    "olivia":  {"day": 500, "std": 400, "we": 0.3,  "bill": 12000,
                "w": [0.15, 0.15, 0.30, 0.10, 0.10, 0.10, 0.05, 0.05]},
    "peter":   {"day": 55,  "std": 35,  "we": 0.3,  "bill": 2000,
                "w": [0.30, 0.10, 0.10, 0.05, 0.03, 0.05, 0.27, 0.10]},
    "queen":   {"day": 160, "std": 100, "we": 0.5,  "bill": 5500,
                "w": [0.25, 0.10, 0.25, 0.10, 0.10, 0.10, 0.05, 0.05]},
    "ryan":    {"day": 280, "std": 250, "we": 0.2,  "bill": 6500,
                "w": [0.25, 0.15, 0.15, 0.10, 0.15, 0.05, 0.10, 0.05]},
    "sophia":  {"day": 45,  "std": 40,  "we": 0.7,  "bill": 1200,
                "w": [0.30, 0.20, 0.15, 0.15, 0.05, 0.05, 0.05, 0.05]},
    "tom":     {"day": 70,  "std": 60,  "we": 0.2,  "bill": 2800,
                "w": [0.25, 0.20, 0.10, 0.10, 0.10, 0.15, 0.05, 0.05]},
    "una":     {"day": 200, "std": 150, "we": 0.5,  "bill": 5000,
                "w": [0.20, 0.15, 0.25, 0.10, 0.15, 0.05, 0.05, 0.05]},
    "victor":  {"day": 200, "std": 150, "we": 0.2,  "bill": 8000,
                "w": [0.25, 0.20, 0.30, 0.08, 0.05, 0.05, 0.05, 0.02]},
    "wendy":   {"day": 130, "std": 90,  "we": 0.6,  "bill": 3500,
                "w": [0.20, 0.20, 0.15, 0.10, 0.10, 0.15, 0.05, 0.05]},
}

start, end = datetime(2024, 1, 2), datetime(2026, 7, 10)
print(f"\n生成消费记录: {start.date()} ~ {end.date()}")

for uname, uid in actual_created:
    p = profiles[uname]
    recs = 0
    print(f"\n  [{uname}] 日均{p['day']}元, 月固{p['bill']}元")

    # 每月固定账单
    bm = datetime(2024, 1, 1)
    while bm < end:
        bd = bm.replace(day=random.randint(1, 10))
        housing_ids = cat_groups.get('住房', [all_cat_ids[0]])
        shop_ids = cat_groups.get('购物消费', [all_cat_ids[0]])

        # 房租
        batch_rows.append((
            uid, bd.date(), int(p['bill'] * 0.35), '银行转账',
            random.choice(housing_ids), random.choice(merchant_ids),
            random.choice(region_ids), '月租金'
        ))
        # 水电
        batch_rows.append((
            uid, (bd + timedelta(days=random.randint(3, 8))).date(),
            int(p['bill'] * random.uniform(0.03, 0.06)), '银行卡',
            random.choice(housing_ids), random.choice(merchant_ids),
            random.choice(region_ids), '水电燃气'
        ))
        # 话费
        batch_rows.append((
            uid, (bd + timedelta(days=random.randint(10, 20))).date(),
            random.randint(50, 150), '支付宝',
            random.choice(shop_ids), random.choice(merchant_ids),
            random.choice(region_ids), '话费充值'
        ))
        recs += 3
        bm += timedelta(days=32)
        bm = bm.replace(day=1)

    # 日常消费
    cur = start
    while cur <= end:
        if random.random() < 0.78:
            n = random.choices([1, 2, 3, 4, 5], weights=[0.45, 0.30, 0.15, 0.07, 0.03])[0]
            base = p['day'] / max(n, 1)
            for _ in range(n):
                amt = max(1, int(abs(np.random.normal(base, p['std'] / 2.5))))
                if cur.weekday() >= 5 and random.random() < p['we']:
                    amt = int(amt * random.uniform(1.3, 2.5))
                rv = random.random()
                cum = 0
                sel = cat_order[0]
                for ci, w in enumerate(p['w']):
                    cum += w
                    if rv <= cum:
                        sel = cat_order[ci]
                        break
                subs = cat_groups.get(sel, [all_cat_ids[0]])
                batch_rows.append((
                    uid, cur.date(), amt, random.choice(payments),
                    random.choice(subs), random.choice(merchant_ids),
                    random.choice(region_ids), ''
                ))
                recs += 1
                if len(batch_rows) >= BATCH:
                    flush()
        cur += timedelta(days=1)

    flush()
    print(f"    => {recs} 条")

# ========== 6. 汇总 ==========
print(f"\n{'=' * 55}")
print(f"导入完成! 新增 {total_inserted:,} 条")
print(f"{'=' * 55}")

stats = query("SELECT COUNT(*) as c FROM users")
recs = query("SELECT COUNT(*) as c FROM spending_record")
print(f"用户: {stats[0]['c']} | 记录: {recs[0]['c']:,}")

print(f"\n{'用户':<12} {'笔数':>7} {'总金额':>13} {'日均':>8}")
print("-" * 44)
for u in query("SELECT u.id, u.username FROM users u WHERE u.status=1 ORDER BY u.id"):
    s = query("SELECT COUNT(*) as c, COALESCE(SUM(amount),0) as t FROM spending_record WHERE user_id=%s", (u['id'],))
    r = s[0]
    days = max(1, (end - start).days)
    print(f"  {u['username']:<10} {r['c']:>7} ¥{int(r['t']):>11,} ¥{int(r['t']/days):>6}")
