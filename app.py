from flask import Flask, jsonify, redirect, render_template, request, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from transformers import pipeline
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import os
import boto3
import pyrebase

load_dotenv()


# CONFIGURACION FIREBASE

firebaseConfig = {
    "apiKey": os.getenv("FIREBASE_API_KEY"),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
    "projectId": os.getenv("FIREBASE_PROJECT_ID"),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
    "appId": os.getenv("FIREBASE_APP_ID"),
    "databaseURL": "",
}

firebase = pyrebase.initialize_app(firebaseConfig)
auth = firebase.auth()


app = Flask(__name__)
app.config["SECRET_KEY"] = "clave_secreta_123"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)

# Reedireccion al login si se quiere entrar sin auth
login_manager.login_view = "login"


s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)

BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}


def allowed_file(filename):
    """Verifica si la extensión del archivo es permitida"""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="paciente", nullable=False)


# FUNCIONES TURNO Y MODELO
class Turno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    fecha = db.Column(db.String(20), nullable=False)
    hora = db.Column(db.String(10), nullable=False)
    especialidad = db.Column(db.String(50), nullable=False)
    motivo = db.Column(db.String(200))
    estado = db.Column(db.String(20), default="Pendiente")
    paciente = db.relationship("User", backref=db.backref("turnos", lazy=True))


@app.route("/reservar-turno", methods=["GET", "POST"])
@login_required
def reservar_turno():
    if request.method == "POST":
        fecha = request.form["fecha"]
        hora = request.form["hora"]
        especialidad = request.form["especialidad"]
        motivo = request.form["motivo"]

        nuevo_turno = Turno(
            user_id=current_user.id,
            fecha=fecha,
            hora=hora,
            especialidad=especialidad,
            motivo=motivo,
        )

        db.session.add(nuevo_turno)
        db.session.commit()

        flash("¡Turno solicitado con éxito!", "success")
        return redirect(url_for("portal"))

    return render_template("./portal/turnos_portal.html")


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route("/")
def root():
    return render_template("index.html")


@app.route("/panel_medico")
@login_required
def panel_medico():

    if current_user.role != "medico":
        flash("Acceso denegado. Solo los médicos pueden ver esta página.", "error")
        return redirect(url_for("portal"))
    turnos_pendientes = (
        Turno.query.filter_by(estado="Pendiente").order_by(Turno.fecha).all()
    )
    return render_template(
        "./portal/panel_medico.html",
        turnos=turnos_pendientes,
        get_estudios=obtener_estudios_paciente,
    )


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        email_form = request.form["email"]
        contrasena_form = request.form["password"]

        user = User.query.filter_by(username=email_form).first()

        if user and bcrypt.check_password_hash(user.password, contrasena_form):
            login_user(user)

            if user.role == "medico":
                return redirect(url_for("panel_medico"))
            else:
                return redirect(url_for("portal"))
            # -------------------------------------

        else:
            return redirect(url_for("error"))

    return render_template(
        "login.html",
        firebase_api_key=os.getenv("FIREBASE_API_KEY"),
        firebase_auth_domain=os.getenv("FIREBASE_AUTH_DOMAIN"),
        firebase_project_id=os.getenv("FIREBASE_PROJECT_ID"),
        firebase_storage_bucket=os.getenv("FIREBASE_STORAGE_BUCKET"),
        firebase_messaging_sender_id=os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
        firebase_app_id=os.getenv("FIREBASE_APP_ID"),
    )


# LOGIN DE GOOGLE


@app.route("/login_google", methods=["POST"])
def login_google():
    data = request.get_json()
    token = data.get("token")

    if not token:
        return jsonify({"success": False, "message": "No token provided"}), 400

    try:

        user_info = auth.get_account_info(token)

        firebase_uid = user_info["users"][0]["localId"]
        email = user_info["users"][0]["email"]
        user = User.query.filter_by(username=email).first()

        if not user:

            import secrets

            random_password = secrets.token_hex(16)
            hashed_pw = bcrypt.generate_password_hash(random_password).decode("utf-8")

            user = User(username=email, password=hashed_pw)
            db.session.add(user)
            db.session.commit()

        login_user(user)

        return jsonify({"success": True, "message": "Login exitoso"})

    except Exception as e:
        print(f"Error en login google: {e}")
        return jsonify({"success": False, "message": str(e)}), 400


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        email_form = request.form["email"]
        contrasena_form = request.form.get("password")

        hashed_pw = bcrypt.generate_password_hash(contrasena_form).decode("utf-8")

        user = User(username=email_form, password=hashed_pw)
        db.session.add(user)
        db.session.commit()

        return redirect(url_for("login"))
    else:
        return render_template("register.html")


@app.route("/error")
def error():
    return render_template("error_account.html")


# RUTAS PORTAL
@app.route("/portal")
@login_required
def portal():
    mis_turnos = (
        Turno.query.filter_by(user_id=current_user.id)
        .order_by(Turno.fecha)
        .limit(3)
        .all()
    )
    return render_template("./portal/home_portal.html", turnos=mis_turnos)


@app.route("/medicos")
@login_required
def medicos():
    return render_template("./portal/medicos_portal.html")


@app.route("/miplan")
@login_required
def miplan():
    return render_template("./portal/miplan_portal.html")


@app.route("/turnos")
@login_required
def turnos():
    return render_template("./portal/turnos_portal.html")


@app.route("/estudios")
@login_required
def estudios():
    return render_template("./portal/estudios_portal.html")


@app.route("/contacto_portal")
@login_required
def contactoPortal():
    return render_template("./portal/contacto_portal.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("root"))


# FUNCIONES BUCKETS
@app.route("/upload_file", methods=["POST"])
@login_required
def upload_file():

    if "file" not in request.files:
        flash("No se envió ningún archivo", "error")
        return redirect(url_for("estudios"))

    file = request.files["file"]

    if file.filename == "":
        flash("No se seleccionó ningún archivo", "error")
        return redirect(url_for("estudios"))

    if not allowed_file(file.filename):
        flash(
            "Tipo de archivo no permitido. Solo se permiten PDF, JPG, JPEG y PNG",
            "error",
        )
        return redirect(url_for("estudios"))

    try:

        filename = secure_filename(file.filename)

        user_id = current_user.id
        s3_key = f"estudios/user_{user_id}/{filename}"

        s3_client.upload_fileobj(
            file,
            BUCKET_NAME,
            s3_key,
            ExtraArgs={
                "ContentType": file.content_type,
                "ContentDisposition": "inline",
            },
        )

        flash(f"Archivo '{filename}' subido exitosamente", "success")
        print(f"[SUCCESS] Archivo subido: {s3_key}")

    except Exception as e:
        flash(f" Error al subir el archivo: {str(e)}", "error")
        print(f"[ERROR] Error en upload: {str(e)}")

    return redirect(url_for("estudios"))


@app.route("/listar-archivos", methods=["GET"])
@login_required
def list_files():
    try:
        user_id = current_user.id
        prefix = f"estudios/user_{user_id}/"

        response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)

        if "Contents" not in response:
            return jsonify(
                {
                    "archivos": [],
                    "mensaje": "No tienes estudios médicos cargados",
                    "total": 0,
                }
            )

        files = []
        for obj in response["Contents"]:

            if obj["Key"].endswith("/"):
                continue

            url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": BUCKET_NAME, "Key": obj["Key"]},
                ExpiresIn=3600,
            )

            nombre_archivo = obj["Key"].split("/")[-1]

            files.append(
                {
                    "nombre": nombre_archivo,
                    "key": obj["Key"],
                    "tamaño": obj["Size"],
                    "tamaño_mb": round(obj["Size"] / (1024 * 1024), 2),
                    "fecha": obj["LastModified"].isoformat(),
                    "url": url,
                }
            )

        return jsonify({"archivos": files, "total": len(files)})

    except Exception as e:
        print(f"[ERROR] Error al listar archivos: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/eliminar-archivo", methods=["POST"])
@login_required
def delete_file():
    """Elimina un archivo médico del usuario en S3"""
    try:
        data = request.get_json()
        filename = data.get("filename")

        if not filename:
            return jsonify({"error": "No se especificó el archivo"}), 400

        user_id = current_user.id
        s3_key = f"estudios/user_{user_id}/{filename}"

        s3_client.delete_object(Bucket=BUCKET_NAME, Key=s3_key)

        return jsonify(
            {
                "mensaje": f"Archivo '{filename}' eliminado correctamente",
                "success": True,
            }
        )

    except Exception as e:
        print(f"[ERROR] Error al eliminar archivo: {str(e)}")
        return jsonify({"error": str(e)}), 500


qa_pipeline = pipeline(
    "question-answering",
    model="mrm8488/distill-bert-base-spanish-wwm-cased-finetuned-spa-squad2-es",
    tokenizer="mrm8488/distill-bert-base-spanish-wwm-cased-finetuned-spa-squad2-es",
)
mitoken = os.getenv("OPENIA_MEDIFED_TURNO")

contexto = """
**Reserva y Solicitud de Turnos:**
Para reservar, sacar o pedir un turno médico, ingresa al "Portal de Pacientes", ve a la sección "Turnos", elige especialidad, día y horario. El horario es lunes a viernes, 8:00 a.m. a 12:00 p.m.

**Cambiar Médico:**
Si quieres cambiar de médico, debes cancelar tu turno actual y solicitar uno nuevo seleccionando el profesional de tu preferencia en la lista.

**Cambiar Día u Hora:**
Para modificar o cambiar el día y horario, cancela el turno vigente y reserva uno nuevo en el calendario disponible.

**Cambiar Lugar o Centro:**
Para cambiar el centro de atención, cancela tu turno y elige la nueva ubicación al reservar nuevamente.

**Cancelar o Anular Turnos:**
Para cancelar o anular un turno, ve a la sección "Mis Turnos" en el "Portal de Pacientes" y selecciona la opción cancelar.
"""


@app.route("/chat", methods=["GET", "POST"])
@login_required
def chat():

    if request.method == "POST":
        data = request.json
        pregunta = data.get("prompt", "").strip()

        if not pregunta:
            return jsonify({"response": "Por Favor, escribi una pregunta."})
        try:
            resultado = qa_pipeline({"question": pregunta, "context": contexto})
            respuesta = resultado.get("answer", "No encontre una respuesta adecuada.")
        except Exception:
            respuesta = (
                "Hubo un error al procesar tu pregunta. Por favor intenta de nuevo. "
            )

        return jsonify({"response": respuesta})
    else:
        return render_template("./portal/chatbot_portal.html")


@app.route("/crear-medico-prueba")
def crear_medico():

    medico_existente = User.query.filter_by(username="medico@medifed.com").first()

    if not medico_existente:
        hashed_pw = bcrypt.generate_password_hash("medico123").decode("utf-8")

        nuevo_medico = User(
            username="medico@medifed.com", password=hashed_pw, role="medico"
        )

        db.session.add(nuevo_medico)
        db.session.commit()
        return "Usuario Médico creado: medico@medifed.com / medico123"

    return "El médico ya existe."


@app.route("/confirmar_turno/<int:turno_id>", methods=["POST"])
@login_required
def confirmar_turno(turno_id):

    if current_user.role != "medico":
        flash("Acceso denegado.", "error")
        return redirect(url_for("portal"))

    turno = Turno.query.get_or_404(turno_id)
    turno.estado = "Confirmado"
    db.session.commit()

    flash(f"Turno del paciente {turno.paciente.username} confirmado.", "success")
    return redirect(url_for("panel_medico"))


def obtener_estudios_paciente(user_id):
    try:
        prefix = f"estudios/user_{user_id}/"
        response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)

        files = []
        if "Contents" in response:
            for obj in response["Contents"]:
                if obj["Key"].endswith("/"):
                    continue

                url = s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": BUCKET_NAME, "Key": obj["Key"]},
                    ExpiresIn=3600,
                )
                nombre_archivo = obj["Key"].split("/")[-1]
                files.append({"nombre": nombre_archivo, "url": url})
        return files
    except Exception as e:
        print(f"Error obteniendo estudios: {e}")
        return []


app.route("/faqs", methods=["GET"])


def faqs():
    preguntas_frecuentes = [
        "Como reservo turno?",
        "Como cambio de medico?",
        "Como cambio de dia?",
        "Como cambio de hora?",
        "Como cambio de lugar?",
        "Como cancelo turno?",
    ]
    return jsonify(preguntas_frecuentes)


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)
