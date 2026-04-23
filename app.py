from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, date
import os
from dotenv import load_dotenv
from functools import wraps
from collections import defaultdict

load_dotenv()

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///spendwise.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-in-production-please")

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)


# ── Models ─────────────────────────────────────────────────────────────────

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    currency = db.Column(db.String(5), default="₹")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    transactions = db.relationship("Transaction", backref="user", lazy=True, cascade="all, delete-orphan")
    budgets = db.relationship("Budget", backref="user", lazy=True, cascade="all, delete-orphan")


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200), nullable=True)
    date = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "type": self.type, "amount": self.amount,
            "category": self.category, "description": self.description or "",
            "date": self.date,
        }


class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    month = db.Column(db.String(7), nullable=False)  # YYYY-MM

    def to_dict(self):
        return {"id": self.id, "category": self.category, "amount": self.amount, "month": self.month}


# ── Auth decorator ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json:
                return jsonify({"error": "Not logged in"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# ── Page routes ────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("dashboard.html")

@app.route("/transactions")
@login_required
def transactions_page():
    return render_template("transactions.html")

@app.route("/budgets")
@login_required
def budgets_page():
    return render_template("budgets.html")

@app.route("/reports")
@login_required
def reports_page():
    return render_template("reports.html")

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/signup")
def signup_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ── Auth API ───────────────────────────────────────────────────────────────

@app.route("/api/auth/signup", methods=["POST"])
def signup():
    data = request.get_json()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not name or not email or not password:
        return jsonify({"error": "All fields are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 409
    hashed = bcrypt.generate_password_hash(password).decode("utf-8")
    user = User(name=name, email=email, password=hashed)
    db.session.add(user)
    db.session.commit()
    session["user_id"] = user.id
    session["user_name"] = user.name
    session["user_email"] = user.email
    return jsonify({"success": True}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    user = User.query.filter_by(email=email).first()
    if not user or not bcrypt.check_password_hash(user.password, password):
        return jsonify({"error": "Invalid email or password"}), 401
    session["user_id"] = user.id
    session["user_name"] = user.name
    session["user_email"] = user.email
    return jsonify({"success": True}), 200


# ── Transactions API ───────────────────────────────────────────────────────

@app.route("/api/transactions", methods=["GET"])
@login_required
def get_transactions():
    uid = session["user_id"]
    month = request.args.get("month")
    category = request.args.get("category")
    type_ = request.args.get("type")
    search = request.args.get("search", "").strip().lower()

    q = Transaction.query.filter_by(user_id=uid)
    if month: q = q.filter(Transaction.date.startswith(month))
    if category: q = q.filter_by(category=category)
    if type_: q = q.filter_by(type=type_)
    if search:
        q = q.filter(
            db.or_(
                Transaction.description.ilike(f"%{search}%"),
                Transaction.category.ilike(f"%{search}%"),
            )
        )
    txs = q.order_by(Transaction.date.desc(), Transaction.created_at.desc()).all()
    return jsonify([t.to_dict() for t in txs])


@app.route("/api/transactions", methods=["POST"])
@login_required
def add_transaction():
    uid = session["user_id"]
    data = request.get_json()
    if not all([data.get("type"), data.get("amount"), data.get("category"), data.get("date")]):
        return jsonify({"error": "All fields required"}), 400
    if data["type"] not in ["income", "expense"]:
        return jsonify({"error": "Invalid type"}), 400
    if float(data["amount"]) <= 0:
        return jsonify({"error": "Amount must be positive"}), 400
    t = Transaction(user_id=uid, type=data["type"], amount=float(data["amount"]),
                    category=data["category"], description=data.get("description", ""), date=data["date"])
    db.session.add(t)
    db.session.commit()
    return jsonify(t.to_dict()), 201


@app.route("/api/transactions/<int:tid>", methods=["PUT"])
@login_required
def update_transaction(tid):
    uid = session["user_id"]
    t = Transaction.query.filter_by(id=tid, user_id=uid).first_or_404()
    data = request.get_json()
    t.type = data.get("type", t.type)
    t.amount = float(data.get("amount", t.amount))
    t.category = data.get("category", t.category)
    t.description = data.get("description", t.description)
    t.date = data.get("date", t.date)
    db.session.commit()
    return jsonify(t.to_dict())


@app.route("/api/transactions/<int:tid>", methods=["DELETE"])
@login_required
def delete_transaction(tid):
    uid = session["user_id"]
    t = Transaction.query.filter_by(id=tid, user_id=uid).first_or_404()
    db.session.delete(t)
    db.session.commit()
    return jsonify({"message": "Deleted"})


# ── Summary API ────────────────────────────────────────────────────────────

@app.route("/api/summary")
@login_required
def summary():
    uid = session["user_id"]
    month = request.args.get("month")
    q = Transaction.query.filter_by(user_id=uid)
    if month: q = q.filter(Transaction.date.startswith(month))
    txs = q.all()
    total_income = sum(t.amount for t in txs if t.type == "income")
    total_expense = sum(t.amount for t in txs if t.type == "expense")
    by_category = defaultdict(lambda: {"income": 0, "expense": 0})
    for t in txs:
        by_category[t.category][t.type] += t.amount

    # Top expense category
    exp_cats = {k: v["expense"] for k, v in by_category.items() if v["expense"] > 0}
    top_cat = max(exp_cats, key=exp_cats.get) if exp_cats else None

    return jsonify({
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": total_income - total_expense,
        "by_category": dict(by_category),
        "top_expense_category": top_cat,
        "savings_rate": round((total_income - total_expense) / total_income * 100, 1) if total_income > 0 else 0,
    })


# ── Reports API ────────────────────────────────────────────────────────────

@app.route("/api/reports/monthly")
@login_required
def monthly_report():
    uid = session["user_id"]
    year = request.args.get("year", str(date.today().year))
    txs = Transaction.query.filter_by(user_id=uid).filter(Transaction.date.startswith(year)).all()

    monthly = defaultdict(lambda: {"income": 0, "expense": 0})
    for t in txs:
        m = t.date[:7]  # YYYY-MM
        monthly[m][t.type] += t.amount

    # Build all 12 months
    months = [f"{year}-{str(i).zfill(2)}" for i in range(1, 13)]
    result = []
    for m in months:
        result.append({
            "month": m,
            "label": datetime.strptime(m, "%Y-%m").strftime("%b"),
            "income": monthly[m]["income"],
            "expense": monthly[m]["expense"],
            "balance": monthly[m]["income"] - monthly[m]["expense"],
        })
    return jsonify(result)


@app.route("/api/reports/category-trend")
@login_required
def category_trend():
    uid = session["user_id"]
    year = request.args.get("year", str(date.today().year))
    category = request.args.get("category", "")
    q = Transaction.query.filter_by(user_id=uid, type="expense")
    if year: q = q.filter(Transaction.date.startswith(year))
    if category: q = q.filter_by(category=category)
    txs = q.all()

    monthly = defaultdict(float)
    for t in txs:
        monthly[t.date[:7]] += t.amount

    months = [f"{year}-{str(i).zfill(2)}" for i in range(1, 13)]
    return jsonify([{"month": m, "label": datetime.strptime(m, "%Y-%m").strftime("%b"), "amount": monthly[m]} for m in months])


# ── Budgets API ────────────────────────────────────────────────────────────

@app.route("/api/budgets", methods=["GET"])
@login_required
def get_budgets():
    uid = session["user_id"]
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    budgets = Budget.query.filter_by(user_id=uid, month=month).all()

    # Calculate actual spending per category this month
    txs = Transaction.query.filter_by(user_id=uid, type="expense").filter(
        Transaction.date.startswith(month)).all()
    spent = defaultdict(float)
    for t in txs:
        spent[t.category] += t.amount

    result = []
    for b in budgets:
        s = spent.get(b.category, 0)
        result.append({
            **b.to_dict(),
            "spent": s,
            "remaining": b.amount - s,
            "percent": min(round(s / b.amount * 100, 1), 100) if b.amount > 0 else 0,
            "overspent": s > b.amount,
        })
    return jsonify(result)


@app.route("/api/budgets", methods=["POST"])
@login_required
def add_budget():
    uid = session["user_id"]
    data = request.get_json()
    category = data.get("category", "").strip()
    amount = float(data.get("amount", 0))
    month = data.get("month", date.today().strftime("%Y-%m"))
    if not category or amount <= 0:
        return jsonify({"error": "Category and positive amount required"}), 400
    # Upsert: update if exists
    existing = Budget.query.filter_by(user_id=uid, category=category, month=month).first()
    if existing:
        existing.amount = amount
    else:
        b = Budget(user_id=uid, category=category, amount=amount, month=month)
        db.session.add(b)
    db.session.commit()
    return jsonify({"success": True}), 201


@app.route("/api/budgets/<int:bid>", methods=["DELETE"])
@login_required
def delete_budget(bid):
    uid = session["user_id"]
    b = Budget.query.filter_by(id=bid, user_id=uid).first_or_404()
    db.session.delete(b)
    db.session.commit()
    return jsonify({"message": "Deleted"})


# ── Insights API ───────────────────────────────────────────────────────────

@app.route("/api/insights")
@login_required
def insights():
    uid = session["user_id"]
    month = request.args.get("month", date.today().strftime("%Y-%m"))

    # Current month spending
    txs = Transaction.query.filter_by(user_id=uid).filter(Transaction.date.startswith(month)).all()
    total_inc = sum(t.amount for t in txs if t.type == "income")
    total_exp = sum(t.amount for t in txs if t.type == "expense")
    cat_exp = defaultdict(float)
    for t in txs:
        if t.type == "expense":
            cat_exp[t.category] += t.amount

    # Previous month
    y, m = map(int, month.split("-"))
    if m == 1: prev = f"{y-1}-12"
    else: prev = f"{y}-{str(m-1).zfill(2)}"
    prev_txs = Transaction.query.filter_by(user_id=uid, type="expense").filter(
        Transaction.date.startswith(prev)).all()
    prev_exp = sum(t.amount for t in prev_txs)

    # Build insights list
    tips = []
    if total_exp > 0 and total_inc > 0:
        sr = (total_inc - total_exp) / total_inc * 100
        if sr >= 30:
            tips.append({"icon": "🏆", "type": "positive", "text": f"Excellent! You're saving {sr:.0f}% of your income this month."})
        elif sr >= 10:
            tips.append({"icon": "✅", "type": "neutral", "text": f"Good job — saving {sr:.0f}% of income. Aim for 20%+ for financial health."})
        else:
            tips.append({"icon": "⚠️", "type": "warning", "text": f"Only saving {sr:.0f}% this month. Try reducing your top expense categories."})

    if cat_exp:
        top = max(cat_exp, key=cat_exp.get)
        pct = cat_exp[top] / total_exp * 100 if total_exp > 0 else 0
        tips.append({"icon": "📊", "type": "info", "text": f"'{top}' is your biggest expense at {pct:.0f}% of total spending (₹{cat_exp[top]:,.0f})."})

    if prev_exp > 0 and total_exp > 0:
        diff = total_exp - prev_exp
        pct = abs(diff) / prev_exp * 100
        if diff > 0:
            tips.append({"icon": "📈", "type": "warning", "text": f"Spending is up {pct:.0f}% vs last month (+₹{diff:,.0f}). Watch your budget."})
        else:
            tips.append({"icon": "📉", "type": "positive", "text": f"Great! Spending is down {pct:.0f}% vs last month (saved ₹{abs(diff):,.0f})."})

    # Check overspent budgets
    budgets = Budget.query.filter_by(user_id=uid, month=month).all()
    for b in budgets:
        spent = cat_exp.get(b.category, 0)
        if spent > b.amount:
            tips.append({"icon": "🚨", "type": "danger", "text": f"Over budget on '{b.category}'! Spent ₹{spent:,.0f} vs ₹{b.amount:,.0f} limit."})

    return jsonify(tips[:5])  # max 5 insights


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
