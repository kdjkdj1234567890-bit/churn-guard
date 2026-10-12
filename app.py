#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
조용히 떠나는 단골을 붙잡으세요.
단골 이탈 알림 + 재방문 쿠폰 발송 시뮬레이션 (사장님용 데모)

- Python Flask + SQLite, 외부 API/유료 서비스 없음
- 실행: bash run.sh  →  http://localhost:5008
"""
import hashlib
import os
import sqlite3
from datetime import date, datetime, timedelta

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, url_for,
)
from werkzeug.exceptions import HTTPException

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "customers.db")

# ---------------------------------------------------------------- 업종별 프리셋
# "어떤 가게세요?"에서 고른 업종에 따라 이탈 판단 기준과 쿠폰 예시가 바뀐다.
INDUSTRIES = {
    "beauty": {
        "name": "미용실·네일샵",
        "icon": "💇",
        "tagline": "커트 4주 · 염색 6주 · 네일 3주",
        "desc": "시술 주기에 맞춰 단골을 챙겨 드려요.",
        "warning_days": 45,   # 45일 이상 안 오면 "조금씩 멀어지는 중"
        "danger_days": 90,    # 90일 이상 안 오면 "떠나가는 중"
        "cycle_text": "커트 4주·염색 6주·네일 3주 주기로 판단해요. 45일 이상 안 오시면 확인해 보세요.",
        "mission": "사람들이 거울 앞에서 웃게 만드세요",
        "benchmark_rate": 62,
        "fomo_count": 128,
        "examples": [
            {
                "title": "오랜만에 찾아주셔서 감사 쿠폰",
                "benefit": "다음 염색 예약 시 5천원 할인",
                "message": "고객님, 오랜만이에요! 다음 염색 예약하실 때 5천원 할인해 드려요. 편하실 때 들러 주세요.",
            },
            {
                "title": "단골 네일 케어 쿠폰",
                "benefit": "네일 케어 20% 할인",
                "message": "고객님, 손톱 정리하실 때가 된 것 같아요. 이번 주 예약하시면 20% 할인해 드립니다!",
            },
        ],
    },
    "cleaning": {
        "name": "청소업체",
        "icon": "🧹",
        "tagline": "정기청소 2주 · 월 1회",
        "desc": "정기 청소 주기로 이탈을 판단해요.",
        "warning_days": 21,   # 정기(2주) 1.5주기
        "danger_days": 45,    # 월 1회 기준 1.5개월
        "cycle_text": "정기 청소(2주~월 1회) 주기로 판단해요. 21일 이상 안 오시면 확인해 보세요.",
        "mission": "깨끗한 공간에서 누군가의 새 하루를 열게 만드세요",
        "benchmark_rate": 71,
        "fomo_count": 86,
        "examples": [
            {
                "title": "정기 청소 10% 할인 쿠폰",
                "benefit": "정기 청소 10% 할인",
                "message": "고객님, 이번 달 정기 청소 날짜가 다가와요. 미리 예약하시면 10% 할인해 드립니다!",
            },
            {
                "title": "입주청소 연계 할인 쿠폰",
                "benefit": "입주청소 예약 시 3만원 할인",
                "message": "고객님, 이사·입주 앞두고 계시면 청소도 함께 맡겨 주세요. 3만원 할인해 드려요.",
            },
        ],
    },
    "moving": {
        "name": "이사·용달",
        "icon": "🚚",
        "tagline": "소개가 곧 매출",
        "desc": "이사는 자주 없으니 소개로 이어가요.",
        "warning_days": 180,  # 6개월
        "danger_days": 365,   # 1년
        "cycle_text": "이사는 자주 안 오시는 게 당연해요. 6개월 이상 소식 없으면 지인 소개 쿠폰으로 이어가 보세요.",
        "mission": "누군가의 새 출발을 함께하세요",
        "benchmark_rate": 28,
        "fomo_count": 43,
        "examples": [
            {
                "title": "지인 소개 감사 쿠폰",
                "benefit": "소개해 주시면 입주청소 20% 할인",
                "message": "고객님, 주변에 이사하시는 분 계시면 소개 부탁드려요. 소개해 주신 분께 입주청소 20% 할인해 드립니다!",
            },
            {
                "title": "입주청소 연계 할인 쿠폰",
                "benefit": "이사+입주청소 함께 예약 시 5만원 할인",
                "message": "고객님, 다음 이사 때 입주청소까지 함께 맡기시면 5만원 할인해 드려요.",
            },
        ],
    },
}
INDUSTRY_ORDER = ["beauty", "cleaning", "moving"]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "churn-alert-demo-local-key")


# ---------------------------------------------------------------- DB
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            memo TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            visit_date TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            memo TEXT DEFAULT '',
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            benefit TEXT NOT NULL,
            message TEXT DEFAULT '',
            send_mode TEXT NOT NULL DEFAULT 'now',
            scheduled_at TEXT,
            sent_at TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS coupon_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coupon_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            revisited INTEGER NOT NULL DEFAULT 0,
            revisit_visit_id INTEGER,
            revisit_amount INTEGER DEFAULT 0,
            FOREIGN KEY (coupon_id) REFERENCES coupons(id),
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    # 기존 DB 마이그레이션: 효과 확인 여부 컬럼
    cols = {r["name"] for r in db.execute("PRAGMA table_info(coupons)").fetchall()}
    if "simulated_at" not in cols:
        db.execute("ALTER TABLE coupons ADD COLUMN simulated_at TEXT")
    db.commit()


# ---------------------------------------------------------------- 업종 설정
def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    db.commit()


def industry_code():
    """선택된 업종 코드. 없으면 None."""
    code = get_setting("industry")
    return code if code in INDUSTRIES else None


def industry():
    """선택된 업종 설정 dict. 없으면 미용실 기준(기본값)."""
    return INDUSTRIES[industry_code() or "beauty"]


def get_thresholds():
    """(주의 기준일, 위험 기준일) — 업종에 따라 달라진다."""
    ind = industry()
    return ind["warning_days"], ind["danger_days"]


def today():
    return date.today()


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def fmt_date(s):
    d = parse_date(s)
    return d.strftime("%Y.%m.%d") if d else (s or "-")


def fmt_won(n):
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    return f"{n:,}원"


def fmt_manwon(n):
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 10000:
        return f"{n // 10000:,}만원"
    return f"{n:,}원"


# ---------------------------------------------------------------- 분석 로직
def customer_stats(customer_id):
    """고객 한 명의 방문 통계를 계산한다."""
    db = get_db()
    rows = db.execute(
        "SELECT visit_date, amount FROM visits WHERE customer_id=? ORDER BY visit_date",
        (customer_id,),
    ).fetchall()
    total = sum(r["amount"] for r in rows)
    count = len(rows)
    avg = round(total / count) if count else 0
    first = rows[0]["visit_date"] if rows else None
    last = rows[-1]["visit_date"] if rows else None
    months = 1
    if first and last:
        d1, d2 = parse_date(first), parse_date(last)
        if d1 and d2:
            months = max(1, (d2 - d1).days // 30 + 1)
    return {
        "count": count,
        "total": total,
        "avg": avg,
        "first": first,
        "last": last,
        "months": months,
        "monthly_avg": round(total / months) if count else 0,
    }


def risk_status(customer_id):
    """마지막 방문일 기준으로 이탈 상태를 판정한다.

    반환: status(normal/warning/danger/none), days, label, desc
    """
    db = get_db()
    row = db.execute(
        "SELECT MAX(visit_date) AS last_date FROM visits WHERE customer_id=?",
        (customer_id,),
    ).fetchone()
    last = row["last_date"] if row else None
    if not last:
        return {
            "status": "none", "days": None,
            "label": "아직 기록 없음",
            "desc": "방문 기록이 없어 상태를 알 수 없어요. 첫 방문을 기록해 보세요.",
            "badge": "gray",
        }
    warning_days, danger_days = get_thresholds()
    d = parse_date(last)
    days = (today() - d).days if d else 0
    if days >= danger_days:
        return {
            "status": "danger", "days": days,
            "label": "떠나가는 중",
            "desc": f"{days}일째 안 오고 있어요. 지금 잡지 않으면 돌아오지 않을 수 있어요.",
            "badge": "red",
        }
    if days >= warning_days:
        return {
            "status": "warning", "days": days,
            "label": "조금씩 멀어지는 중",
            "desc": f"{days}일째 안 오고 있어요. 쿠폰 한 장이면 다시 올 수 있는 단골이에요.",
            "badge": "amber",
        }
    return {
        "status": "normal", "days": days,
        "label": "잘 오는 중",
        "desc": f"{days}일 전에 오셨어요. 지금은 걱정 없어요.",
        "badge": "green",
    }


def return_likelihood(customer_id):
    """돌아올 가능성 (휴리스틱). level: 높음/보통/낮음, pct: 0~95"""
    stats = customer_stats(customer_id)
    risk = risk_status(customer_id)
    if stats["count"] == 0:
        return {"level": "알 수 없음", "pct": None}
    # 단골도(방문 횟수)와 최근성으로 점수 계산
    freq_score = min(40, stats["count"] * 8)          # 자주 올수록 +
    spend_score = min(20, stats["monthly_avg"] // 5000)  # 많이 쓸수록 +
    if risk["status"] == "normal":
        recency_score = 30
    elif risk["status"] == "warning":
        recency_score = 18
    elif risk["status"] == "danger":
        recency_score = 8
    else:
        recency_score = 10
    pct = max(5, min(95, freq_score + spend_score + recency_score))
    level = "높음" if pct >= 60 else ("보통" if pct >= 35 else "낮음")
    return {"level": level, "pct": pct}


def estimated_loss(customer_id):
    """이 고객이 완전히 떠나면 3개월간 잃게 되는 예상 매출."""
    stats = customer_stats(customer_id)
    return stats["monthly_avg"] * 3


def all_customers_with_risk():
    db = get_db()
    customers = db.execute(
        "SELECT * FROM customers ORDER BY name"
    ).fetchall()
    result = []
    for c in customers:
        risk = risk_status(c["id"])
        stats = customer_stats(c["id"])
        like = return_likelihood(c["id"])
        result.append({
            "customer": c, "risk": risk, "stats": stats, "like": like,
            "loss": estimated_loss(c["id"]) if risk["status"] in ("warning", "danger") else 0,
        })
    return result


def dashboard_data():
    db = get_db()
    total_customers = db.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
    items = all_customers_with_risk()
    warning = [i for i in items if i["risk"]["status"] == "warning"]
    danger = [i for i in items if i["risk"]["status"] == "danger"]
    at_risk = warning + danger
    loss = sum(i["loss"] for i in at_risk)

    month_ago = (today() - timedelta(days=30)).isoformat()
    month_sales = db.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM visits WHERE visit_date >= ?", (month_ago,)
    ).fetchone()["s"]
    month_customers = db.execute(
        "SELECT COUNT(DISTINCT customer_id) c FROM visits WHERE visit_date >= ?", (month_ago,)
    ).fetchone()["c"]

    coupons_sent = db.execute(
        "SELECT COUNT(*) c FROM coupons WHERE status='sent'"
    ).fetchone()["c"]
    coupons_scheduled = db.execute(
        "SELECT COUNT(*) c FROM coupons WHERE status='scheduled'"
    ).fetchone()["c"]

    # 쿠폰 효과 합계
    eff = db.execute(
        """SELECT COUNT(*) AS sent,
                  COALESCE(SUM(revisited),0) AS back,
                  COALESCE(SUM(revisit_amount),0) AS revenue
           FROM coupon_targets"""
    ).fetchone()
    conv = round(eff["back"] / eff["sent"] * 100) if eff["sent"] else 0

    recent = db.execute(
        """SELECT v.visit_date, v.amount, c.name
           FROM visits v JOIN customers c ON c.id=v.customer_id
           ORDER BY v.visit_date DESC, v.id DESC LIMIT 5"""
    ).fetchall()

    top_risk = sorted(at_risk, key=lambda i: i["risk"]["days"] or 0, reverse=True)[:3]

    return {
        "total_customers": total_customers,
        "warning_count": len(warning),
        "danger_count": len(danger),
        "at_risk_count": len(at_risk),
        "loss": loss,
        "month_sales": month_sales,
        "month_customers": month_customers,
        "coupons_sent": coupons_sent,
        "coupons_scheduled": coupons_scheduled,
        "conv": conv,
        "recovered": eff["revenue"],
        "recovered_count": eff["back"],
        "recent": recent,
        "top_risk": top_risk,
    }


# ---------------------------------------------------------------- 감동 설계 (쓰나미 모먼트)
def owner_name():
    """설정에서 저장한 사장님 성함."""
    return (get_setting("owner_name") or "").strip()


def daypart_greeting():
    """시간대별 인사 — '누군가 챙겨주는 존재'처럼 말을 건넵니다."""
    h = datetime.now().hour
    name = owner_name()
    who = f"{name} 사장님" if name else "사장님"
    if 5 <= h < 11:
        return f"좋은 아침이에요, {who}. 오늘도 힘내세요 😊"
    if 11 <= h < 17:
        return f"바쁜 오후에도 단골은 지킴이가 챙기고 있어요, {who}."
    if 17 <= h < 23:
        return f"오늘 수고하셨어요, {who}."
    return f"늦은 시간까지 고생하세요, {who}. 내일 아침 브리핑에서 만나요 🌙"


def latest_return_story():
    """쓰나미 트리거: 가장 최근에 돌아온 단골의 이야기."""
    db = get_db()
    r = db.execute(
        """SELECT ct.customer_id, c.name, v.visit_date AS back_date
           FROM coupon_targets ct
           JOIN customers c ON c.id = ct.customer_id
           JOIN visits v ON v.id = ct.revisit_visit_id
           WHERE ct.revisited = 1
           ORDER BY v.visit_date DESC LIMIT 1"""
    ).fetchone()
    if not r:
        return None
    prev = db.execute(
        "SELECT MAX(visit_date) d FROM visits WHERE customer_id=? AND visit_date < ?",
        (r["customer_id"], r["back_date"]),
    ).fetchone()["d"]
    gap_txt = ""
    if prev:
        d1, d2 = parse_date(prev), parse_date(r["back_date"])
        if d1 and d2:
            days = (d2 - d1).days
            gap_txt = f"{days // 30}개월" if days >= 60 else f"{days}일"
    first = r["name"][0] if r["name"] else "김"
    text = f"{first}○○님이 돌아왔어요."
    if gap_txt:
        text += f" {gap_txt} 만이에요."
    text += " 사장님이 건넨 안부 덕분이에요."
    return {"text": text, "name": r["name"]}


def neighbor_story():
    """이웃 자극 (익명화): 같은 동네 가게의 이번 달 소식."""
    db = get_db()
    ym = today().strftime("%Y-%m")
    mine = db.execute(
        """SELECT COUNT(*) c FROM coupon_targets ct
           JOIN visits v ON v.id = ct.revisit_visit_id
           WHERE ct.revisited=1 AND substr(v.visit_date,1,7) = ?""",
        (ym,),
    ).fetchone()["c"]
    ind = industry()
    # 업종별 익명 이웃 가게 이름 (데모용)
    neighbor_names = {
        "beauty": "김○○헤어", "cleaning": "박○○클린", "moving": "이○○익스프레스",
    }
    code = industry_code() or "beauty"
    # 결정적 익명 수치: 내 실적 기준 ±2 (항상 이웃이 살짝 앞서도록)
    h = int(hashlib.md5(f"neighbor:{code}:{ym}".encode()).hexdigest()[:4], 16)
    theirs = mine + 1 + (h % 3)
    return {
        "shop": neighbor_names.get(code, "김○○상회"),
        "industry": ind["name"],
        "theirs": theirs,
        "mine": mine,
    }


# ---------------------------------------------------------------- 오늘 브리핑 (도파민 대시보드)
LEVEL_NAMES = {1: "첫걸음", 2: "익숙함", 3: "소문난 가게", 4: "단골 부자", 5: "전설의 단골집"}
LEVEL_CUTS = [60, 150, 300, 600]  # 레벨업 점수 기준
AVG_LEVEL = 2  # 옆 가게 평균 레벨 (익명 통계 예시)


def revisit_rate_90():
    """최근 90일 재방문율: 90일 내 2회 이상 방문한 단골 / 90일 내 1회 이상 방문한 단골."""
    db = get_db()
    cutoff = (today() - timedelta(days=89)).isoformat()
    rows = db.execute(
        "SELECT customer_id, COUNT(*) c FROM visits WHERE visit_date >= ? GROUP BY customer_id",
        (cutoff,),
    ).fetchall()
    once = len(rows)
    multi = sum(1 for r in rows if r["c"] >= 2)
    return round(multi / once * 100) if once else 0


def monthly_report():
    """이번 달 성공 리포트: 돌아온 단골 · 되찾은 매출 · 평균 대비."""
    db = get_db()
    ym = today().strftime("%Y-%m")
    r = db.execute(
        """SELECT COUNT(*) c, COALESCE(SUM(ct.revisit_amount),0) s
           FROM coupon_targets ct JOIN visits v ON v.id = ct.revisit_visit_id
           WHERE ct.revisited=1 AND substr(v.visit_date,1,7) = ?""",
        (ym,),
    ).fetchone()
    my_rate = revisit_rate_90()
    bench = industry()["benchmark_rate"]
    return {
        "month": today().strftime("%m").lstrip("0"),
        "back": r["c"], "revenue": r["s"],
        "my_rate": my_rate, "bench": bench, "diff": my_rate - bench,
    }


def briefing_data():
    """사장님 오늘 브리핑: 도파민 숫자 + 벤치마크 + 레벨 + 락인 + 가격심리."""
    db = get_db()
    ind = industry()
    week_ago = (today() - timedelta(days=6)).isoformat()  # 최근 7일

    r = db.execute(
        """SELECT COUNT(*) c, COALESCE(SUM(ct.revisit_amount),0) s
           FROM coupon_targets ct JOIN visits v ON v.id = ct.revisit_visit_id
           WHERE ct.revisited=1 AND v.visit_date >= ?""",
        (week_ago,),
    ).fetchone()
    back_week, revenue_week = r["c"], r["s"]
    total_back = db.execute(
        "SELECT COALESCE(SUM(revisited),0) s FROM coupon_targets").fetchone()["s"]
    total_revenue = db.execute(
        "SELECT COALESCE(SUM(revisit_amount),0) s FROM coupon_targets").fetchone()["s"]
    visits_week = db.execute(
        "SELECT COUNT(*) c FROM visits WHERE visit_date >= ?", (week_ago,)).fetchone()["c"]
    n_customers = db.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
    n_visits = db.execute("SELECT COUNT(*) c FROM visits").fetchone()["c"]

    # 사장님 가게 재방문율 (최근 90일 기준)
    my_rate = revisit_rate_90()
    bench_rate = ind["benchmark_rate"]

    # 단골 관리 레벨
    coupons_sent = db.execute(
        "SELECT COUNT(*) c FROM coupons WHERE status='sent'").fetchone()["c"]
    score = n_customers * 2 + n_visits + coupons_sent * 15 + total_back * 25
    level = 1 + sum(score >= t for t in LEVEL_CUTS)
    next_cut = next((t for t in LEVEL_CUTS if score < t), None)
    progress = round(score / next_cut * 100) if next_cut else 100

    # 가격 심리: 월 3만원 플랜 대비 ROI
    d = dashboard_data()
    monthly_keep = d["loss"] // 3
    roi = monthly_keep // 30000 if monthly_keep >= 30000 else 0

    return {
        "ind": ind,
        "back_week": back_week, "revenue_week": revenue_week,
        "total_back": total_back, "total_revenue": total_revenue,
        "visits_week": visits_week,
        "n_customers": n_customers, "n_visits": n_visits,
        "my_rate": my_rate, "bench_rate": bench_rate,
        "level": level, "level_name": LEVEL_NAMES[level],
        "score": score, "next_cut": next_cut, "progress": progress,
        "avg_level": AVG_LEVEL,
        "monthly_keep": monthly_keep, "roi": roi,
        "at_risk_count": d["at_risk_count"],
        "today_str": today().strftime("%m월 %d일"),
    }


# ---------------------------------------------------------------- 쿠폰 시뮬레이션
def deterministic_roll(coupon_id, customer_id):
    """쿠폰+고객 조합으로 0~1 사이 결정적 난수를 만든다 (같은 쿠폰은 항상 같은 결과)."""
    h = hashlib.md5(f"{coupon_id}:{customer_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def send_coupon(coupon_id):
    """쿠폰을 발송 처리한다 (실제 SMS 없음, 시뮬레이션)."""
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.execute(
        "UPDATE coupons SET status='sent', sent_at=? WHERE id=? AND status IN ('draft','scheduled')",
        (now, coupon_id),
    )
    db.commit()


def simulate_effect(coupon_id):
    """쿠폰 발송 효과를 시뮬레이션한다. 돌아온 고객에게는 방문 기록을 자동 추가한다.

    반환: (돌아온 수, 되찾은 매출)
    """
    db = get_db()
    targets = db.execute(
        "SELECT * FROM coupon_targets WHERE coupon_id=? AND revisited=0", (coupon_id,)
    ).fetchall()
    coupon = db.execute("SELECT * FROM coupons WHERE id=?", (coupon_id,)).fetchone()
    if not coupon:
        return 0, 0
    sent_date = parse_date((coupon["sent_at"] or "")[:10]) or today()
    back, revenue = 0, 0
    for t in targets:
        risk = risk_status(t["customer_id"])
        stats = customer_stats(t["customer_id"])
        if risk["status"] == "danger":
            prob = 0.30
        elif risk["status"] == "warning":
            prob = 0.50
        elif risk["status"] == "normal":
            prob = 0.65
        else:
            prob = 0.40
        if deterministic_roll(coupon_id, t["customer_id"]) < prob:
            # 돌아온 날: 발송 후 1~7일 뒤 (결정적)
            gap = 1 + int(deterministic_roll(t["customer_id"], coupon_id) * 7) % 7
            visit_date = (sent_date + timedelta(days=gap)).isoformat()
            amount = stats["avg"] if stats["avg"] > 0 else 30000
            cur = db.execute(
                "INSERT INTO visits (customer_id, visit_date, amount, memo) VALUES (?,?,?,?)",
                (t["customer_id"], visit_date, amount, "쿠폰 보고 재방문 (시뮬레이션)"),
            )
            visit_id = cur.lastrowid
            db.execute(
                "UPDATE coupon_targets SET revisited=1, revisit_visit_id=?, revisit_amount=? WHERE id=?",
                (visit_id, amount, t["id"]),
            )
            back += 1
            revenue += amount
    db.execute("UPDATE coupons SET simulated_at=? WHERE id=?",
               (datetime.now().strftime("%Y-%m-%d %H:%M"), coupon_id))
    db.commit()
    return back, revenue


# ---------------------------------------------------------------- 라우트
@app.before_request
def _require_industry():
    """가게 종류를 아직 안 골랐고, 단골 기록도 비어 있으면 오프닝(짜증 모먼트)으로."""
    if request.endpoint in ("start", "opening", "about", "static", None):
        return None
    try:
        if industry_code() is None:
            n = get_db().execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
            if n == 0:
                return redirect(url_for("opening"))
            # 기존 DB 호환: 기록은 있는데 업종이 없으면 미용실 기준으로 둔다
            set_setting("industry", "beauty")
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("industry check error")
    return None


@app.route("/opening")
def opening():
    """짜증 모먼트 오프닝: 사장님의 아픈 순간을 숫자로 보여주고 안심시킨다."""
    try:
        return render_template("opening.html")
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("opening error")
        return render_template("error.html",
                               message="화면을 불러오는 중 문제가 생겼어요."), 500


@app.route("/start", methods=["GET", "POST"])
def start():
    """어떤 가게세요? — 업종 선택 온보딩."""
    try:
        if request.method == "POST":
            code = (request.form.get("industry") or "").strip()
            if code not in INDUSTRIES:
                flash("가게 종류를 선택해 주세요.")
                return redirect(url_for("start"))
            set_setting("industry", code)
            db = get_db()
            n = db.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
            if n == 0:
                from seed import seed_all
                seed_all(db, code)
            ind = INDUSTRIES[code]
            flash(f"{ind['icon']} {ind['name']} 기준으로 단골 기록을 준비했어요.")
            return redirect(url_for("dashboard"))
        current = industry_code()
        return render_template("start.html", industries=INDUSTRIES,
                               order=INDUSTRY_ORDER, current=current)
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("start error")
        return render_template("error.html",
                               message="화면을 불러오는 중 문제가 생겼어요."), 500


@app.route("/")
def dashboard():
    try:
        ind = industry()
        warning_days, danger_days = get_thresholds()
        return render_template("dashboard.html", d=dashboard_data(), fmt_won=fmt_won,
                               fmt_manwon=fmt_manwon, fmt_date=fmt_date,
                               ind=ind, warning_days=warning_days,
                               danger_days=danger_days,
                               greeting=daypart_greeting(),
                               story=latest_return_story())
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("dashboard error")
        return render_template("error.html",
                               message="화면을 불러오는 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요."), 500


@app.route("/briefing")
def briefing():
    """사장님 오늘 브리핑: 도파민 대시보드."""
    try:
        return render_template("briefing.html", b=briefing_data(),
                               fmt_won=fmt_won, fmt_manwon=fmt_manwon,
                               greeting=daypart_greeting(),
                               neighbor=neighbor_story())
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("briefing error")
        return render_template("error.html",
                               message="브리핑을 불러오는 중 문제가 생겼어요."), 500


@app.route("/about")
def about():
    """우리의 약속: 레거시 철학."""
    try:
        return render_template("about.html")
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("about error")
        return render_template("error.html",
                               message="소개 화면을 불러오는 중 문제가 생겼어요."), 500


@app.route("/settings")
def settings():
    """설정: 가게 정보 · 사장님 성함 · 추천 리워드 · 성장 시뮬레이터."""
    try:
        ind = industry()
        ref_code = get_setting("ref_code")
        ref_link = f"https://dangoljikimi.example/r/{ref_code}" if ref_code else None
        return render_template("settings.html", ind=ind,
                               ref_code=ref_code, ref_link=ref_link,
                               owner_name=owner_name())
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("settings error")
        return render_template("error.html",
                               message="설정 화면을 불러오는 중 문제가 생겼어요."), 500


@app.route("/settings/owner", methods=["POST"])
def owner_save():
    """사장님 성함 저장."""
    try:
        name = (request.form.get("owner_name") or "").strip()[:10]
        set_setting("owner_name", name)
        if name:
            flash(f"{name} 사장님, 앞으로 이름으로 불러드릴게요 😊")
        else:
            flash("성함을 지웠어요.")
        return redirect(url_for("settings"))
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("owner save error")
        flash("저장 중 문제가 생겼어요. 다시 시도해 주세요.")
        return redirect(url_for("settings"))


@app.route("/settings/export")
def export_data():
    """내 데이터 가져가기 — 단골 기록 CSV 다운로드.

    떠나고 싶으시면 언제든 데이터를 들고 가세요. 우리는 보관만 합니다.
    """
    try:
        import csv
        import io
        db = get_db()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["고객명", "전화번호", "고객메모", "방문일", "금액(원)", "방문메모"])
        for r in db.execute(
            """SELECT c.name, c.phone, c.memo, v.visit_date, v.amount, v.memo AS vmemo
               FROM customers c LEFT JOIN visits v ON v.customer_id = c.id
               ORDER BY c.name, v.visit_date"""
        ):
            w.writerow([
                r["name"] or "", r["phone"] or "", r["memo"] or "",
                r["visit_date"] or "", r["amount"] or "", r["vmemo"] or "",
            ])
        body = "\ufeff" + out.getvalue()  # Excel 한글 깨짐 방지
        resp = app.response_class(body, mimetype="text/csv; charset=utf-8")
        resp.headers["Content-Disposition"] = "attachment; filename=dangol-data.csv"
        return resp
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("export error")
        flash("데이터 가져오기 중 문제가 생겼어요. 다시 시도해 주세요.")
        return redirect(url_for("settings"))


@app.route("/settings/referral", methods=["POST"])
def referral_create():
    """소개 링크 생성 시뮬레이션."""
    try:
        import secrets
        code = get_setting("ref_code")
        if not code:
            code = secrets.token_hex(4).upper()
            set_setting("ref_code", code)
        flash("소개 링크가 만들어졌어요. 옆 가게 사장님께 공유해 보세요! (시뮬레이션)")
        return redirect(url_for("settings"))
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("referral error")
        flash("링크 생성 중 문제가 생겼어요. 다시 시도해 주세요.")
        return redirect(url_for("settings"))


@app.route("/customers")
def customers():
    try:
        q = (request.args.get("q") or "").strip()
        items = all_customers_with_risk()
        if q:
            items = [i for i in items if q in i["customer"]["name"]
                     or q in (i["customer"]["phone"] or "")]
        return render_template("customers.html", items=items, q=q,
                               fmt_won=fmt_won, fmt_date=fmt_date)
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("customers error")
        return render_template("error.html",
                               message="단골 명단을 불러오는 중 문제가 생겼어요."), 500


@app.route("/customers/add", methods=["POST"])
def customer_add():
    try:
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        memo = (request.form.get("memo") or "").strip()
        if not name:
            flash("고객 이름은 꼭 적어 주세요.")
            return redirect(url_for("customers"))
        if len(name) > 30:
            flash("이름이 너무 길어요. 30자 이내로 적어 주세요.")
            return redirect(url_for("customers"))
        db = get_db()
        cur = db.execute(
            "INSERT INTO customers (name, phone, memo, created_at) VALUES (?,?,?,?)",
            (name, phone, memo, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
        db.commit()
        flash(f"'{name}' 고객을 등록했어요. 이제 방문 기록을 추가해 보세요.")
        return redirect(url_for("customer_detail", customer_id=cur.lastrowid))
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("customer_add error")
        flash("고객 등록 중 문제가 생겼어요. 다시 시도해 주세요.")
        return redirect(url_for("customers"))


@app.route("/customers/<int:customer_id>")
def customer_detail(customer_id):
    try:
        db = get_db()
        c = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not c:
            abort(404)
        visits = db.execute(
            "SELECT * FROM visits WHERE customer_id=? ORDER BY visit_date DESC, id DESC",
            (customer_id,),
        ).fetchall()
        stats = customer_stats(customer_id)
        risk = risk_status(customer_id)
        like = return_likelihood(customer_id)
        sent_coupons = db.execute(
            """SELECT cp.id, cp.title, cp.benefit, cp.sent_at, cp.status, t.revisited
               FROM coupon_targets t JOIN coupons cp ON cp.id=t.coupon_id
               WHERE t.customer_id=? ORDER BY cp.id DESC""",
            (customer_id,),
        ).fetchall()
        return render_template("customer_detail.html", c=c, visits=visits, stats=stats,
                               risk=risk, like=like, sent_coupons=sent_coupons,
                               fmt_won=fmt_won, fmt_manwon=fmt_manwon, fmt_date=fmt_date,
                               today_str=today().isoformat())
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("customer_detail error")
        return render_template("error.html",
                               message="고객 정보를 불러오는 중 문제가 생겼어요."), 500


@app.route("/customers/<int:customer_id>/visit", methods=["POST"])
def visit_add(customer_id):
    try:
        db = get_db()
        c = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not c:
            abort(404)
        visit_date = (request.form.get("visit_date") or "").strip() or today().isoformat()
        if not parse_date(visit_date):
            flash("날짜 형식이 올바르지 않아요.")
            return redirect(url_for("customer_detail", customer_id=customer_id))
        if parse_date(visit_date) > today():
            flash("미래 날짜는 기록할 수 없어요.")
            return redirect(url_for("customer_detail", customer_id=customer_id))
        try:
            amount = int((request.form.get("amount") or "0").replace(",", "").strip())
        except ValueError:
            flash("금액은 숫자로만 적어 주세요.")
            return redirect(url_for("customer_detail", customer_id=customer_id))
        if amount <= 0 or amount > 100_000_000:
            flash("금액을 다시 확인해 주세요.")
            return redirect(url_for("customer_detail", customer_id=customer_id))
        memo = (request.form.get("memo") or "").strip()
        db.execute(
            "INSERT INTO visits (customer_id, visit_date, amount, memo) VALUES (?,?,?,?)",
            (customer_id, visit_date, amount, memo),
        )
        db.commit()
        flash(f"{fmt_date(visit_date)} 방문 기록을 저장했어요. ({fmt_won(amount)})")
        return redirect(url_for("customer_detail", customer_id=customer_id))
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("visit_add error")
        flash("방문 기록 저장 중 문제가 생겼어요. 다시 시도해 주세요.")
        return redirect(url_for("customer_detail", customer_id=customer_id))


@app.route("/risk")
def risk_list():
    """떠나가는 단골 목록 (안 온 지 오래된 순)."""
    try:
        items = all_customers_with_risk()
        order = {"danger": 0, "warning": 1, "none": 2, "normal": 3}
        items.sort(key=lambda i: (order[i["risk"]["status"]],
                                  -(i["risk"]["days"] or 0)))
        at_risk = [i for i in items if i["risk"]["status"] in ("danger", "warning")]
        normal = [i for i in items if i["risk"]["status"] == "normal"]
        none_items = [i for i in items if i["risk"]["status"] == "none"]
        total_loss = sum(i["loss"] for i in at_risk)
        return render_template("risk.html", items=items, at_risk=at_risk,
                               normal=normal, none_items=none_items,
                               total_loss=total_loss, fmt_won=fmt_won,
                               fmt_manwon=fmt_manwon, fmt_date=fmt_date,
                               ind=industry())
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("risk_list error")
        return render_template("error.html",
                               message="떠나가는 단골 목록을 불러오는 중 문제가 생겼어요."), 500


@app.route("/coupons")
def coupons():
    try:
        db = get_db()
        rows = db.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
        items = []
        for cp in rows:
            t = db.execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(revisited),0) AS back,
                          COALESCE(SUM(revisit_amount),0) AS revenue
                   FROM coupon_targets WHERE coupon_id=?""",
                (cp["id"],),
            ).fetchone()
            items.append({"coupon": cp, "targets": t["n"], "back": t["back"],
                          "revenue": t["revenue"],
                          "conv": round(t["back"] / t["n"] * 100) if t["n"] else 0})
        return render_template("coupons.html", items=items, fmt_date=fmt_date,
                               fmt_won=fmt_won, fmt_manwon=fmt_manwon)
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("coupons error")
        return render_template("error.html",
                               message="쿠폰 발송 내역을 불러오는 중 문제가 생겼어요."), 500


@app.route("/coupons/new")
def coupon_new():
    """쿠폰 작성 화면. risk 페이지에서 선택한 고객을 미리 체크한다."""
    try:
        # risk 페이지에서 여러 명을 체크하면 ?targets=1&targets=2 형태로 넘어온다
        preselect_ids = set()
        for v in request.args.getlist("targets"):
            for x in v.split(","):
                if x.strip().isdigit():
                    preselect_ids.add(int(x))
        items = all_customers_with_risk()
        at_risk = [i for i in items if i["risk"]["status"] in ("danger", "warning")]
        others = [i for i in items if i["risk"]["status"] not in ("danger", "warning")]
        return render_template("coupon_new.html", at_risk=at_risk, others=others,
                               preselect_ids=preselect_ids,
                               today_str=today().isoformat(),
                               ind=industry())
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("coupon_new error")
        return render_template("error.html",
                               message="쿠폰 작성 화면을 불러오는 중 문제가 생겼어요."), 500


@app.route("/coupons/create", methods=["POST"])
def coupon_create():
    try:
        title = (request.form.get("title") or "").strip()
        benefit = (request.form.get("benefit") or "").strip()
        message = (request.form.get("message") or "").strip()
        send_mode = request.form.get("send_mode") or "now"
        scheduled_at = (request.form.get("scheduled_at") or "").strip()
        target_ids = [int(x) for x in request.form.getlist("targets") if x.isdigit()]

        if not title:
            flash("쿠폰 제목을 적어 주세요. (예: 단골 감사 쿠폰)")
            return redirect(url_for("coupon_new"))
        if not benefit:
            flash("혜택 내용을 적어 주세요. (예: 20% 할인)")
            return redirect(url_for("coupon_new"))
        if not target_ids:
            flash("쿠폰을 보낼 고객을 한 명 이상 선택해 주세요.")
            return redirect(url_for("coupon_new"))

        db = get_db()
        # 존재하는 고객만 대상으로
        placeholders = ",".join("?" * len(target_ids))
        valid = {r["id"] for r in db.execute(
            f"SELECT id FROM customers WHERE id IN ({placeholders})", target_ids)}
        target_ids = [i for i in target_ids if i in valid]
        if not target_ids:
            flash("선택한 고객 정보를 찾을 수 없어요. 다시 선택해 주세요.")
            return redirect(url_for("coupon_new"))

        status = "scheduled" if send_mode == "schedule" else "draft"
        sent_at = None
        if send_mode == "schedule":
            if not parse_date(scheduled_at):
                flash("예약 날짜를 올바르게 선택해 주세요.")
                return redirect(url_for("coupon_new"))
            if parse_date(scheduled_at) < today():
                flash("예약 날짜는 오늘 이후로 선택해 주세요.")
                return redirect(url_for("coupon_new"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cur = db.execute(
            """INSERT INTO coupons (title, benefit, message, send_mode, scheduled_at,
                                    sent_at, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (title, benefit, message, send_mode, scheduled_at or None,
             sent_at, status, now),
        )
        coupon_id = cur.lastrowid
        for cid in target_ids:
            db.execute(
                "INSERT INTO coupon_targets (coupon_id, customer_id) VALUES (?,?)",
                (coupon_id, cid),
            )
        db.commit()

        if send_mode == "now":
            send_coupon(coupon_id)
            flash(f"쿠폰 '{title}'을(를) {len(target_ids)}명에게 발송했어요. (실제 문자 없이 시뮬레이션)")
        else:
            flash(f"쿠폰 '{title}' 발송을 {fmt_date(scheduled_at)}로 예약했어요.")
        return redirect(url_for("coupon_detail", coupon_id=coupon_id))
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("coupon_create error")
        flash("쿠폰을 만드는 중 문제가 생겼어요. 다시 시도해 주세요.")
        return redirect(url_for("coupon_new"))


@app.route("/coupons/<int:coupon_id>")
def coupon_detail(coupon_id):
    try:
        db = get_db()
        cp = db.execute("SELECT * FROM coupons WHERE id=?", (coupon_id,)).fetchone()
        if not cp:
            abort(404)
        targets = db.execute(
            """SELECT t.*, c.name, c.phone
               FROM coupon_targets t JOIN customers c ON c.id=t.customer_id
               WHERE t.coupon_id=? ORDER BY c.name""",
            (coupon_id,),
        ).fetchall()
        enriched = []
        for t in targets:
            r = risk_status(t["customer_id"])
            enriched.append({"t": t, "risk": r})
        n = len(targets)
        back = sum(1 for t in targets if t["revisited"])
        revenue = sum(t["revisit_amount"] or 0 for t in targets)
        conv = round(back / n * 100) if n else 0
        return render_template("coupon_detail.html", cp=cp, targets=enriched,
                               n=n, back=back, revenue=revenue, conv=conv,
                               fmt_date=fmt_date, fmt_won=fmt_won,
                               fmt_manwon=fmt_manwon)
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("coupon_detail error")
        return render_template("error.html",
                               message="쿠폰 정보를 불러오는 중 문제가 생겼어요."), 500


@app.route("/coupons/<int:coupon_id>/send", methods=["POST"])
def coupon_send(coupon_id):
    """예약된 쿠폰을 지금 발송한다."""
    try:
        db = get_db()
        cp = db.execute("SELECT * FROM coupons WHERE id=?", (coupon_id,)).fetchone()
        if not cp:
            abort(404)
        if cp["status"] == "sent":
            flash("이미 발송된 쿠폰이에요.")
        else:
            send_coupon(coupon_id)
            n = db.execute("SELECT COUNT(*) c FROM coupon_targets WHERE coupon_id=?",
                           (coupon_id,)).fetchone()["c"]
            flash(f"쿠폰 '{cp['title']}'을(를) {n}명에게 발송했어요. (실제 문자 없이 시뮬레이션)")
        return redirect(url_for("coupon_detail", coupon_id=coupon_id))
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("coupon_send error")
        flash("발송 중 문제가 생겼어요. 다시 시도해 주세요.")
        return redirect(url_for("coupon_detail", coupon_id=coupon_id))


@app.route("/coupons/<int:coupon_id>/simulate", methods=["POST"])
def coupon_simulate(coupon_id):
    """쿠폰 효과를 시뮬레이션한다 (돌아온 고객의 방문 기록 자동 추가)."""
    try:
        db = get_db()
        cp = db.execute("SELECT * FROM coupons WHERE id=?", (coupon_id,)).fetchone()
        if not cp:
            abort(404)
        if cp["status"] != "sent":
            flash("먼저 쿠폰을 발송해 주세요.")
            return redirect(url_for("coupon_detail", coupon_id=coupon_id))
        back, revenue = simulate_effect(coupon_id)
        if back:
            flash(f"시뮬레이션 결과: {back}명이 다시 찾아왔고, {fmt_manwon(revenue)}의 매출을 되찾았어요!")
        else:
            flash("시뮬레이션 결과: 이번에는 돌아온 고객이 없었어요. 혜택을 더 키워 보세요.")
        return redirect(url_for("coupon_detail", coupon_id=coupon_id))
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("coupon_simulate error")
        flash("효과 확인 중 문제가 생겼어요. 다시 시도해 주세요.")
        return redirect(url_for("coupon_detail", coupon_id=coupon_id))


@app.route("/effect")
def effect():
    """쿠폰 효과 측정 화면."""
    try:
        db = get_db()
        rows = db.execute(
            "SELECT * FROM coupons WHERE status='sent' ORDER BY sent_at DESC, id DESC"
        ).fetchall()
        items = []
        total_sent = total_back = total_revenue = 0
        simulated_count = 0
        for cp in rows:
            t = db.execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(revisited),0) AS back,
                          COALESCE(SUM(revisit_amount),0) AS revenue
                   FROM coupon_targets WHERE coupon_id=?""",
                (cp["id"],),
            ).fetchone()
            conv = round(t["back"] / t["n"] * 100) if t["n"] else 0
            items.append({"coupon": cp, "n": t["n"], "back": t["back"],
                          "revenue": t["revenue"], "conv": conv})
            # 합계는 효과를 확인한 쿠폰만 집계 (확인 전 쿠폰이 전환율을 깎지 않게)
            if cp["simulated_at"]:
                simulated_count += 1
                total_sent += t["n"]
                total_back += t["back"]
                total_revenue += t["revenue"]
        total_conv = round(total_back / total_sent * 100) if total_sent else 0
        return render_template("effect.html", items=items, total_sent=total_sent,
                               total_back=total_back, total_revenue=total_revenue,
                               total_conv=total_conv, simulated_count=simulated_count,
                               report=monthly_report(), ind=industry(),
                               fmt_date=fmt_date,
                               fmt_won=fmt_won, fmt_manwon=fmt_manwon)
    except HTTPException:
        raise
    except Exception:
        app.logger.exception("effect error")
        return render_template("error.html",
                               message="효과 측정 화면을 불러오는 중 문제가 생겼어요."), 500


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html",
                           message="찾으시는 페이지가 없어요. 주소를 다시 확인해 주세요."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html",
                           message="일시적인 문제가 생겼어요. 잠시 후 다시 시도해 주세요."), 500


def ensure_seed(industry_code_="beauty"):
    """DB가 비어 있으면 업종에 맞는 예시 고객 데이터를 채운다."""
    db = get_db()
    n = db.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
    if n == 0:
        from seed import seed_all
        seed_all(db, industry_code_)


if __name__ == "__main__":
    with app.app_context():
        init_db()
    port = int(os.environ.get("PORT", "5008"))
    app.run(host="0.0.0.0", port=port, debug=False)
