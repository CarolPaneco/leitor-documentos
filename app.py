
import json
import os
from datetime import datetime

from config import TRAINING_CORRECTIONS


def salvar_correcao_json(
    documento_id,
    campo,
    valor_original,
    valor_corrigido,
    contexto=""
):

    os.makedirs(
        TRAINING_CORRECTIONS,
        exist_ok=True
    )

    registro = {
        "documento_id": documento_id,
        "campo": campo,
        "valor_original": valor_original,
        "valor_corrigido": valor_corrigido,
        "contexto": contexto,
        "data": datetime.now().isoformat()
    }

    nome = (
        f"correcao_"
        f"{documento_id}_"
        f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}.json"
    )

    caminho = os.path.join(
        TRAINING_CORRECTIONS,
        nome
    )

    with open(
        caminho,
        "w",
        encoding="utf-8"
    ) as arquivo:

        json.dump(
            registro,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    return caminho
