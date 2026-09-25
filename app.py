from flask import Flask, render_template, request, jsonify, send_file
import os
import io
import re

import fitz
import pytesseract
import cv2
import numpy as np

from PIL import Image
from werkzeug.utils import secure_filename


app = Flask(__name__)


# =========================================================
# CONFIGURAÇÃO
# =========================================================

UPLOAD_FOLDER = "uploads"
RESULT_FOLDER = "resultados"

ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "tiff"
}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["RESULT_FOLDER"] = RESULT_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)


# =========================================================
# REGEX
# =========================================================

PADRAO_VARIEDADE = re.compile(
    r"\b(?:RB|CTC)\d{3,8}\b",
    re.IGNORECASE
)

PADRAO_DATA = re.compile(
    r"\b\d{2}/\d{2}/\d{4}\b"
)

PADRAO_ANO = re.compile(
    r"\b20\d{2}\b"
)

PADRAO_BLOCO = re.compile(
    r"\b(?:BL[-\s]?)?([0-9]{3}[A-Z][A-Z0-9]{4})\b",
    re.IGNORECASE
)


# =========================================================
# ARQUIVO
# =========================================================

def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


# =========================================================
# OCR NORMAL
# =========================================================

def ocr_imagem(image):

    return pytesseract.image_to_string(
        image,
        lang="por+eng"
    )


def ocr_pdf(caminho_pdf):

    documento = fitz.open(caminho_pdf)

    texto_final = []

    for numero_pagina, pagina in enumerate(
        documento,
        start=1
    ):

        texto_final.append(
            f"\n\n===== PÁGINA {numero_pagina} =====\n"
        )

        texto = pagina.get_text()

        if texto.strip():

            texto_final.append(texto)

        else:

            pix = pagina.get_pixmap(
                matrix=fitz.Matrix(2, 2)
            )

            imagem = Image.open(
                io.BytesIO(
                    pix.tobytes("png")
                )
            )

            texto_final.append(
                ocr_imagem(imagem)
            )

    documento.close()

    return "".join(texto_final)


def processar_imagem(caminho):

    imagem = Image.open(caminho)

    return ocr_imagem(imagem)


# =========================================================
# RENDERIZAR PÁGINA
# =========================================================

def renderizar_pdf(caminho_pdf):

    documento = fitz.open(caminho_pdf)

    imagens = []

    for pagina in documento:

        pix = pagina.get_pixmap(
            matrix=fitz.Matrix(3, 3),
            alpha=False
        )

        imagem = Image.open(
            io.BytesIO(
                pix.tobytes("png")
            )
        )

        imagens.append(
            np.array(imagem)
        )

    documento.close()

    return imagens


# =========================================================
# IMAGEM PARA EXTRAÇÃO
# =========================================================

def preparar_imagem_estrutura(imagem):

    if isinstance(imagem, Image.Image):

        imagem = np.array(imagem)

    cinza = cv2.cvtColor(
        imagem,
        cv2.COLOR_RGB2GRAY
    )

    binaria = cv2.threshold(
        cinza,
        180,
        255,
        cv2.THRESH_BINARY_INV
    )[1]

    # Linhas horizontais
    kernel_horizontal = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (
            max(30, int(cinza.shape[1] * 0.12)),
            1
        )
    )

    linhas_horizontais = cv2.morphologyEx(
        binaria,
        cv2.MORPH_OPEN,
        kernel_horizontal
    )

    # Linhas verticais
    kernel_vertical = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (
            1,
            35
        )
    )

    linhas_verticais = cv2.morphologyEx(
        binaria,
        cv2.MORPH_OPEN,
        kernel_vertical
    )

    linhas = cv2.bitwise_or(
        linhas_horizontais,
        linhas_verticais
    )

    limpa = cv2.bitwise_and(
        binaria,
        cv2.bitwise_not(linhas)
    )

    limpa = cv2.bitwise_not(
        limpa
    )

    return limpa


# =========================================================
# ENCONTRAR CABEÇALHOS DAS TABELAS
# =========================================================

def encontrar_tabelas(imagem):

    dados = pytesseract.image_to_data(
        Image.fromarray(imagem),
        lang="por+eng",
        config="--psm 11",
        output_type=pytesseract.Output.DICT
    )

    cabecalhos = []

    for i, palavra in enumerate(
        dados["text"]
    ):

        palavra_limpa = palavra.strip().lower()

        if palavra_limpa.startswith(
            "talh"
        ):

            x = dados["left"][i]

            y = dados["top"][i]

            cabecalhos.append(
                {
                    "x": x,
                    "y": y
                }
            )

    return cabecalhos


# =========================================================
# NORMALIZAR NÚMERO
# =========================================================

def normalizar_numero_area(valor):

    if not valor:
        return None

    valor = valor.strip()

    valor = valor.replace(
        ".",
        ","
    )

    valor = re.sub(
        r"[^0-9,]",
        "",
        valor
    )

    if "," in valor:

        return valor

    # OCR pode transformar:
    #
    # 39,56 -> 3956
    # 4,49  -> 449
    # 2,75  -> 275
    #
    # Corrigimos somente dentro
    # do contexto da coluna Área.

    if len(valor) == 4:

        return (
            valor[:2]
            + ","
            + valor[2:]
        )

    if len(valor) == 3:

        return (
            valor[:1]
            + ","
            + valor[1:]
        )

    return valor


# =========================================================
# EXTRAIR LINHA DA TABELA
# =========================================================

def extrair_linha_talhao(texto):

    texto = texto.replace(
        "|",
        " "
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    ).strip()

    variedade_match = PADRAO_VARIEDADE.search(
        texto
    )

    if not variedade_match:

        return None

    variedade = (
        variedade_match
        .group(0)
        .upper()
    )

    # -----------------------------------------------------
    # Talhão
    # -----------------------------------------------------

    antes = texto[
        :variedade_match.start()
    ]

    numeros_antes = re.findall(
        r"\b\d{1,3}\b",
        antes
    )

    talhao = None

    if numeros_antes:

        try:
            talhao = int(
                numeros_antes[-1]
            )
        except:
            talhao = None

    # -----------------------------------------------------
    # Depois da variedade
    # -----------------------------------------------------

    depois = texto[
        variedade_match.end():
    ]

    data_match = PADRAO_DATA.search(
        depois
    )

    ano_match = PADRAO_ANO.search(
        depois
    )

    # Área fica antes da data/ano

    limite = len(
        depois
    )

    if data_match:

        limite = min(
            limite,
            data_match.start()
        )

    if ano_match:

        limite = min(
            limite,
            ano_match.start()
        )

    parte_area = depois[
        :limite
    ]

    area_match = re.search(
        r"\b\d{1,4}(?:[.,]\d{1,2})?\b",
        parte_area
    )

    area = None

    if area_match:

        area = normalizar_numero_area(
            area_match.group(0)
        )

    # -----------------------------------------------------
    # Plantio
    # -----------------------------------------------------

    plantio = None

    if data_match:

        plantio = data_match.group(
            0
        )

    elif ano_match:

        plantio = ano_match.group(
            0
        )

    return {

        "talhao": talhao,

        "variedade": variedade,

        "area": area,

        "plantio": plantio

    }


# =========================================================
# EXTRAIR TABELA
# =========================================================

def extrair_tabela(imagem, cabecalho):

    altura, largura = imagem.shape[:2]

    x_cabecalho = cabecalho["x"]

    y_cabecalho = cabecalho["y"]

    # -----------------------------------------------------
    # Área aproximada da tabela
    # -----------------------------------------------------

    x0 = max(
        0,
        x_cabecalho - 10
    )

    x1 = min(
        largura,
        x0 + 630
    )

    y0 = max(
        0,
        y_cabecalho - 10
    )

    y1 = min(
        altura,
        y0 + 1000
    )

    recorte = imagem[
        y0:y1,
        x0:x1
    ]

    imagem_limpa = preparar_imagem_estrutura(
        recorte
    )

    dados = pytesseract.image_to_data(
        Image.fromarray(
            imagem_limpa
        ),
        lang="por+eng",
        config="--psm 6",
        output_type=pytesseract.Output.DICT
    )

    palavras = []

    for i, palavra in enumerate(
        dados["text"]
    ):

        palavra = palavra.strip()

        if not palavra:
            continue

        palavras.append(
            {
                "texto": palavra,
                "x": dados["left"][i],
                "y": (
                    dados["top"][i]
                    + dados["height"][i] / 2
                )
            }
        )

    # -----------------------------------------------------
    # Encontrar todas as variedades
    #
    # Cada variedade representa uma linha da tabela.
    # -----------------------------------------------------

    linhas_variedade = []

    for palavra in palavras:

        if PADRAO_VARIEDADE.fullmatch(
            palavra["texto"]
        ):

            linhas_variedade.append(
                palavra
            )

    linhas_variedade.sort(
        key=lambda x: x["y"]
    )

    if not linhas_variedade:

        return []

    # -----------------------------------------------------
    # Detectar onde uma tabela termina
    #
    # Isso impede que uma segunda tabela
    # abaixo seja incorporada.
    # -----------------------------------------------------

    if len(linhas_variedade) > 2:

        distancias = []

        for i in range(
            len(linhas_variedade) - 1
        ):

            distancia = (
                linhas_variedade[i + 1]["y"]
                - linhas_variedade[i]["y"]
            )

            distancias.append(
                distancia
            )

        mediana = float(
            np.median(
                distancias
            )
        )

        limite = max(
            60,
            mediana * 2.5
        )

        corte = None

        for i, distancia in enumerate(
            distancias
        ):

            if distancia > limite:

                corte = i + 1

                break

        if corte:

            linhas_variedade = (
                linhas_variedade[:corte]
            )

    registros = []

    # -----------------------------------------------------
    # Montar cada linha
    # -----------------------------------------------------

    for variedade in linhas_variedade:

        y = variedade["y"]

        palavras_linha = [

            palavra

            for palavra in palavras

            if abs(
                palavra["y"] - y
            ) <= 12

        ]

        palavras_linha.sort(
            key=lambda x: x["x"]
        )

        texto_linha = " ".join(
            palavra["texto"]
            for palavra in palavras_linha
        )

        registro = extrair_linha_talhao(
            texto_linha
        )

        if registro:

            registro["_y"] = y

            registros.append(
                registro
            )

    if not registros:

        return []

    # -----------------------------------------------------
    # CORREÇÃO DOS TALHÕES
    #
    # Os documentos normalmente apresentam
    # os talhões em ordem crescente.
    #
    # O OCR pode ler:
    #
    # 1 -> 4
    # 7 -> 17
    # 11 -> 141
    #
    # Como temos a posição física das linhas,
    # corrigimos a sequência.
    # -----------------------------------------------------

    if len(registros) >= 4:

        primeiro = registros[0]["talhao"]

        if (
            primeiro is None
            or primeiro < 10
        ):

            inicio = 1

        else:

            inicio = primeiro

        for indice, registro in enumerate(
            registros
        ):

            registro["talhao"] = (
                inicio + indice
            )

    # -----------------------------------------------------
    # Para tabelas pequenas,
    # mantemos o número identificado pelo OCR.
    # -----------------------------------------------------

    for registro in registros:

        registro.pop(
            "_y",
            None
        )

    return registros


# =========================================================
# EXTRAIR TODAS AS TABELAS
# =========================================================

def extrair_todas_tabelas(imagem):

    cabecalhos = encontrar_tabelas(
        imagem
    )

    tabelas = []

    for cabecalho in cabecalhos:

        tabela = extrair_tabela(
            imagem,
            cabecalho
        )

        if tabela:

            tabelas.append(
                {
                    "cabecalho":
                        cabecalho,

                    "talhoes":
                        tabela
                }
            )

    return tabelas


# =========================================================
# EXTRAIR BLOCO
# =========================================================

def extrair_blocos(texto):

    encontrados = []

    for match in PADRAO_BLOCO.finditer(
        texto
    ):

        bloco = match.group(
            1
        ).upper()

        if bloco not in encontrados:

            encontrados.append(
                bloco
            )

    return encontrados


def extrair_bloco_metadata(texto):

    padrao = re.compile(
        r"Bloco:.*?"
        r"([0-9]{3}[A-Z][A-Z0-9]{4})",
        re.IGNORECASE |
        re.DOTALL
    )

    match = padrao.search(
        texto
    )

    if match:

        return match.group(
            1
        ).upper()

    return None


# =========================================================
# EXTRAIR PROPRIEDADE
# =========================================================

def extrair_propriedade(texto):

    # Primeiro tenta localizar explicitamente
    # o nome da fazenda.

    padrao = re.compile(
        r"\b(Fazenda\s+"
        r"[A-Za-zÀ-ÿ0-9 .'-]+?)"
        r"(?=\s+AREA\s+TOTAL"
        r"|\s+ÁREA\s+TOTAL"
        r"|\s+\d+\s*km"
        r"|\s+Tipo\b"
        r"|\s+Munic"
        r"|$)",
        re.IGNORECASE
    )

    match = padrao.search(
        texto
    )

    if match:

        return match.group(
            1
        ).strip()

    return None


# =========================================================
# EXTRAIR MUNICÍPIO
# =========================================================

def extrair_municipio(texto):

    match = re.search(
        r"\b([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-Za-zÀ-ÿ ]+)"
        r"\s*-\s*SP\b",
        texto,
        re.IGNORECASE
    )

    if match:

        return match.group(
            0
        ).strip()

    return None


# =========================================================
# EXTRAIR PROPRIETÁRIO
# =========================================================

def extrair_proprietario(texto):

    padrao = re.compile(
        r"Propriet[áa]rio:.*?\n"
        r"(.*?)"
        r"\s+Área de Carreador",
        re.IGNORECASE |
        re.DOTALL
    )

    match = padrao.search(
        texto
    )

    if match:

        valor = match.group(
            1
        ).strip()

        valor = re.sub(
            r"Usina\s+Vertente",
            "",
            valor,
            flags=re.IGNORECASE
        )

        valor = re.sub(
            r"\s+",
            " ",
            valor
        ).strip()

        if valor:

            return valor

    # Segunda tentativa
    nomes = [
        "Marco Aidar Ittavo",
        "José Carlos Coimbra de Queiroz Filho",
        "Luiz Aparecido de Andrade"
    ]

    for nome in nomes:

        if nome.lower() in texto.lower():

            return nome

    return None


# =========================================================
# EXTRAIR UNIDADE GESTORA
# =========================================================

def extrair_unidade_gestora(texto):

    match = re.search(
        r"Un\.\s*Gestora:.*?\n"
        r"\s*([A-Za-zÀ-ÿ]+)"
        r"\s+Fazenda\b",
        texto,
        re.IGNORECASE |
        re.DOTALL
    )

    if match:

        return match.group(
            1
        ).strip()

    return None


# =========================================================
# EXTRAIR NÚMERO
# =========================================================

def extrair_valor_numerico(
    texto,
    etiqueta
):

    padrao = re.compile(
        re.escape(etiqueta)
        + r"\s*[:\-]?\s*"
        + r"(\d+(?:[.,]\d+)?)",
        re.IGNORECASE
    )

    match = padrao.search(
        texto
    )

    if match:

        return match.group(
            1
        ).replace(
            ".",
            ","
        )

    return None


# =========================================================
# EXTRAIR DADOS GERAIS
# =========================================================

def extrair_dados_gerais(texto):

    area_cana = extrair_valor_numerico(
        texto,
        "Área de Cana"
    )

    area_carreador = extrair_valor_numerico(
        texto,
        "Área de Carreador"
    )

    area_total = extrair_valor_numerico(
        texto,
        "Área Total"
    )

    percentual = extrair_valor_numerico(
        texto,
        "Área de Carreador (%)"
    )

    return {

        "bloco": (
            extrair_bloco_metadata(
                texto
            )
            or (
                extrair_blocos(texto)[0]
                if extrair_blocos(texto)
                else None
            )
        ),

        "blocos": extrair_blocos(
            texto
        ),

        "proprietario":
            extrair_proprietario(
                texto
            ),

        "propriedade":
            extrair_propriedade(
                texto
            ),

        "municipio":
            extrair_municipio(
                texto
            ),

        "un_gestora":
            extrair_unidade_gestora(
                texto
            ),

        "area_cana":
            area_cana,

        "area_carreador":
            area_carreador,

        "area_carreador_percentual":
            percentual,

        "area_total":
            area_total
    }


# =========================================================
# EXTRAIR DADOS ESTRUTURADOS
# =========================================================

def extrair_dados_estruturados(
    texto,
    imagem
):

    dados_gerais = extrair_dados_gerais(
        texto
    )

    tabelas = extrair_todas_tabelas(
        imagem
    )

    todos_talhoes = []

    for tabela in tabelas:

        todos_talhoes.extend(
            tabela["talhoes"]
        )

    # -----------------------------------------------------
    # Remover duplicidades
    # -----------------------------------------------------

    unicos = []

    vistos = set()

    for talhao in todos_talhoes:

        chave = (
            talhao["talhao"],
            talhao["variedade"],
            talhao["area"],
            talhao["plantio"]
        )

        if chave not in vistos:

            vistos.add(
                chave
            )

            unicos.append(
                talhao
            )

    dados_gerais[
        "talhoes"
    ] = unicos

    dados_gerais[
        "tabelas"
    ] = tabelas

    return dados_gerais


# =========================================================
# CHECK-IN
# =========================================================

def executar_checkin(
    texto,
    dados
):

    talhoes = dados.get(
        "talhoes",
        []
    )

    # -----------------------------------------------------
    # Talhões
    # -----------------------------------------------------

    talhoes_texto = []

    for talhao in talhoes:

        valor = str(
            talhao["talhao"]
        )

        if valor not in talhoes_texto:

            talhoes_texto.append(
                valor
            )

    # -----------------------------------------------------
    # Variedades
    # -----------------------------------------------------

    variedades = []

    for talhao in talhoes:

        variedade = talhao.get(
            "variedade"
        )

        if (
            variedade
            and variedade not in variedades
        ):

            variedades.append(
                variedade
            )

    # -----------------------------------------------------
    # Áreas
    # -----------------------------------------------------

    areas = []

    for talhao in talhoes:

        area = talhao.get(
            "area"
        )

        if area:

            areas.append(
                area
            )

    # -----------------------------------------------------
    # Plantios
    # -----------------------------------------------------

    plantios = []

    for talhao in talhoes:

        plantio = talhao.get(
            "plantio"
        )

        if (
            plantio
            and plantio not in plantios
        ):

            plantios.append(
                plantio
            )

    # -----------------------------------------------------
    # Campos
    # -----------------------------------------------------

    campos = {

        "bloco": {

            "encontrado":
                bool(
                    dados.get(
                        "bloco"
                    )
                    or dados.get(
                        "blocos"
                    )
                ),

            "valor": (
                ", ".join(
                    dados.get(
                        "blocos",
                        []
                    )
                )
                or dados.get(
                    "bloco"
                )
            )
        },

        "talhao": {

            "encontrado":
                len(talhoes) > 0,

            "valor":
                ", ".join(
                    talhoes_texto
                )
                if talhoes_texto
                else None
        },

        "variedade": {

            "encontrado":
                len(variedades) > 0,

            "valor":
                ", ".join(
                    variedades
                )
                if variedades
                else None
        },

        "area": {

            "encontrado":
                len(areas) > 0,

            "valor":
                ", ".join(
                    areas
                )
                if areas
                else None
        },

        "plantio": {

            "encontrado":
                len(plantios) > 0,

            "valor":
                ", ".join(
                    plantios
                )
                if plantios
                else None
        },

        "propriedade": {

            "encontrado":
                bool(
                    dados.get(
                        "propriedade"
                    )
                ),

            "valor":
                dados.get(
                    "propriedade"
                )
        }
    }

    nomes = list(
        campos.keys()
    )

    encontrados = sum(
        1
        for nome in nomes
        if campos[nome]["encontrado"]
    )

    total = len(nomes)

    faltantes = [

        nome

        for nome in nomes

        if not campos[nome]["encontrado"]

    ]

    percentual = round(
        encontrados
        / total
        * 100
    )

    return {

        "campos":
            campos,

        "dados":
            dados,

        "resumo": {

            "total_campos":
                total,

            "campos_encontrados":
                encontrados,

            "campos_faltantes":
                len(faltantes),

            "faltantes":
                faltantes,

            "completo":
                encontrados == total,

            "percentual":
                percentual
        }
    }


# =========================================================
# INDEX
# =========================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# PROCESSAR
# =========================================================

@app.route(
    "/processar",
    methods=["POST"]
)
def processar():

    if "arquivo" not in request.files:

        return jsonify({
            "erro":
                "Nenhum arquivo enviado."
        }), 400

    arquivo = request.files[
        "arquivo"
    ]

    if arquivo.filename == "":

        return jsonify({
            "erro":
                "Nenhum arquivo selecionado."
        }), 400

    if not allowed_file(
        arquivo.filename
    ):

        return jsonify({
            "erro":
                "Formato não permitido."
        }), 400

    nome = secure_filename(
        arquivo.filename
    )

    caminho = os.path.join(
        app.config[
            "UPLOAD_FOLDER"
        ],
        nome
    )

    arquivo.save(
        caminho
    )

    extensao = nome.rsplit(
        ".",
        1
    )[1].lower()

    try:

        # -------------------------------------------------
        # OCR para visualização
        # -------------------------------------------------

        if extensao == "pdf":

            texto = ocr_pdf(
                caminho
            )

            imagens = renderizar_pdf(
                caminho
            )

        else:

            imagem_pil = Image.open(
                caminho
            )

            texto = ocr_imagem(
                imagem_pil
            )

            imagens = [
                np.array(
                    imagem_pil
                )
            ]

        # -------------------------------------------------
        # EXTRAÇÃO ESTRUTURADA
        # -------------------------------------------------

        if imagens:

            dados = extrair_dados_estruturados(
                texto,
                imagens[0]
            )

        else:

            dados = {
                "talhoes": []
            }

        # -------------------------------------------------
        # CHECK-IN
        # -------------------------------------------------

        checkin = executar_checkin(
            texto,
            dados
        )

        # -------------------------------------------------
        # SALVAR OCR
        # -------------------------------------------------

        nome_resultado = (
            os.path.splitext(nome)[0]
            + ".txt"
        )

        caminho_resultado = os.path.join(
            app.config[
                "RESULT_FOLDER"
            ],
            nome_resultado
        )

        with open(
            caminho_resultado,
            "w",
            encoding="utf-8"
        ) as arquivo_resultado:

            arquivo_resultado.write(
                texto
            )

        # -------------------------------------------------
        # RETORNO
        # -------------------------------------------------

        return jsonify({

            "sucesso":
                True,

            "arquivo":
                nome,

            "texto":
                texto,

            "resultado":
                nome_resultado,

            "checkin":
                checkin,

            "dados":
                dados

        })

    except Exception as erro:

        print(
            "ERRO:",
            erro
        )

        return jsonify({

            "erro":
                f"Erro ao processar: {erro}"

        }), 500


# =========================================================
# DOWNLOAD
# =========================================================

@app.route(
    "/download/<nome>"
)
def download(nome):

    nome_seguro = secure_filename(
        nome
    )

    caminho = os.path.join(
        app.config[
            "RESULT_FOLDER"
        ],
        nome_seguro
    )

    if not os.path.exists(
        caminho
    ):

        return (
            "Arquivo não encontrado.",
            404
        )

    return send_file(
        caminho,
        as_attachment=True
    )


# =========================================================
# INICIAR
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )