
import os
import sys
import json

from services.document_processor import (
    get_processor
)

from services.agricultural_extractor import (
    extrair_dados
)

from services.validator import (
    validar_documento
)


def main():

    if len(sys.argv) < 2:

        print(
            "\nUso:"
        )

        print(
            "python test_ia.py caminho/documento.pdf\n"
        )

        return

    caminho = sys.argv[1]

    if not os.path.exists(
        caminho
    ):

        print(
            "Arquivo não encontrado:",
            caminho
        )

        return

    print("")
    print("=" * 70)
    print("TESTE DO MOTOR DE IA")
    print("=" * 70)
    print("")
    print("Arquivo:", caminho)
    print("")

    processor = get_processor()

    resultado = processor.processar(
        caminho,
        "teste"
    )

    if not resultado["sucesso"]:

        print("")
        print(
            "ERRO:"
        )

        print(
            resultado["erro"]
        )

        return

    print("")
    print("=" * 70)
    print("OCR CONCLUÍDO")
    print("=" * 70)

    documento = extrair_dados(
        resultado
    )

    documento = validar_documento(
        documento
    )

    print("")
    print("=" * 70)
    print("RESULTADO AGRÍCOLA")
    print("=" * 70)

    print(
        json.dumps(
            documento,
            ensure_ascii=False,
            indent=2
        )
    )


if __name__ == "__main__":
    main()
