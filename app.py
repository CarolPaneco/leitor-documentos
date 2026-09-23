from flask import Flask, render_template, request, jsonify, send_file
import os
import io
import fitz
import pytesseract
from PIL import Image
from werkzeug.utils import secure_filename

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
RESULT_FOLDER = "resultados"

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["RESULT_FOLDER"] = RESULT_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def ocr_imagem(image):
    return pytesseract.image_to_string(image, lang="por+eng")


def ocr_pdf(caminho_pdf):
    documento = fitz.open(caminho_pdf)
    texto_final = []

    for numero_pagina, pagina in enumerate(documento, start=1):
        texto = pagina.get_text()

        texto_final.append(f"\n\n===== PÁGINA {numero_pagina} =====\n")

        if texto.strip():
            texto_final.append(texto)
        else:
            pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2))
            imagem = Image.open(io.BytesIO(pix.tobytes("png")))
            texto_final.append(ocr_imagem(imagem))

    documento.close()
    return "".join(texto_final)


def processar_imagem(caminho):
    imagem = Image.open(caminho)
    return ocr_imagem(imagem)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/processar", methods=["POST"])
def processar():
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    arquivo = request.files["arquivo"]

    if arquivo.filename == "":
        return jsonify({"erro": "Nenhum arquivo selecionado."}), 400

    if not allowed_file(arquivo.filename):
        return jsonify({
            "erro": "Formato não permitido. Use PDF, PNG, JPG, JPEG ou TIFF."
        }), 400

    nome = secure_filename(arquivo.filename)
    caminho = os.path.join(app.config["UPLOAD_FOLDER"], nome)
    arquivo.save(caminho)

    extensao = nome.rsplit(".", 1)[1].lower()

    try:
        if extensao == "pdf":
            texto = ocr_pdf(caminho)
        else:
            texto = processar_imagem(caminho)

        nome_resultado = os.path.splitext(nome)[0] + ".txt"
        caminho_resultado = os.path.join(
            app.config["RESULT_FOLDER"],
            nome_resultado
        )

        with open(caminho_resultado, "w", encoding="utf-8") as f:
            f.write(texto)

        return jsonify({
            "sucesso": True,
            "arquivo": nome,
            "texto": texto,
            "resultado": nome_resultado
        })

    except Exception as erro:
        return jsonify({"erro": f"Erro ao processar: {erro}"}), 500


@app.route("/download/<nome>")
def download(nome):
    nome_seguro = secure_filename(nome)
    caminho = os.path.join(app.config["RESULT_FOLDER"], nome_seguro)

    if not os.path.exists(caminho):
        return "Arquivo não encontrado.", 404

    return send_file(caminho, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
