from copy import deepcopy

CAMPOS_METADATA_OBRIGATORIOS = [
    "bloco",
    "propriedade",
]

CAMPOS_TALHAO_OBRIGATORIOS = [
    "talhao",
    "variedade",
    "area",
    "plantio",
]


def validar_documento(documento):
    documento = deepcopy(documento or {})
    erros = []
    avisos = list(documento.get("avisos") or [])

    metadata = documento.setdefault("metadata", {})

    for campo in CAMPOS_METADATA_OBRIGATORIOS:
        if not str(metadata.get(campo, "") or "").strip():
            erros.append(f"Campo obrigatório ausente: {campo}")

    total = 0
    incompletos = 0

    for bloco in documento.get("blocos") or []:
        for talhao in bloco.get("talhoes") or []:
            total += 1
            faltantes = [
                campo for campo in CAMPOS_TALHAO_OBRIGATORIOS
                if not str(talhao.get(campo, "") or "").strip()
            ]
            if faltantes:
                incompletos += 1
                avisos.append(
                    f"Talhão {talhao.get('talhao') or '?'}: "
                    f"campos incompletos ({', '.join(faltantes)})."
                )

    if total == 0:
        erros.append("Nenhum talhão foi identificado.")

    documento["validacao"] = {
        "valido": not erros,
        "erros": erros,
        "avisos": avisos,
        "total_talhoes": total,
        "talhoes_incompletos": incompletos,
    }
    documento["status_validacao"] = "OK" if not erros else "ATENCAO"
    documento["avisos"] = avisos
    return documento


def documento_valido(documento):
    return bool((documento or {}).get("validacao", {}).get("valido"))
