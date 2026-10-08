# ============================================================
# DOCUMENT PROCESSOR
# Motor local de OCR
#
# Tecnologias:
# - PyMuPDF
# - OpenCV
# - Tesseract OCR
# - Pillow
#
# Não utiliza PaddleOCR nem APIs externas.
# ============================================================

import os
import re
import json
import traceback
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np
import pymupdf
import pytesseract
if os.name == "nt":
    for _cmd in [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "tesseract", "tesseract.exe"),
    ]:
        if os.path.exists(_cmd):
            pytesseract.pytesseract.tesseract_cmd = _cmd
            break

from pytesseract import Output

from config import RESULT_FOLDER, CPU_THREADS


# ============================================================
# CONFIGURAÇÕES
# ============================================================

DPI_PADRAO = 250

# Idioma principal.
# O tesseract-ocr-por precisa estar instalado.
IDIOMA_OCR = "por+eng"

# Configurações de OCR
PSM_PADRAO = 6
PSM_LINHAS = 11

# Confiança mínima para considerar uma palavra útil
CONFIANCA_MINIMA = 25

# Extensões de imagem aceitas
EXTENSOES_IMAGEM = {
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def limpar_texto(texto: Any) -> str:
    """
    Normaliza um texto OCR.
    """

    if texto is None:
        return ""

    texto = str(texto)

    texto = texto.replace("\n", " ")
    texto = texto.replace("\r", " ")
    texto = texto.replace("\t", " ")

    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()


def normalizar_numero(texto: str) -> str:
    """
    Tenta corrigir números agrícolas reconhecidos pelo OCR.

    Exemplos:

        3956  -> 39,56
        449   -> 4,49
        540   -> 5,40
        2957  -> 29,57

    Não altera números que já possuem vírgula ou ponto.
    """

    texto = limpar_texto(texto)

    if not texto:
        return ""

    # Remove caracteres estranhos nas extremidades
    texto = texto.strip(".,;:|")

    # Já está decimal
    if re.fullmatch(r"\d+[,.]\d{1,2}", texto):
        return texto.replace(".", ",")

    # Somente números
    if re.fullmatch(r"\d+", texto):

        if len(texto) == 3:
            return f"{texto[0]},{texto[1:]}"

        if len(texto) == 4:
            return f"{texto[:-2]},{texto[-2:]}"

    return texto


def ponto_medio(box: List[float]) -> Tuple[float, float]:
    """
    Recebe [x1, y1, x2, y2].
    Retorna o centro.
    """

    if not box or len(box) < 4:
        return 0.0, 0.0

    x1, y1, x2, y2 = box[:4]

    return (
        (float(x1) + float(x2)) / 2,
        (float(y1) + float(y2)) / 2,
    )


def serializar(valor: Any) -> Any:
    """
    Converte objetos NumPy para estruturas JSON.
    """

    if valor is None:
        return None

    if isinstance(valor, np.ndarray):
        return valor.tolist()

    if isinstance(valor, np.generic):
        return valor.item()

    if isinstance(valor, dict):
        return {
            str(k): serializar(v)
            for k, v in valor.items()
        }

    if isinstance(valor, (list, tuple)):
        return [
            serializar(v)
            for v in valor
        ]

    return valor


# ============================================================
# CLASSE PRINCIPAL
# ============================================================

class DocumentProcessor:

    def __init__(self):

        print("=" * 70)
        print("INICIALIZANDO MOTOR LOCAL DE DOCUMENTOS")
        print("=" * 70)

        self.cpu_threads = CPU_THREADS

        # Verifica Tesseract
        try:
            versao = pytesseract.get_tesseract_version()

            print(
                f"Tesseract detectado: {versao}"
            )

        except Exception as erro:

            print(
                "AVISO: Tesseract não foi encontrado."
            )

            print(erro)

        # Verifica português
        try:

            idiomas = pytesseract.get_languages(
                config=""
            )

            if "por" in idiomas:

                print(
                    "Idioma português do Tesseract: OK"
                )

            else:

                print(
                    "AVISO: idioma 'por' não encontrado."
                )

                print(
                    "Instale com:"
                )

                print(
                    "sudo apt install -y tesseract-ocr-por"
                )

        except Exception as erro:

            print(
                "Aviso ao verificar idiomas:",
                erro
            )

        print(
            "Motor local pronto."
        )

        print("=" * 70)


    # ========================================================
    # PROCESSAR DOCUMENTO
    # ========================================================

    def processar(
        self,
        arquivo: str,
        nome_base: str
    ) -> Dict[str, Any]:

        pasta_saida = os.path.join(
            RESULT_FOLDER,
            nome_base
        )

        os.makedirs(
            pasta_saida,
            exist_ok=True
        )

        print("")
        print("=" * 70)
        print("PROCESSANDO DOCUMENTO")
        print("=" * 70)

        print(
            "Arquivo:",
            arquivo
        )

        print(
            "Saída:",
            pasta_saida
        )

        resultados = []

        try:

            imagens = self._carregar_paginas(
                arquivo
            )

            if not imagens:

                raise RuntimeError(
                    "Nenhuma página/imagem foi encontrada."
                )

            print(
                f"Total de páginas: {len(imagens)}"
            )

            for indice, imagem in enumerate(imagens):

                print("")
                print(
                    "-" * 70
                )

                print(
                    f"PROCESSANDO PÁGINA {indice + 1}/{len(imagens)}"
                )

                print(
                    "-" * 70
                )

                resultado_pagina = (
                    self._processar_pagina(
                        imagem,
                        indice
                    )
                )

                resultados.append(
                    resultado_pagina
                )

                # Salvar imagem processada
                caminho_imagem = os.path.join(
                    pasta_saida,
                    f"pagina_{indice + 1:03d}.png"
                )

                cv2.imwrite(
                    caminho_imagem,
                    imagem
                )

            # Salvar OCR completo
            caminho_ocr = os.path.join(
                pasta_saida,
                "ocr_completo.json"
            )

            with open(
                caminho_ocr,
                "w",
                encoding="utf-8"
            ) as arquivo_ocr:

                json.dump(
                    resultados,
                    arquivo_ocr,
                    ensure_ascii=False,
                    indent=2
                )

            # Criar texto OCR
            texto_completo = (
                self._montar_texto_completo(
                    resultados
                )
            )

            caminho_txt = os.path.join(
                pasta_saida,
                "ocr_completo.txt"
            )

            with open(
                caminho_txt,
                "w",
                encoding="utf-8"
            ) as arquivo_txt:

                arquivo_txt.write(
                    texto_completo
                )

            print("")
            print("=" * 70)
            print("OCR CONCLUÍDO")
            print("=" * 70)

            return {
                "sucesso": True,
                "pasta_saida": pasta_saida,
                "paginas": resultados,
                "texto_completo": texto_completo,
            }

        except Exception as erro:

            traceback.print_exc()

            return {
                "sucesso": False,
                "erro": str(erro),
                "pasta_saida": pasta_saida,
                "paginas": [],
            }


    # ========================================================
    # CARREGAR PDF OU IMAGEM
    # ========================================================

    def _carregar_paginas(
        self,
        arquivo: str
    ) -> List[np.ndarray]:

        if not os.path.exists(arquivo):

            raise FileNotFoundError(
                f"Arquivo não encontrado: {arquivo}"
            )

        extensao = (
            os.path.splitext(arquivo)[1]
            .lower()
        )

        # ----------------------------------------------------
        # PDF
        # ----------------------------------------------------

        if extensao == ".pdf":

            return self._pdf_para_imagens(
                arquivo
            )

        # ----------------------------------------------------
        # IMAGEM
        # ----------------------------------------------------

        if extensao in EXTENSOES_IMAGEM:

            imagem = cv2.imread(
                arquivo
            )

            if imagem is None:

                raise RuntimeError(
                    "Não foi possível abrir a imagem."
                )

            return [
                imagem
            ]

        raise ValueError(
            f"Formato não suportado: {extensao}"
        )


    # ========================================================
    # PDF -> IMAGENS
    # ========================================================

    def _pdf_para_imagens(
        self,
        arquivo: str
    ) -> List[np.ndarray]:

        imagens = []

        documento = pymupdf.open(
            arquivo
        )

        try:

            for pagina in documento:

                matriz = pymupdf.Matrix(
                    DPI_PADRAO / 72,
                    DPI_PADRAO / 72
                )

                pix = pagina.get_pixmap(
                    matrix=matriz,
                    alpha=False
                )

                largura = pix.width
                altura = pix.height

                dados = np.frombuffer(
                    pix.samples,
                    dtype=np.uint8
                )

                imagem = dados.reshape(
                    altura,
                    largura,
                    pix.n
                )

                if pix.n == 4:

                    imagem = cv2.cvtColor(
                        imagem,
                        cv2.COLOR_RGBA2BGR
                    )

                else:

                    imagem = cv2.cvtColor(
                        imagem,
                        cv2.COLOR_RGB2BGR
                    )

                imagens.append(
                    imagem
                )

        finally:

            documento.close()

        return imagens


    # ========================================================
    # PROCESSAMENTO DA PÁGINA
    # ========================================================

    def _processar_pagina(
        self,
        imagem: np.ndarray,
        indice: int
    ) -> Dict[str, Any]:

        altura, largura = imagem.shape[:2]

        print(
            f"Dimensões: {largura} x {altura}"
        )

        # ----------------------------------------------------
        # PRÉ-PROCESSAMENTO
        # ----------------------------------------------------

        imagem_cinza = cv2.cvtColor(
            imagem,
            cv2.COLOR_BGR2GRAY
        )

        # Aumenta contraste
        imagem_contraste = cv2.createCLAHE(
            clipLimit=2.0,
            tileGridSize=(8, 8)
        ).apply(
            imagem_cinza
        )

        # ----------------------------------------------------
        # OCR PRINCIPAL
        # ----------------------------------------------------

        dados_ocr = self._executar_ocr(
            imagem_contraste
        )

        # ----------------------------------------------------
        # OCR COM THRESHOLD
        # ----------------------------------------------------

        imagem_threshold = cv2.threshold(
            imagem_contraste,
            180,
            255,
            cv2.THRESH_BINARY
        )[1]

        dados_ocr_threshold = self._executar_ocr(
            imagem_threshold
        )

        # ----------------------------------------------------
        # OCR ALTERNATIVO PARA LAYOUTS DE MAPA/RODAPÉ
        # ----------------------------------------------------
        # PSM 3 costuma recuperar rótulos e valores lineares que o PSM 6
        # perde, sem substituir o OCR principal usado pela tabela.
        dados_ocr_global = self._executar_ocr(
            imagem_contraste,
            psm=3
        )

        # ----------------------------------------------------
        # CONSOLIDAR
        # ----------------------------------------------------

        palavras = self._consolidar_ocr(
            dados_ocr,
            dados_ocr_threshold,
            dados_ocr_global
        )

        print(
            f"Palavras OCR: {len(palavras)}"
        )

        # ----------------------------------------------------
        # LINHAS
        # ----------------------------------------------------

        linhas = self._agrupar_linhas(
            palavras
        )

        print(
            f"Linhas detectadas: {len(linhas)}"
        )

        # ----------------------------------------------------
        # TABELAS
        # ----------------------------------------------------

        tabelas = self._detectar_tabelas(
            linhas,
            palavras,
            largura,
            altura
        )

        print(
            f"Regiões de tabela: {len(tabelas)}"
        )

        # ----------------------------------------------------
        # LAYOUT
        # ----------------------------------------------------

        layout = self._detectar_layout(
            linhas,
            largura,
            altura
        )

        # ----------------------------------------------------
        # MARKDOWN
        # ----------------------------------------------------

        markdown = self._gerar_markdown(
            linhas
        )

        return {
            "input_path": None,
            "page_index": indice,

            "width": largura,
            "height": altura,

            "ocr": palavras,

            "linhas": linhas,

            "layout": layout,

            "tabelas": tabelas,

            "markdown": markdown,

            "texto": self._linhas_para_texto(
                linhas
            ),
        }


    # ========================================================
    # OCR
    # ========================================================

    def _executar_ocr(
        self,
        imagem: np.ndarray,
        psm: int = None
    ) -> List[Dict[str, Any]]:

        modo = PSM_PADRAO if psm is None else psm
        config = (
            f"--oem 3 --psm {modo}"
        )

        resultado = pytesseract.image_to_data(
            imagem,
            lang=IDIOMA_OCR,
            config=config,
            output_type=Output.DICT
        )

        palavras = []

        total = len(
            resultado["text"]
        )

        for i in range(total):

            texto = limpar_texto(
                resultado["text"][i]
            )

            if not texto:
                continue

            try:

                confianca = float(
                    resultado["conf"][i]
                )

            except Exception:

                confianca = 0

            if confianca < CONFIANCA_MINIMA:

                continue

            x = int(
                resultado["left"][i]
            )

            y = int(
                resultado["top"][i]
            )

            w = int(
                resultado["width"][i]
            )

            h = int(
                resultado["height"][i]
            )

            palavras.append({

                "texto": texto,

                "confianca": round(
                    confianca,
                    2
                ),

                "x": x,
                "y": y,
                "largura": w,
                "altura": h,

                "x2": x + w,
                "y2": y + h,

                "centro_x": x + (w / 2),
                "centro_y": y + (h / 2),

                "pagina": None,

                "origem": "tesseract"

            })

        return palavras


    # ========================================================
    # CONSOLIDAR OCR
    # ========================================================

    def _consolidar_ocr(
        self,
        *listas
    ) -> List[Dict[str, Any]]:

        todas = []

        for lista in listas:

            if lista:
                todas.extend(
                    lista
                )

        if not todas:
            return []

        # Ordenação espacial
        todas.sort(
            key=lambda item: (
                item["y"],
                item["x"]
            )
        )

        resultado = []

        for palavra in todas:

            duplicada = False

            for existente in resultado:

                dx = abs(
                    palavra["centro_x"]
                    -
                    existente["centro_x"]
                )

                dy = abs(
                    palavra["centro_y"]
                    -
                    existente["centro_y"]
                )

                if (
                    dx < 15
                    and
                    dy < 15
                ):

                    # Mantém maior confiança
                    if (
                        palavra["confianca"]
                        >
                        existente["confianca"]
                    ):

                        existente.update(
                            palavra
                        )

                    duplicada = True

                    break

            if not duplicada:

                resultado.append(
                    palavra
                )

        resultado.sort(
            key=lambda item: (
                item["y"],
                item["x"]
            )
        )

        return resultado


    # ========================================================
    # AGRUPAR PALAVRAS EM LINHAS
    # ========================================================

    def _agrupar_linhas(
        self,
        palavras: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:

        if not palavras:
            return []

        palavras = sorted(
            palavras,
            key=lambda p: (
                p["centro_y"],
                p["x"]
            )
        )

        grupos = []

        tolerancia_y = 18

        for palavra in palavras:

            adicionada = False

            for grupo in reversed(
                grupos[-5:]
            ):

                if abs(
                    palavra["centro_y"]
                    -
                    grupo["centro_y"]
                ) <= tolerancia_y:

                    grupo["palavras"].append(
                        palavra
                    )

                    # recalcula centro
                    ys = [
                        p["centro_y"]
                        for p in grupo["palavras"]
                    ]

                    grupo["centro_y"] = (
                        sum(ys) / len(ys)
                    )

                    adicionada = True

                    break

            if not adicionada:

                grupos.append({

                    "centro_y":
                        palavra["centro_y"],

                    "palavras": [
                        palavra
                    ]

                })

        linhas = []

        for indice, grupo in enumerate(
            grupos
        ):

            palavras_linha = sorted(
                grupo["palavras"],
                key=lambda p: p["x"]
            )

            texto = " ".join(
                p["texto"]
                for p in palavras_linha
            )

            linhas.append({

                "indice": indice,

                "y": round(
                    grupo["centro_y"],
                    2
                ),

                "texto": texto,

                "palavras":
                    palavras_linha,

                "x_inicio":
                    min(
                        p["x"]
                        for p in palavras_linha
                    ),

                "x_fim":
                    max(
                        p["x2"]
                        for p in palavras_linha
                    )

            })

        return linhas


    # ========================================================
    # DETECTAR TABELAS
    # ========================================================

    def _detectar_tabelas(
        self,
        linhas: List[Dict[str, Any]],
        palavras: List[Dict[str, Any]],
        largura: int,
        altura: int
    ) -> List[Dict[str, Any]]:

        if not linhas:
            return []

        # Procuramos linhas que tenham indícios
        # de tabela agrícola.

        candidatos = []

        for linha in linhas:

            texto = linha["texto"].lower()

            sinais = 0

            if "talhão" in texto:
                sinais += 3

            if "variedade" in texto:
                sinais += 3

            if "área" in texto:
                sinais += 2

            if "plantio" in texto:
                sinais += 2

            if re.search(
                r"\brb\d{5,}",
                texto,
                re.IGNORECASE
            ):

                sinais += 2

            if sinais >= 3:

                candidatos.append(
                    linha
                )

        tabelas = []

        for candidato in candidatos:

            y_inicio = max(
                0,
                int(
                    candidato["y"] - 40
                )
            )

            # Procura aproximadamente 1000 px
            # abaixo do cabeçalho.
            y_fim = min(
                altura,
                int(
                    candidato["y"] + 1200
                )
            )

            palavras_regiao = [

                p for p in palavras

                if (
                    y_inicio
                    <=
                    p["centro_y"]
                    <=
                    y_fim
                )

            ]

            if not palavras_regiao:
                continue

            x_inicio = max(
                0,
                min(
                    p["x"]
                    for p in palavras_regiao
                ) - 20
            )

            x_fim = min(
                largura,
                max(
                    p["x2"]
                    for p in palavras_regiao
                ) + 20
            )

            tabelas.append({

                "tipo":
                    "tabela_agricola",

                "cabecalho":
                    candidato["texto"],

                "bbox": [
                    x_inicio,
                    y_inicio,
                    x_fim,
                    y_fim
                ],

                "texto":
                    " ".join(
                        p["texto"]
                        for p in palavras_regiao
                    ),

                "palavras":
                    palavras_regiao,

                "linhas":
                    self._agrupar_linhas(
                        palavras_regiao
                    )

            })

        # Remove regiões muito sobrepostas
        tabelas = self._remover_tabelas_duplicadas(
            tabelas
        )

        return tabelas


    # ========================================================
    # REMOVER TABELAS DUPLICADAS
    # ========================================================

    def _remover_tabelas_duplicadas(
        self,
        tabelas: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:

        resultado = []

        for tabela in tabelas:

            duplicada = False

            x1, y1, x2, y2 = (
                tabela["bbox"]
            )

            for existente in resultado:

                a1, b1, a2, b2 = (
                    existente["bbox"]
                )

                sobreposicao_y = (
                    min(y2, b2)
                    -
                    max(y1, b1)
                )

                if sobreposicao_y > 0:

                    altura_menor = min(
                        y2 - y1,
                        b2 - b1
                    )

                    if (
                        altura_menor > 0
                        and
                        sobreposicao_y
                        /
                        altura_menor
                        >
                        0.75
                    ):

                        duplicada = True

                        break

            if not duplicada:

                resultado.append(
                    tabela
                )

        return resultado


    # ========================================================
    # DETECTAR LAYOUT
    # ========================================================

    def _detectar_layout(
        self,
        linhas: List[Dict[str, Any]],
        largura: int,
        altura: int
    ) -> List[Dict[str, Any]]:

        layout = []

        for linha in linhas:

            texto = linha["texto"]

            texto_lower = texto.lower()

            if (
                "talhão" in texto_lower
                or
                "variedade" in texto_lower
                or
                "plantio" in texto_lower
                or
                "área" in texto_lower
            ):

                tipo = "table_header"

            elif re.search(
                r"\brb\d{5,}",
                texto,
                re.IGNORECASE
            ):

                tipo = "table_row"

            elif (
                "propriedade" in texto_lower
                or
                "proprietário" in texto_lower
                or
                "município" in texto_lower
                or
                "bloco" in texto_lower
            ):

                tipo = "metadata"

            else:

                tipo = "text"

            layout.append({

                "tipo": tipo,

                "bbox": [
                    linha["x_inicio"],
                    linha["y"],
                    linha["x_fim"],
                    linha["y"]
                ],

                "conteudo": texto

            })

        return layout


    # ========================================================
    # TEXTO DAS LINHAS
    # ========================================================

    def _linhas_para_texto(
        self,
        linhas: List[Dict[str, Any]]
    ) -> str:

        return "\n".join(
            linha["texto"]
            for linha in linhas
        )


    # ========================================================
    # MARKDOWN
    # ========================================================

    def _gerar_markdown(
        self,
        linhas: List[Dict[str, Any]]
    ) -> str:

        if not linhas:
            return ""

        partes = []

        for linha in linhas:

            texto = linha["texto"]

            if not texto:
                continue

            partes.append(
                texto
            )

        return "\n\n".join(
            partes
        )


    # ========================================================
    # TEXTO COMPLETO
    # ========================================================

    def _montar_texto_completo(
        self,
        resultados: List[Dict[str, Any]]
    ) -> str:

        partes = []

        for pagina in resultados:

            indice = pagina.get(
                "page_index",
                0
            )

            partes.append(
                "=" * 70
            )

            partes.append(
                f"PÁGINA {indice + 1}"
            )

            partes.append(
                "=" * 70
            )

            partes.append(
                pagina.get(
                    "texto",
                    ""
                )
            )

        return "\n\n".join(
            partes
        )


# ============================================================
# SINGLETON
# ============================================================

_processor = None


def get_processor():

    global _processor

    if _processor is None:

        _processor = DocumentProcessor()

    return _processor