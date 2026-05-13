from dotenv import load_dotenv
load_dotenv()
import os
import uuid
from pathlib import Path
from datetime import datetime
 
from flask import Flask, render_template, request, send_from_directory, redirect, url_for, flash
from flask_login import LoginManager, login_required, current_user
from werkzeug.utils import secure_filename
 
from models import db, User, AnalysisRecord
from auth import auth_bp
from payments import payments_bp, SUBSCRIPTION_PLANS, CREDIT_PACKAGES
 
# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
 
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", f"sqlite:///{BASE_DIR / 'neurovia.db'}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
 
UPLOAD_FOLDER = BASE_DIR / "uploads"
REPORT_FOLDER = BASE_DIR / "reports"
TR_REPORT_FOLDER = REPORT_FOLDER / "tr"
EN_REPORT_FOLDER = REPORT_FOLDER / "en"
ALLOWED_EXTENSIONS = {"edf", "csv", "mat"}
 
for folder in [UPLOAD_FOLDER, REPORT_FOLDER, TR_REPORT_FOLDER, EN_REPORT_FOLDER]:
    folder.mkdir(exist_ok=True)
 
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["TR_REPORT_FOLDER"] = str(TR_REPORT_FOLDER)
app.config["EN_REPORT_FOLDER"] = str(EN_REPORT_FOLDER)
 
# DB
db.init_app(app)
 
# Login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "auth.login"
login_manager.login_message = "Bu sayfaya erişmek için giriş yapmalısınız."
 
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
 
# Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(payments_bp)
 
# Create DB tables
with app.app_context():
    db.create_all()
 
 
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
 
 
# ---------------------------------------------------------------------------
# Main index (landing page)
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", error_message=None)
 
 
# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    recent_analyses = AnalysisRecord.query.filter_by(
        user_id=current_user.id
    ).order_by(AnalysisRecord.created_at.desc()).limit(10).all()
 
    return render_template(
        "dashboard.html",
        user=current_user,
        recent_analyses=recent_analyses,
        subscription_plans=SUBSCRIPTION_PLANS,
        credit_packages=CREDIT_PACKAGES,
    )
 
 
# ---------------------------------------------------------------------------
# Analysis upload + run
# ---------------------------------------------------------------------------
@app.route("/analyze", methods=["GET", "POST"])
@login_required
def analyze():
    error_message = None
 
    # GET: show upload form
    if request.method == "GET":
        return render_template("analyze.html", error_message=None)
 
    # POST: handle upload
    if not current_user.can_analyze:
        error_message = (
            "Analiz hakkınız bulunmuyor. Lütfen bir plan satın alın veya kredi yükleyin."
        )
        return render_template("analyze.html", error_message=error_message)
 
    if "file" not in request.files:
        error_message = "Dosya yüklenemedi."
        return render_template("analyze.html", error_message=error_message)
 
    file = request.files["file"]
 
    if file.filename == "":
        error_message = "Dosya seçilmedi."
        return render_template("analyze.html", error_message=error_message)
 
    if not allowed_file(file.filename):
        error_message = "Desteklenmeyen dosya formatı. Lütfen .edf, .csv veya .mat dosyası yükleyin."
        return render_template("analyze.html", error_message=error_message)
 
    file_path = None
 
    try:
        from main import run_analysis  # import here to avoid circular issues
 
        original_filename = secure_filename(file.filename)
        unique_id = str(uuid.uuid4())[:8]
        unique_filename = f"{unique_id}_{original_filename}"
        file_path = UPLOAD_FOLDER / unique_filename
        file.save(str(file_path))
 
        result = run_analysis(str(file_path))
 
        # Deduct credit if not on subscription
        used_credit = False
        if not current_user.has_active_subscription:
            current_user.credits -= 1
            used_credit = True
 
        # Record analysis
        record = AnalysisRecord(
            user_id=current_user.id,
            file_name=original_filename,
            dominant_band=result.get("yorum", "")[:100] if result.get("yorum") else None,
            brain_state=result.get("brain_state", "")[:100] if result.get("brain_state") else None,
            pdf_name_tr=result.get("pdf_name_tr"),
            pdf_name_en=result.get("pdf_name_en"),
            used_credit=used_credit
        )
        db.session.add(record)
        db.session.commit()
 
        return render_template(
            "result.html",
            channel_count=result.get("channel_count"),
            sampling_rate=result.get("sampling_rate"),
            peak_freq=result.get("peak_freq"),
            avg_p2p=result.get("avg_p2p"),
            yorum=result.get("yorum"),
            ratios=result.get("ratios"),
            brain_state=result.get("brain_state"),
            signal_quality=result.get("signal_quality"),
            analysis_scores=result.get("analysis_scores"),
            pdf_name_tr=result.get("pdf_name_tr"),
            pdf_name_en=result.get("pdf_name_en"),
        )
 
    except Exception as e:
        error_message = f"Analiz sırasında bir hata oluştu: {str(e)}"
        return render_template("analyze.html", error_message=error_message)
 
    finally:
        if file_path and Path(file_path).exists():
            try:
                os.remove(str(file_path))
            except Exception:
                pass
 
 
# ---------------------------------------------------------------------------
# Report download
# ---------------------------------------------------------------------------
@app.route("/download/<lang>/<filename>")
@login_required
def download_report(lang, filename):
    safe_filename = secure_filename(filename)
 
    if lang == "tr":
        folder = app.config["TR_REPORT_FOLDER"]
    elif lang == "en":
        folder = app.config["EN_REPORT_FOLDER"]
    else:
        return "Geçersiz rapor dili.", 400
 
    return send_from_directory(folder, safe_filename, as_attachment=True)
 
 
# ---------------------------------------------------------------------------
# Cancel subscription
# ---------------------------------------------------------------------------
@app.route("/subscription/cancel", methods=["POST"])
@login_required
def cancel_subscription():
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
 
    sub = current_user.subscription
    if sub and sub.stripe_subscription_id and sub.status == "active":
        try:
            stripe.Subscription.modify(
                sub.stripe_subscription_id,
                cancel_at_period_end=True
            )
            sub.status = "canceled"
            db.session.commit()
            flash("Aboneliğiniz dönem sonunda iptal edilecek.", "info")
        except Exception as e:
            flash(f"İptal işlemi başarısız: {str(e)}", "error")
 
    return redirect(url_for("dashboard"))
 
 
if __name__ == "__main__":
    app.run(debug=True, port=5000)
 