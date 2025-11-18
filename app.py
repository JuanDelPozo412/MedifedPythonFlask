from flask import Flask, jsonify, redirect, render_template, request, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt 
from flask_login import (
     LoginManager, UserMixin, login_user, logout_user, login_required, current_user
)
from transformers import pipeline
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import os
import boto3

load_dotenv()



app = Flask(__name__)
app. config["SECRET_KEY"] = "clave_secreta_123" 
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt (app)
login_manager = LoginManager(app)

#Reedireccion al login si se quiere entrar sin auth
login_manager.login_view = "login" 


s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_REGION')
)

BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png'}

def allowed_file(filename):
    """Verifica si la extensión del archivo es permitida"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


class User(UserMixin, db.Model):
     id = db.Column(db.Integer, primary_key= True)
     username = db.Column(db.String(80), unique = True, nullable = False)
     password = db.Column(db.String(200), nullable = False)


@login_manager.user_loader
def load_user(user_id):
     return User.query.get(int(user_id))



@app.route('/')
def root():
    return render_template("index.html")

@app.route('/login', methods=["GET","POST"])
def login():

    if request.method == "POST":
        email_form = request.form["email"]
        contrasena_form = request.form["password"]

        user = User.query.filter_by(username = email_form).first()
        if user and bcrypt.check_password_hash(user.password, contrasena_form):
            login_user(user)
            return redirect(url_for("portal"))
        else:
             return redirect(url_for("error"))
         
    return render_template("login.html")


@app.route("/register", methods=["GET","POST"])
def register():

    if request.method == "POST":
         email_form = request.form["email"]
         contrasena_form = request.form.get('password')

         hashed_pw = bcrypt.generate_password_hash(contrasena_form).decode("utf-8")

         user = User(username = email_form, password = hashed_pw)
         db.session.add(user)
         db.session.commit()

         return redirect(url_for("login"))
    else:
         return render_template("register.html")

@app.route('/error')
def error():
     return render_template("error_account.html")

# RUTAS PORTAL 
@app.route('/portal')
@login_required
def portal():
     return render_template("./portal/home_portal.html")

@app.route('/medicos')
@login_required
def medicos():
     return render_template("./portal/medicos_portal.html")
     
@app.route('/miplan')
@login_required
def miplan():
     return render_template("./portal/miplan_portal.html")
     

@app.route('/turnos')
@login_required
def turnos():
     return render_template("./portal/turnos_portal.html")

@app.route('/estudios')
@login_required
def estudios():
     return render_template("./portal/estudios_portal.html")

@app.route('/contacto_portal')
@login_required
def contactoPortal():
     return render_template("./portal/contacto_portal.html")
     

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("root"))


# FUNCIONES BUCKETS 
@app.route('/upload_file', methods=['POST'])
@login_required
def upload_file():

    if 'file' not in request.files:
        flash("No se envió ningún archivo", "error")
        return redirect(url_for('estudios'))
    
    file = request.files['file']
    
 
    if file.filename == '':
        flash("No se seleccionó ningún archivo", "error")
        return redirect(url_for('estudios'))
    
 
    if not allowed_file(file.filename):
        flash("Tipo de archivo no permitido. Solo se permiten PDF, JPG, JPEG y PNG", "error")
        return redirect(url_for('estudios'))
    
    try:
   
        filename = secure_filename(file.filename)
        
        user_id = current_user.id
        s3_key = f"estudios/user_{user_id}/{filename}"
        

        s3_client.upload_fileobj(
            file,
            BUCKET_NAME,
            s3_key,
            ExtraArgs={
                'ContentType': file.content_type,
                'ContentDisposition': 'inline'
            }
        )
        
        flash(f"Archivo '{filename}' subido exitosamente", "success")
        print(f"[SUCCESS] Archivo subido: {s3_key}")
        
    except Exception as e:
        flash(f" Error al subir el archivo: {str(e)}", "error")
        print(f"[ERROR] Error en upload: {str(e)}")
    
    return redirect(url_for('estudios'))


@app.route('/listar-archivos', methods=['GET'])
@login_required
def list_files():
    try:
        user_id = current_user.id
        prefix = f"estudios/user_{user_id}/"
        

        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=prefix
        )
        
        if 'Contents' not in response:
            return jsonify({
                "archivos": [], 
                "mensaje": "No tienes estudios médicos cargados",
                "total": 0
            })
        
 
        files = []
        for obj in response['Contents']:
         
            if obj['Key'].endswith('/'):
                continue
                
           
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': BUCKET_NAME,
                    'Key': obj['Key']
                },
                ExpiresIn=3600  
            )
            
      
            nombre_archivo = obj['Key'].split('/')[-1]
            
            files.append({
                'nombre': nombre_archivo,
                'key': obj['Key'],
                'tamaño': obj['Size'],
                'tamaño_mb': round(obj['Size'] / (1024 * 1024), 2),
                'fecha': obj['LastModified'].isoformat(),
                'url': url
            })
        
        return jsonify({
            "archivos": files,
            "total": len(files)
        })
        
    except Exception as e:
        print(f"[ERROR] Error al listar archivos: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/eliminar-archivo', methods=['POST'])
@login_required
def delete_file():
    """Elimina un archivo médico del usuario en S3"""
    try:
        data = request.get_json()
        filename = data.get('filename')
        
        if not filename:
            return jsonify({"error": "No se especificó el archivo"}), 400
        
        user_id = current_user.id
        s3_key = f"estudios/user_{user_id}/{filename}"
        
     
        s3_client.delete_object(
            Bucket=BUCKET_NAME,
            Key=s3_key
        )
        
        return jsonify({
            "mensaje": f"Archivo '{filename}' eliminado correctamente",
            "success": True
        })
        
    except Exception as e:
        print(f"[ERROR] Error al eliminar archivo: {str(e)}")
        return jsonify({"error": str(e)}), 500




qa_pipeline = pipeline(
    "question-answering",
    model="mrm8488/distill-bert-base-spanish-wwm-cased-finetuned-spa-squad2-es",
    tokenizer="mrm8488/distill-bert-base-spanish-wwm-cased-finetuned-spa-squad2-es"
)
mitoken = os.getenv("OPENIA_MEDIFED_TURNO")

contexto = """Bienvenido al centro de ayuda para la gestión de turnos médicos.

**Reserva de Turnos:**
Para reservar un turno, primero debes crear una cuenta e iniciar sesión en el "Portal de Pacientes". Una vez dentro, busca la sección "Turnos", elige la especialidad médica y luego selecciona un día y horario disponible en el calendario. El horario de atención para solicitar turnos es de lunes a viernes, de 8:00 a.m. a 12:00 p.m.

**Cambio de Médico:**
Para cambiar de médico, debes cancelar tu turno actual y luego solicitar uno nuevo. Durante el proceso de solicitar un nuevo turno, podrás seleccionar la especialidad y ver la lista de médicos disponibles para elegir el de tu preferencia.

**Cambio de Día o de Hora:**
Para cambiar el día o la hora de tu turno, el procedimiento es cancelar el turno que ya tienes y solicitar uno nuevo. Al solicitar el nuevo turno, el sistema te mostrará un calendario con todos los días y horarios disponibles para que puedas elegir el que más te convenga.

**Cambio de Lugar:**
El cambio de lugar o centro de atención se realiza al momento de solicitar un nuevo turno. Después de cancelar tu cita actual, inicia el proceso de reserva y el sistema te dará la opción de elegir entre los diferentes centros médicos disponibles.

**Cancelación de Turnos:**
Para cancelar un turno, debes ingresar al "Portal de Pacientes", dirigirte a la sección "Mis Turnos" y allí encontrarás la opción para cancelar la cita que ya no necesites.

**Límites del Asistente:**
Mi función es guiarte en el proceso. No puedo registrarte, iniciar sesión por ti, ni reservar o cancelar turnos en tu nombre. Si tienes preguntas sobre diagnósticos o costos, te recomiendo contactar directamente al centro médico, ya que mi especialidad es solo la gestión de turnos online.
"""
@app.route('/chat' , methods = ['GET','POST'])
@login_required
def chat():

     if request.method == "POST":
          data = request.json
          pregunta = data.get('prompt', '').strip()

          if not pregunta:
               return jsonify ({'response': 'Por Favor, escribi una pregunta.'})
          try:
               resultado = qa_pipeline({
                    'question': pregunta,
                    'context': contexto
               })     
               respuesta = resultado.get('answer', 'No encontre una respuesta adecuada.')
          except Exception:
               respuesta = "Hubo un error al procesar tu pregunta. Por favor intenta de nuevo. "

          return jsonify({'response': respuesta})
     else:
          return render_template("./portal/chatbot_portal.html")

app.route('/faqs', methods =['GET'])
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

if __name__ == '__main__':
       app.run(debug=True)




