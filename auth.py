from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User
 
auth_bp = Blueprint("auth", __name__)
 
 
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
 
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
 
        if not name or not email or not password:
            error = "Tüm alanları doldurun."
        elif password != password2:
            error = "Şifreler eşleşmiyor."
        elif len(password) < 6:
            error = "Şifre en az 6 karakter olmalıdır."
        elif User.query.filter_by(email=email).first():
            error = "Bu e-posta adresi zaten kayıtlı."
        else:
            user = User(name=name, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("dashboard"))
 
    return render_template("auth/register.html", error=error)
 
 
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
 
    error = None
    next_page = request.args.get("next")
 
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
 
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            error = "E-posta veya şifre hatalı."
        else:
            login_user(user, remember=True)
            return redirect(next_page or url_for("dashboard"))
 
    return render_template("auth/login.html", error=error)
 
 
@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))
 