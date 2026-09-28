import os
from copy import deepcopy


# ============================================================
# DIRETÓRIOS
# ============================================================

BASE_DIR = os.path.abspath(
    os.path.dirname(__file__)
)

# Como BASE_DIR aponta para /models,
# voltamos um nível para chegar à raiz do projeto.
PROJECT_DIR = os.path.abspath(
    os.path.join(BASE_DIR, "..")
)

UPLOAD_FOLDER = os.path.join(
    PROJECT_DIR,
    "uploads"
)

RESULT_FOLDER = os.path.join(
    PROJECT_DIR,
    "resultados"
)

TRAINING_FOLDER = os.path.join(
    PROJECT_DIR,
    "treinamento"
)

TRAINING_DOCUMENTS = os.path.join(
    TRAINING_FOLDER,
    "documentos"
)

TRAINING_CORRECTIONS = os.path.join(
    TRAINING_FOLDER,
    "correcoes"
)

TRAINING_EXAMPLES = os.path.join(
    TRAINING_FOLDER,
    "exemplos"
)

DATABASE_FOLDER = os.path.join(
    PROJECT_DIR,
    "database"
)

DATABASE_PATH = os.path.join(
    DATABASE_FOLDER,
    "ia.db"
)


# ============================================================
# CONFIGURAÇÕES
# ============================================================

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

CPU_THREADS = int(
    os.environ.get(
        "CPU_THREADS",
        "4"
    )
)


# ============================================================
# CRIAÇÃO DOS DIRETÓRIOS
# ============================================================

for folder in [
    UPLOAD_FOLDER,
    RESULT_FOLDER,
    TRAINING_FOLDER,
    TRAINING_DOCUMENTS,
    TRAINING_CORRECTIONS,
    TRAINING_EXAMPLES,
    DATABASE_FOLDER,
]:
    os.makedirs(
        folder,
        exist_ok=True
    )


# ============================================================
# SCHEMA DO TALHÃO
# ============================================================

def novo_talhao():
    """
    Estrutura padrão de um talhão.
    """

    return {
        "talhao": "",
        "variedade": "",
        "area": "",
        "plantio": ""
    }


# ============================================================
# SCHEMA DO BLOCO
# ============================================================

def novo_bloco(codigo=""):
    """
    Estrutura padrão de um bloco.
    """

    return {
        "bloco": codigo or "",
        "talhoes": []
    }


# ============================================================
# SCHEMA DO DOCUMENTO
# ============================================================

def novo_documento():
    """
    Estrutura padrão completa do documento agrícola.
    """

    return {
        "metadata": {
            "bloco": "",
            "proprietario": "",
            "propriedade": "",
            "municipio": "",
            "area_local": "",
            "area_cana": "",
            "area_carreador": "",
            "area_carreador_percentual": "",
            "area_total": "",
            "status": "",
            "unidade_gestora": "",
            "distancia_unidade_gestora": "",
            "declividade_media": "",
            "latitude": "",
            "longitude": "",
            "escala": "",
            "tipo": "",
            "agrupamento": "",
            "levantamento": "",
            "desenho": "",
            "data_ultimo_desenho": ""
        },

        "blocos": [],

        "confianca": {}
    }


# ============================================================
# VALIDAÇÃO BÁSICA DO SCHEMA
# ============================================================

def validar_schema_documento(documento):
    """
    Garante que o documento tenha a estrutura mínima esperada.
    """

    if not isinstance(documento, dict):
        documento = novo_documento()

    documento.setdefault(
        "metadata",
        {}
    )

    documento.setdefault(
        "blocos",
        []
    )

    documento.setdefault(
        "confianca",
        {}
    )

    campos_metadata = [
        "bloco",
        "proprietario",
        "propriedade",
        "municipio",
        "area_local",
        "area_cana",
        "area_carreador",
        "area_carreador_percentual",
        "area_total",
        "status",
        "unidade_gestora",
        "distancia_unidade_gestora",
        "declividade_media",
        "latitude",
        "longitude",
        "escala",
        "tipo",
        "agrupamento",
        "levantamento",
        "desenho",
        "data_ultimo_desenho"
    ]

    for campo in campos_metadata:

        documento["metadata"].setdefault(
            campo,
            ""
        )

    return documento


# ============================================================
# CLONE DE SCHEMA
# ============================================================

def copiar_documento():
    """
    Retorna uma cópia independente do schema.
    """

    return deepcopy(
        novo_documento()
    )