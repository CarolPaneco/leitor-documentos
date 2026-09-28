"""
Validador de documentos agrícolas.

Este módulo recebe o documento produzido pelo
AgriculturalExtractor e verifica se os campos
obrigatórios estão preenchidos.
"""

from models.schemas import novo_documento


# ============================================================
# CAMPOS OBRIGATÓRIOS
# ============================================================

CAMPOS_METADATA_OBRIGATORIOS = [
    "bloco",
    "proprietario",
    "propriedade",
    "area_local",
    "area_cana",
]


CAMPOS_TALHAO_OBRIGATORIOS = [
    "talhao",
    "variedade",
    "area",
    "plantio",
]


# ============================================================
# VALIDAÇÃO PRINCIPAL
# ============================================================

def validar_documento(documento):
    """
    Valida e normaliza a estrutura do documento.

    Não altera os dados extraídos de forma agressiva.
    Apenas garante que a estrutura exista e registra
    problemas encontrados.
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

    # --------------------------------------------------------
    # Estrutura de validação
    # --------------------------------------------------------

    erros = []
    avisos = []

    metadata = documento["metadata"]

    # --------------------------------------------------------
    # Validação dos metadados
    # --------------------------------------------------------

    for campo in CAMPOS_METADATA_OBRIGATORIOS:

        valor = metadata.get(
            campo,
            ""
        )

        if valor is None or str(valor).strip() == "":
            erros.append(
                f"Campo obrigatório não encontrado: {campo}"
            )

    # --------------------------------------------------------
    # Validação dos blocos
    # --------------------------------------------------------

    if not documento["blocos"]:

        avisos.append(
            "Nenhum bloco foi identificado no documento."
        )

    # --------------------------------------------------------
    # Validação dos talhões
    # --------------------------------------------------------

    total_talhoes = 0

    for indice_bloco, bloco in enumerate(
        documento["blocos"],
        start=1
    ):

        if not isinstance(bloco, dict):

            erros.append(
                f"Bloco {indice_bloco} possui estrutura inválida."
            )

            continue

        codigo_bloco = bloco.get(
            "bloco",
            ""
        )

        talhoes = bloco.get(
            "talhoes",
            []
        )

        if not codigo_bloco:

            avisos.append(
                f"Bloco {indice_bloco} foi identificado "
                f"sem código."
            )

        if not isinstance(talhoes, list):

            erros.append(
                f"Bloco {codigo_bloco or indice_bloco} "
                f"possui lista de talhões inválida."
            )

            continue

        for indice_talhao, talhao in enumerate(
            talhoes,
            start=1
        ):

            total_talhoes += 1

            if not isinstance(talhao, dict):

                erros.append(
                    f"Talhão {indice_talhao} do bloco "
                    f"{codigo_bloco or indice_bloco} "
                    f"possui estrutura inválida."
                )

                continue

            for campo in CAMPOS_TALHAO_OBRIGATORIOS:

                valor = talhao.get(
                    campo,
                    ""
                )

                if valor is None or str(valor).strip() == "":

                    avisos.append(
                        f"Talhão "
                        f"{talhao.get('talhao', indice_talhao)} "
                        f"do bloco "
                        f"{codigo_bloco or indice_bloco}: "
                        f"campo '{campo}' não identificado."
                    )

    # --------------------------------------------------------
    # Informações da validação
    # --------------------------------------------------------

    documento["validacao"] = {
        "valido": len(erros) == 0,
        "erros": erros,
        "avisos": avisos,
        "total_blocos": len(
            documento["blocos"]
        ),
        "total_talhoes": total_talhoes
    }

    # --------------------------------------------------------
    # Status geral
    # --------------------------------------------------------

    if erros:

        documento["status_validacao"] = "ERRO"

    elif avisos:

        documento["status_validacao"] = "ATENCAO"

    else:

        documento["status_validacao"] = "OK"

    return documento


# ============================================================
# FUNÇÃO AUXILIAR
# ============================================================

def documento_valido(documento):
    """
    Retorna True quando o documento não possui
    erros de validação.
    """

    documento = validar_documento(
        documento
    )

    return documento.get(
        "validacao",
        {}
    ).get(
        "valido",
        False
    )