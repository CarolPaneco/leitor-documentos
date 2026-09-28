
import re


def validar_documento(documento):

    avisos = []

    metadata = documento.get(
        "metadata",
        {}
    )

    if not metadata.get(
        "propriedade"
    ):
        avisos.append(
            "Propriedade não identificada."
        )

    if not metadata.get(
        "municipio"
    ):
        avisos.append(
            "Município não identificado."
        )

    blocos = documento.get(
        "blocos",
        []
    )

    if not blocos:
        avisos.append(
            "Nenhum bloco/tabela foi identificado."
        )

    for bloco in blocos:

        talhoes = bloco.get(
            "talhoes",
            []
        )

        if not talhoes:

            avisos.append(
                f"O bloco {bloco.get('bloco', '')} não possui talhões identificados."
            )

        numeros = []

        for talhao in talhoes:

            numero = talhao.get(
                "talhao",
                ""
            )

            if numero.isdigit():

                numeros.append(
                    int(numero)
                )

            if not talhao.get(
                "variedade"
            ):

                avisos.append(
                    f"Bloco {bloco.get('bloco')}: "
                    f"talhão {numero} sem variedade."
                )

            if not talhao.get(
                "area"
            ):

                avisos.append(
                    f"Bloco {bloco.get('bloco')}: "
                    f"talhão {numero} sem área."
                )

            if not talhao.get(
                "plantio"
            ):

                avisos.append(
                    f"Bloco {bloco.get('bloco')}: "
                    f"talhão {numero} sem plantio."
                )

        # Não assumimos que a sequência precisa ser contínua.
        # Apenas sinalizamos duplicidade.
        if len(
            numeros
        ) != len(
            set(numeros)
        ):

            avisos.append(
                f"Bloco {bloco.get('bloco')}: "
                "existem talhões duplicados."
            )

    documento[
        "avisos"
    ] = avisos

    return documento
