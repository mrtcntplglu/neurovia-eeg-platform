from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Stripe customer ID
    stripe_customer_id = db.Column(db.String(100), nullable=True)

    # Credits for one-time purchases
    credits = db.Column(db.Integer, default=0)

    # Relationships
    subscription = db.relationship("Subscription", back_populates="user", uselist=False)
    credit_purchases = db.relationship("CreditPurchase", back_populates="user")
    analyses = db.relationship("AnalysisRecord", back_populates="user")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def has_active_subscription(self):
        if self.subscription and self.subscription.is_active:
            return True
        return False

    @property
    def can_analyze(self):
        """Check if user can run an analysis."""
        if self.has_active_subscription:
            sub = self.subscription
            if sub.plan == "unlimited":
                return True
            # Check monthly usage limit
            from calendar import monthrange
            now = datetime.utcnow()
            first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            monthly_count = AnalysisRecord.query.filter(
                AnalysisRecord.user_id == self.id,
                AnalysisRecord.created_at >= first_day
            ).count()
            limit = 20 if sub.plan == "starter" else 50
            return monthly_count < limit
        return self.credits > 0

    @property
    def remaining_analyses(self):
        """Return remaining analyses info as a string."""
        if self.has_active_subscription:
            sub = self.subscription
            if sub.plan == "unlimited":
                return "Sınırsız"
            from calendar import monthrange
            now = datetime.utcnow()
            first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            monthly_count = AnalysisRecord.query.filter(
                AnalysisRecord.user_id == self.id,
                AnalysisRecord.created_at >= first_day
            ).count()
            limit = 20 if sub.plan == "starter" else 50
            return f"{limit - monthly_count}/{limit} (bu ay)"
        return f"{self.credits} kredi"


class Subscription(db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    stripe_subscription_id = db.Column(db.String(100), unique=True, nullable=True)
    stripe_price_id = db.Column(db.String(100), nullable=True)
    plan = db.Column(db.String(50), nullable=False)  # starter, pro, unlimited
    status = db.Column(db.String(50), default="active")  # active, canceled, past_due
    current_period_end = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", back_populates="subscription")

    @property
    def is_active(self):
        if self.status != "active":
            return False
        if self.current_period_end and datetime.utcnow() > self.current_period_end:
            return False
        return True

    @property
    def plan_display(self):
        return {
            "starter": "Starter (20 Analiz/ay)",
            "pro": "Pro (50 Analiz/ay)",
            "unlimited": "Unlimited (Sınırsız)"
        }.get(self.plan, self.plan)


class CreditPurchase(db.Model):
    __tablename__ = "credit_purchases"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    stripe_payment_intent_id = db.Column(db.String(100), unique=True, nullable=True)
    stripe_session_id = db.Column(db.String(100), unique=True, nullable=True)
    credits_purchased = db.Column(db.Integer, nullable=False)
    amount_paid = db.Column(db.Integer, nullable=False)  # in cents
    status = db.Column(db.String(50), default="pending")  # pending, completed, failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="credit_purchases")


class AnalysisRecord(db.Model):
    __tablename__ = "analysis_records"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    file_name = db.Column(db.String(200), nullable=True)
    dominant_band = db.Column(db.String(50), nullable=True)
    brain_state = db.Column(db.String(100), nullable=True)
    pdf_name_tr = db.Column(db.String(200), nullable=True)
    pdf_name_en = db.Column(db.String(200), nullable=True)
    used_credit = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="analyses")