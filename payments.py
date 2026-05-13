import os
import stripe
from datetime import datetime
from flask import Blueprint, redirect, url_for, request, jsonify, current_app, render_template
from flask_login import login_required, current_user
from models import db, User, Subscription, CreditPurchase

payments_bp = Blueprint("payments", __name__)

# ---------------------------------------------------------------------------
# Stripe plan definitions
# Prices are in cents (USD). Replace price IDs with your real Stripe Price IDs.
# ---------------------------------------------------------------------------
SUBSCRIPTION_PLANS = {
    "starter": {
        "name": "Starter",
        "description": "20 analiz / ay",
        "price_usd": 2900,          # $29.00
        "price_id": "price_STARTER_ID_HERE",   # replace with real Stripe Price ID
        "analyses": 20,
    },
    "pro": {
        "name": "Pro",
        "description": "50 analiz / ay",
        "price_usd": 5900,          # $59.00
        "price_id": "price_PRO_ID_HERE",       # replace with real Stripe Price ID
        "analyses": 50,
    },
    "unlimited": {
        "name": "Unlimited",
        "description": "Sınırsız analiz",
        "price_usd": 9900,          # $99.00
        "price_id": "price_UNLIMITED_ID_HERE", # replace with real Stripe Price ID
        "analyses": None,
    },
}

CREDIT_PACKAGES = {
    "credits_5": {
        "name": "5 Kredi",
        "credits": 5,
        "price_usd": 1500,   # $15.00
        "price_id": "price_CREDITS5_ID_HERE",  # replace with real Stripe Price ID
    },
    "credits_15": {
        "name": "15 Kredi",
        "credits": 15,
        "price_usd": 3900,   # $39.00
        "price_id": "price_CREDITS15_ID_HERE", # replace with real Stripe Price ID
    },
    "credits_30": {
        "name": "30 Kredi",
        "credits": 30,
        "price_usd": 6900,   # $69.00
        "price_id": "price_CREDITS30_ID_HERE", # replace with real Stripe Price ID
    },
}


def get_stripe_customer(user):
    """Get or create a Stripe customer for a user."""
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

    if user.stripe_customer_id:
        return user.stripe_customer_id

    customer = stripe.Customer.create(
        email=user.email,
        name=user.name,
        metadata={"user_id": user.id}
    )
    user.stripe_customer_id = customer.id
    db.session.commit()
    return customer.id


# ---------------------------------------------------------------------------
# Subscription checkout
# ---------------------------------------------------------------------------
@payments_bp.route("/checkout/subscription/<plan>")
@login_required
def checkout_subscription(plan):
    if plan not in SUBSCRIPTION_PLANS:
        return "Geçersiz plan.", 400

    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    plan_info = SUBSCRIPTION_PLANS[plan]
    customer_id = get_stripe_customer(current_user)
    domain = os.environ.get("APP_DOMAIN", "http://localhost:5000")

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        mode="subscription",
        line_items=[{
            "price": plan_info["price_id"],
            "quantity": 1,
        }],
        metadata={
            "user_id": current_user.id,
            "plan": plan,
            "type": "subscription"
        },
        success_url=domain + url_for("payments.payment_success") + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=domain + url_for("payments.payment_cancel"),
    )
    return redirect(session.url, code=303)


# ---------------------------------------------------------------------------
# Credit checkout
# ---------------------------------------------------------------------------
@payments_bp.route("/checkout/credits/<package>")
@login_required
def checkout_credits(package):
    if package not in CREDIT_PACKAGES:
        return "Geçersiz paket.", 400

    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    pkg_info = CREDIT_PACKAGES[package]
    customer_id = get_stripe_customer(current_user)
    domain = os.environ.get("APP_DOMAIN", "http://localhost:5000")

    # Record purchase as pending
    purchase = CreditPurchase(
        user_id=current_user.id,
        credits_purchased=pkg_info["credits"],
        amount_paid=pkg_info["price_usd"],
        status="pending"
    )
    db.session.add(purchase)
    db.session.commit()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        mode="payment",
        line_items=[{
            "price": pkg_info["price_id"],
            "quantity": 1,
        }],
        metadata={
            "user_id": current_user.id,
            "credits": pkg_info["credits"],
            "purchase_id": purchase.id,
            "type": "credits"
        },
        success_url=domain + url_for("payments.payment_success") + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=domain + url_for("payments.payment_cancel"),
    )

    purchase.stripe_session_id = session.id
    db.session.commit()

    return redirect(session.url, code=303)


# ---------------------------------------------------------------------------
# Stripe Webhook
# ---------------------------------------------------------------------------
@payments_bp.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    payload = request.get_data(as_text=False)
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    event_type = event["type"]
    data = event["data"]["object"]

    # --- Subscription created or updated ---
    if event_type in ("checkout.session.completed",):
        _handle_checkout_completed(data)

    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(data)

    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data)

    elif event_type == "invoice.payment_failed":
        _handle_invoice_failed(data)

    return jsonify({"status": "ok"}), 200


def _handle_checkout_completed(session_data):
    meta = session_data.get("metadata", {})
    payment_type = meta.get("type")
    user_id = meta.get("user_id")

    if not user_id:
        return

    user = User.query.get(int(user_id))
    if not user:
        return

    if payment_type == "subscription":
        plan = meta.get("plan")
        stripe_sub_id = session_data.get("subscription")

        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
        stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
        period_end = datetime.utcfromtimestamp(stripe_sub["current_period_end"])

        existing_sub = user.subscription
        if existing_sub:
            existing_sub.stripe_subscription_id = stripe_sub_id
            existing_sub.plan = plan
            existing_sub.status = "active"
            existing_sub.current_period_end = period_end
            existing_sub.updated_at = datetime.utcnow()
        else:
            new_sub = Subscription(
                user_id=user.id,
                stripe_subscription_id=stripe_sub_id,
                plan=plan,
                status="active",
                current_period_end=period_end
            )
            db.session.add(new_sub)

        db.session.commit()

    elif payment_type == "credits":
        purchase_id = meta.get("purchase_id")
        credits = int(meta.get("credits", 0))

        purchase = CreditPurchase.query.get(int(purchase_id)) if purchase_id else None

        if purchase and purchase.status != "completed":
            purchase.status = "completed"
            purchase.stripe_session_id = session_data.get("id")
            user.credits = (user.credits or 0) + credits
            db.session.commit()


def _handle_subscription_updated(sub_data):
    stripe_sub_id = sub_data.get("id")
    sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
    if sub:
        sub.status = sub_data.get("status", sub.status)
        period_end = sub_data.get("current_period_end")
        if period_end:
            sub.current_period_end = datetime.utcfromtimestamp(period_end)
        sub.updated_at = datetime.utcnow()
        db.session.commit()


def _handle_subscription_deleted(sub_data):
    stripe_sub_id = sub_data.get("id")
    sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
    if sub:
        sub.status = "canceled"
        sub.updated_at = datetime.utcnow()
        db.session.commit()


def _handle_invoice_failed(invoice_data):
    stripe_sub_id = invoice_data.get("subscription")
    if stripe_sub_id:
        sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
        if sub:
            sub.status = "past_due"
            sub.updated_at = datetime.utcnow()
            db.session.commit()


# ---------------------------------------------------------------------------
# Success / Cancel pages
# ---------------------------------------------------------------------------
@payments_bp.route("/payment/success")
@login_required
def payment_success():
    return render_template("payment/success.html")


@payments_bp.route("/payment/cancel")
@login_required
def payment_cancel():
    return render_template("payment/cancel.html")