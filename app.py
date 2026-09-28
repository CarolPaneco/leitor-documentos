
import os
import json
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file
)

from config import TRAINING_CORRECTIONS

from services.document_processor import (
    get_processor
)

from services.agricultural_extractor import (
    extrair_dados
)

from services.validator import (
    validar_documento
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

UPLOAD_FOLDER = os.path.join(
    BASE_DIR,
    "uploads"
)

RESULTADOS_FOLDER = os.path.join(
    BASE_DIR,
    "resultados"
)

CORRECOES_FOLDER = os.path.join(
    BASE_DIR,
    "correcoes"
)


os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)

os.makedirs(
    RESULTADOS_FOLDER,
    exist_ok=True
)

os.makedirs(
    CORRECOES_FOLDER,
    exist_ok=True
)


app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)


app.config[
    "UPLOAD_FOLDER"
] = UPLOAD_FOLDER


# ============================================================
# EXTENSÕES PERMITIDAS
# ============================================================

EXTENSOES_PERMITIDAS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff"
}


# ============================================================
# UTILIDADES
# ============================================================

def extensao_permitida(nome):

    extensao = os.path.splitext(
        nome
    )[1].lower()

    return extensao in EXTENSOES_PERMITIDAS


def salvar_json(
    caminho,
    dados
):

    with open(
        caminho,
        "w",
        encoding="utf-8"
    ) as arquivo:

        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )


def carregar_json(
    caminho
):

    if not os.path.exists(
        caminho
    ):

        return None

    with open(
        caminho,
        "r",
        encoding="utf-8"
    ) as arquivo:

        return json.load(
            arquivo
        )


# ============================================================
# SALVAR CORREÇÃO
# ============================================================

def salvar_correcao_json(
    documento_id,
    campo,
    valor_original,
    valor_corrigido,
    contexto=""
):

    os.makedirs(
        CORRECOES_FOLDER,
        exist_ok=True
    )


    caminho = os.path.join(
        CORRECOES_FOLDER,
        "correcoes.json"
    )


    dados = carregar_json(
        caminho
    )


    if not dados:

        dados = []


    dados.append({

        "documento_id":
            documento_id,

        "campo":
            campo,

        "valor_original":
            valor_original,

        "valor_corrigido":
            valor_corrigido,

        "contexto":
            contexto,

        "data":
            datetime.now().isoformat()

    })


    salvar_json(
        caminho,
        dados
    )


# ============================================================
# CONTAR CORREÇÕES
# ============================================================

def contar_correcoes():

    caminho = os.path.join(
        CORRECOES_FOLDER,
        "correcoes.json"
    )


    dados = carregar_json(
        caminho
    )


    if not dados:

        return 0


    return len(
        dados
    )


# ============================================================
# CONTAR DOCUMENTOS
# ============================================================

def contar_documentos():

    if not os.path.exists(
        RESULTADOS_FOLDER
    ):

        return 0


    total = 0


    for nome in os.listdir(
        RESULTADOS_FOLDER
    ):

        caminho = os.path.join(
            RESULTADOS_FOLDER,
            nome
        )


        if os.path.isdir(
            caminho
        ):

            total += 1


    return total


# ============================================================
# EXPORTAR EXCEL
# SOMENTE OS 6 CAMPOS
# ============================================================

def exportar_excel(
    documento,
    caminho_saida
):

    import pandas as pd


    linhas_talhoes = []


    metadata = (
        documento.get(
            "metadata"
        )
        or {}
    )


    blocos = (
        documento.get(
            "blocos"
        )
        or []
    )


    # ========================================================
    # PERCORRER BLOCOS E TALHÕES
    # ========================================================

    for bloco in blocos:

        codigo_bloco = (

            bloco.get(
                "bloco"
            )

            or bloco.get(
                "codigo"
            )

            or documento.get(
                "bloco"
            )

            or ""

        )


        talhoes = (
            bloco.get(
                "talhoes"
            )
            or []
        )


        for talhao in talhoes:

            linhas_talhoes.append({

                "Bloco":
                    codigo_bloco,

                "Talhão":
                    talhao.get(
                        "talhao",
                        ""
                    ),

                "Variedade":
                    talhao.get(
                        "variedade",
                        ""
                    ),

                "Área":
                    talhao.get(
                        "area",
                        ""
                    ),

                "Plantio":
                    talhao.get(
                        "plantio",
                        ""
                    ),

                "Propriedade":
                    metadata.get(
                        "propriedade",
                        ""
                    )

            })


    # ========================================================
    # DATAFRAME
    # ========================================================

    df_talhoes = pd.DataFrame(
        linhas_talhoes
    )


    # ========================================================
    # GARANTIR AS 6 COLUNAS
    # ========================================================

    colunas = [

        "Bloco",
        "Talhão",
        "Variedade",
        "Área",
        "Plantio",
        "Propriedade"

    ]


    # Caso não exista nenhum talhão,
    # cria o DataFrame com as colunas corretas.

    if df_talhoes.empty:

        df_talhoes = pd.DataFrame(
            columns=colunas
        )

    else:

        # Garante que somente essas
        # colunas existam no Excel.

        for coluna in colunas:

            if coluna not in df_talhoes.columns:

                df_talhoes[coluna] = ""


        df_talhoes = df_talhoes[
            colunas
        ]


    # ========================================================
    # CRIAR EXCEL
    # ========================================================

    with pd.ExcelWriter(
        caminho_saida,
        engine="openpyxl"
    ) as writer:

        df_talhoes.to_excel(
            writer,
            index=False,
            sheet_name="Talhões"
        )


    return caminho_saida


# ============================================================
# ROTA PRINCIPAL
# ============================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# ============================================================
# PROCESSAR DOCUMENTO
# ============================================================

@app.route(
    "/api/processar",
    methods=["POST"]
)
def processar_documento():

    try:

        if "arquivo" not in request.files:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Nenhum arquivo enviado."

            }), 400


        arquivo = request.files[
            "arquivo"
        ]


        if not arquivo.filename:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Nome do arquivo inválido."

            }), 400


        if not extensao_permitida(
            arquivo.filename
        ):

            return jsonify({

                "sucesso": False,

                "erro":
                    "Formato de arquivo não permitido."

            }), 400


        # ====================================================
        # NOME SEGURO
        # ====================================================

        nome_original = arquivo.filename

        nome_base = os.path.splitext(
            nome_original
        )[0]


        extensao = os.path.splitext(
            nome_original
        )[1].lower()


        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )


        nome_arquivo = (
            f"{timestamp}_{nome_base}"
            f"{extensao}"
        )


        caminho_arquivo = os.path.join(
            UPLOAD_FOLDER,
            nome_arquivo
        )


        arquivo.save(
            caminho_arquivo
        )


        # ====================================================
        # ID DO DOCUMENTO
        # ====================================================

        documento_id = (
            f"{timestamp}_{nome_base}"
        )


        # ====================================================
        # PROCESSAMENTO OCR
        # ====================================================

        processor = get_processor()


        resultado_ocr = processor.processar(

            caminho_arquivo,

            documento_id

        )


        if not resultado_ocr.get(
            "sucesso"
        ):

            return jsonify({

                "sucesso": False,

                "erro":
                    resultado_ocr.get(
                        "erro",
                        "Erro durante o OCR."
                    )

            }), 500


        # ====================================================
        # EXTRAÇÃO AGRÍCOLA
        # ====================================================

        documento = extrair_dados(
            resultado_ocr
        )


        # ====================================================
        # VALIDAÇÃO
        # ====================================================

        documento = validar_documento(
            documento
        )


        # ====================================================
        # SALVAR RESULTADO
        # ====================================================

        pasta_resultado = os.path.join(
            RESULTADOS_FOLDER,
            documento_id
        )


        os.makedirs(
            pasta_resultado,
            exist_ok=True
        )


        caminho_json = os.path.join(
            pasta_resultado,
            "resultado.json"
        )


        salvar_json(
            caminho_json,
            documento
        )


        # ====================================================
        # EXCEL
        # ====================================================

        caminho_excel = os.path.join(
            pasta_resultado,
            "resultado.xlsx"
        )


        exportar_excel(
            documento,
            caminho_excel
        )


        # ====================================================
        # GARANTIR OS CAMPOS PRINCIPAIS
        # ====================================================

        metadata = (
            documento.get(
                "metadata"
            )
            or {}
        )


        blocos = (
            documento.get(
                "blocos"
            )
            or []
        )


        total_talhoes = sum(

            len(
                bloco.get(
                    "talhoes",
                    []
                )
            )

            for bloco in blocos

        )


        # ====================================================
        # RESPOSTA
        # ====================================================

        return jsonify({

            "sucesso": True,

            "documento_id":
                documento_id,

            "arquivo":
                nome_original,

            "resultado":
                documento,

            "total_blocos":
                len(blocos),

            "total_talhoes":
                total_talhoes,

            "bloco":
                metadata.get(
                    "bloco",
                    documento.get(
                        "bloco",
                        ""
                    )
                ),

            "propriedade":
                metadata.get(
                    "propriedade",
                    documento.get(
                        "propriedade",
                        ""
                    )
                ),

            "download":
                f"/download/{documento_id}"

        })


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro)

        }), 500


# ============================================================
# ESTATÍSTICAS
# ============================================================

@app.route(
    "/api/estatisticas",
    methods=["GET"]
)
def estatisticas():

    documentos = contar_documentos()

    correcoes = contar_correcoes()


    return jsonify({

        "documentos":
            documentos,

        "extracoes":
            documentos,

        "correcoes":
            correcoes

    })


# ============================================================
# REGISTRAR CORREÇÃO
# ============================================================

@app.route(
    "/api/corrigir",
    methods=["POST"]
)
def corrigir():

    try:

        dados = request.get_json(
            silent=True
        )


        if not dados:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Dados da correção não enviados."

            }), 400


        documento_id = dados.get(
            "documento_id",
            ""
        )


        campo = dados.get(
            "campo",
            ""
        )


        valor_original = dados.get(
            "valor_original",
            ""
        )


        valor_corrigido = dados.get(
            "valor_corrigido",
            ""
        )


        contexto = dados.get(
            "contexto",
            ""
        )


        # ====================================================
        # CAMPOS PERMITIDOS
        # ====================================================

        campos_permitidos = {

            "bloco",
            "talhao",
            "variedade",
            "area",
            "plantio",
            "propriedade"

        }


        if campo not in campos_permitidos:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Campo não permitido."

            }), 400


        salvar_correcao_json(

            documento_id,

            campo,

            valor_original,

            valor_corrigido,

            contexto

        )


        return jsonify({

            "sucesso": True,

            "mensagem":
                "Correção registrada com sucesso."

        })


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro)

        }), 500


# ============================================================
# OBTER DOCUMENTO
# ============================================================

@app.route(
    "/api/documento/<documento_id>",
    methods=["GET"]
)
def obter_documento(
    documento_id
):

    caminho = os.path.join(

        RESULTADOS_FOLDER,

        documento_id,

        "resultado.json"

    )


    documento = carregar_json(
        caminho
    )


    if documento is None:

        return jsonify({

            "sucesso": False,

            "erro":
                "Documento não encontrado."

        }), 404


    return jsonify({

        "sucesso": True,

        "resultado":
            documento

    })


# ============================================================
# CONFIRMAR DOCUMENTO
# ============================================================

@app.route(
    "/api/confirmar",
    methods=["POST"]
)
def confirmar():

    try:

        dados = request.get_json(
            silent=True
        )


        if not dados:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Dados não enviados."

            }), 400


        documento_id = dados.get(
            "documento_id"
        )


        documento = dados.get(
            "documento"
        )


        if not documento_id:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Documento não informado."

            }), 400


        if not documento:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Documento vazio."

            }), 400


        # ====================================================
        # SALVAR DOCUMENTO ATUALIZADO
        # ====================================================

        pasta = os.path.join(

            RESULTADOS_FOLDER,

            documento_id

        )


        os.makedirs(
            pasta,
            exist_ok=True
        )


        caminho_json = os.path.join(

            pasta,

            "resultado.json"

        )


        salvar_json(
            caminho_json,
            documento
        )


        # ====================================================
        # GERAR EXCEL ATUALIZADO
        # ====================================================

        caminho_excel = os.path.join(

            pasta,

            "resultado.xlsx"

        )


        exportar_excel(

            documento,

            caminho_excel

        )


        return jsonify({

            "sucesso": True,

            "mensagem":
                "Documento confirmado.",

            "download":
                f"/download/{documento_id}"

        })


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro)

        }), 500


# ============================================================
# DOWNLOAD DO EXCEL
# ============================================================

@app.route(
    "/download/<documento_id>"
)
def download(
    documento_id
):

    caminho = os.path.join(

        RESULTADOS_FOLDER,

        documento_id,

        "resultado.xlsx"

    )


    if not os.path.exists(
        caminho
    ):

        return jsonify({

            "sucesso": False,

            "erro":
                "Arquivo Excel não encontrado."

        }), 404


    return send_file(

        caminho,

        as_attachment=True,

        download_name="resultado.xlsx"

    )


# ============================================================
# ERRO 413
# ============================================================

@app.errorhandler(413)
def arquivo_muito_grande(
    erro
):

    return jsonify({

        "sucesso": False,

        "erro":
            "O arquivo enviado é muito grande."

    }), 413


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=5000,

        debug=True

    )

