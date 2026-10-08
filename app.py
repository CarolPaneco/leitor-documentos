import io
import os
import shutil
import tempfile
import uuid
from datetime import datetime

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
)

from werkzeug.utils import secure_filename

from services.document_processor import get_processor
from services.agricultural_extractor import extrair_dados
from services.validator import validar_documento


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)


ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "bmp",
    "tif",
    "tiff",
}


app = Flask(__name__)

# Mantemos um limite alto para cada arquivo individual.
# O navegador NÃO enviará mais todos os arquivos juntos.
app.config["MAX_CONTENT_LENGTH"] = (
    100 * 1024 * 1024
)


# ============================================================
# UTILITÁRIOS
# ============================================================

def extensao_permitida(nome):
    """
    Verifica se a extensão do arquivo é permitida.
    """

    return (
        "." in nome
        and nome.rsplit(
            ".",
            1
        )[1].lower()
        in ALLOWED_EXTENSIONS
    )


def extrair_linhas_excel(documento):
    """
    Converte o resultado de UM documento
    para as 6 colunas finais do Excel.

    Campos:

    1. Bloco
    2. Talhão
    3. Variedade
    4. Área
    5. Plantio
    6. Propriedade
    7. Proprietário
    8. Município
    """

    metadata = (
        documento.get("metadata")
        or {}
    )

    propriedade = str(
        metadata.get("propriedade")
        or documento.get("propriedade")
        or ""
    ).strip()

    proprietario = str(
        metadata.get("proprietario")
        or metadata.get("proprietário")
        or documento.get("proprietario")
        or documento.get("proprietário")
        or ""
    ).strip()

    municipio = str(
        metadata.get("municipio")
        or metadata.get("município")
        or documento.get("municipio")
        or documento.get("município")
        or ""
    ).strip()

    bloco_doc = str(
        metadata.get("bloco")
        or documento.get("bloco")
        or ""
    ).strip()

    blocos = documento.get("blocos") or []

    # Fallback se blocos estiver vazio mas talhoes existir
    if not blocos and (documento.get("talhoes") or []):
        blocos = [{
            "bloco": bloco_doc,
            "talhoes": documento.get("talhoes") or [],
        }]

    linhas = []

    for bloco in blocos:
        codigo_bloco = str(
            bloco.get("bloco")
            or bloco.get("codigo")
            or bloco_doc
            or ""
        ).strip()

        bloco_prop = str(
            bloco.get("propriedade")
            or propriedade
        ).strip()

        bloco_proprio = str(
            bloco.get("proprietario")
            or bloco.get("proprietário")
            or proprietario
        ).strip()

        bloco_mun = str(
            bloco.get("municipio")
            or bloco.get("município")
            or municipio
        ).strip()

        for talhao in (
            bloco.get("talhoes")
            or []
        ):
            if not isinstance(talhao, dict):
                continue

            linhas.append({
                "Bloco":
                    str(
                        talhao.get("bloco")
                        or codigo_bloco
                        or ""
                    ).strip(),

                "Talhão":
                    str(
                        talhao.get("talhao")
                        or talhao.get("talhão")
                        or talhao.get("numero")
                        or ""
                    ).strip(),

                "Variedade":
                    str(
                        talhao.get("variedade")
                        or ""
                    ).strip(),

                "Área":
                    str(
                        talhao.get("area")
                        or talhao.get("área")
                        or ""
                    ).strip(),

                "Plantio":
                    str(
                        talhao.get("plantio")
                        or talhao.get("data_plantio")
                        or ""
                    ).strip(),

                "Propriedade":
                    str(
                        talhao.get("propriedade")
                        or bloco_prop
                        or ""
                    ).strip(),

                "Proprietário":
                    str(
                        talhao.get("proprietario")
                        or talhao.get("proprietário")
                        or bloco_proprio
                        or ""
                    ).strip(),

                "Município":
                    str(
                        talhao.get("municipio")
                        or talhao.get("município")
                        or bloco_mun
                        or ""
                    ).strip(),
            })

    return linhas


def criar_excel(linhas):
    """
    Cria o Excel somente em memória.

    Nenhum Excel é salvo no projeto.
    """

    import pandas as pd

    colunas = [
        "Bloco",
        "Talhão",
        "Variedade",
        "Área",
        "Plantio",
        "Propriedade",
        "Proprietário",
        "Município",
    ]

    linhas_normalizadas = []
    for item in (linhas or []):
        if not isinstance(item, dict):
            continue

        linhas_normalizadas.append({
            "Bloco": str(item.get("Bloco") or item.get("bloco") or "").strip(),
            "Talhão": str(item.get("Talhão") or item.get("Talhao") or item.get("talhao") or item.get("talhão") or "").strip(),
            "Variedade": str(item.get("Variedade") or item.get("variedade") or "").strip(),
            "Área": str(item.get("Área") or item.get("Area") or item.get("area") or item.get("área") or "").strip(),
            "Plantio": str(item.get("Plantio") or item.get("plantio") or item.get("data_plantio") or "").strip(),
            "Propriedade": str(item.get("Propriedade") or item.get("propriedade") or "").strip(),
            "Proprietário": str(item.get("Proprietário") or item.get("Proprietario") or item.get("proprietario") or item.get("proprietário") or "").strip(),
            "Município": str(item.get("Município") or item.get("Municipio") or item.get("municipio") or item.get("município") or "").strip(),
        })

    df = pd.DataFrame(
        linhas_normalizadas,
        columns=colunas
    )

    # Mantém tudo como texto limpo.
    for coluna in colunas:
        df[coluna] = (
            df[coluna]
            .fillna("")
            .astype(str)
        )

    memoria = io.BytesIO()

    with pd.ExcelWriter(
        memoria,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            index=False,
            sheet_name="Dados"
        )

        planilha = (
            writer.book["Dados"]
        )

        planilha.freeze_panes = "A2"

        planilha.auto_filter.ref = (
            planilha.dimensions
        )

        larguras = {
            "A": 18,
            "B": 12,
            "C": 18,
            "D": 14,
            "E": 15,
            "F": 35,
            "G": 35,
            "H": 20,
        }

        for coluna, largura in (
            larguras.items()
        ):

            planilha.column_dimensions[
                coluna
            ].width = largura

    memoria.seek(0)

    return memoria


def processar_arquivo_temporario(
    arquivo,
    processor,
    indice
):
    """
    Processa UM arquivo.

    O arquivo é salvo somente em uma pasta
    temporária do sistema.

    Depois do processamento:
    - upload é apagado;
    - pasta de resultado do OCR é apagada.

    Nada é salvo em uploads/ do projeto.
    """

    nome_original = (
        arquivo.filename
        or f"documento_{indice}"
    )

    extensao = os.path.splitext(
        nome_original
    )[1].lower()

    pasta_temp = tempfile.mkdtemp(
        prefix="leitor_documentos_"
    )

    nome_seguro = secure_filename(
        nome_original
    )

    if not nome_seguro:

        nome_seguro = (
            f"documento_{indice}"
            f"{extensao}"
        )

    caminho_temp = os.path.join(
        pasta_temp,
        nome_seguro
    )

    resultado_ocr = None

    try:

        # ----------------------------------------------------
        # SALVA TEMPORARIAMENTE
        # ----------------------------------------------------

        arquivo.save(
            caminho_temp
        )


        # ----------------------------------------------------
        # ID DO PROCESSAMENTO
        # ----------------------------------------------------

        documento_id = (
            datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + "_"
            + uuid.uuid4().hex[:8]
            + f"_{indice}"
        )


        # ----------------------------------------------------
        # OCR
        # ----------------------------------------------------

        resultado_ocr = (
            processor.processar(
                caminho_temp,
                documento_id
            )
        )


        if not resultado_ocr.get(
            "sucesso"
        ):

            raise RuntimeError(
                resultado_ocr.get(
                    "erro",
                    "Não foi possível "
                    "processar o documento."
                )
            )


        # ----------------------------------------------------
        # EXTRAÇÃO AGRÍCOLA
        # ----------------------------------------------------

        documento = extrair_dados(
            resultado_ocr
        )


        # ----------------------------------------------------
        # VALIDAÇÃO
        # ----------------------------------------------------

        documento = validar_documento(
            documento
        )


        # ----------------------------------------------------
        # CONVERTE PARA AS 6 COLUNAS
        # ----------------------------------------------------

        linhas = extrair_linhas_excel(
            documento
        )


        return {
            "nome": nome_original,
            "linhas": linhas,
        }


    finally:

        # ----------------------------------------------------
        # REMOVE UPLOAD TEMPORÁRIO
        # ----------------------------------------------------

        try:

            shutil.rmtree(
                pasta_temp,
                ignore_errors=True
            )

        except Exception:
            pass


        # ----------------------------------------------------
        # REMOVE RESULTADO DO OCR
        # ----------------------------------------------------

        if resultado_ocr:

            pasta_resultado = (
                resultado_ocr.get(
                    "pasta_saida"
                )
            )

            if pasta_resultado:

                try:

                    shutil.rmtree(
                        pasta_resultado,
                        ignore_errors=True
                    )

                except Exception:
                    pass


# ============================================================
# PÁGINA
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def index():

    return render_template(
        "index.html"
    )


# ============================================================
# PROCESSAR UM ARQUIVO
# ============================================================

@app.route(
    "/api/processar-arquivo",
    methods=["POST"]
)
def api_processar_arquivo():

    arquivo = request.files.get(
        "arquivo"
    )


    # --------------------------------------------------------
    # VERIFICA ARQUIVO
    # --------------------------------------------------------

    if (
        arquivo is None
        or not arquivo.filename
    ):

        return jsonify({

            "sucesso": False,

            "erro":
                "Nenhum arquivo foi enviado."

        }), 400


    # --------------------------------------------------------
    # VERIFICA EXTENSÃO
    # --------------------------------------------------------

    if not extensao_permitida(
        arquivo.filename
    ):

        return jsonify({

            "sucesso": False,

            "erro":
                (
                    "Formato não permitido: "
                    + arquivo.filename
                )

        }), 400


    try:

        processor = get_processor()


        resultado = (
            processar_arquivo_temporario(
                arquivo,
                processor,
                1
            )
        )


        linhas = (
            resultado.get(
                "linhas",
                []
            )
        )


        return jsonify({

            "sucesso": True,

            "nome":
                resultado["nome"],

            "linhas":
                linhas,

            "quantidade_linhas":
                len(linhas),

        })


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro),

        }), 500


# ============================================================
# GERAR EXCEL FINAL
# ============================================================

@app.route(
    "/api/gerar-excel",
    methods=["POST"]
)
def api_gerar_excel():

    try:

        dados = request.get_json(
            silent=True
        )


        if not dados:

            return jsonify({

                "sucesso": False,

                "erro":
                    "Nenhum dado foi recebido."

            }), 400


        linhas = (
            dados.get(
                "linhas",
                []
            )
        )


        if not isinstance(
            linhas,
            list
        ):

            return jsonify({

                "sucesso": False,

                "erro":
                    "Formato de dados inválido."

            }), 400


        if not linhas:

            return jsonify({

                "sucesso": False,

                "erro":
                    (
                        "Nenhuma linha foi "
                        "extraída dos arquivos."
                    )

            }), 422


        # ----------------------------------------------------
        # GERA EXCEL
        # ----------------------------------------------------

        excel = criar_excel(
            linhas
        )


        nome_download = (
            "dados_agricolas_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + ".xlsx"
        )


        resposta = send_file(

            excel,

            as_attachment=True,

            download_name=(
                nome_download
            ),

            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),

        )


        return resposta


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro),

        }), 500


# ============================================================
# API DIRETA PARA POWER AUTOMATE / SHAREPOINT
# (Recebe PDF e devolve o Excel diretamente)
# ============================================================

@app.route(
    "/api/processar-e-gerar-excel",
    methods=["POST"]
)
def api_processar_e_gerar_excel():
    import base64

    nome_arquivo = request.headers.get("X-Filename") or request.headers.get("x-filename") or "documento.pdf"
    conteudo_bytes = None

    if "arquivo" in request.files:
        arq = request.files["arquivo"]
        nome_arquivo = arq.filename or nome_arquivo
        conteudo_bytes = arq.read()
    elif request.is_json:
        dados = request.get_json(silent=True) or {}
        nome_arquivo = dados.get("nome", nome_arquivo)
        b64 = dados.get("conteudo") or dados.get("arquivo") or ""
        try:
            conteudo_bytes = base64.b64decode(b64)
        except Exception:
            conteudo_bytes = None
    else:
        conteudo_bytes = request.get_data()

    if not conteudo_bytes:
        return jsonify({
            "sucesso": False,
            "erro": "Nenhum arquivo ou conteúdo binário foi recebido.",
        }), 400

    extensao = os.path.splitext(nome_arquivo)[1].lower() or ".pdf"
    if extensao not in [f".{ext}" for ext in ALLOWED_EXTENSIONS]:
        extensao = ".pdf"

    pasta_temp = tempfile.mkdtemp(prefix="pa_upload_")
    caminho_temp = os.path.join(pasta_temp, f"doc{extensao}")
    resultado_ocr = None

    try:
        with open(caminho_temp, "wb") as f:
            f.write(conteudo_bytes)

        documento_id = (
            datetime.now().strftime("%Y%m%d_%H%M%S")
            + "_"
            + uuid.uuid4().hex[:8]
        )

        processor = get_processor()
        resultado_ocr = processor.processar(caminho_temp, documento_id)

        if not resultado_ocr.get("sucesso"):
            raise RuntimeError(
                resultado_ocr.get("erro", "Erro no processamento do documento.")
            )

        documento = extrair_dados(resultado_ocr)
        documento = validar_documento(documento)
        linhas = extrair_linhas_excel(documento)

        if not linhas:
            return jsonify({
                "sucesso": False,
                "erro": "Nenhum dado ou tabela encontrada no documento.",
            }), 422

        excel = criar_excel(linhas)
        nome_base = os.path.splitext(nome_arquivo)[0]
        nome_download = f"{nome_base}_dados.xlsx"

        return send_file(
            excel,
            as_attachment=True,
            download_name=nome_download,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as erro:
        import traceback
        traceback.print_exc()
        return jsonify({
            "sucesso": False,
            "erro": str(erro),
        }), 500

    finally:
        try:
            shutil.rmtree(pasta_temp, ignore_errors=True)
        except Exception:
            pass

        if resultado_ocr:
            pasta_res = resultado_ocr.get("pasta_saida")
            if pasta_res and os.path.exists(pasta_res):
                try:
                    shutil.rmtree(pasta_res, ignore_errors=True)
                except Exception:
                    pass


# ============================================================
# COMPATIBILIDADE
# ============================================================

@app.route(
    "/api/processar",
    methods=["POST"]
)
def api_processar_compatibilidade():

    """
    Mantém a rota antiga funcionando.

    Porém o novo JavaScript não utiliza essa rota.

    A nova rota /api/processar-arquivo
    envia somente UM arquivo por requisição.
    """

    arquivos = request.files.getlist(
        "arquivos"
    )


    if not arquivos:

        arquivo = request.files.get(
            "arquivo"
        )

        if arquivo is not None:

            arquivos = [arquivo]


    if not arquivos:

        return jsonify({

            "sucesso": False,

            "erro":
                "Nenhum arquivo foi enviado."

        }), 400


    try:

        processor = get_processor()

        todas_linhas = []

        erros = []

        processados = 0


        for indice, arquivo in enumerate(
            arquivos,
            start=1
        ):

            if (
                not arquivo
                or not arquivo.filename
            ):
                continue


            if not extensao_permitida(
                arquivo.filename
            ):

                erros.append({

                    "arquivo":
                        arquivo.filename,

                    "erro":
                        "Formato não permitido."

                })

                continue


            try:

                resultado = (
                    processar_arquivo_temporario(
                        arquivo,
                        processor,
                        indice
                    )
                )


                todas_linhas.extend(
                    resultado["linhas"]
                )


                processados += 1


            except Exception as erro:

                erros.append({

                    "arquivo":
                        arquivo.filename,

                    "erro":
                        str(erro),

                })


        if not todas_linhas:

            return jsonify({

                "sucesso": False,

                "erro":
                    (
                        "Nenhum dado agrícola "
                        "foi extraído."
                    ),

                "erros":
                    erros,

            }), 422


        excel = criar_excel(
            todas_linhas
        )


        nome_download = (
            "dados_agricolas_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + ".xlsx"
        )


        resposta = send_file(

            excel,

            as_attachment=True,

            download_name=(
                nome_download
            ),

            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),

        )


        resposta.headers[
            "X-Arquivos-Processados"
        ] = str(
            processados
        )


        resposta.headers[
            "X-Linhas-Extraidas"
        ] = str(
            len(todas_linhas)
        )


        if erros:

            resposta.headers[
                "X-Arquivos-Com-Erros"
            ] = str(
                len(erros)
            )


        return resposta


    except Exception as erro:

        import traceback

        traceback.print_exc()


        return jsonify({

            "sucesso": False,

            "erro":
                str(erro),

        }), 500


# ============================================================
# ERRO 413
# ============================================================

@app.errorhandler(413)
def arquivo_muito_grande(_erro):

    return jsonify({

        "sucesso": False,

        "erro":
            (
                "O arquivo individual "
                "ultrapassa o limite de "
                "100 MB."
            )

    }), 413


# ============================================================
# VIGILANTE DE E-MAILS (AUTOMÁTICO)
# ============================================================

try:
    import threading
    from services.email_watcher import loop_vigilante_email
    _email_thread = threading.Thread(target=loop_vigilante_email, args=(25,), daemon=True)
    _email_thread.start()
    print("[APP] Vigilante de e-mails iniciado em segundo plano!")
except Exception as _e:
    print(f"[APP] Falha ao iniciar vigilante de e-mail: {_e}")


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    print("=" * 70)
    print("IA DOCUMENTOS AGRÍCOLAS")
    print("Modo: múltiplos arquivos -> Excel")
    print("Upload: arquivos processados individualmente")
    print("=" * 70)


    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get(
                "PORT",
                5000
            )
        ),

        debug=True,

    )