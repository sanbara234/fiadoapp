# FiadoApp 📒

## Correr en local (tu computadora)
```bash
pip install -r requirements.txt
uvicorn main:app --reload
```
Abrí http://localhost:8000

---

## Deploy en Railway (recomendado)

### 1. Subir a GitHub
- Creá cuenta en github.com
- Nuevo repositorio → "fiadoapp"
- Subí todos los archivos

### 2. Crear proyecto en Railway
- Entrá a railway.app
- "New Project" → "Deploy from GitHub repo"
- Seleccioná el repo "fiadoapp"

### 3. Agregar PostgreSQL
- En el proyecto → "New" → "Database" → "PostgreSQL"
- Railway conecta solo la DB al proyecto

### 4. Configurar variables
- Settings → Variables → agregar:
  - `DATABASE_URL` = (Railway lo pone automático desde la DB)

### 5. Configurar el start command
- Settings → Deploy → Start Command:
  ```
  uvicorn main:app --host 0.0.0.0 --port $PORT
  ```

### 6. Deploy
- Railway hace el deploy solo
- Te da una URL tipo: https://fiadoapp.railway.app

---

## Deploy en PythonAnywhere

### 1. Crear cuenta
- pythonanywhere.com → plan gratuito

### 2. Subir archivos
- Files → subí main.py, requirements.txt, static/index.html

### 3. Instalar dependencias
- Consola Bash:
  ```bash
  pip install --user fastapi uvicorn python-multipart
  ```

### 4. Crear Web App
- Web → Add new web app
- Manual configuration → Python 3.10
- WSGI file → reemplazar con:
  ```python
  import sys
  sys.path.insert(0, '/home/TUUSUARIO/fiadoapp')
  from main import app as application
  ```

### 5. Importante para PythonAnywhere
- PythonAnywhere gratuito usa SQLite (no PostgreSQL)
- Los datos se guardan en fiado.db en tu carpeta
- No se pierden al reiniciar

---

## Estructura
```
fiadoapp/
├── main.py          # Backend (auto-detecta SQLite o PostgreSQL)
├── requirements.txt
└── static/
    └── index.html   # Frontend
```
