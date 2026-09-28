
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
RESULT_FOLDER = os.path.join(BASE_DIR, "resultados")

TRAINING_FOLDER = os.path.join(BASE_DIR, "treinamento")
TRAINING_DOCUMENTS = os.path.join(TRAINING_FOLDER, "documentos")
TRAINING_CORRECTIONS = os.path.join(TRAINING_FOLDER, "correcoes")
TRAINING_EXAMPLES = os.path.join(TRAINING_FOLDER, "exemplos")

DATABASE_FOLDER = os.path.join(BASE_DIR, "database")
DATABASE_PATH = os.path.join(DATABASE_FOLDER, "ia.db")

ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "bmp",
    "tif",
    "tiff"
}

MAX_FILE_SIZE = 50 * 1024 * 1024

CPU_THREADS = int(os.environ.get("CPU_THREADS", "4"))

for folder in [
    UPLOAD_FOLDER,
    RESULT_FOLDER,
    TRAINING_FOLDER,
    TRAINING_DOCUMENTS,
    TRAINING_CORRECTIONS,
    TRAINING_EXAMPLES,
    DATABASE_FOLDER,
]:
    os.makedirs(folder, exist_ok=True)
