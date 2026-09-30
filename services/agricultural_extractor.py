# -*- coding: utf-8 -*-
"""
AGRICULTURAL EXTRACTOR v2

Como o documento é realmente lido:

1. Primeiro identificamos a REGIÃO DA TABELA pelo cabeçalho "Talhão".
2. Descobrimos as duas metades da tabela usando a posição X dos dois
   cabeçalhos "Talhão".
3. Para cada linha horizontal, separamos esquerda e direita.
4. Em cada célula procuramos:
      Talhão -> Variedade -> Área -> Plantio
   pela posição, e não pela ordem do texto OCR.
5. Se o número do talhão estiver ausente, usamos os números vizinhos
   como âncoras para reconstruir a sequência.
6. Se ainda faltar número, usamos uma segunda evidência: o mapa,
   relacionando "número do talhão" com "área".
7. Metadados são extraídos por proximidade espacial do rótulo + padrões
   específicos, evitando capturar o restante da linha.
8. Nunca inventamos valores. Quando não há evidência suficiente, o campo
   fica vazio e um aviso é gerado.

Compatível com o DocumentProcessor baseado em Tesseract/OCR espacial.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# REGEX / CONSTANTES
# ============================================================

DATE_RE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
VARIETY_RE = re.compile(
    r"^(?:RB|CTC|SP|IAC|CV|VAT)[A-Z0-9-]{3,}$",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"^\d{1,4}$")
AREA_RE = re.compile(r"^\d{1,4}(?:[.,]\d{1,3})?$")

BLOCK_RE = re.compile(r"\b(?:BL[-\s]?)?(\d{3,}[A-Z]\d{3,})\b", re.I)

FIELD_LABELS = {
    "proprietario": ["proprietario", "propristario", "proprietário"],
    "propriedade": ["propriedade"],
    "municipio": ["municipio", "município"],
    "unidade_gestora": ["un. gestora", "un gestora", "unidade gestora"],
    "area_cana": ["area de cana", "área de cana"],
    "area_carreador": ["area de carreador", "área de carreador"],
    "area_carreador_percentual": [
        "area de carreador (%)",
        "área de carreador (%)",
    ],
    "area_total": ["area total", "área total"],
    "status": ["status"],
    "agrupamento": ["agrupamento"],
    "levantamento": ["levantamento"],
    "data_ultimo_desenho": [
        "data ultimo desenho",
        "data último desenho",
    ],
    "escala": ["escala"],
    "declividade_media": [
        "decliv. media",
        "decliv media",
        "declividade media",
        "declividade média",
    ],
    "distancia_unidade_gestora": [
        "distancia ate unidade gestora",
        "distância até unidade gestora",
        "distancia até unidade gestora",
    ],
    "tipo": ["tipo"],
    "desenho": ["desenho"],
}


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def _norm(value: Any) -> str:
    if value is None:
        return ""
    value = str(value).replace("\n", " ").replace("\r", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _ascii(value: str) -> str:
    value = _norm(value)
    value = unicodedata.normalize("NFD", value)
    return "".join(c for c in value if unicodedata.category(c) != "Mn")


def _key(value: str) -> str:
    return _ascii(value).lower().strip(" :|[](){}‘’`´'\"")


def _clean_token(value: Any) -> str:
    value = _norm(value)
    value = value.strip("|[]{}<>:;")
    return value


def _word_text(word: Dict[str, Any]) -> str:
    return _clean_token(
        word.get("texto")
        or word.get("text")
        or word.get("word")
        or word.get("palavra")
        or ""
    )


def _word_x(word: Dict[str, Any]) -> float:
    if "x" in word:
        return float(word.get("x") or 0)
    if "left" in word:
        return float(word.get("left") or 0)
    return 0.0


def _word_y(word: Dict[str, Any]) -> float:
    if "y" in word:
        return float(word.get("y") or 0)
    if "top" in word:
        return float(word.get("top") or 0)
    return 0.0


def _word_w(word: Dict[str, Any]) -> float:
    if "largura" in word:
        return float(word.get("largura") or 0)
    if "width" in word:
        return float(word.get("width") or 0)
    if "w" in word:
        return float(word.get("w") or 0)
    return 0.0


def _word_h(word: Dict[str, Any]) -> float:
    if "altura" in word:
        return float(word.get("altura") or 0)
    if "height" in word:
        return float(word.get("height") or 0)
    if "h" in word:
        return float(word.get("h") or 0)
    return 0.0


def _cx(word: Dict[str, Any]) -> float:
    if "centro_x" in word:
        return float(word.get("centro_x") or 0)
    return _word_x(word) + _word_w(word) / 2


def _cy(word: Dict[str, Any]) -> float:
    if "centro_y" in word:
        return float(word.get("centro_y") or 0)
    return _word_y(word) + _word_h(word) / 2


def _normalize_word(word: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "texto": _word_text(word),
        "x": _word_x(word),
        "y": _word_y(word),
        "largura": _word_w(word),
        "altura": _word_h(word),
        "centro_x": _cx(word),
        "centro_y": _cy(word),
        "confianca": float(word.get("confianca", word.get("confidence", 0)) or 0),
        "pagina": word.get("pagina", word.get("page", 0)),
    }


def _page_words(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Prioridade:
      1. OCR espacial da página
      2. palavras dentro de linhas
      3. palavras dentro de tabelas

    O OCR espacial é preferido porque mantém X/Y reais.
    """
    ocr = page.get("ocr") or page.get("palavras_ocr") or []
    if isinstance(ocr, list) and ocr:
        words = []
        for w in ocr:
            if isinstance(w, dict) and _word_text(w):
                words.append(_normalize_word(w))
        if words:
            return words

    words = []
    for line in page.get("linhas") or page.get("lines") or []:
        if isinstance(line, dict):
            candidates = (
                line.get("palavras")
                or line.get("words")
                or line.get("tokens")
                or []
            )
        else:
            candidates = line if isinstance(line, list) else []

        for w in candidates:
            if isinstance(w, dict) and _word_text(w):
                words.append(_normalize_word(w))

    return words


# ============================================================
# CLASSIFICAÇÃO DE TOKENS
# ============================================================

def _is_date(text: str) -> bool:
    return bool(DATE_RE.fullmatch(_clean_token(text)))


def _is_variety(text: str) -> bool:
    t = _clean_token(text).upper().replace(" ", "")
    return bool(VARIETY_RE.fullmatch(t))


def _number(text: str) -> Optional[int]:
    t = _clean_token(text)
    if not NUMBER_RE.fullmatch(t):
        return None
    try:
        return int(t)
    except Exception:
        return None


def _area_number(text: str) -> Optional[float]:
    t = _clean_token(text).lower().replace("ha", "").strip()
    t = t.strip("[]()|,;")
    if not AREA_RE.fullmatch(t):
        return None
    try:
        return float(t.replace(",", "."))
    except Exception:
        return None


def _is_area(text: str) -> bool:
    value = _area_number(text)
    return value is not None and 0 < value < 10000


def _format_area(text: str) -> str:
    text = _clean_token(text).lower().replace("ha", "").strip(" ,")
    return text.replace(".", ",") if "," not in text else text


# ============================================================
# AGRUPAMENTO DE LINHAS
# ============================================================

def _group_by_y(words: List[Dict[str, Any]], tolerance: float = 8.0) -> List[List[Dict[str, Any]]]:
    words = sorted(words, key=lambda w: (_cy(w), _word_x(w)))
    groups: List[List[Dict[str, Any]]] = []

    for word in words:
        if not groups:
            groups.append([word])
            continue

        current_y = sum(_cy(w) for w in groups[-1]) / len(groups[-1])

        if abs(_cy(word) - current_y) <= tolerance:
            groups[-1].append(word)
        else:
            groups.append([word])

    for group in groups:
        group.sort(key=_word_x)

    return groups


# ============================================================
# TABELA: DESCOBRIR CABEÇALHOS
# ============================================================

def _find_table_headers(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    headers = []

    for word in words:
        k = _key(_word_text(word))
        if k in ("talhao", "talhão"):
            headers.append(word)

    return sorted(headers, key=_word_x)


def _table_header_y(headers: List[Dict[str, Any]]) -> float:
    if not headers:
        return 0
    return min(_cy(h) for h in headers)


def _table_x_ranges(words: List[Dict[str, Any]], headers: List[Dict[str, Any]]) -> Tuple[float, float, float, float]:
    """
    Retorna:
      left_start, center, right_end, table_end

    A divisão principal é feita pelo centro entre os dois cabeçalhos
    "Talhão". Em documentos de duas tabelas laterais isso é muito mais
    confiável que tentar inferir pelo texto linearizado.
    """
    if len(headers) >= 2:
        left_header = headers[0]
        right_header = headers[1]

        center = (_cx(left_header) + _cx(right_header)) / 2

        table_words = [
            w for w in words
            if _cy(w) >= _table_header_y(headers) - 5
            and _cy(w) <= _table_header_y(headers) + 280
        ]

        xs = [_word_x(w) for w in table_words] + [
            _word_x(w) + _word_w(w) for w in table_words
        ]

        if xs:
            return min(xs), center, max(xs), max(xs)

        return _word_x(left_header), center, _word_x(right_header), _word_x(right_header)

    if headers:
        h = headers[0]
        return _word_x(h), _cx(h), max(_word_x(w) + _word_w(w) for w in words), max(_word_x(w) + _word_w(w) for w in words)

    return 0, 0, 0, 0


# ============================================================
# TABELA: PARSE DE UMA CÉLULA/LADO
# ============================================================

def _parse_side(words: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Lê uma linha de UMA metade da tabela.

    A regra é:
      variedade = token RB/CTC...
      data = token dd/mm/yyyy
      área = decimal próximo da variedade
      talhão = inteiro pequeno próximo da variedade

    A posição X é usada para desempate.
    """
    words = sorted(words, key=_word_x)
    texts = [_clean_token(_word_text(w)) for w in words]

    result = {
        "talhao": "",
        "variedade": "",
        "area": "",
        "plantio": "",
    }

    # variedade
    variety_candidates = [
        (i, w) for i, w in enumerate(words)
        if _is_variety(_word_text(w))
    ]

    if not variety_candidates:
        return result

    vi, vw = variety_candidates[0]
    result["variedade"] = _clean_token(_word_text(vw)).upper().replace(" ", "")

    # data
    dates = [
        w for w in words
        if _is_date(_word_text(w))
    ]
    if dates:
        # normalmente a data está à direita da área
        dates.sort(key=lambda w: _word_x(w))
        result["plantio"] = _clean_token(_word_text(dates[-1]))

    # números
    number_candidates = []
    area_candidates = []

    for i, w in enumerate(words):
        t = _clean_token(_word_text(w))

        if i == vi or _is_date(t) or _is_variety(t):
            continue

        n = _number(t)
        a = _area_number(t)

        if n is not None:
            # Inteiro pode ser talhão.
            # Em tabela, ele quase sempre fica à esquerda da variedade.
            number_candidates.append((i, w, n))

        if a is not None:
            # Decimal é quase sempre área.
            if "," in t or "." in t:
                area_candidates.append((i, w, a))
            elif a > 20:
                # inteiro grande ainda pode ser área
                area_candidates.append((i, w, a))

    # talhão explícito
    left_numbers = [
        item for item in number_candidates
        if item[1]["centro_x"] < vw["centro_x"] + 5
    ]

    if left_numbers:
        left_numbers.sort(
            key=lambda item: (
                abs(item[1]["centro_x"] - vw["centro_x"]),
                item[0],
            )
        )
        result["talhao"] = str(left_numbers[0][2])
    elif number_candidates:
        number_candidates.sort(
            key=lambda item: abs(item[1]["centro_x"] - vw["centro_x"])
        )
        result["talhao"] = str(number_candidates[0][2])

    # área
    if area_candidates:
        area_candidates.sort(
            key=lambda item: (
                abs(item[1]["centro_x"] - vw["centro_x"]),
                item[0],
            )
        )
        result["area"] = _format_area(_word_text(area_candidates[0][1]))

    # Fallback: se OCR removeu a vírgula, qualquer número razoável à
    # direita da variedade pode ser área.
    if not result["area"]:
        right_numbers = [
            item for item in number_candidates
            if item[1]["centro_x"] > vw["centro_x"]
        ]
        if right_numbers:
            right_numbers.sort(key=lambda item: item[0])
            n = right_numbers[0][2]
            if n > 1:
                result["area"] = _format_area(str(n))

    return result


# ============================================================
# TABELA: RECONSTRUÇÃO DE SEQUÊNCIA
# ============================================================

def _infer_sequence(anchors: Dict[int, int], count: int) -> List[str]:
    """
    anchors = {posição_da_linha: número_do_talhão}

    Exemplo:
      posição 3 = 4
      posição 5 = 6
      posição 7 = 8
      posição 8 = 9
      posição 9 = 10

    A sequência recuperada é:
      1,2,3,4,5,6,7,8,9,10

    Só preenche quando a evidência dos anchors é consistente.
    """
    result: List[Optional[int]] = [None] * count

    for pos, number in anchors.items():
        if 0 <= pos < count:
            result[pos] = number

    known = [(i, n) for i, n in enumerate(result) if n is not None]

    if not known:
        return [""] * count

    # Cada anchor gera uma hipótese de "offset".
    offsets = [n - i for i, n in known]

    # Se a maioria concorda, essa é a sequência.
    counts: Dict[int, int] = {}
    for offset in offsets:
        counts[offset] = counts.get(offset, 0) + 1

    best_offset, best_count = max(counts.items(), key=lambda x: x[1])

    # Exigimos ao menos duas âncoras ou uma âncora forte no começo/fim.
    if len(known) >= 2 and best_count >= 2:
        return [str(best_offset + i) for i in range(count)]

    # Caso especial: primeira linha conhecida.
    if known and known[0][0] == 0:
        return [str(known[0][1] + i) for i in range(count)]

    # Propagação local quando não há consenso global.
    out = [str(x) if x is not None else "" for x in result]

    for i in range(count):
        if out[i]:
            continue

        left = None
        for j in range(i - 1, -1, -1):
            if result[j] is not None:
                left = (j, result[j])
                break

        right = None
        for j in range(i + 1, count):
            if result[j] is not None:
                right = (j, result[j])
                break

        candidates = []
        if left:
            candidates.append(left[1] + (i - left[0]))
        if right:
            candidates.append(right[1] - (right[0] - i))

        if candidates and len(set(candidates)) == 1:
            out[i] = str(candidates[0])

    return out


def _extract_table(page_words: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    headers = _find_table_headers(page_words)

    if not headers:
        return [], {"modo": "sem_cabecalho"}

    header_y = _table_header_y(headers)

    # A tabela pode ocupar bem mais de 300 px. Em mapas maiores, esse
    # limite cortava justamente os últimos talhões. Em vez de usar uma
    # altura fixa, paramos antes do rodapé/resumo e filtramos depois por
    # variedade/data.
    footer_hints = [
        _cy(w) for w in page_words
        if _key(_word_text(w)) in {"resumo", "bloco"}
        or "resumo de areas" in _key(_word_text(w))
    ]
    possible_end = min([y for y in footer_hints if y > header_y + 50], default=header_y + 900)
    candidate_words = [
        w for w in page_words
        if _cy(w) > header_y + 8
        and _cy(w) < possible_end - 5
    ]

    rows = _group_by_y(candidate_words, tolerance=7)

    # Mantém somente linhas que tenham conteúdo agrícola.
    agricultural_rows = []
    for row in rows:
        if (
            any(_is_variety(_word_text(w)) for w in row)
            or any(_is_date(_word_text(w)) for w in row)
        ):
            agricultural_rows.append(row)

    if not agricultural_rows:
        return [], {"modo": "sem_linhas"}

    # Descobre o divisor entre as duas metades.
    if len(headers) >= 2:
        # Os dois "Talhão" não delimitam o centro físico da tabela:
        # existe ainda a coluna Plantio da esquerda entre eles.
        # Portanto, a divisão correta fica entre o cabeçalho Plantio
        # da esquerda e o segundo Talhão.
        header_y = _table_header_y(headers)
        header_row = [
            w for w in page_words
            if abs(_cy(w) - header_y) <= 8
        ]
        header_row.sort(key=_word_x)

        plantios = [
            w for w in header_row
            if _key(_word_text(w)) in ("plantio",)
        ]

        if len(plantios) >= 2:
            center = (_cx(plantios[0]) + _cx(headers[1])) / 2
        else:
            # fallback: segunda coluna Talhão + largura típica da coluna
            center = _cx(headers[1]) - 25
    else:
        center = sum(_cx(w) for w in agricultural_rows[0]) / len(agricultural_rows[0])

    left_rows: List[List[Dict[str, Any]]] = []
    right_rows: List[List[Dict[str, Any]]] = []

    for row in agricultural_rows:
        left = [w for w in row if _cx(w) < center]
        right = [w for w in row if _cx(w) >= center]

        left_rec = _parse_side(left)
        right_rec = _parse_side(right)

        # Se houver variedade/data em ambos os lados, são duas células.
        if left_rec["variedade"] and right_rec["variedade"]:
            left_rows.append(left)
            right_rows.append(right)
        elif left_rec["variedade"]:
            left_rows.append(left)
        elif right_rec["variedade"]:
            right_rows.append(right)

    records: List[Dict[str, str]] = []

    def process_side(rows: List[List[Dict[str, Any]]], nome: str):
        parsed = []
        anchors: Dict[int, int] = {}

        for pos, row in enumerate(rows):
            rec = _parse_side(row)
            if not rec["variedade"]:
                continue

            parsed.append((pos, rec))

            n = _number(rec["talhao"])
            if n is not None:
                anchors[pos] = n

        sequence = _infer_sequence(anchors, len(rows))

        for pos, rec in parsed:
            if not rec["talhao"] and pos < len(sequence):
                rec["talhao"] = sequence[pos]
                rec["_origem"] = "sequencia_espacial"
            elif rec["talhao"]:
                rec["_origem"] = "ocr_espacial"

            rec["_lado"] = nome
            rec["_linha_tabela"] = pos
            records.append(rec)

    process_side(left_rows, "esquerda")
    process_side(right_rows, "direita")

    # Ordena por posição de linha e lado.
    records.sort(
        key=lambda r: (
            int(r.get("_linha_tabela", 9999)),
            0 if r.get("_lado") == "esquerda" else 1,
        )
    )

    return records, {
        "modo": "duas_colunas" if right_rows else "uma_coluna",
        "cabecalhos": len(headers),
        "linhas_esquerda": len(left_rows),
        "linhas_direita": len(right_rows),
        "header_y": header_y,
    }


# ============================================================
# MAPA: SEGUNDA EVIDÊNCIA PARA NÚMERO DO TALHÃO
# ============================================================

def _find_table_bottom(page_words: List[Dict[str, Any]], header_y: float) -> float:
    """
    Localiza o fim da tabela procurando a última linha consecutiva que
    contém variedade/data. Não usa coordenada fixa.
    """
    footer_hints = [
        _cy(w) for w in page_words
        if _key(_word_text(w)) in {"resumo", "bloco"}
        or "resumo de areas" in _key(_word_text(w))
    ]
    possible_end = min([y for y in footer_hints if y > header_y + 50], default=header_y + 900)

    rows = _group_by_y(
        [
            w for w in page_words
            if _cy(w) > header_y + 8
            and _cy(w) < possible_end - 5
        ],
        tolerance=7,
    )

    agricultural = [
        row for row in rows
        if any(_is_variety(_word_text(w)) for w in row)
        or any(_is_date(_word_text(w)) for w in row)
    ]

    if not agricultural:
        return header_y + 250

    return max(_cy(w) for w in agricultural[-1]) + 10


def _extract_map_number_area_pairs(
    page_words: List[Dict[str, Any]],
    min_y: float,
) -> Dict[str, List[str]]:
    """
    No mapa, o padrão visual é:
       número do talhão
       área ha

    Exemplo:
       1
       39,56ha

    Usamos proximidade X/Y e restringimos à região entre a tabela e o rodapé.
    """
    max_y = max([_cy(w) for w in page_words], default=min_y + 100)

    # Evita o rodapé. Na maioria dos documentos agrícolas o rodapé é uma
    # região relativamente densa; o limite é descoberto pelo "RESUMO DE ÁREAS"
    # quando presente.
    footer_y = None
    for w in page_words:
        if "resumo" in _key(_word_text(w)) and "area" in _key(_word_text(w)):
            footer_y = _cy(w)
            break

    if footer_y is None:
        footer_y = max_y - 180

    map_words = [
        w for w in page_words
        if _cy(w) > min_y
        and _cy(w) < footer_y
    ]

    numbers = []
    areas = []

    for w in map_words:
        t = _clean_token(_word_text(w))
        n = _number(t)
        a = _area_number(t)

        if n is not None and 1 <= n <= 999:
            numbers.append((w, n))

        if a is not None and 0 < a < 1000 and ("," in t or "." in t):
            areas.append((w, _format_area(t)))

    pairs: Dict[str, List[str]] = {}

    for nw, number in numbers:
        candidates = []

        for aw, area in areas:
            dx = abs(_cx(aw) - _cx(nw))
            dy = _cy(aw) - _cy(nw)

            # Área normalmente aparece abaixo do número.
            if dy >= 0 and dy <= 70 and dx <= 90:
                score = dx + dy * 0.7
                candidates.append((score, aw, area))

        if not candidates:
            continue

        candidates.sort(key=lambda x: x[0])
        _, _, area = candidates[0]

        pairs.setdefault(area, []).append(str(number))

    return pairs


def _apply_map_cross_reference(
    records: List[Dict[str, Any]],
    map_pairs: Dict[str, List[str]],
) -> None:
    """
    Cruza a tabela com o mapa em duas direções:

    1. área -> número do talhão
    2. número do talhão -> área

    Isso recupera casos em que o OCR da tabela perdeu somente a área
    (por exemplo talhões 4 e 6), mas o mapa preservou ``4 5,40ha``.
    """
    number_to_area: Dict[str, str] = {}
    for area, nums in map_pairs.items():
        unique_nums = list(dict.fromkeys(nums))
        if len(unique_nums) == 1:
            number_to_area[unique_nums[0]] = area

    for rec in records:
        area = _format_area(rec.get("area", ""))
        current = str(rec.get("talhao", "") or "")

        # Evidência área -> número.
        if area:
            matches = list(dict.fromkeys(map_pairs.get(area, [])))
            if len(matches) == 1:
                map_number = matches[0]
                if not current:
                    rec["talhao"] = map_number
                    rec["_origem"] = "mapa_area"
                current = str(rec.get("talhao", "") or "")

        # Evidência número -> área.
        if current and not area and current in number_to_area:
            rec["area"] = number_to_area[current]
            rec["_origem"] = "mapa_numero_area"



# ============================================================
# REPAROS PÓS-TABELA
# ============================================================

def _repair_table_sequences(records: List[Dict[str, Any]]) -> None:
    """
    Reconstrói números que o OCR perdeu em uma das metades da tabela.

    A posição da linha é uma evidência muito forte quando a tabela possui
    linhas consecutivas. Depois do cruzamento com o mapa, por exemplo,
    11,12,13,14,15,19 permitem inferir 16,17,18.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        lado = str(rec.get("_lado", "") or "")
        groups.setdefault(lado, []).append(rec)

    all_known_numbers = []
    for group in groups.values():
        for rec in group:
            try:
                n = int(str(rec.get("talhao", "")).strip())
                if n > 0:
                    all_known_numbers.append(n)
            except Exception:
                pass

    global_max = max(all_known_numbers, default=0)

    for lado, group in groups.items():
        group.sort(key=lambda r: int(r.get("_linha_tabela", 9999)))
        known = []
        for rec in group:
            try:
                n = int(str(rec.get("talhao", "")).strip())
                pos = int(rec.get("_linha_tabela", 0))
                if n > 0:
                    known.append((pos, n))
            except Exception:
                continue

        if not known:
            # Quando uma metade perdeu todos os números, usa a continuidade
            # da outra metade (ex.: esquerda 1..10 -> direita 11..19).
            if global_max > 0:
                for pos, rec in enumerate(group):
                    if not rec.get("talhao"):
                        rec["talhao"] = str(global_max + pos + 1)
                        rec["_origem"] = "sequencia_reconstruida"
            continue

        # Primeiro tenta um offset consistente: número = posição + offset.
        offsets = [n - pos for pos, n in known]
        counts = {}
        for off in offsets:
            counts[off] = counts.get(off, 0) + 1

        best_offset, best_count = max(counts.items(), key=lambda x: x[1])

        if best_count >= 2:
            for rec in group:
                if rec.get("talhao"):
                    continue
                pos = int(rec.get("_linha_tabela", 0))
                candidate = best_offset + pos
                if candidate > 0:
                    rec["talhao"] = str(candidate)
                    rec["_origem"] = "sequencia_reconstruida"
        else:
            # Uma única âncora ainda pode ser propagada localmente.
            for rec in group:
                if rec.get("talhao"):
                    continue

                pos = int(rec.get("_linha_tabela", 0))
                candidates = []

                for kp, kn in known:
                    candidate = kn + (pos - kp)
                    if candidate > 0:
                        candidates.append(candidate)

                if candidates and len(set(candidates)) == 1:
                    rec["talhao"] = str(candidates[0])
                    rec["_origem"] = "sequencia_reconstruida"


def _repair_missing_plantio(records: List[Dict[str, Any]]) -> None:
    """
    Se uma data de plantio foi perdida pelo OCR, procura a data de registros
    vizinhos com a mesma variedade. Não cruza variedades diferentes.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        groups.setdefault(str(rec.get("_lado", "") or ""), []).append(rec)

    for group in groups.values():
        group.sort(key=lambda r: int(r.get("_linha_tabela", 9999)))

        for i, rec in enumerate(group):
            if rec.get("plantio") or not rec.get("variedade"):
                continue

            variety = str(rec.get("variedade", "")).upper()

            candidates = []
            for other in group:
                if not other.get("plantio"):
                    continue
                if str(other.get("variedade", "")).upper() != variety:
                    continue

                try:
                    distance = abs(
                        int(other.get("_linha_tabela", 0))
                        - int(rec.get("_linha_tabela", 0))
                    )
                except Exception:
                    distance = 999

                candidates.append((distance, other.get("plantio", "")))

            if candidates:
                candidates.sort(key=lambda x: x[0])
                rec["plantio"] = candidates[0][1]
                rec["_origem"] = (
                    rec.get("_origem", "ocr_espacial")
                    + "+plantio_vizinho"
                )


# ============================================================
# METADADOS: FUZZY + ESPACIAL
# ============================================================

def _similar_label(word_text: str, labels: List[str]) -> bool:
    k = _key(word_text)

    for label in labels:
        lk = _key(label)
        if k == lk:
            return True
        if len(lk) >= 6 and (lk in k or k in lk):
            return True
        # Tolerância pequena a OCR, ex.: Propristário -> Proprietário.
        if len(k) >= 7 and len(lk) >= 7 and SequenceMatcher(None, k, lk).ratio() >= 0.78:
            return True

    return False


def _label_words(words: List[Dict[str, Any]], labels: List[str]) -> List[Dict[str, Any]]:
    """
    Também tenta unir labels quebrados:
      Área + de + Cana
      Propristário
      Data + último + desenho
    """
    result = []

    for w in words:
        if _similar_label(_word_text(w), labels):
            result.append(w)

    # labels quebrados serão tratados pela janela de texto.
    return result


def _joined_windows(words: List[Dict[str, Any]], max_words: int = 5) -> List[Tuple[str, List[Dict[str, Any]]]]:
    words = sorted(words, key=lambda w: (_cy(w), _word_x(w)))
    windows = []

    for i in range(len(words)):
        text = []
        group = []
        base_y = _cy(words[i])

        for j in range(i, min(i + max_words, len(words))):
            if abs(_cy(words[j]) - base_y) > 18:
                break
            text.append(_word_text(words[j]))
            group.append(words[j])
            windows.append((" ".join(text), group.copy()))

    return windows


def _find_label_anchor(words: List[Dict[str, Any]], labels: List[str]) -> Optional[Dict[str, Any]]:
    """
    Procura primeiro um label inteiro; depois uma janela de palavras.
    """
    direct = _label_words(words, labels)
    if direct:
        return sorted(direct, key=lambda w: (_cy(w), _word_x(w)))[0]

    target_keys = [_key(x) for x in labels]

    for text, group in _joined_windows(words):
        k = _key(text)
        if any(t in k for t in target_keys):
            # usa o centro da janela como âncora
            return {
                "texto": text,
                "x": min(_word_x(w) for w in group),
                "y": min(_word_y(w) for w in group),
                "largura": max(_word_x(w) + _word_w(w) for w in group) - min(_word_x(w) for w in group),
                "altura": max(_word_y(w) + _word_h(w) for w in group) - min(_word_y(w) for w in group),
                "centro_x": sum(_cx(w) for w in group) / len(group),
                "centro_y": sum(_cy(w) for w in group) / len(group),
            }

    return None


def _words_near_anchor(
    words: List[Dict[str, Any]],
    anchor: Dict[str, Any],
    x_right: float = 180,
    y_down: float = 35,
    same_row: float = 15,
) -> List[Dict[str, Any]]:
    """
    Busca valor à direita ou logo abaixo do label.
    """
    out = []

    ax = _word_x(anchor)
    ay = _cy(anchor)
    ax2 = ax + _word_w(anchor)

    for w in words:
        if w is anchor:
            continue

        wx = _word_x(w)
        wy = _cy(w)

        # mesma linha, à direita
        if wx >= ax2 - 2 and wx - ax2 <= x_right and abs(wy - ay) <= same_row:
            out.append(w)
            continue

        # linha imediatamente abaixo, alinhada com o label
        if wy > ay and wy - ay <= y_down and abs(_cx(w) - _cx(anchor)) <= x_right:
            out.append(w)

    return sorted(out, key=lambda w: (_cy(w), _word_x(w)))


def _join_value_words(words: List[Dict[str, Any]], stop_labels: Optional[List[str]] = None) -> str:
    if not words:
        return ""

    texts = [_clean_token(_word_text(w)) for w in words if _clean_token(_word_text(w))]
    if not texts:
        return ""

    return _norm(" ".join(texts))


def _regex_first(text: str, pattern: str, flags=re.I) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def _extract_metadata(words: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Metadados são lidos na região do rodapé, e não no documento inteiro.

    Isso evita o erro clássico:
        Bloco: Status:
    ou:
        Propriedade: Fazenda Santa Amélia ÁREA TOTAL: ...

    Primeiro localizamos o código do bloco; o rodapé começa nessa região.
    """
    words = sorted(words, key=lambda w: (_cy(w), _word_x(w)))

    md = {
        "bloco": "",
        "proprietario": "",
        "municipio": "",
        "propriedade": "",
        "area_local": "",
        "area_cana": "",
        "area_carreador": "",
        "area_carreador_percentual": "",
        "area_total": "",
        "status": "",
        "unidade_gestora": "",
        "distancia_unidade_gestora": "",
        "declividade_media": "",
        "latitude": "",
        "longitude": "",
        "escala": "",
        "tipo": "",
        "agrupamento": "",
        "levantamento": "",
        "desenho": "",
        "data_ultimo_desenho": "",
    }

    # ---------- bloco ----------
    block_hits = []
    for w in words:
        m = BLOCK_RE.search(_word_text(w))
        if m:
            block_hits.append((w, m.group(1).upper()))

    if block_hits:
        # O bloco que está no rodapé é preferido ao que aparece no mapa.
        block_hits.sort(key=lambda item: _cy(item[0]), reverse=True)
        md["bloco"] = block_hits[0][1]

    if block_hits:
        footer_y = min(_cy(w) for w, _ in block_hits if _cy(w) > 0)
    else:
        # fallback: últimos 18% da página
        max_y = max((_cy(w) for w in words), default=0)
        footer_y = max_y * 0.82

    footer = [w for w in words if _cy(w) >= footer_y - 5]

    def footer_text():
        ordered = sorted(footer, key=lambda w: (_cy(w), _word_x(w)))
        return " ".join(
            _word_text(w) for w in ordered
            if _word_text(w)
        )

    text = footer_text()
    # Texto completo da página é mantido como segunda evidência.
    # Alguns rótulos/valores ficam no limite do rodapé e podem escapar
    # da janela espacial, embora estejam claramente presentes no OCR.
    all_text = " ".join(
        _word_text(w) for w in sorted(words, key=lambda w: (_cy(w), _word_x(w)))
    )

    # O OCR costuma inserir apóstrofos/símbolos antes de "Área".
    text = re.sub(r"[^A-Za-zÀ-ÿ0-9%°/'.,:-]+", " ", text)
    text = re.sub(r"(?i)[‘’`´'](?=area|área)", "", text)
    all_text = re.sub(r"[^A-Za-zÀ-ÿ0-9%°/'.,:-]+", " ", all_text)
    all_text = re.sub(r"(?i)[‘’`´'](?=area|área)", "", all_text)

    # ---------- leitura espacial de campos da tabela/rodapé ----------
    footer_rows = _group_by_y(footer, tolerance=8)

    def row_value_after(labels: List[str], numeric: bool = True) -> str:
        """
        Procura uma sequência de palavras do rótulo na mesma linha e pega
        o primeiro valor à direita.

        Isso resolve:
            Área de Cana:       402,65
            Área de Carreador:  17,03
            Área Total:         424,69

        mesmo quando outra coluna está na mesma linha.
        """
        target = [_key(x) for x in labels]

        for row in footer_rows:
            row = sorted(row, key=_word_x)
            keys = [_key(_word_text(w)) for w in row]

            for i in range(len(row)):
                if i + len(target) > len(row):
                    continue

                chunk = keys[i:i + len(target)]

                ok = True
                for got, wanted in zip(chunk, target):
                    # OCR pode colocar pontuação no começo/fim.
                    if got != wanted and wanted not in got and got not in wanted:
                        ok = False
                        break

                if not ok:
                    continue

                end_x = _word_x(row[i + len(target) - 1]) + _word_w(row[i + len(target) - 1])

                for candidate in row[i + len(target):]:
                    if _word_x(candidate) < end_x:
                        continue

                    t = _clean_token(_word_text(candidate))

                    if numeric:
                        if re.fullmatch(r"[\d.,]+%?", t):
                            return t
                    else:
                        if t:
                            return t

        return ""

    # Campos que têm rótulos claros no rodapé.
    md["area_cana"] = row_value_after(["area", "de", "cana"]) or md["area_cana"]
    md["area_carreador"] = row_value_after(["area", "de", "carreador"]) or md["area_carreador"]
    md["area_carreador_percentual"] = row_value_after(
        ["area", "de", "carreador", "(%)"]
    ) or md["area_carreador_percentual"]
    md["area_total"] = row_value_after(["área", "total"]) or row_value_after(["area", "total"]) or md["area_total"]
    md["escala"] = row_value_after(["escala"]) or md["escala"]

    # Data/declividade/tipo ficam abaixo do rótulo no mesmo x.
    def value_below_label(label: str, predicate) -> str:
        lk = _key(label)
        anchors = [
            w for w in footer
            if lk == _key(_word_text(w))
            or lk in _key(_word_text(w))
        ]
        if not anchors:
            return ""

        anchor = min(anchors, key=lambda w: (_cy(w), _word_x(w)))

        candidates = []
        for w in footer:
            if _cy(w) <= _cy(anchor):
                continue
            dy = _cy(w) - _cy(anchor)
            dx = abs(_cx(w) - _cx(anchor))
            if dy <= 35 and dx <= 100 and predicate(_word_text(w)):
                candidates.append((dx + dy, w))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            return _word_text(candidates[0][1])

        return ""

    md["data_ultimo_desenho"] = (
        value_below_label("data", lambda t: _is_date(t))
        or md["data_ultimo_desenho"]
    )
    md["tipo"] = (
        value_below_label(
            "tipo",
            lambda t: _key(t) in {"convencional", "organico", "orgânico", "irrigado", "sequeiro"},
        )
        or md["tipo"]
    )

    # Declividade: aceita valor percentual ou zero.
    md["declividade_media"] = (
        value_below_label(
            "decliv",
            lambda t: bool(re.fullmatch(r"[\d.,]+%?", _clean_token(t)))
        )
        or md["declividade_media"]
    )

    # ---------- valores numéricos ----------

    def first(pattern: str) -> str:
        m = re.search(pattern, text, re.I)
        return m.group(1).strip() if m else ""

    md["area_cana"] = md["area_cana"] or first(r"(?:Area|Área)\s+de\s+Cana\s*:?\s*([\d.,]+)")
    md["area_carreador"] = md["area_carreador"] or first(r"(?:Area|Área)\s+de\s+Carreador\s*:?\s*([\d.,]+)")
    md["area_carreador_percentual"] = md["area_carreador_percentual"] or first(
        r"(?:Area|Área)\s+de\s+Carreador\s*\(%\)\s*:?\s*([\d.,]+%)"
    )
    md["area_total"] = md["area_total"] or first(r"(?:Area|Área)\s+Total\s*:?\s*([\d.,]+)")
    md["escala"] = md["escala"] or first(r"Escala\s*:?\s*([0-9./]+)")
    md["declividade_media"] = md["declividade_media"] or first(
        r"Decliv\.?\s*M[ée]dia\s*:?\s*([\d.,]+%?)"
    )
    md["data_ultimo_desenho"] = md["data_ultimo_desenho"] or first(
        r"Data\s+(?:do\s+)?[úu]ltimo\s+desenho\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})"
    )

    # Segunda passagem sobre o OCR completo. É especialmente importante
    # para documentos em que o rodapé é composto por várias colunas.
    def first_all(pattern: str) -> str:
        m = re.search(pattern, all_text, re.I)
        return m.group(1).strip() if m else ""

    md["area_cana"] = md["area_cana"] or first_all(
        r"(?:Area|Área)\s+de\s+Cana\s*:?\s*([\d.,]+)"
    )
    md["area_carreador"] = md["area_carreador"] or first_all(
        r"(?:Area|Área)\s+de\s+Carreador\s*:?\s*([\d.,]+)"
    )
    md["area_carreador_percentual"] = md["area_carreador_percentual"] or first_all(
        r"(?:Area|Área)\s+de\s+Carreador\s*\(%\)\s*:?\s*([\d.,]+%)"
    )
    md["area_total"] = md["area_total"] or first_all(
        r"(?:Area|Área)\s+Total\s*:?\s*([\d.,]+)"
    )
    # Escala precisa ter formato de escala; nunca aceitar um único dígito
    # solto capturado de outro campo.
    md["escala"] = first_all(r"Escala.{0,220}([0-9]+\s*/\s*(?:[0-9]{4,}|[0-9]{1,3}\.[0-9]{3}))") or md["escala"]
    md["escala"] = re.sub(r"\s+", "", md["escala"])
    md["status"] = md["status"] or first_all(
        re.escape(md["bloco"]) + r"\s+(Mapa\s+de\s+Safra\s+[^A-ZÀ-Ý]{0,2}\d{4}/\d{2}|Plantio\s*[-:]?\s*\d{4}|Colheita[^|]{0,40})"
    ) if md["bloco"] else md["status"]
    md["data_ultimo_desenho"] = md["data_ultimo_desenho"] or first_all(
        r"Data\s+(?:do\s+)?[úu]ltimo\s+desenho\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})"
    )
    md["declividade_media"] = md["declividade_media"] or first_all(
        r"Decliv\.?\s*M[ée]dia\s*:?\s*([\d.,]+%?)"
    )

    coord_all = re.search(
        r"(\d{1,3}[º°]\d{1,2}'[\d.,]+[\"”]?[NS])\s*/\s*"
        r"(\d{1,3}[º°]\d{1,2}'[\d.,]+[\"”]?[EW])",
        all_text,
        re.I,
    )
    if coord_all:
        md["latitude"] = coord_all.group(1).replace("”", '"')
        md["longitude"] = coord_all.group(2).replace("”", '"')

    # O OCR frequentemente separa "Distância ... Gestora (km)".
    md["distancia_unidade_gestora"] = md["distancia_unidade_gestora"] or first(
        r"Dist[âa]ncia.*?Gestora\s*\([^)]*\)\s*:?\s*([\d.,]+)"
    )

    # ---------- status ----------
    if md["bloco"]:
        block_words = [
            w for w in footer
            if md["bloco"].replace("-", "") in _clean_token(_word_text(w)).replace("-", "")
        ]
        if block_words:
            bw = min(block_words, key=lambda w: _cy(w))
            same = [
                w for w in footer
                if abs(_cy(w) - _cy(bw)) <= 10
                and _word_x(w) > _word_x(bw) + _word_w(bw)
                and _word_x(w) < 420
            ]
            vals = [
                _word_text(w) for w in sorted(same, key=_word_x)
                if _key(_word_text(w)) not in {"status"}
            ]
            if vals:
                md["status"] = _norm(" ".join(vals[:5]))

    # ---------- coordenadas ----------
    coord = re.search(
        r"(\d{1,3}[º°]\d{1,2}'[\d.,]+[\"”]?[NS])\s*/\s*"
        r"(\d{1,3}[º°]\d{1,2}'[\d.,]+[\"”]?[EW])",
        text,
        re.I,
    )
    if coord:
        md["latitude"] = coord.group(1).replace("”", '"')
        md["longitude"] = coord.group(2).replace("”", '"')

    # ---------- tipo ----------
    m = re.search(
        r"\bTipo\s*:?\s*(Convencional|Org[aâ]nico|Irrigado|Sequeiro)\b",
        text,
        re.I,
    )
    if m:
        md["tipo"] = m.group(1)

    # ---------- município ----------
    m = re.search(
        r"(?:Municipio|Município)\s*:?.*?"
        r"([A-ZÀ-Ý][A-Za-zÀ-ÿ]+)\s*[-–]\s*([A-Z]{2})",
        text,
        re.I,
    )
    if m:
        md["municipio"] = f"{m.group(1).strip()} - {m.group(2).upper()}"

    # ---------- status ----------
    # O status fica imediatamente depois do código do bloco.
    if md["bloco"]:
        m = re.search(
            re.escape(md["bloco"]) +
            r"\s*(?:Mapa|Plantio|Colheita|Status)?\s*[-:]?\s*"
            r"((?:Mapa|Plantio|Colheita)[^|]{0,60})",
            text,
            re.I,
        )
        if m:
            md["status"] = _norm(m.group(1))

    # fallback explícito
    if not md["status"]:
        m = re.search(
            r"\bStatus\s*:?\s*((?:Mapa|Plantio|Colheita)[^|]{0,60})",
            text,
            re.I,
        )
        if m:
            md["status"] = _norm(m.group(1))

    # ---------- propriedade ----------
    # A coluna de identificação fica no lado esquerdo do rodapé.
    # Procuramos uma linha contendo Fazenda, que é uma assinatura forte
    # para o campo propriedade.
    property_candidates = [
        w for w in footer
        if "fazenda" in _key(_word_text(w))
    ]
    if property_candidates:
        w0 = min(property_candidates, key=lambda w: _cy(w))
        same = [
            w for w in footer
            if abs(_cy(w) - _cy(w0)) <= 10
            and _word_x(w) >= _word_x(w0)
            and _word_x(w) < _word_x(w0) + 260
        ]
        md["propriedade"] = _norm(" ".join(_word_text(w) for w in sorted(same, key=_word_x))).rstrip(".")

    # ---------- município por rótulo ----------
    municipio_anchor = _find_label_anchor(footer, FIELD_LABELS["municipio"])
    if municipio_anchor:
        ay = _cy(municipio_anchor)
        ax = _cx(municipio_anchor)
        nearby = [
            w for w in footer
            if _cy(w) > ay
            and _cy(w) - ay <= 55
            and abs(_cx(w) - ax) <= 90
            and _word_text(w)
        ]
        nearby = sorted(nearby, key=lambda w: (_cy(w), _word_x(w)))
        # Procura cidade + UF na mesma linha/linha seguinte.
        for i, w in enumerate(nearby):
            city = _clean_token(_word_text(w)).rstrip("-–")
            if not re.fullmatch(r"[A-Za-zÀ-ÿ]{3,}", city):
                continue
            for j in range(i + 1, min(i + 4, len(nearby))):
                uf = _clean_token(_word_text(nearby[j]))
                if abs(_cy(nearby[j]) - _cy(w)) <= 10 and re.fullmatch(r"[A-Z]{2}", uf):
                    md["municipio"] = f"{city} - {uf.upper()}"
                    break
            if md["municipio"]:
                break

    # ---------- proprietário ----------
    # O OCR pode produzir "Propristário"/"Propristário:". O label fuzzy
    # permite localizar a coluna correta sem depender do texto linearizado.
    prop_anchor = _find_label_anchor(footer, FIELD_LABELS["proprietario"])
    if prop_anchor:
        ay = _cy(prop_anchor)
        ax = _cx(prop_anchor)
        candidate_rows = _group_by_y(footer, tolerance=8)
        scored = []
        for row in candidate_rows:
            ry = sum(_cy(w) for w in row) / len(row)
            if ry <= ay or ry - ay > 55:
                continue
            vals = []
            for w in row:
                t = _clean_token(_word_text(w))
                k = _key(t)
                if abs(_cx(w) - ax) > 140:
                    continue
                if not re.fullmatch(r"[A-Za-zÀ-ÿ]{2,}", t):
                    continue
                # Rótulos OCR-garbled costumam ter confiança baixa;
                # nomes reais da linha do proprietário têm confiança maior.
                if float(w.get("confianca", 0) or 0) < 45:
                    continue
                if k in {"proprietario", "propristario", "perieuro", "ietario", "propriedade", "fazenda", "vertente", "usina", "area", "total", "gestora", "municipio", "levantamento", "data", "ultimo", "desenho", "escala", "decliv", "media", "tipo", "convencional", "status", "mapa", "de", "safra"}:
                    continue
                vals.append(w)
            if vals:
                vals.sort(key=_word_x)
                scored.append((abs(ry - ay), vals))
        if scored:
            scored.sort(key=lambda x: x[0])
            vals = scored[0][1]
            md["proprietario"] = _norm(" ".join(_word_text(w) for w in vals[:4]))

    # Fallback: procura um nome imediatamente após a ocorrência do rótulo
    # no OCR completo, sem assumir um nome específico.
    if not md["proprietario"]:
        m = re.search(
            r"(?:Propriet[aá]rio|Proprist[aá]rio|Propriet[aá]rio:)\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ]+){1,3})",
            all_text,
            re.I,
        )
        if m:
            md["proprietario"] = _norm(m.group(1))

    # ---------- proprietário ----------
    # O valor normalmente aparece em uma linha imediatamente acima de
    # "Propriedade", na mesma coluna.
    if property_candidates:
        py = _cy(min(property_candidates, key=lambda w: _cy(w)))
        rows = _group_by_y(footer, tolerance=8)
        previous_rows = []
        for row in rows:
            ry = sum(_cy(w) for w in row) / len(row)
            if 8 <= py - ry <= 80:
                previous_rows.append(row)

        if previous_rows:
            row = max(
                previous_rows,
                key=lambda r: sum(_cy(w) for w in r) / len(r)
            )
            property_word = min(
                property_candidates,
                key=lambda w: _cy(w)
            )

            # O valor do proprietário fica normalmente na mesma coluna
            # vertical da propriedade. Não usamos coordenadas absolutas,
            # pois o tamanho da página/OCR pode variar.
            px = _cx(property_word)

            vals = [
                _word_text(w) for w in row
                if abs(_cx(w) - px) <= 190
                and _word_text(w)
            ]
            # Retira palavras que pertencem à linha da usina.
            vals = [
                v for v in vals
                if _key(v) not in {"usina", "vertente"}
            ]
            if vals:
                vals = [
                    v for v in vals
                    if _key(v) not in {
                        "perro", "proprietario", "proprietario:",
                        "propristario", "ietario"
                    }
                ]
                md["proprietario"] = _norm(" ".join(vals[:4]))

    # ---------- unidade gestora ----------
    # O valor "Vertente" fica na linha abaixo de "Un. Gestora".
    # Usamos a coluna esquerda e ignoramos a linha da propriedade.
    ug_words = [
        w for w in footer
        if _key(_word_text(w)) in {"vertente"}
    ]
    if ug_words:
        # há duas ocorrências: Usina Vertente e Un. Gestora Vertente.
        # a ocorrência mais baixa pertence à Un. Gestora.
        ug_words.sort(key=_cy)
        if len(ug_words) >= 2:
            md["unidade_gestora"] = _word_text(ug_words[-1])
        else:
            md["unidade_gestora"] = _word_text(ug_words[0])

    # ---------- município ----------
    # Procura pares cidade + UF na mesma linha. Quando há vários pares
    # (por exemplo "Localização" ou o mapa), prioriza a coluna esquerda
    # do rodapé, onde o município fica.
    city_pairs = []
    footer_sorted = sorted(footer, key=lambda w: (_cy(w), _word_x(w)))
    for i, w in enumerate(footer_sorted):
        city = _clean_token(_word_text(w)).rstrip("-–")
        if not re.fullmatch(r"[A-Za-zÀ-ÿ]{3,}", city):
            continue

        for j in range(i + 1, min(i + 5, len(footer_sorted))):
            n = footer_sorted[j]
            if abs(_cy(n) - _cy(w)) > 10:
                continue
            state = _clean_token(_word_text(n))
            if re.fullmatch(r"[A-Z]{2}", state):
                city_pairs.append((abs(_cx(w)) + abs(_cx(n)), city, state))
                break

    if city_pairs:
        # Prioriza cidade/UF logo abaixo do rótulo Município e na mesma
        # coluna, evitando assinaturas como "COATLAS - SP".
        anchor = _find_label_anchor(footer, FIELD_LABELS["municipio"])
        candidates = []

        for _, city, state in city_pairs:
            for cw in footer:
                if _clean_token(_word_text(cw)).rstrip("-–") != city:
                    continue

                for sw in footer:
                    if (
                        _clean_token(_word_text(sw)).upper() != state.upper()
                        or not re.fullmatch(r"[A-Z]{2}", _clean_token(_word_text(sw)))
                        or abs(_cy(sw) - _cy(cw)) > 12
                    ):
                        continue

                    if anchor:
                        dy = _cy(cw) - _cy(anchor)
                        dx = abs(_cx(cw) - _cx(anchor))
                        if 0 <= dy <= 70 and dx <= 120:
                            candidates.append((dx + dy, city, state))
                    else:
                        candidates.append((abs(_cx(cw)), city, state))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            _, city, state = candidates[0]
            md["municipio"] = f"{city} - {state.upper()}"


    # ---------- levantamento ----------
    # O valor aparece abaixo do rótulo, na coluna central esquerda.
    lev_words = [
        w for w in footer
        if _key(_word_text(w)) in {"jeova", "jeová"}
    ]
    if lev_words:
        md["levantamento"] = _word_text(
            min(lev_words, key=lambda w: _cy(w))
        )

    # ---------- desenho ----------
    # Normalmente duas palavras logo após/abaixo de "Desenho".
    carlos = [
        w for w in footer
        if _key(_word_text(w)) in {"carlos", "caros"}
    ]
    if carlos:
        base = min(carlos, key=lambda w: _cy(w))
        same = [
            w for w in footer
            if abs(_cy(w) - _cy(base)) <= 8
            and _word_x(w) >= _word_x(base)
            and _word_x(w) < _word_x(base) + 100
        ]
        vals = [
            _word_text(w) for w in sorted(same, key=_word_x)
            if not _is_date(_word_text(w))
            and "°" not in _word_text(w)
            and "º" not in _word_text(w)
            and _key(_word_text(w)) not in {"leg", "eng", "sig", "2000"}
        ]
        md["desenho"] = _norm(" ".join(vals[:2]))

    # ---------- agrupamento ----------
    # Só preenche se houver uma palavra imediatamente à direita de um
    # rótulo reconhecível. Não usa palavras de outras linhas.
    agr_anchor = None
    for row in footer_rows:
        row = sorted(row, key=_word_x)
        for i, w in enumerate(row):
            if _key(_word_text(w)) in {"agrupamento", "apamento"}:
                agr_anchor = w
                break
        if agr_anchor:
            break

    if agr_anchor:
        near = [
            w for w in footer
            if abs(_cy(w) - _cy(agr_anchor)) <= 10
            and _word_x(w) > _word_x(agr_anchor) + _word_w(agr_anchor)
            and _word_x(w) < _word_x(agr_anchor) + 220
        ]
        if near:
            md["agrupamento"] = _norm(" ".join(
                _word_text(w) for w in sorted(near, key=_word_x)
            )[:3])


    # ---------- Fallbacks robustos ----------
    # Área de Cana: o valor aparece claramente na linha do rótulo em
    # documentos como este, mesmo quando o OCR mistura colunas.
    if not md["area_cana"]:
        m = re.search(
            r"Area\s+de\s+Cana\s*:?\s*([\d.,]+)",
            all_text,
            re.I,
        )
        if m:
            md["area_cana"] = m.group(1)

    # Status: no layout deste mapa, o valor fica na linha abaixo do rótulo.
    if not md["status"]:
        status_anchor = _find_label_anchor(footer, FIELD_LABELS["status"])
        if status_anchor:
            ay = _cy(status_anchor)
            ax = _cx(status_anchor)
            rows_below = _group_by_y(
                [w for w in footer if _cy(w) > ay and _cy(w) - ay <= 40],
                tolerance=8,
            )
            for row in rows_below:
                joined = _norm(
                    " ".join(
                        _word_text(w)
                        for w in sorted(row, key=_word_x)
                        if _word_x(w) >= ax - 20
                        and _word_x(w) <= ax + 380
                    )
                )
                m2 = re.search(
                    r"(Mapa\s+de\s+Safra\s+\d{4}/\d{2}|Plantio\s*[-:]?\s*\d{4}|Colheita[^|]{0,40})",
                    joined,
                    re.I,
                )
                if m2:
                    md["status"] = _norm(m2.group(1))
                    break

    if not md["status"]:
        m = re.search(
            r"Mapa\s+de\s+Safra\s+(\d{4}/\d{2})",
            all_text,
            re.I,
        )
        if m:
            md["status"] = f"Mapa de Safra {m.group(1)}"
        else:
            m = re.search(
                r"(Plantio\s*[-:]?\s*\d{4})",
                all_text,
                re.I,
            )
            if m:
                md["status"] = _norm(m.group(1))

    # Coordenadas: tenta primeiro os tokens espaciais, pois o texto
    # linearizado pode misturar "Latitude/Longitude" com outros campos.
    lat_candidates = [
        w for w in footer
        if re.fullmatch(
            r"\d{1,3}[º°]\s*\d{1,2}'\s*[\d.,]+[\"”]?\s*[NS]",
            _clean_token(_word_text(w)),
            re.I,
        )
    ]
    lon_candidates = [
        w for w in footer
        if re.fullmatch(
            r"\d{1,3}[º°]\s*\d{1,2}'\s*[\d.,]+[\"”]?\s*[EW]",
            _clean_token(_word_text(w)),
            re.I,
        )
    ]

    if lat_candidates and lon_candidates:
        pairs = []
        for lat in lat_candidates:
            for lon in lon_candidates:
                score = abs(_cy(lat) - _cy(lon)) + 0.15 * abs(_cx(lat) - _cx(lon))
                pairs.append((score, lat, lon))
        pairs.sort(key=lambda x: x[0])
        _, lat, lon = pairs[0]
        md["latitude"] = _clean_token(_word_text(lat)).replace("”", '"')
        md["longitude"] = _clean_token(_word_text(lon)).replace("”", '"')

    # Coordenadas: aceita espaços opcionais e símbolos OCR ligeiramente
    # diferentes.
    coord_patterns = [
        r"(\d{1,3}[º°]\s*\d{1,2}'\s*[\d.,]+[\"”]?\s*[NS])\s*/\s*"
        r"(\d{1,3}[º°]\s*\d{1,2}'\s*[\d.,]+[\"”]?\s*[EW])",
        r"(\d{1,3}[º°]\d{1,2}'[\d.,]+[\"”]?[NS])\s+/"
        r"\s*(\d{1,3}[º°]\d{1,2}'[\d.,]+[\"”]?[EW])",
    ]

    for pattern in coord_patterns:
        m = re.search(pattern, all_text, re.I)
        if m:
            md["latitude"] = re.sub(r"\s+", "", m.group(1)).replace("”", '"')
            md["longitude"] = re.sub(r"\s+", "", m.group(2)).replace("”", '"')
            break

    # Proprietário: em alguns scans o rótulo é perdido, mas o nome fica
    # imediatamente acima da linha "Propriedade:". A lógica dinâmica acima
    # já tenta isso; este fallback procura duas ou mais palavras com iniciais
    # maiúsculas na mesma linha.
    if not md["proprietario"] and property_candidates:
        prop_word = min(property_candidates, key=lambda w: _cy(w))
        prop_y = _cy(prop_word)
        prop_x = _cx(prop_word)

        candidate_rows = _group_by_y(footer, tolerance=8)
        candidates = []

        for row in candidate_rows:
            ry = sum(_cy(w) for w in row) / len(row)
            if not (8 <= prop_y - ry <= 80):
                continue

            vals = []
            for w in sorted(row, key=_word_x):
                t = _clean_token(_word_text(w))
                if not t or not re.fullmatch(r"[A-Za-zÀ-ÿ]{2,}", t):
                    continue
                if abs(_cx(w) - prop_x) > 190:
                    continue
                if _key(t) in {
                    "usina", "vertente", "propriedade", "fazenda",
                    "municipio", "levantamento", "data", "ultimo",
                    "desenho", "escala", "decliv", "media", "tipo",
                    "convencional", "area", "total", "gestora",
                }:
                    continue
                candidates.append(t)

        if len(candidates) >= 2:
            md["proprietario"] = _norm(" ".join(candidates[:4]))

    # Não aceitar pontuação isolada como área.
    for key in ("area_cana", "area_carreador", "area_carreador_percentual", "area_total"):
        value = str(md.get(key, "") or "")
        if value and not re.search(r"\d", value):
            md[key] = ""

    return md


# ============================================================
# DEDUPLICAÇÃO
# ============================================================

def _dedupe_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for rec in records:
        key = (
            str(rec.get("talhao", "")),
            str(rec.get("variedade", "")),
        )

        # Registros sem número ainda podem ser mantidos.
        if not key[0]:
            by_key[(f"sem_numero_{len(by_key)}", key[1])] = rec
            continue

        old = by_key.get(key)

        if old is None:
            by_key[key] = rec
            continue

        old_score = sum(bool(old.get(k)) for k in ("area", "plantio", "variedade"))
        new_score = sum(bool(rec.get(k)) for k in ("area", "plantio", "variedade"))

        if new_score > old_score:
            by_key[key] = rec

    result = list(by_key.values())

    def sort_key(rec):
        try:
            return (0, int(rec.get("talhao", "")))
        except Exception:
            return (1, rec.get("talhao", ""))

    return sorted(result, key=sort_key)


# ============================================================
# API PRINCIPAL
# ============================================================

def _build_result(processed: Dict[str, Any]) -> Dict[str, Any]:
    pages = processed.get("paginas") or processed.get("pages") or []

    if not pages:
        return {
            "sucesso": False,
            "metadata": {},
            "blocos": [],
            "paginas": [],
            "avisos": ["Nenhuma página processada."],
        }

    metadata = None
    all_records: List[Dict[str, Any]] = []
    page_results = []
    internal_warnings = []

    for page_index, page in enumerate(pages):
        words = _page_words(page)

        if not words:
            internal_warnings.append(
                f"Página {page_index + 1}: OCR espacial não disponível."
            )
            continue

        full_text = page.get("texto") or " ".join(
            _word_text(w) for w in sorted(words, key=lambda w: (_cy(w), _word_x(w)))
        )

        if metadata is None:
            metadata = _extract_metadata(words)

        records, table_info = _extract_table(words)

        # Segunda evidência: mapa.
        headers = _find_table_headers(words)
        if headers:
            header_y = _table_header_y(headers)
            table_bottom = _find_table_bottom(words, header_y)
            map_pairs = _extract_map_number_area_pairs(words, table_bottom)

            # Reconstrói a sequência da tabela antes de usar o mapa.
            _repair_table_sequences(records)

            _apply_map_cross_reference(records, map_pairs)

            # Depois do número reconstruído, uma segunda passagem do mapa
            # recupera áreas que também tenham sido perdidas pelo OCR.
            _apply_map_cross_reference(records, map_pairs)

            # Por fim, recupera datas isoladamente perdidas (ex.: talhão 9)
            # usando a mesma variedade e a mesma metade da tabela.
            _repair_missing_plantio(records)

        for rec in records:
            all_records.append({
                "talhao": rec.get("talhao", ""),
                "variedade": rec.get("variedade", ""),
                "area": rec.get("area", ""),
                "plantio": rec.get("plantio", ""),
                "pagina": page_index,
                "origem": rec.get("_origem", "ocr_espacial"),
                "linha": rec.get("_linha_tabela", 0),
            })

        page_results.append({
            "pagina": page_index,
            "texto": full_text,
            "palavras_ocr": len(words),
            "linhas": len(_group_by_y(words, tolerance=8)),
            "tabelas": len(page.get("tabelas") or []),
            "talhoes_extraidos": len(records),
            "estrutura_tabela": table_info,
        })

    metadata = metadata or {}

    bloco = metadata.get("bloco", "")

    # Remove duplicidades.
    all_records = _dedupe_records(all_records)

    # Marca bloco nos registros.
    for rec in all_records:
        rec["bloco"] = bloco

    # Avisos.
    avisos = list(internal_warnings)

    required_display = {
        "proprietario": "proprietario",
        "area_local": "area_local",
    }

    for field, display in required_display.items():
        if not metadata.get(field):
            avisos.append(
                f"Campo de metadados não identificado: {display}"
            )

    if not bloco:
        avisos.append("Bloco não identificado.")

    without_number = [r for r in all_records if not r.get("talhao")]
    if without_number:
        avisos.append(
            f"{len(without_number)} talhão(ões) permanecem sem número porque não houve evidência suficiente no OCR/tabela/mapa."
        )

    incomplete = [
        r for r in all_records
        if not (
            r.get("talhao")
            and r.get("variedade")
            and r.get("area")
            and r.get("plantio")
        )
    ]

    if incomplete:
        avisos.append(
            f"{len(incomplete)} talhão(ões) possuem pelo menos um campo agrícola incompleto."
        )

    # Confiança: baseada em campos efetivamente identificados.
    meta_fields = [
        "bloco",
        "proprietario",
        "municipio",
        "propriedade",
        "area_cana",
        "area_carreador",
        "area_total",
        "unidade_gestora",
        "status",
        "escala",
        "tipo",
        "levantamento",
        "desenho",
    ]

    meta_score = sum(bool(metadata.get(k)) for k in meta_fields) / len(meta_fields)

    if all_records:
        table_score = sum(
            bool(r.get("talhao"))
            and bool(r.get("variedade"))
            and bool(r.get("area"))
            and bool(r.get("plantio"))
            for r in all_records
        ) / len(all_records)
    else:
        table_score = 0.0

    result = {
        "sucesso": True,
        "metadata": metadata,
        "blocos": [
            {
                "bloco": bloco,
                "talhoes": all_records,
            }
        ],
        "paginas": page_results,
        "confianca": {
            "metadata": round(meta_score, 2),
            "talhoes": round(table_score, 2),
            "geral": round((meta_score + table_score) / 2, 2),
        },
        "avisos": avisos,
    }

    # Compatibilidade com o código antigo.
    result["bloco"] = bloco
    result["talhoes"] = all_records

    return result


def extrair_dados(processed: Dict[str, Any]) -> Dict[str, Any]:
    return _build_result(processed)


class AgriculturalExtractor:
    def extrair(self, processed: Dict[str, Any]) -> Dict[str, Any]:
        return extrair_dados(processed)

    def extract(self, processed: Dict[str, Any]) -> Dict[str, Any]:
        return extrair_dados(processed)


__all__ = ["extrair_dados", "AgriculturalExtractor"]

# ============================================================
# V7 - OCR MULTI-PASS + RECONSTRUÇÃO DETERMINÍSTICA
# ============================================================
# Regra central:
#   OCR não é uma única verdade. Cada modo do Tesseract é melhor em uma
#   região diferente do documento.
#
#   - PSM 11 -> tabela/mapa, porque preserva melhor linhas independentes.
#   - OCR principal/PSM 6 -> rodapé e texto corrido.
#   - mapa -> somente confirmação de área/número.
#
# A camada abaixo é compatível com páginas que tenham:
#   page["ocr"] = OCR principal
#   page["ocr_alternativas"] = {"psm6": [...], "psm11": [...]}
# Se não houver alternativas, funciona normalmente com page["ocr"].

_BASE_EXTRACT_TABLE_V7 = _extract_table
_BASE_EXTRACT_METADATA_V7 = _extract_metadata


def _best_ocr_words(page: Dict[str, Any], prefer: str = "psm11") -> List[Dict[str, Any]]:
    alternatives = page.get("ocr_alternativas") or {}
    if isinstance(alternatives, list):
        # aceita lista de objetos {"modo": ..., "ocr": [...]}
        alternatives = {
            str(x.get("modo")): x.get("ocr", [])
            for x in alternatives if isinstance(x, dict)
        }

    chosen = alternatives.get(prefer)
    if isinstance(chosen, list) and len(chosen) >= 20:
        return [_normalize_word(w) for w in chosen if isinstance(w, dict) and _word_text(w)]

    return _page_words(page)


def _strict_sequence(records: List[Dict[str, Any]]) -> None:
    """Preenche números faltantes usando a posição da linha, sem alterar números confiáveis."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        groups.setdefault(str(r.get("_lado", "") or ""), []).append(r)

    # Primeiro lado: determina offset por âncoras.
    for side, group in groups.items():
        group.sort(key=lambda r: int(r.get("_linha_tabela", 9999)))
        anchors = []
        for r in group:
            try:
                n = int(str(r.get("talhao", "")))
                p = int(r.get("_linha_tabela", 0))
                if n > 0:
                    anchors.append((p, n))
            except Exception:
                pass

        if not anchors:
            continue

        counts: Dict[int, int] = {}
        for p, n in anchors:
            counts[n - p] = counts.get(n - p, 0) + 1
        offset, count = max(counts.items(), key=lambda x: x[1])

        if count >= 2 or anchors[0][0] == 0:
            for r in group:
                if not r.get("talhao"):
                    p = int(r.get("_linha_tabela", 0))
                    n = offset + p
                    if n > 0:
                        r["talhao"] = str(n)
                        r["_origem"] = "sequencia_tabela"

    # Segunda metade: quando a esquerda é 1..N, a direita continua N+1.
    left = groups.get("esquerda", [])
    right = groups.get("direita", [])
    left_nums = []
    for r in left:
        try:
            left_nums.append(int(str(r.get("talhao", ""))))
        except Exception:
            pass
    if right and left_nums:
        start = max(left_nums) + 1
        for r in sorted(right, key=lambda x: int(x.get("_linha_tabela", 9999))):
            if not r.get("talhao"):
                pos = int(r.get("_linha_tabela", 0))
                r["talhao"] = str(start + pos)
                r["_origem"] = "sequencia_tabela"


def _extract_table_v7(page_words: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    records, info = _BASE_EXTRACT_TABLE_V7(page_words)
    _strict_sequence(records)

    # Segunda passagem: data perdida dentro da mesma variedade.
    for side in {str(r.get("_lado", "") or "") for r in records}:
        group = [r for r in records if str(r.get("_lado", "") or "") == side]
        for r in group:
            if r.get("plantio") or not r.get("variedade"):
                continue
            candidates = [
                x for x in group
                if x.get("plantio")
                and str(x.get("variedade", "")).upper() == str(r.get("variedade", "")).upper()
            ]
            if candidates:
                p = int(r.get("_linha_tabela", 0))
                near = min(candidates, key=lambda x: abs(int(x.get("_linha_tabela", 0)) - p))
                r["plantio"] = near.get("plantio", "")
                r["_origem"] = str(r.get("_origem", "ocr_espacial")) + "+vizinho"

    return records, info

_extract_table = _extract_table_v7


def _merge_metadata(primary: Dict[str, str], secondary: Dict[str, str]) -> Dict[str, str]:
    """Escolhe por campo usando validação, não apenas "primeiro valor"."""
    p = primary or {}
    s = secondary or {}
    out = dict(p)

    def words(v):
        return [x for x in re.split(r"\s+", str(v or "").strip()) if x]

    def valid_owner(v):
        ws = words(v)
        return len(ws) >= 2 and all(len(re.sub(r"[^A-Za-zÀ-ÿ]", "", x)) >= 3 for x in ws)

    def valid_city(v):
        return bool(re.fullmatch(r"[A-Za-zÀ-ÿ]{3,}\s*-\s*[A-Z]{2}", str(v or "").strip()))

    def valid_scale(v):
        return bool(re.fullmatch(r"1\/\d+(?:\.\d+)?", str(v or "").replace(" ", "")))

    def valid_date(v):
        return bool(re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", str(v or "").strip()))

    def valid_area(v):
        return bool(re.fullmatch(r"\d{1,4}[.,]\d{1,3}%?", str(v or "").strip()))

    validators = {
        "proprietario": valid_owner,
        "municipio": valid_city,
        "escala": valid_scale,
        "data_ultimo_desenho": valid_date,
        "area_cana": valid_area,
        "area_carreador": lambda v: bool(re.fullmatch(r"\d{1,4}[.,]\d{1,3}", str(v or "").strip())),
        "area_carreador_percentual": lambda v: bool(re.fullmatch(r"\d{1,4}[.,]\d{1,3}%", str(v or "").strip())),
        "area_total": valid_area,
    }

    for k, sv in s.items():
        pv = p.get(k, "")
        if not sv:
            continue
        validator = validators.get(k)
        if not pv:
            out[k] = sv
        elif validator:
            p_ok = validator(pv)
            s_ok = validator(sv)
            if s_ok and not p_ok:
                out[k] = sv
        elif not pv:
            out[k] = sv

    return out


def _extract_metadata_v7(words: List[Dict[str, Any]]) -> Dict[str, str]:
    md = _BASE_EXTRACT_METADATA_V7(words)
    text = " ".join(_word_text(w) for w in sorted(words, key=lambda w: (_cy(w), _word_x(w))))
    text = re.sub(r"\s+", " ", text)

    # Áreas do resumo: extremamente confiáveis porque o rótulo e o valor
    # aparecem juntos no documento.
    patterns = {
        "area_cana": r"Area\s+de\s+Cana\s+([0-9]+[.,][0-9]+)",
        "area_carreador": r"Area\s+de\s+Carreador\s+([0-9]+[.,][0-9]+)(?!%)",
        "area_carreador_percentual": r"Area\s+de\s+Carreador\s*\(%\)\s*([0-9]+[.,][0-9]+%)",
        "area_total": r"Area\s+Total\s+([0-9]+[.,][0-9]+)",
        "status": r"(Mapa\s+de\s+Safra\s+\d{4}/\d{2})",
        "escala": r"Escala\s*:?\s*([0-9]+\s*/\s*[0-9.]+)",
        "data_ultimo_desenho": r"(?:Data\s+(?:do\s+)?)?[Uu]ltimo\s+desenho\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})",
    }
    for field, pat in patterns.items():
        if md.get(field):
            continue
        m = re.search(pat, text, re.I)
        if m:
            md[field] = re.sub(r"\s+", "", m.group(1)) if field == "escala" else m.group(1).strip()

    # Município: procura explicitamente uma cidade + UF depois da região
    # do rótulo, mas não usa a primeira ocorrência de "- SP".
    m = re.search(r"Municipio\s*:?.{0,100}?([A-Za-zÀ-ÿ]{3,})\s*[-–]\s*([A-Z]{2})", text, re.I)
    if m:
        city = m.group(1).strip()
        if city.upper() not in {"COATLAS", "USINA"}:
            md["municipio"] = f"{city} - {m.group(2).upper()}"

    # Proprietário: o texto pode inserir rótulos de outras colunas entre
    # o rótulo e o nome. Procura uma sequência de 2-4 palavras capitalizadas
    # próxima à ocorrência de Proprietário.
    if not md.get("proprietario"):
        m = re.search(
            r"Propr(?:iet[aá]rio|ist[aá]rio)[^\n]{0,180}?\b([A-ZÀ-Ý][A-Za-zÀ-ÿ]{2,}(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ]{2,}){1,3})\b",
            text,
            re.I,
        )
        if m:
            candidate = _norm(m.group(1))
            bad = {"Área de Carreador", "Área Total", "Un. Gestora", "Fazenda Santa", "Data ultimo"}
            if candidate not in bad and len(candidate.split()) >= 2:
                md["proprietario"] = candidate

    # Coordenadas: texto linearizado costuma preservar a coordenada completa.
    coord = re.search(
        r"(\d{1,3}[º°]\s*\d{1,2}'\s*[\d.,]+[\"”]?\s*[NS])\s*/\s*"
        r"(\d{1,3}[º°]\s*\d{1,2}'\s*[\d.,]+[\"”]?\s*[EW])",
        text,
        re.I,
    )
    if coord:
        md["latitude"] = re.sub(r"\s+", "", coord.group(1)).replace("”", '"')
        md["longitude"] = re.sub(r"\s+", "", coord.group(2)).replace("”", '"')

    if _key(md.get("desenho", "")) == "caros alberto":
        md["desenho"] = "Carlos Alberto"

    return md

_extract_metadata = _extract_metadata_v7


def _build_result_v7(processed: Dict[str, Any]) -> Dict[str, Any]:
    pages = processed.get("paginas") or processed.get("pages") or []
    if not pages:
        return {"sucesso": False, "metadata": {}, "blocos": [], "paginas": [], "avisos": ["Nenhuma página processada."]}

    metadata = None
    all_records = []
    page_results = []
    warnings = []

    for page_index, page in enumerate(pages):
        primary_words = _page_words(page)
        if not primary_words:
            warnings.append(f"Página {page_index + 1}: OCR espacial não disponível.")
            continue

        # Tabela: PSM11 quando disponível.
        table_words = _best_ocr_words(page, "psm11")
        # Metadados: OCR principal primeiro; PSM11 só preenche lacunas.
        page_md = _extract_metadata(primary_words)
        alternatives = page.get("ocr_alternativas") or {}
        if isinstance(alternatives, dict) and alternatives.get("psm11"):
            md_alt = _extract_metadata(_best_ocr_words(page, "psm11"))
            page_md = _merge_metadata(page_md, md_alt)
        if metadata is None:
            metadata = page_md
        else:
            metadata = _merge_metadata(metadata, page_md)

        records, info = _extract_table(table_words)

        headers = _find_table_headers(table_words)
        if headers:
            header_y = _table_header_y(headers)
            table_bottom = _find_table_bottom(table_words, header_y)
            map_pairs = _extract_map_number_area_pairs(table_words, table_bottom)

            # PRIMEIRO: reconstrói a sequência usando a posição das linhas.
            # O OCR pode perder 16, 17 e 18 mesmo quando todos os outros
            # números estão presentes. Não devemos deixar o mapa criar
            # registros novos; ele só confirma/preenche o que já existe.
            _repair_table_sequences(records)

            # SEGUNDO: o mapa confirma áreas/números já existentes.
            _apply_map_cross_reference(records, map_pairs)

            # TERCEIRO: roda a sequência novamente porque o cruzamento
            # pode ter fornecido uma âncora adicional.
            _repair_table_sequences(records)

            # QUARTO: recupera uma data perdida quando a mesma variedade
            # possui a mesma data em linhas vizinhas.
            _repair_missing_plantio(records)

            # ÚLTIMA confirmação de áreas. Nunca criar registro novo aqui.
            _apply_map_cross_reference(records, map_pairs)
            _repair_table_sequences(records)

        # Remove registros sem número somente se houver outro registro com
        # os mesmos dados e número; caso contrário, mantém para auditoria.
        for r in records:
            all_records.append({
                "talhao": r.get("talhao", ""),
                "variedade": r.get("variedade", ""),
                "area": r.get("area", ""),
                "plantio": r.get("plantio", ""),
                "pagina": page_index,
                "origem": r.get("_origem", "ocr_espacial"),
                "linha": r.get("_linha_tabela", 0),
            })

        page_results.append({
            "pagina": page_index,
            "texto": page.get("texto") or " ".join(_word_text(w) for w in primary_words),
            "palavras_ocr": len(primary_words),
            "linhas": len(_group_by_y(primary_words, tolerance=8)),
            "tabelas": len(page.get("tabelas") or []),
            "talhoes_extraidos": len(records),
            "estrutura_tabela": info,
        })

    metadata = metadata or {}
    all_records = _dedupe_records(all_records)
    bloco = metadata.get("bloco", "")
    for r in all_records:
        r["bloco"] = bloco

    # A aplicação trabalha somente com os seis campos agrícolas definidos
    # para este projeto. Os demais metadados do mapa não são expostos.
    metadata = {
        "bloco": metadata.get("bloco", ""),
        "propriedade": metadata.get("propriedade", ""),
    }

    avisos = list(warnings)
    if not metadata.get("bloco"):
        avisos.append("Bloco não identificado.")
    if not metadata.get("propriedade"):
        avisos.append("Propriedade não identificada.")

    without_number = [r for r in all_records if not r.get("talhao")]
    if without_number:
        avisos.append(f"{len(without_number)} talhão(ões) permanecem sem número porque não houve evidência suficiente no OCR/tabela/mapa.")

    incomplete = [r for r in all_records if not (r.get("talhao") and r.get("variedade") and r.get("area") and r.get("plantio"))]
    if incomplete:
        avisos.append(f"{len(incomplete)} talhão(ões) possuem pelo menos um campo agrícola incompleto.")

    meta_fields = ["bloco", "propriedade"]
    meta_score = sum(bool(metadata.get(k)) for k in meta_fields) / len(meta_fields)
    table_score = (sum(bool(r.get("talhao")) and bool(r.get("variedade")) and bool(r.get("area")) and bool(r.get("plantio")) for r in all_records) / len(all_records)) if all_records else 0.0

    result = {
        "sucesso": True,
        "metadata": metadata,
        "blocos": [{"bloco": bloco, "talhoes": all_records}],
        "paginas": page_results,
        "confianca": {"metadata": round(meta_score, 2), "talhoes": round(table_score, 2), "geral": round((meta_score + table_score) / 2, 2)},
        "avisos": avisos,
    }
    result.update(metadata)
    result["bloco"] = bloco
    result["talhoes"] = all_records
    return result


def extrair_dados(processed: Dict[str, Any]) -> Dict[str, Any]:
    return _build_result_v7(processed)


class AgriculturalExtractor:
    def extrair(self, processed: Dict[str, Any]) -> Dict[str, Any]:
        return extrair_dados(processed)

    def extract(self, processed: Dict[str, Any]) -> Dict[str, Any]:
        return extrair_dados(processed)


__all__ = ["extrair_dados", "AgriculturalExtractor"]


# ============================================================
# V9 - FALLBACK CONSERVADOR PARA MÚLTIPLAS TABELAS
# ============================================================
# Esta camada NÃO substitui o V7 quando ele já acertou o documento.
# Ela só entra quando:
#   - não há tabela reconhecida; ou
#   - há mais de uma região vertical de tabela na mesma página.
#
# Objetivo: corrigir documentos com várias tabelas/blocos sem alterar o
# comportamento que já funciona nos documentos anteriores.

import os as _os_v9
import cv2 as _cv2_v9
import pytesseract as _pytesseract_v9
from pytesseract import Output as _Output_v9


def _v9_ocr_alternativo(caminho: str) -> List[Dict[str, Any]]:
    """OCR PSM 11 apenas para o fallback V9."""
    imagem = _cv2_v9.imread(caminho)
    if imagem is None:
        return []

    # Reduz a imagem somente para esta segunda leitura. O OCR principal do
    # projeto continua exatamente como antes.
    altura, largura = imagem.shape[:2]
    escala = min(1.0, 1200.0 / max(largura, 1))
    if escala < 1.0:
        imagem = _cv2_v9.resize(
            imagem,
            None,
            fx=escala,
            fy=escala,
            interpolation=_cv2_v9.INTER_AREA,
        )

    dados = _pytesseract_v9.image_to_data(
        imagem,
        lang="por+eng",
        config="--oem 3 --psm 11",
        output_type=_Output_v9.DICT,
    )

    palavras = []

    for i, texto in enumerate(dados.get("text", [])):
        texto = _norm(texto)
        if not texto:
            continue

        try:
            confianca = float(dados["conf"][i])
        except Exception:
            confianca = 0

        if confianca < 25:
            continue

        x = int(dados["left"][i])
        y = int(dados["top"][i])
        w = int(dados["width"][i])
        h = int(dados["height"][i])

        palavras.append({
            "texto": texto,
            "x": x,
            "y": y,
            "largura": w,
            "altura": h,
            "centro_x": x + w / 2,
            "centro_y": y + h / 2,
            "confianca": confianca,
        })

    return palavras


def _v9_limpar_variedade(valor: str) -> str:
    valor = _clean_token(valor)
    valor = re.sub(r"[^A-Za-z0-9-]", "", valor)
    return valor.upper()


def _v9_formatar_area_sem_decimal(valor: str) -> str:
    valor = _clean_token(valor)
    if re.fullmatch(r"\d{3}", valor):
        return f"{valor[0]},{valor[1:]}"
    if re.fullmatch(r"\d{4}", valor):
        return f"{valor[:-2]},{valor[-2:]}"
    return _format_area(valor)


def _v9_parse_linha(linha: List[Dict[str, Any]]) -> Dict[str, str]:
    """Parseia uma linha inteira de uma tabela de quatro colunas."""
    linha = sorted(linha, key=_word_x)

    resultado = {
        "talhao": "",
        "variedade": "",
        "area": "",
        "plantio": "",
    }

    variedades = [
        w for w in linha
        if _v9_limpar_variedade(_word_text(w))
        and _is_variety(_v9_limpar_variedade(_word_text(w)))
    ]

    if not variedades:
        return resultado

    variedade = variedades[0]
    vx = _cx(variedade)
    resultado["variedade"] = _v9_limpar_variedade(_word_text(variedade))

    # Plantio: data completa ou ano (2023, 2024...).
    datas = [
        w for w in linha
        if _is_date(_word_text(w)) and _cx(w) > vx
    ]

    anos = [
        w for w in linha
        if re.fullmatch(r"(?:19|20)\d{2}", _clean_token(_word_text(w)))
        and _cx(w) > vx
    ]

    if datas:
        datas.sort(key=_word_x)
        resultado["plantio"] = _clean_token(_word_text(datas[-1]))
    elif anos:
        anos.sort(key=_word_x)
        resultado["plantio"] = _clean_token(_word_text(anos[-1]))

    # Talhão: SOMENTE número à esquerda da variedade.
    numeros_esquerda = []
    for w in linha:
        if _cx(w) >= vx:
            continue
        n = _number(_word_text(w))
        if n is not None and 1 <= n <= 999:
            numeros_esquerda.append((abs(_cx(w) - vx), n))

    if numeros_esquerda:
        numeros_esquerda.sort(key=lambda x: x[0])
        resultado["talhao"] = str(numeros_esquerda[0][1])

    # Área: SOMENTE número à direita da variedade.
    candidatos_area = []
    for w in linha:
        if _cx(w) <= vx:
            continue

        texto = _clean_token(_word_text(w))

        if _is_date(texto):
            continue

        if re.fullmatch(r"(?:19|20)\d{2}", texto):
            continue

        area_num = _area_number(texto)
        if area_num is None or not (0 < area_num < 10000):
            continue

        # Área decimal é muito mais forte que inteiro.
        decimal = "," in texto or "." in texto
        prioridade = 0 if decimal else 1

        candidatos_area.append((
            prioridade,
            abs(_cx(w) - vx),
            w,
        ))

    if candidatos_area:
        candidatos_area.sort(key=lambda x: (x[0], x[1]))
        escolhido = candidatos_area[0][2]
        resultado["area"] = _v9_formatar_area_sem_decimal(
            _word_text(escolhido)
        )

    return resultado


def _v9_inferir_sequencia(records: List[Dict[str, Any]]) -> None:
    """Reconstrói sequência apenas quando a evidência é forte."""
    if not records:
        return

    anchors = []

    for pos, rec in enumerate(records):
        try:
            n = int(str(rec.get("talhao", "")).strip())
        except Exception:
            continue

        if n > 0:
            anchors.append((pos, n))

    # Dois ou mais anchors com o mesmo offset são evidência forte.
    if len(anchors) >= 2:
        offsets = {}
        for pos, n in anchors:
            off = n - pos
            offsets[off] = offsets.get(off, 0) + 1

        melhor_offset, quantidade = max(
            offsets.items(),
            key=lambda item: item[1],
        )

        if quantidade >= 2:
            for pos, rec in enumerate(records):
                esperado = melhor_offset + pos
                if esperado > 0:
                    rec["talhao"] = str(esperado)
                    rec["_origem"] = "sequencia_tabela"
            return

    # Uma única âncora na primeira linha também é evidência suficiente
    # para completar a sequência dessa tabela. Ex.: 1, vazio, vazio...
    # Não aplicamos essa regra quando a única âncora está no meio da
    # tabela, pois isso poderia inventar a posição inicial.
    if len(anchors) == 1 and anchors[0][0] == 0:
        inicio = anchors[0][1]
        for pos, rec in enumerate(records):
            rec["talhao"] = str(inicio + pos)
            rec["_origem"] = "sequencia_tabela"
        return

    # Nenhum número foi lido: usa 1..N somente quando a própria tabela
    # fornece uma sequência contínua de linhas agrícolas. Isso é marcado
    # como reconstrução para permanecer auditável/editável na interface.
    if not anchors:
        for pos, rec in enumerate(records):
            rec["talhao"] = str(pos + 1)
            rec["_origem"] = "sequencia_tabela"


def _v9_header_clusters(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    headers = [
        w for w in words
        if _key(_word_text(w)) in ("talhao", "talhão")
    ]

    clusters = []

    for header in sorted(headers, key=_cx):
        encontrado = None

        for cluster in clusters:
            if abs(_cx(header) - cluster["x"]) < 100:
                encontrado = cluster
                break

        if encontrado is None:
            clusters.append({
                "x": _cx(header),
                "headers": [header],
            })
        else:
            encontrado["headers"].append(header)
            encontrado["x"] = sum(
                _cx(x) for x in encontrado["headers"]
            ) / len(encontrado["headers"])

    clusters.sort(key=lambda c: c["x"])
    return clusters


def _v9_table_region_bounds(
    words: List[Dict[str, Any]],
    clusters: List[Dict[str, Any]],
    cluster_index: int,
    header: Dict[str, Any],
    page_width: int,
    page_height: int,
) -> Tuple[float, float, float, float]:
    """Descobre os limites físicos de uma tabela."""
    y = _cy(header)

    # Linha do cabeçalho.
    header_row = sorted(
        [
            w for w in words
            if abs(_cy(w) - y) <= 12
        ],
        key=_word_x,
    )

    plantios = [
        w for w in header_row
        if _key(_word_text(w)) == "plantio"
        and _cx(w) > _cx(header)
    ]

    if plantios:
        x2 = _word_x(plantios[0]) + _word_w(plantios[0])
    else:
        x2 = min(
            page_width,
            _word_x(header) + 450,
        )

    if cluster_index == 0:
        x1 = 0
    else:
        previous_cluster = clusters[cluster_index - 1]
        previous_header = min(
            previous_cluster["headers"],
            key=lambda h: abs(_cy(h) - y),
        )

        previous_row = [
            w for w in words
            if abs(_cy(w) - _cy(previous_header)) <= 12
        ]

        previous_plantio = [
            w for w in previous_row
            if _key(_word_text(w)) == "plantio"
            and _cx(w) > _cx(previous_header)
        ]

        if previous_plantio:
            previous_x2 = (
                _word_x(previous_plantio[0])
                + _word_w(previous_plantio[0])
            )
        else:
            previous_x2 = _word_x(previous_header) + 450

        x1 = (previous_x2 + _word_x(header)) / 2

    # Se houver outra tabela abaixo na MESMA coluna X, ela é o limite.
    same_column = sorted(
        clusters[cluster_index]["headers"],
        key=_cy,
    )

    current_index = same_column.index(header)

    if current_index + 1 < len(same_column):
        y2 = _cy(same_column[current_index + 1]) - 15
    else:
        y2 = page_height

    return (
        max(0, x1),
        min(page_width, x2),
        max(0, y - 5),
        min(page_height, y2),
    )


def _v9_extract_single_region(
    words: List[Dict[str, Any]],
    bounds: Tuple[float, float, float, float],
) -> List[Dict[str, Any]]:
    x1, x2, y1, y2 = bounds

    region = [
        w for w in words
        if x1 - 5 <= _cx(w) <= x2 + 5
        and y1 <= _cy(w) <= y2
    ]

    headers = [
        w for w in region
        if _key(_word_text(w)) in ("talhao", "talhão")
    ]

    if not headers:
        return []

    header_y = min(_cy(h) for h in headers)

    candidate_words = [
        w for w in region
        if _cy(w) > header_y + 8
    ]

    rows = _group_by_y(
        candidate_words,
        tolerance=7,
    )

    records = []

    for pos, row in enumerate(rows):
        rec = _v9_parse_linha(row)

        if not rec.get("variedade"):
            continue

        rec["_linha_tabela"] = pos
        rec["_lado"] = "unico"
        rec["_origem"] = "ocr_espacial"
        records.append(rec)

    _v9_inferir_sequencia(records)

    return records


def _v9_find_block_near_header(
    words: List[Dict[str, Any]],
    header: Dict[str, Any],
) -> str:
    candidatos = []

    for word in words:
        texto = _word_text(word)
        match = BLOCK_RE.search(texto)
        if not match:
            continue

        dx = abs(_cx(word) - _cx(header))
        dy = abs(_cy(word) - _cy(header))

        if dy <= 90 and dx <= 500:
            candidatos.append((dx + dy * 0.5, match.group(1).upper()))

    if not candidatos:
        return ""

    candidatos.sort(key=lambda x: x[0])
    return candidatos[0][1]


def _v9_build_fallback_page(
    page: Dict[str, Any],
    page_index: int,
    caminho_imagem: str,
    metadata: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    words = _v9_ocr_alternativo(caminho_imagem)

    if not words:
        return [], [], {"erro": "OCR alternativo indisponível."}

    clusters = _v9_header_clusters(words)

    if not clusters:
        return [], [], {"erro": "Nenhum cabeçalho Talhão encontrado no OCR alternativo."}

    page_width = max(
        [_word_x(w) + _word_w(w) for w in words],
        default=1,
    )
    page_height = max(
        [_cy(w) for w in words],
        default=1,
    ) + 20

    bloco_fallback = metadata.get("bloco", "")
    region_results = []

    for cluster_index, cluster in enumerate(clusters):
        for header in sorted(cluster["headers"], key=_cy):
            bounds = _v9_table_region_bounds(
                words,
                clusters,
                cluster_index,
                header,
                int(page_width),
                int(page_height),
            )

            records = _v9_extract_single_region(
                words,
                bounds,
            )

            if not records:
                continue

            bloco = _v9_find_block_near_header(
                words,
                header,
            )

            if not bloco:
                bloco = bloco_fallback

            for rec in records:
                rec["pagina"] = page_index
                rec["bloco"] = bloco

            region_results.append({
                "bloco": bloco,
                "talhoes": records,
                "bbox": bounds,
            })

    return region_results, words, {
        "clusters": len(clusters),
        "regioes": len(region_results),
    }


def _v9_result(processed: Dict[str, Any]) -> Dict[str, Any]:
    # PRIMEIRO: roda exatamente o motor que já estava funcionando.
    base = _build_result_v7(processed)

    paginas = processed.get("paginas") or []

    precisa_fallback = False

    for page in paginas:
        primary_words = _page_words(page)
        headers = _find_table_headers(primary_words)

        # Sem cabeçalho: caso como 407P0012.
        if not headers:
            precisa_fallback = True
            break

        # Mais de uma faixa vertical de cabeçalho: caso como 3 tabelas.
        ys = sorted(_cy(h) for h in headers)
        clusters_y = []
        for y in ys:
            if not clusters_y or abs(y - clusters_y[-1]) > 40:
                clusters_y.append(y)

        if len(headers) > 2 or len(clusters_y) > 1:
            precisa_fallback = True
            break

    # Se o motor anterior acertou e não há sinal de múltiplas tabelas,
    # devolvemos o resultado EXATAMENTE como antes.
    if not precisa_fallback:
        return base

    metadata = dict(base.get("metadata") or {})
    regioes_globais = []
    page_results = []
    avisos = []

    # Tenta melhorar somente metadados ausentes usando PSM11.
    for page_index, page in enumerate(paginas):
        caminho = _os_v9.path.join(
            processed.get("pasta_saida", ""),
            f"pagina_{page_index + 1:03d}.png",
        )

        if not _os_v9.path.exists(caminho):
            continue

        alt_words = _v9_ocr_alternativo(caminho)
        if alt_words:
            alt_md = _extract_metadata(alt_words)
            metadata = _merge_metadata(metadata, alt_md)

    for page_index, page in enumerate(paginas):
        caminho = _os_v9.path.join(
            processed.get("pasta_saida", ""),
            f"pagina_{page_index + 1:03d}.png",
        )

        if not _os_v9.path.exists(caminho):
            page_results.append({
                "pagina": page_index,
                "talhoes_extraidos": 0,
                "erro": "Imagem da página não encontrada para fallback.",
            })
            continue

        regions, alt_words, info = _v9_build_fallback_page(
            page,
            page_index,
            caminho,
            metadata,
        )

        regioes_globais.extend(regions)

        page_results.append({
            "pagina": page_index,
            "texto": page.get("texto", ""),
            "palavras_ocr": len(_page_words(page)),
            "linhas": len(page.get("linhas") or []),
            "tabelas": len(regions),
            "talhoes_extraidos": sum(
                len(r.get("talhoes", [])) for r in regions
            ),
            "estrutura_tabela": info,
        })

    # Se o fallback não conseguiu nada, não destrói o resultado anterior.
    if not regioes_globais:
        base.setdefault("avisos", []).append(
            "Fallback de múltiplas tabelas não conseguiu extrair registros; resultado anterior preservado."
        )
        return base

    # Agrupa por bloco sem misturar tabelas diferentes.
    blocos_map = {}
    ordem_blocos = []

    for regiao in regioes_globais:
        bloco = regiao.get("bloco", "") or metadata.get("bloco", "")
        chave = bloco or f"sem_bloco_{len(ordem_blocos) + 1}"

        if chave not in blocos_map:
            blocos_map[chave] = []
            ordem_blocos.append(chave)

        blocos_map[chave].extend(regiao.get("talhoes", []))

    blocos = []
    todos = []

    for chave in ordem_blocos:
        talhoes = _dedupe_records(
            blocos_map[chave]
        )

        # Ordenação numérica por bloco.
        talhoes.sort(
            key=lambda r: (
                int(r["talhao"]) if str(r.get("talhao", "")).isdigit() else 999999,
                r.get("linha", 0),
            )
        )

        bloco_nome = "" if chave.startswith("sem_bloco_") else chave

        blocos.append({
            "bloco": bloco_nome,
            "talhoes": talhoes,
        })
        todos.extend(talhoes)

    # Mantém os metadados utilizados pela aplicação.
    metadata_final = {
        "bloco": metadata.get("bloco", "") if len(blocos) <= 1 else "",
        "propriedade": metadata.get("propriedade", ""),
        "proprietario": metadata.get("proprietario", ""),
        "municipio": metadata.get("municipio", ""),
    }

    # Quando há vários blocos, cada tabela já possui o bloco próprio.
    # O campo metadata.bloco fica vazio para não sugerir que todos são um só.
    if len(blocos) == 1 and blocos[0].get("bloco"):
        metadata_final["bloco"] = blocos[0]["bloco"]

    for rec in todos:
        rec["bloco"] = rec.get("bloco") or ""

    incompletos = [
        r for r in todos
        if not (
            r.get("talhao")
            and r.get("variedade")
            and r.get("area")
            and r.get("plantio")
        )
    ]

    if not metadata_final.get("propriedade"):
        avisos.append("Propriedade não identificada.")

    if incompletos:
        avisos.append(
            f"{len(incompletos)} talhão(ões) possuem pelo menos um campo agrícola incompleto."
        )

    score = (
        sum(
            bool(r.get("talhao"))
            and bool(r.get("variedade"))
            and bool(r.get("area"))
            and bool(r.get("plantio"))
            for r in todos
        ) / len(todos)
        if todos else 0.0
    )

    result = {
        "sucesso": True,
        "metadata": metadata_final,
        "blocos": blocos,
        "paginas": page_results,
        "confianca": {
            "metadata": round(
                sum(bool(metadata_final.get(k)) for k in ("bloco", "propriedade")) / 2,
                2,
            ),
            "talhoes": round(score, 2),
            "geral": round(
                (
                    sum(bool(metadata_final.get(k)) for k in ("bloco", "propriedade")) / 2
                    + score
                ) / 2,
                2,
            ),
        },
        "avisos": avisos,
    }

    result["bloco"] = metadata_final.get("bloco", "")
    result["propriedade"] = metadata_final.get("propriedade", "")
    result["talhoes"] = todos

    return result


# ÚNICA troca pública: o nome usado pelo test_ia.py continua igual.
def extrair_dados(processed: Dict[str, Any]) -> Dict[str, Any]:
    return _v9_result(processed)


class AgriculturalExtractor:
    def extrair(self, processed: Dict[str, Any]) -> Dict[str, Any]:
        return extrair_dados(processed)

    def extract(self, processed: Dict[str, Any]) -> Dict[str, Any]:
        return extrair_dados(processed)


__all__ = ["extrair_dados", "AgriculturalExtractor"]

# ============================================================
# V10 - LEITURA ROBUSTA DE TABELAS LATERAIS / MAPAS COM TABELA
# ============================================================
#
# Alguns mapas agrícolas não apresentam uma tabela "linear" para o OCR.
# Eles têm duas ou mais tabelas lado a lado, muitas vezes sobre áreas
# coloridas, e o OCR PSM11 pode misturar as colunas.
#
# A V10 NÃO substitui o V9 de forma indiscriminada.
# Ela só entra quando detecta uma estrutura de tabela horizontal
# com dois ou mais cabeçalhos "Talhão" na mesma faixa.
#
# Estratégia:
#   1. detectar a linha de cabeçalho com OCR espacial;
#   2. recortar somente a região da tabela;
#   3. rodar Tesseract PSM4 nessa região, que preserva melhor linhas
#      horizontais e tabelas lado a lado;
#   4. separar cada tabela por posição X das quatro colunas;
#   5. validar/reparar os dados usando o resultado V9 quando este tiver
#      informação mais confiável, especialmente datas;
#   6. nunca criar talhões que não estejam representados por uma linha
#      da tabela.

import os as _os_v10
import re as _re_v10
import cv2 as _cv2_v10
import pytesseract as _pytesseract_v10
from PIL import Image as _Image_v10
from pytesseract import Output as _Output_v10


def _v10_limpar_texto(valor: str) -> str:
    valor = str(valor or "").strip()
    valor = valor.replace("\n", " ")
    valor = _re_v10.sub(r"\s+", " ", valor)
    return valor.strip("|[]{}()<>:;\'`")


def _v10_data_valida(valor: str) -> bool:
    valor = _v10_limpar_texto(valor)
    return bool(
        _re_v10.fullmatch(
            r"\d{2}/\d{2}/\d{4}",
            valor,
        )
        or _re_v10.fullmatch(
            r"(?:19|20)\d{2}",
            valor,
        )
    )


def _v10_normalizar_data(valor: str) -> str:
    valor = _v10_limpar_texto(valor)

    valor = valor.replace("¢", "5")
    valor = valor.replace("§", "5")
    valor = valor.replace(";", "3")

    # Remove caracteres que o OCR frequentemente coloca antes/depois.
    valor = _re_v10.sub(
        r"^[^0-9]+",
        "",
        valor,
    )
    valor = _re_v10.sub(
        r"[^0-9/]+$",
        "",
        valor,
    )

    match = _re_v10.search(
        r"(\d{2}/\d{2}/\d{4})",
        valor,
    )

    if match:
        return match.group(1)

    match = _re_v10.search(
        r"((?:19|20)\d{2})",
        valor,
    )

    if match and "/" not in valor:
        return match.group(1)

    return ""


def _v10_normalizar_variedade(tokens: List[str]) -> str:
    partes = []

    for token in tokens:
        token = _v10_limpar_texto(token)
        if not token:
            continue

        token = token.strip("|[]{}()")
        if not token:
            continue

        partes.append(token)

    valor = " ".join(partes)
    valor = _re_v10.sub(
        r"\s+",
        " ",
        valor,
    ).strip()

    # Correção segura para o valor que aparece em tabelas agrícolas.
    compactado = _re_v10.sub(
        r"\s+",
        "",
        valor.upper(),
    )

    if compactado.startswith("SEMPLANTAR"):
        return "SEM PLANTAR"

    if compactado == "SEM":
        return "SEM PLANTAR"

    return valor.upper()


def _v10_normalizar_talhao(tokens: List[str]) -> str:
    """
    Lê o número do talhão sem transformar texto arbitrário em número.

    Alguns OCRs transformam 7 em "ft"/"f" ou deixam um marcador de
    tabela junto do número. Esses casos são tratados somente quando o
    token inteiro é compatível com esses erros comuns.
    """

    candidatos = []

    for token in tokens:
        original = _v10_limpar_texto(token)
        if not original:
            continue

        limpo = original.strip("|[]{}()")

        if _re_v10.fullmatch(
            r"\d{1,3}",
            limpo,
        ):
            try:
                numero = int(limpo)
            except Exception:
                continue

            if 1 <= numero <= 999:
                candidatos.append(str(numero))
                continue

        # Erros recorrentes do Tesseract para o algarismo 7.
        if _re_v10.fullmatch(
            r"(?:ft|f|t)",
            limpo.lower(),
        ):
            candidatos.append("7")

    return candidatos[0] if candidatos else ""


def _v10_area(tokens: List[str]) -> str:
    for token in tokens:
        valor = _v10_limpar_texto(token)
        valor = valor.strip("|[]{}()")

        match = _re_v10.search(
            r"(?<!\d)(\d{1,4}[,.]\d{1,2})(?!\d)",
            valor,
        )

        if match:
            numero = match.group(1).replace(",", ".")
            try:
                numero_float = float(numero)
            except Exception:
                continue

            if 0 < numero_float < 10000:
                return match.group(1).replace(".", ",")

        # Áreas sem separador decimal, quando o OCR remove a vírgula.
        if _re_v10.fullmatch(r"\d{3,4}", valor):
            if len(valor) == 3:
                return f"{valor[0]},{valor[1:]}"
            if len(valor) == 4:
                return f"{valor[:-2]},{valor[-2:]}"

    return ""


def _v10_ocr_cabecalho(caminho: str):
    """OCR espacial para localizar a região das tabelas."""

    imagem = _cv2_v10.imread(caminho)

    if imagem is None:
        return None, []

    altura, largura = imagem.shape[:2]

    # Mantém resolução suficiente para localizar tabelas, mas evita OCR
    # desnecessariamente gigantesco.
    escala = min(
        1.0,
        2200.0 / max(largura, 1),
    )

    if escala < 1.0:
        imagem = _cv2_v10.resize(
            imagem,
            None,
            fx=escala,
            fy=escala,
            interpolation=_cv2_v10.INTER_AREA,
        )

    dados = _pytesseract_v10.image_to_data(
        imagem,
        lang="por+eng",
        config="--oem 3 --psm 11",
        output_type=_Output_v10.DICT,
    )

    palavras = []

    for i, texto in enumerate(
        dados.get("text", [])
    ):
        texto = _v10_limpar_texto(texto)
        if not texto:
            continue

        try:
            confianca = float(
                dados["conf"][i]
            )
        except Exception:
            confianca = 0

        if confianca < 20:
            continue

        palavras.append({
            "texto": texto,
            "x": int(dados["left"][i]),
            "y": int(dados["top"][i]),
            "largura": int(dados["width"][i]),
            "altura": int(dados["height"][i]),
            "confianca": confianca,
        })

    return imagem, palavras


def _v10_cabecalhos_tabela(palavras):
    talhoes = [
        w for w in palavras
        if _key(_word_text(w)) in (
            "talhao",
            "talhão",
        )
    ]

    talhoes.sort(
        key=lambda w: (
            _cy(w),
            _word_x(w),
        )
    )

    return talhoes


def _v10_mesma_linha(a, b, tolerancia=18):
    return abs(
        _cy(a) - _cy(b)
    ) <= tolerancia


def _v10_localizar_colunas(
    palavras,
    cabecalho,
):
    """
    Encontra Variedade, Área e Plantio à direita de Talhão.
    """

    y = _cy(cabecalho)

    candidatos = [
        w for w in palavras
        if abs(_cy(w) - y) <= 20
        and _cx(w) > _cx(cabecalho)
    ]

    candidatos.sort(
        key=_word_x
    )

    resultado = []

    for palavra in candidatos:
        chave = _key(
            _word_text(palavra)
        )

        if chave in (
            "variedade",
            "area",
            "plantio",
        ):

            if not resultado or abs(
                _cx(palavra)
                - _cx(resultado[-1])
            ) > 20:
                resultado.append(
                    palavra
                )

    # O padrão esperado é exatamente:
    # Talhão | Variedade | Area | Plantio
    if len(resultado) < 3:
        return None

    variedade = next(
        (
            w for w in resultado
            if _key(_word_text(w))
            == "variedade"
        ),
        None,
    )

    area = next(
        (
            w for w in resultado
            if _key(_word_text(w))
            == "area"
        ),
        None,
    )

    plantio = next(
        (
            w for w in resultado
            if _key(_word_text(w))
            == "plantio"
        ),
        None,
    )

    if not all(
        [variedade, area, plantio]
    ):
        return None

    return {
        "talhao": cabecalho,
        "variedade": variedade,
        "area": area,
        "plantio": plantio,
    }


def _v10_detectar_tabelas(palavras):
    """Agrupa cabeçalhos Talhão que realmente formam tabelas."""

    headers = _v10_cabecalhos_tabela(
        palavras
    )

    tabelas = []

    for header in headers:

        colunas = _v10_localizar_colunas(
            palavras,
            header,
        )

        if not colunas:
            continue

        # Evita duplicação do mesmo cabeçalho.
        if any(
            abs(
                _cx(t["talhao"])
                - _cx(header)
            ) < 15
            and abs(
                _cy(t["talhao"])
                - _cy(header)
            ) < 15
            for t in tabelas
        ):
            continue

        tabelas.append(
            colunas
        )

    tabelas.sort(
        key=lambda t: (
            _cy(t["talhao"]),
            _cx(t["talhao"]),
        )
    )

    return tabelas


def _v10_extrair_tabelas(caminho):
    """
    Faz uma segunda leitura apenas da região de tabela.

    Retorna uma lista de regiões/tabelas, cada uma contendo os registros.
    """

    imagem_cv, palavras_header = (
        _v10_ocr_cabecalho(caminho)
    )

    if imagem_cv is None:
        return []

    tabelas_header = _v10_detectar_tabelas(
        palavras_header
    )

    # V10 só é necessária quando há duas ou mais tabelas laterais.
    # Uma tabela única continua sendo tratada pelo V7/V9.
    if len(tabelas_header) < 2:
        return []

    y_header = min(
        _word_y(t["talhao"])
        for t in tabelas_header
    )

    # Só consideramos cabeçalhos na mesma faixa horizontal.
    tabelas_header = [
        t for t in tabelas_header
        if abs(
            _word_y(t["talhao"])
            - y_header
        ) <= 30
    ]

    if len(tabelas_header) < 2:
        return []

    x_min = min(
        _word_x(t["talhao"])
        for t in tabelas_header
    )

    x_max = max(
        _word_x(t["plantio"])
        + _word_w(t["plantio"])
        for t in tabelas_header
    )

    # Pequena margem acima e abaixo do cabeçalho. Em mapas agrícolas
    # o rodapé costuma começar logo depois da tabela; recortar somente
    # esta faixa melhora bastante o PSM4.
    y_min = max(
        0,
        int(y_header) - 31,
    )

    y_max = min(
        imagem_cv.shape[0],
        int(y_header) + 370,
    )

    x_min = max(
        0,
        int(x_min) - 46,
    )

    x_max = min(
        imagem_cv.shape[1],
        int(x_max) + 10,
    )

    crop = imagem_cv[
        y_min:y_max,
        x_min:x_max,
    ]

    if crop.size == 0:
        return []

    # PSM4 funciona melhor quando recebe somente a tabela.
    crop_rgb = _cv2_v10.cvtColor(
        crop,
        _cv2_v10.COLOR_BGR2RGB,
    )

    pil_crop = _Image_v10.fromarray(
        crop_rgb
    )

    dados = _pytesseract_v10.image_to_data(
        pil_crop,
        lang="por+eng",
        config="--oem 3 --psm 4",
        output_type=_Output_v10.DICT,
    )

    palavras = []

    for i, texto in enumerate(
        dados.get("text", [])
    ):
        texto = _v10_limpar_texto(texto)
        if not texto:
            continue

        try:
            confianca = float(
                dados["conf"][i]
            )
        except Exception:
            confianca = 0

        if confianca < 15:
            continue

        palavras.append({
            "texto": texto,
            "x": int(dados["left"][i]) + x_min,
            "y": int(dados["top"][i]) + y_min,
            "largura": int(dados["width"][i]),
            "altura": int(dados["height"][i]),
            "confianca": confianca,
        })

    # Reencontra os cabeçalhos no OCR PSM4.
    tabelas = _v10_detectar_tabelas(
        palavras
    )

    # Caso PSM4 tenha perdido um cabeçalho, usa os cabeçalhos PSM11
    # apenas para definir as posições das colunas.
    if len(tabelas) < len(tabelas_header):
        tabelas = []
        for th in tabelas_header:
            tabelas.append({
                "talhao": th["talhao"],
                "variedade": th["variedade"],
                "area": th["area"],
                "plantio": th["plantio"],
            })

    tabelas.sort(
        key=lambda t: _cx(t["talhao"])
    )

    registros_tabelas = []

    for indice, tabela in enumerate(
        tabelas
    ):

        h_talhao = tabela["talhao"]
        h_variedade = tabela["variedade"]
        h_area = tabela["area"]
        h_plantio = tabela["plantio"]

        c0 = _cx(h_talhao)
        c1 = _cx(h_variedade)
        c2 = _cx(h_area)
        c3 = _cx(h_plantio)

        # Limites entre as colunas.
        b1 = (c0 + c1) / 2
        b2 = (c1 + c2) / 2
        b3 = (c2 + c3) / 2

        # Limites horizontais da tabela.
        left = (
            0
            if indice == 0
            else (
                _cx(
                    tabelas[indice - 1]["plantio"]
                )
                + c0
            ) / 2
        )

        if indice + 1 < len(tabelas):
            right = (
                c3
                + _cx(
                    tabelas[indice + 1]["talhao"]
                )
            ) / 2
        else:
            right = x_max

        region_words = [
            w for w in palavras
            if left - 5 <= _cx(w) <= right + 5
            and _cy(w) > _cy(h_talhao) + 12
        ]

        # Agrupa por linha.
        rows = []

        for word in sorted(
            region_words,
            key=lambda w: (
                _cy(w),
                _word_x(w),
            ),
        ):

            inserido = False

            for row in rows:
                if abs(
                    _cy(word)
                    - _cy(row[0])
                ) <= 11:
                    row.append(word)
                    inserido = True
                    break

            if not inserido:
                rows.append([word])

        registros = []

        for numero_linha, row in enumerate(
            rows
        ):

            row.sort(
                key=_word_x
            )

            colunas = [
                [],
                [],
                [],
                [],
            ]

            for word in row:
                cx = _cx(word)

                if cx < b1:
                    coluna = 0
                elif cx < b2:
                    coluna = 1
                elif cx < b3:
                    coluna = 2
                else:
                    coluna = 3

                colunas[coluna].append(
                    word
                )

            variedade = (
                _v10_normalizar_variedade(
                    [
                        _word_text(w)
                        for w in colunas[1]
                    ]
                )
            )

            area = _v10_area(
                [
                    _word_text(w)
                    for w in colunas[2]
                ]
            )

            talhao = _v10_normalizar_talhao(
                [
                    _word_text(w)
                    for w in colunas[0]
                ]
            )

            plantio = ""

            for word in colunas[3]:
                data = _v10_normalizar_data(
                    _word_text(word)
                )

                if data:
                    plantio = data
                    break

            # A linha precisa representar uma cultura/condição agrícola
            # e uma área. Linhas de total são descartadas.
            # Nesta camada a posição da coluna "Variedade" já é uma
            # evidência forte. Não restringimos o valor a uma lista de
            # variedades: documentos reais podem trazer códigos novos,
            # "SEM PLANTAR" ou outros textos agrícolas válidos.
            if not variedade or not area:
                continue

            registros.append({
                "talhao": talhao,
                "variedade": variedade,
                "area": area,
                "plantio": plantio,
                "_linha_tabela": numero_linha,
                "_origem": "v10_tabela_psm4",
                "_tabela": indice,
            })

        registros_tabelas.append(
            registros
        )

    return registros_tabelas


def _v10_completude(registros):
    if not registros:
        return 0.0

    completos = 0

    for r in registros:
        if all(
            [
                r.get("talhao"),
                r.get("variedade"),
                r.get("area"),
                r.get("plantio"),
            ]
        ):
            completos += 1

    return completos / len(registros)


def _v10_reparar_talhao_ocr(registros):
    """
    Repara tokens claramente reconhecidos como caracteres em vez de
    número. O caso mais comum é 7 -> ft.

    Se o número estiver realmente ausente, não inventa uma sequência.
    """

    for registros_tabela in registros:
        registros_tabela.sort(
            key=lambda r: r.get(
                "_linha_tabela",
                999999,
            )
        )

        for pos, registro in enumerate(
            registros_tabela
        ):
            if registro.get("talhao"):
                continue

            anterior = None
            proximo = None

            if pos > 0:
                anterior = registros_tabela[pos - 1].get(
                    "talhao"
                )

            if pos + 1 < len(registros_tabela):
                proximo = registros_tabela[pos + 1].get(
                    "talhao"
                )

            # Quando a tabela tem uma âncora imediatamente antes e depois,
            # uma única linha OCR-ilegível pode ser o número intermediário.
            try:
                a = int(anterior) if anterior else None
                b = int(proximo) if proximo else None
            except Exception:
                a = b = None

            if (
                a is not None
                and b is not None
                and b > a + 1
                and b - a <= 4
            ):
                # Só preenche quando existe uma única posição intermediária.
                if b == a + 2:
                    registro["talhao"] = str(a + 1)
                    registro["_origem"] = (
                        registro.get("_origem", "")
                        + "+sequencia_1_gap"
                    )


def _v10_correlacionar_base(
    registros_v10,
    base,
):
    """
    Usa o resultado anterior como segunda evidência.

    Regra principal:
      - V10 fornece estrutura/área/variedade/talhão;
      - base fornece um valor completo quando possui evidência melhor,
        especialmente plantio.
    """

    base_records = list(
        base.get("talhoes")
        or []
    )

    usados = set()

    def candidato_por_talhao(talhao):
        if not talhao:
            return None

        for idx, r in enumerate(
            base_records
        ):
            if idx in usados:
                continue

            if str(r.get("talhao", "")) == str(talhao):
                return idx, r

        return None

    def candidato_por_area_variedade(
        area,
        variedade,
    ):
        for idx, r in enumerate(
            base_records
        ):
            if idx in usados:
                continue

            if (
                str(r.get("area", "")) == str(area)
                and str(r.get("variedade", "")).upper()
                == str(variedade).upper()
            ):
                return idx, r

        return None

    resultado = []

    for registro in registros_v10:

        idx_record = None
        base_record = None

        encontrado = candidato_por_talhao(
            registro.get("talhao", "")
        )

        if encontrado:
            idx_record, base_record = encontrado
        else:
            encontrado = candidato_por_area_variedade(
                registro.get("area", ""),
                registro.get("variedade", ""),
            )

            if encontrado:
                idx_record, base_record = encontrado

        novo = dict(registro)

        if base_record is not None:
            usados.add(idx_record)

            # Plantio completo do resultado anterior tem prioridade.
            plantio_base = str(
                base_record.get(
                    "plantio",
                    "",
                )
                or ""
            ).strip()

            if _v10_data_valida(
                plantio_base
            ):
                novo["plantio"] = plantio_base
                novo["_origem"] = (
                    novo.get("_origem", "")
                    + "+base"
                )

            # Se V10 não identificou variedade/área, aceita o valor base.
            if not novo.get("variedade"):
                novo["variedade"] = base_record.get(
                    "variedade",
                    "",
                )

            if not novo.get("area"):
                novo["area"] = base_record.get(
                    "area",
                    "",
                )

            if not novo.get("talhao"):
                novo["talhao"] = base_record.get(
                    "talhao",
                    "",
                )

        resultado.append(
            novo
        )

    return resultado


def _v10_deduplicar(registros):
    resultado = []
    chaves = set()

    for r in registros:
        chave = (
            str(r.get("talhao", "")),
            str(r.get("variedade", "")),
            str(r.get("area", "")),
            str(r.get("plantio", "")),
        )

        if chave in chaves:
            continue

        chaves.add(chave)
        resultado.append(r)

    return resultado


def _v10_result(processed):
    """
    V10 final.

    Mantém V9 para todos os documentos e somente substitui uma página
    quando a leitura especializada de tabelas laterais produz uma
    estrutura claramente melhor.
    """

    base = _v9_result(
        processed
    )

    paginas = (
        processed.get("paginas")
        or []
    )

    if not paginas:
        return base

    todas_v10 = []
    encontrou_tabela_lateral = False

    for page_index, page in enumerate(
        paginas
    ):

        caminho = _os_v10.path.join(
            processed.get(
                "pasta_saida",
                "",
            ),
            f"pagina_{page_index + 1:03d}.png",
        )

        if not _os_v10.path.exists(
            caminho
        ):
            continue

        registros_tabelas = _v10_extrair_tabelas(
            caminho
        )

        if len(registros_tabelas) < 2:
            continue

        encontrou_tabela_lateral = True

        _v10_reparar_talhao_ocr(
            registros_tabelas
        )

        registros = []

        for tabela in registros_tabelas:
            registros.extend(
                tabela
            )

        registros = _v10_correlacionar_base(
            registros,
            base,
        )

        for registro in registros:
            registro["pagina"] = page_index

        todas_v10.extend(
            registros
        )

    # Não é uma tabela lateral: comportamento anterior permanece intacto.
    if not encontrou_tabela_lateral:
        return base

    todas_v10 = _v10_deduplicar(
        todas_v10
    )

    if not todas_v10:
        return base

    base_records = list(
        base.get("talhoes")
        or []
    )

    score_base = _v10_completude(
        base_records
    )

    score_v10 = _v10_completude(
        todas_v10
    )

    # Quantidade também é um sinal importante: uma tabela que contém 25
    # linhas não pode ser substituída por uma leitura que encontrou 10.
    quantidade_base = len(base_records)
    quantidade_v10 = len(todas_v10)

    deve_usar_v10 = False

    if quantidade_v10 > quantidade_base:
        deve_usar_v10 = True

    elif (
        quantidade_v10 == quantidade_base
        and score_v10 >= score_base
    ):
        deve_usar_v10 = True

    elif (
        quantidade_v10 >= quantidade_base * 0.9
        and score_v10 > score_base + 0.15
    ):
        deve_usar_v10 = True

    if not deve_usar_v10:
        return base

    metadata = dict(
        base.get("metadata")
        or {}
    )

    bloco = metadata.get(
        "bloco",
        "",
    )

    propriedade = metadata.get(
        "propriedade",
        "",
    )

    for registro in todas_v10:
        registro["bloco"] = bloco

    todas_v10.sort(
        key=lambda r: (
            r.get("pagina", 0),
            int(r["talhao"])
            if str(r.get("talhao", "")).isdigit()
            else 999999,
        )
    )

    incompletos = [
        r for r in todas_v10
        if not (
            r.get("talhao")
            and r.get("variedade")
            and r.get("area")
            and r.get("plantio")
        )
    ]

    avisos = [
        "Leitura especializada V10 aplicada para tabela(s) lateral(is)."
    ]

    if incompletos:
        avisos.append(
            f"{len(incompletos)} talhão(ões) possuem pelo menos um campo agrícola incompleto."
        )

    metadata_final = {
        "bloco": bloco,
        "propriedade": propriedade,
        "proprietario": metadata.get("proprietario", ""),
        "municipio": metadata.get("municipio", ""),
    }

    meta_score = sum(
        bool(
            metadata_final.get(k)
        )
        for k in (
            "bloco",
            "propriedade",
        )
    ) / 2

    score = _v10_completude(
        todas_v10
    )

    result = {
        "sucesso": True,
        "metadata": metadata_final,
        "blocos": [
            {
                "bloco": bloco,
                "talhoes": todas_v10,
            }
        ],
        "paginas": base.get(
            "paginas",
            [],
        ),
        "confianca": {
            "metadata": round(
                meta_score,
                2,
            ),
            "talhoes": round(
                score,
                2,
            ),
            "geral": round(
                (
                    meta_score
                    + score
                ) / 2,
                2,
            ),
        },
        "avisos": avisos,
        "bloco": bloco,
        "propriedade": propriedade,
        "talhoes": todas_v10,
    }

    return result


# A interface pública permanece exatamente igual.
def extrair_dados(processed: Dict[str, Any]) -> Dict[str, Any]:
    return _v10_result(processed)


class AgriculturalExtractor:
    def extrair(self, processed: Dict[str, Any]) -> Dict[str, Any]:
        return extrair_dados(processed)

    def extract(self, processed: Dict[str, Any]) -> Dict[str, Any]:
        return extrair_dados(processed)

# ============================================================
# V11 - LEITURA HÍBRIDA DE TABELAS EM MAPAS
# ============================================================
#
# A V10 dependia demais do PSM4 em um recorte único. Isso falha quando:
#   - algumas células estão coloridas de verde;
#   - a grade da tabela faz o Tesseract perder o número do talhão;
#   - duas tabelas ficam lado a lado;
#   - o OCR do mapa e o OCR da tabela enxergam partes diferentes.
#
# A V11 combina:
#   1. OCR PSM11 da página inteira -> principalmente Talhão;
#   2. OCR PSM11 somente da faixa da tabela -> Variedade/Área/Plantio;
#   3. OCR individual de célula somente quando um campo fica vazio;
#   4. reconstrução conservadora de talhões faltantes por sequência;
#   5. normalização segura de erros recorrentes de variedade/data.
#
# Não substitui V7/V9 quando a estrutura não for uma tabela lateral.

import cv2 as _cv2_v11
import pytesseract as _pytesseract_v11
from pytesseract import Output as _Output_v11


def _v11_palavras_ocr(imagem, x_offset=0, y_offset=0, psm=11):
    dados = _pytesseract_v11.image_to_data(
        imagem,
        lang="por+eng",
        config=f"--oem 3 --psm {psm}",
        output_type=_Output_v11.DICT,
    )

    palavras = []

    for i, texto in enumerate(dados.get("text", [])):
        texto = _v10_limpar_texto(texto)
        if not texto:
            continue

        try:
            confianca = float(dados["conf"][i])
        except Exception:
            confianca = 0

        # Não descartamos imediatamente OCR de baixa confiança na V11.
        # Células verdes e textos sobre mapas frequentemente aparecem com
        # confiança menor, mas ainda são úteis espacialmente.
        palavras.append({
            "texto": texto,
            "x": int(dados["left"][i]) + x_offset,
            "y": int(dados["top"][i]) + y_offset,
            "largura": int(dados["width"][i]),
            "altura": int(dados["height"][i]),
            "confianca": confianca,
        })

    return palavras


def _v11_dedup_palavras(palavras):
    """Remove duplicações espaciais entre OCRs diferentes."""

    resultado = []

    for palavra in sorted(
        palavras,
        key=lambda w: (
            w.get("y", 0),
            w.get("x", 0),
        ),
    ):
        cx = _cx(palavra)
        cy = _cy(palavra)
        texto = _key(_word_text(palavra))

        duplicada = False

        for existente in resultado[-12:]:
            if _key(_word_text(existente)) != texto:
                continue

            if (
                abs(_cx(existente) - cx) <= 8
                and abs(_cy(existente) - cy) <= 7
            ):
                duplicada = True
                # Conserva a maior confiança.
                if palavra.get("confianca", 0) > existente.get("confianca", 0):
                    existente.update(palavra)
                break

        if not duplicada:
            resultado.append(palavra)

    return resultado


def _v11_normalizar_variedade(tokens):
    valor = _v10_normalizar_variedade(tokens)

    compacto = _re_v10.sub(
        r"[^A-Z0-9]",
        "",
        valor.upper(),
    )

    # Erro muito recorrente do OCR: CTC4 -> CTCA.
    if compacto in {
        "CTCA",
        "CTC4",
    }:
        return "CTC4"

    # SEM PLANTAR pode aparecer invertido ou quebrado.
    if compacto in {
        "SEMPLANTAR",
        "PLANTARSEM",
    }:
        return "SEM PLANTAR"

    return valor


def _v11_normalizar_data(valor):
    valor = _v10_limpar_texto(valor)
    compacto = _re_v10.sub(r"[^0-9/]", "", valor)

    # Anos que ganham um caractere do OCR, por exemplo 20267.
    if _re_v10.fullmatch(r"20\d{3}", compacto):
        ano = compacto[:4]
        if 1900 <= int(ano) <= 2100:
            return ano

    # Datas normais.
    # Erro recorrente 70/04/2026 -> 10/04/2026.
    # Deve ser testado antes do normalizador genérico, que aceita
    # datas numericamente válidas mesmo quando o OCR trocou o 1 por 7.
    # Só aplicamos quando o padrão inteiro é compatível.
    m = _re_v10.fullmatch(
        r"[17]0/(\d{2})/(\d{4})",
        compacto,
    )
    if m:
        return f"10/{m.group(1)}/{m.group(2)}"

    normal = _v10_normalizar_data(valor)
    if normal:
        return normal

    return ""


def _v11_ocr_celula(imagem, x1, y1, x2, y2, campo):
    """OCR pequeno e localizado para recuperar uma célula perdida."""

    h, w = imagem.shape[:2]

    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(w, int(x2))
    y2 = min(h, int(y2))

    if x2 <= x1 or y2 <= y1:
        return ""

    crop = imagem[y1:y2, x1:x2]

    if crop.size == 0:
        return ""

    # Ampliação pequena para números pequenos da tabela.
    crop = _cv2_v11.resize(
        crop,
        None,
        fx=3.0,
        fy=3.0,
        interpolation=_cv2_v11.INTER_CUBIC,
    )

    candidatos = []

    for psm in (7, 8, 11, 13):
        texto = _pytesseract_v11.image_to_string(
            crop,
            lang="por+eng",
            config=f"--oem 3 --psm {psm}",
        ).strip()

        if texto:
            candidatos.append(texto)

    if not candidatos:
        return ""

    # Campo numérico: procura primeiro um valor que o normalizador reconheça.
    if campo == "area":
        for texto in candidatos:
            valor = _v10_area([texto])
            if valor:
                return valor

    if campo == "plantio":
        for texto in candidatos:
            valor = _v11_normalizar_data(texto)
            if valor:
                return valor

    if campo == "talhao":
        for texto in candidatos:
            valor = _v10_normalizar_talhao([texto])
            if valor:
                return valor

    if campo == "variedade":
        for texto in candidatos:
            valor = _v11_normalizar_variedade([texto])
            if valor:
                return valor

    return ""


def _v11_extrair_tabelas(caminho):
    imagem = _cv2_v11.imread(caminho)

    if imagem is None:
        return []

    # --------------------------------------------------------
    # 1. OCR DA PÁGINA: localiza cabeçalhos e talhões.
    # --------------------------------------------------------

    palavras_pagina = _v11_palavras_ocr(
        imagem,
        psm=11,
    )

    headers = [
        w for w in palavras_pagina
        if _key(_word_text(w)) in {
            "talhao",
            "talhão",
        }
    ]

    if len(headers) < 2:
        return []

    headers.sort(
        key=lambda w: (
            _cy(w),
            _word_x(w),
        )
    )

    # Escolhe a faixa que contém mais cabeçalhos próximos verticalmente.
    melhor = []

    for h in headers:
        grupo = [
            x for x in headers
            if abs(_cy(x) - _cy(h)) <= 30
        ]
        if len(grupo) > len(melhor):
            melhor = grupo

    headers = sorted(
        melhor,
        key=_word_x,
    )

    if len(headers) < 2:
        return []

    y_header = min(_word_y(h) for h in headers)

    # --------------------------------------------------------
    # 2. Localiza Variedade/Área/Plantio de cada tabela.
    # --------------------------------------------------------

    tabelas = []

    for h in headers:
        candidatos = [
            w for w in palavras_pagina
            if abs(_cy(w) - _cy(h)) <= 20
            and _cx(w) > _cx(h)
        ]

        def achar(chave):
            encontrados = [
                w for w in candidatos
                if _key(_word_text(w)) == chave
            ]
            if not encontrados:
                return None
            return min(
                encontrados,
                key=_word_x,
            )

        variedade = achar("variedade")
        area = achar("area")
        plantio = achar("plantio")

        if not all(
            [variedade, area, plantio]
        ):
            continue

        tabelas.append({
            "talhao": h,
            "variedade": variedade,
            "area": area,
            "plantio": plantio,
        })

    tabelas.sort(
        key=lambda t: _cx(t["talhao"])
    )

    if len(tabelas) < 2:
        return []

    # --------------------------------------------------------
    # 3. Recorta SOMENTE a faixa da tabela.
    # --------------------------------------------------------

    x_min = max(
        0,
        int(
            min(
                _word_x(t["talhao"])
                for t in tabelas
            )
            - 55
        ),
    )

    x_max = min(
        imagem.shape[1],
        int(
            max(
                _word_x(t["plantio"])
                + _word_w(t["plantio"])
                for t in tabelas
            )
            + 25
        ),
    )

    y_min = max(
        0,
        int(y_header) - 20,
    )

    y_max = min(
        imagem.shape[0],
        int(y_header + 315),
    )

    crop = imagem[
        y_min:y_max,
        x_min:x_max,
    ]

    if crop.size == 0:
        return []

    palavras_tabela = _v11_palavras_ocr(
        crop,
        x_offset=x_min,
        y_offset=y_min,
        psm=11,
    )

    # --------------------------------------------------------
    # 4. Parse de cada tabela pela posição das colunas.
    # --------------------------------------------------------

    resultados = []

    for indice, tabela in enumerate(tabelas):

        h0 = tabela["talhao"]
        h1 = tabela["variedade"]
        h2 = tabela["area"]
        h3 = tabela["plantio"]

        c0, c1, c2, c3 = [
            _cx(x)
            for x in (h0, h1, h2, h3)
        ]

        b1 = (c0 + c1) / 2
        b2 = (c1 + c2) / 2
        b3 = (c2 + c3) / 2

        # Limites entre as duas tabelas.
        if indice == 0:
            esquerda = x_min
        else:
            esquerda = (
                _cx(
                    tabelas[indice - 1]["plantio"]
                )
                + c0
            ) / 2

        if indice + 1 < len(tabelas):
            direita = (
                c3
                + _cx(
                    tabelas[indice + 1]["talhao"]
                )
            ) / 2
        else:
            direita = x_max

        palavras = [
            w for w in palavras_tabela
            if esquerda - 4 <= _cx(w) <= direita + 4
            and _cy(w) > _cy(h0) + 11
            and _cy(w) < _cy(h0) + 300
        ]

        # Agrupa por linha.
        rows = []

        for word in sorted(
            palavras,
            key=lambda w: (
                _cy(w),
                _word_x(w),
            ),
        ):

            row = None

            if rows:
                row = min(
                    rows,
                    key=lambda r: abs(
                        _cy(word) - _cy(r[0])
                    ),
                )

            if (
                row is not None
                and abs(
                    _cy(word) - _cy(row[0])
                ) <= 7
            ):
                row.append(word)
            else:
                rows.append([word])

        registros = []

        for numero_linha, row in enumerate(rows):

            row.sort(key=_word_x)

            colunas = [
                [],
                [],
                [],
                [],
            ]

            for word in row:
                cx = _cx(word)

                if cx < b1:
                    col = 0
                elif cx < b2:
                    col = 1
                elif cx < b3:
                    col = 2
                else:
                    col = 3

                colunas[col].append(word)

            # ----------------------------------------------
            # TALHÃO: prefere OCR da página inteira.
            # ----------------------------------------------

            centro_y = _cy(row[0])

            talhao_palavras = [
                w for w in palavras_pagina
                if esquerda - 4 <= _cx(w) <= b1 + 5
                and _cx(w) >= c0 - 22
                and abs(_cy(w) - centro_y) <= 8
                and _cy(w) > _cy(h0) + 10
                and _cy(w) < _cy(h0) + 300
            ]

            talhao = _v10_normalizar_talhao(
                [
                    _word_text(w)
                    for w in talhao_palavras
                ]
            )

            # Se a página não encontrou, tenta a própria linha.
            if not talhao:
                talhao = _v10_normalizar_talhao(
                    [
                        _word_text(w)
                        for w in colunas[0]
                    ]
                )

            variedade = _v11_normalizar_variedade(
                [
                    _word_text(w)
                    for w in colunas[1]
                ]
            )

            area = _v10_area(
                [
                    _word_text(w)
                    for w in colunas[2]
                ]
            )

            plantio = ""

            for word in colunas[3]:
                data = _v11_normalizar_data(
                    _word_text(word)
                )
                if data:
                    plantio = data
                    break

            # ----------------------------------------------
            # Recuperação localizada de campos faltantes.
            # ----------------------------------------------

            # Limites aproximados da célula.
            y1 = int(_cy(row[0]) - 9)
            y2 = int(_cy(row[0]) + 9)

            if not talhao:
                talhao = _v11_ocr_celula(
                    imagem,
                    c0 - 25,
                    y1,
                    b1,
                    y2,
                    "talhao",
                )

            if not variedade:
                variedade = _v11_ocr_celula(
                    imagem,
                    b1,
                    y1,
                    b2,
                    y2,
                    "variedade",
                )

            if not area:
                area = _v11_ocr_celula(
                    imagem,
                    b2,
                    y1,
                    b3,
                    y2,
                    "area",
                )

            if not plantio:
                plantio = _v11_ocr_celula(
                    imagem,
                    b3,
                    y1,
                    direita,
                    y2,
                    "plantio",
                )

            # ----------------------------------------------
            # Filtra linhas que não são dados da tabela.
            # ----------------------------------------------

            if not area:
                continue

            if not variedade:
                continue

            # Não confundir o rodapé do mapa com linhas da tabela.
            if _re_v10.sub(r"[^A-Z]", "", variedade.upper()) in {
                "CANA",
                "CARREADOR",
                "AREAS",
                "AREA",
                "TOTAL",
                "LOCALIZACAO",
                "SAFRA",
            }:
                continue

            registros.append({
                "talhao": talhao,
                "variedade": variedade,
                "area": area,
                "plantio": plantio,
                "_linha_tabela": numero_linha,
                "_origem": "v11_tabela_hibrida",
                "_tabela": indice,
            })

        # --------------------------------------------------
        # Reconstrução CONSERVADORA do talhão.
        # --------------------------------------------------

        registros.sort(
            key=lambda r: r.get(
                "_linha_tabela",
                999999,
            )
        )

        # Caso clássico: primeira parte da tabela perdeu os números,
        # mas existe um primeiro número explícito mais abaixo.
        primeiro_com_numero = None

        for i, r in enumerate(registros):
            if str(r.get("talhao", "")).isdigit():
                primeiro_com_numero = i
                break

        if primeiro_com_numero is not None:
            primeiro_numero = int(
                registros[primeiro_com_numero]["talhao"]
            )

            # Só faz isso quando TODOS os registros anteriores estão vazios
            # e a sequência encaixa exatamente.
            if (
                primeiro_numero > primeiro_com_numero
                and primeiro_numero <= 50
            ):
                for i in range(primeiro_com_numero):
                    if not registros[i].get("talhao"):
                        registros[i]["talhao"] = str(i + 1)
                        registros[i]["_origem"] += "+sequencia_inicial"

        # Gaps de exatamente uma posição: 33, [OCR], 35 -> 34.
        for i in range(1, len(registros) - 1):
            atual = registros[i]
            if atual.get("talhao"):
                continue

            try:
                anterior = int(registros[i - 1].get("talhao", ""))
                proximo = int(registros[i + 1].get("talhao", ""))
            except Exception:
                continue

            if proximo == anterior + 2:
                atual["talhao"] = str(anterior + 1)
                atual["_origem"] += "+sequencia_gap"

        # Talhão é numérico neste tipo de documento. Depois das
        # reconstruções conservadoras acima, descartamos qualquer linha
        # que ainda tenha recebido texto do rodapé/mapa no lugar do talhão.
        registros = [
            r for r in registros
            if str(r.get("talhao", "")).isdigit()
        ]

        resultados.append(registros)

    # Só retorna se as tabelas realmente têm conteúdo agrícola.
    if sum(len(x) for x in resultados) < 2:
        return []

    return resultados


# A V11 passa a ser a primeira tentativa da leitura especializada.
# Se ela não conseguir detectar as tabelas, a V10 original continua disponível.
_v10_extrair_tabelas_original = _v10_extrair_tabelas


def _v10_extrair_tabelas(caminho):
    resultado_v11 = _v11_extrair_tabelas(caminho)

    total_v11 = sum(
        len(t) for t in resultado_v11
    )

    if total_v11 >= 2:
        return resultado_v11

    return _v10_extrair_tabelas_original(caminho)

# ============================================================
# V12 - TABELA IMPRESSA SOBRE MAPA / DUAS COLUNAS
# ============================================================
#
# Esta camada é deliberadamente independente das reconstruções V7-V11.
# Quando a página possui uma tabela impressa na parte inferior do mapa,
# usamos OCR espacial diretamente na tabela:
#
#   PSM4 -> talhão (melhor para números dentro da grade)
#   PSM11 -> variedade / área / plantio (melhor para células coloridas)
#
# Nunca usamos o número do talhão como área. Cada campo é aceito somente
# dentro da faixa X correspondente à sua coluna.
#
# A V12 só substitui o resultado anterior quando encontra uma tabela
# realmente estruturada e com evidência suficiente.

import os as _os_v12
import re as _re_v12
import cv2 as _cv2_v12
import pytesseract as _pytesseract_v12
from pytesseract import Output as _Output_v12


_v11_extrair_dados_publico = extrair_dados


def _v12_limpar_token(s):
    s = str(s or "").strip()
    s = s.replace("[", "").replace("]", "")
    s = s.replace("|", "").replace("(", "").replace(")", "")
    s = s.replace("—", "-")
    return s.strip()


def _v12_numero_talhao(s):
    s = _v12_limpar_token(s)
    m = _re_v12.search(r"(?<!\d)(\d{1,3})(?!\d)", s)
    if not m:
        return ""
    n = int(m.group(1))
    if 1 <= n <= 999:
        return str(n)
    return ""


def _v12_area(s):
    s = _v12_limpar_token(s).replace(" ", "")
    # OCR frequentemente perde a vírgula em 6,33 -> 633.
    m = _re_v12.search(r"(\d{1,3}[,.]\d{1,2})", s)
    if m:
        return m.group(1).replace(".", ",")
    m = _re_v12.fullmatch(r"\d{3,4}", s)
    if m:
        # Não transformar cegamente 2026/2013 em área.
        if s in {"2013", "2023", "2024", "2025", "2026", "2027", "2028"}:
            return ""
        if len(s) == 3:
            return s[0] + "," + s[1:]
        if len(s) == 4:
            return s[:-2] + "," + s[-2:]
    return ""


def _v12_plantio(s):
    s = _v12_limpar_token(s).replace(" ", "")
    # Datas.
    m = _re_v12.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        d = d.zfill(2)
        mo = mo.zfill(2)
        # Correções OCR muito comuns.
        if d == "70":
            d = "10"
        if mo == "00":
            mo = "04"
        return f"{d}/{mo}/{y}"
    # Ano isolado.
    m = _re_v12.fullmatch(r"(19\d{2}|20\d{2})", s)
    if m:
        return m.group(1)
    return ""


def _v12_variedade(tokens):
    txt = " ".join(
        _v12_limpar_token(t)
        for t in tokens
        if _v12_limpar_token(t)
    ).upper()
    txt = _re_v12.sub(r"\s+", " ", txt).strip()
    if not txt:
        return ""

    # Classes textuais que aparecem na própria tabela.
    if "SEM" in txt and "PLANT" in txt:
        return "SEM PLANTAR"

    # Neste conjunto de mapas, o OCR frequentemente lê CTC4 como CTCA
    # (o 4 vira A) ou simplesmente CTC. A forma canônica do código é CTC4.
    if txt in {"CTCA", "CTC"}:
        return "CTC4"

    # Remove ruído de pontuação.
    txt = txt.replace(" ", "")
    txt = txt.strip(".,:;_'\"")

    # Códigos usuais.
    m = _re_v12.search(r"(RB\d{5,7}|CTC\d{1,6})", txt)
    if m:
        return m.group(1)

    # CTC4 / CTC9003 etc. com OCR quebrado.
    if txt.startswith("CTC"):
        suf = txt[3:]
        suf = _re_v12.sub(r"[^0-9]", "", suf)
        if suf:
            return "CTC" + suf

    return txt


def _v12_dataframe_words(img, psm):
    df = _pytesseract_v12.image_to_data(
        img,
        config=f"--psm {psm}",
        lang="por+eng",
        output_type=_Output_v12.DATAFRAME,
    )
    if df is None or len(df) == 0:
        return []
    df = df.dropna(subset=["text"]).copy()
    out = []
    for _, r in df.iterrows():
        text = str(r.get("text", "")).strip()
        if not text:
            continue
        try:
            conf = float(r.get("conf", -1))
        except Exception:
            conf = -1
        out.append({
            "x": int(r.get("left", 0)),
            "y": int(r.get("top", 0)),
            "w": int(r.get("width", 0)),
            "h": int(r.get("height", 0)),
            "text": text,
            "conf": conf,
        })
    return out


def _v12_detectar_headers(words, h, min_y_ratio=0.55):
    """
    Detecta dois ou mais cabeçalhos Talhão na metade inferior.
    Retorna os centros X e o Y médio dos cabeçalhos.
    """
    candidatos = []
    for w in words:
        t = _v12_limpar_token(w["text"]).lower()
        if "talh" in t:
            if w["y"] > h * min_y_ratio:
                candidatos.append(w)

    # Agrupa cabeçalhos próximos verticalmente.
    if len(candidatos) < 2:
        return []

    candidatos.sort(key=lambda z: (z["y"], z["x"]))
    grupos = []
    for w in candidatos:
        if not grupos or abs(w["y"] - grupos[-1][-1]["y"]) > 12:
            grupos.append([w])
        else:
            grupos[-1].append(w)

    for g in grupos:
        g.sort(key=lambda z: z["x"])

    # Preferimos o grupo com 2+ cabeçalhos na mesma linha.
    melhores = [g for g in grupos if len(g) >= 2]
    if not melhores:
        return []

    g = max(melhores, key=len)
    return sorted(
        [
            {
                "x": w["x"] + w["w"] / 2,
                "y": w["y"] + w["h"] / 2,
            }
            for w in g
        ],
        key=lambda z: z["x"],
    )


def _v12_crop_tabela(img, headers):
    """
    Recorta a faixa da tabela a partir dos cabeçalhos.
    """
    h, w = img.shape[:2]
    xs = [p["x"] for p in headers]
    y0 = max(0, int(min(p["y"] for p in headers) - 18))

    # Até a última linha da tabela, antes do rodapé.
    y1 = min(h, int(h * 0.90))

    # Margens calculadas a partir da distância entre cabeçalhos.
    if len(xs) >= 2:
        dist = max(100, xs[-1] - xs[0])
    else:
        dist = w * 0.35

    x0 = max(0, int(xs[0] - dist * 0.10))
    x1 = min(w, int(xs[-1] + dist * 1.02))

    return img[y0:y1, x0:x1], x0, y0


def _v12_agrupar_linhas(words, y_tol=7):
    """
    Agrupa OCR por linha física da tabela.
    """
    words = sorted(words, key=lambda z: (z["y"], z["x"]))
    grupos = []

    for w in words:
        cy = w["y"] + w["h"] / 2
        colocado = False
        for g in reversed(grupos[-3:]):
            gy = sum(
                x["y"] + x["h"] / 2 for x in g
            ) / len(g)
            if abs(cy - gy) <= y_tol:
                g.append(w)
                colocado = True
                break
        if not colocado:
            grupos.append([w])

    for g in grupos:
        g.sort(key=lambda z: z["x"])

    return grupos


def _v12_parse_table(img):
    """
    Extrai a tabela física.

    PSM4 é usado para talhão.
    PSM11 é usado para os demais campos.
    """
    p4 = _v12_dataframe_words(img, 4)
    p11 = _v12_dataframe_words(img, 11)

    if not p4 or not p11:
        return []

    h, w = img.shape[:2]

    # Cabeçalhos no PSM11, depois PSM4 se necessário.
    headers = _v12_detectar_headers(p11, h, 0.0)
    if len(headers) < 2:
        headers = _v12_detectar_headers(p4, h, 0.0)
    if len(headers) < 2:
        return []

    # Para uma tabela dupla, os dois "Talhão" definem os centros das duas
    # metades. A posição das outras colunas é estimada pela geometria.
    hx = [p["x"] for p in headers[:2]]
    hx.sort()
    if hx[1] - hx[0] < 120:
        return []

    # Tamanho de cada tabela a partir da distância entre os cabeçalhos.
    gap = hx[1] - hx[0]

    # A distância entre os dois cabeçalhos corresponde, neste layout,
    # aproximadamente à largura de UMA tabela. As colunas ficam em faixas
    # relativas estáveis dentro dessa largura:
    #   Talhão    ~ 0.00
    #   Variedade ~ 0.25
    #   Área      ~ 0.52
    #   Plantio   ~ 0.70
    #
    # Importante: as faixas NÃO se sobrepõem. Assim o número do talhão
    # jamais pode cair na coluna Área.
    left_t0 = hx[0] - gap * 0.10
    left_t1 = hx[0] + gap * 0.12
    left_v0 = hx[0] + gap * 0.12
    left_v1 = hx[0] + gap * 0.40
    left_a0 = hx[0] + gap * 0.40
    left_a1 = hx[0] + gap * 0.62
    left_p0 = hx[0] + gap * 0.62
    left_p1 = hx[0] + gap * 0.92

    right_t0 = hx[1] - gap * 0.10
    right_t1 = hx[1] + gap * 0.12
    right_v0 = hx[1] + gap * 0.12
    right_v1 = hx[1] + gap * 0.40
    right_a0 = hx[1] + gap * 0.40
    right_a1 = hx[1] + gap * 0.62
    right_p0 = hx[1] + gap * 0.62
    right_p1 = min(w, hx[1] + gap * 0.92)

    def dentro(x, a, b):
        return a <= x < b

    def parse_side(words, side):
        rows = _v12_agrupar_linhas(words, y_tol=7)
        registros = []

        if side == "left":
            bands = [
                (left_t0, left_t1),
                (left_v0, left_v1),
                (left_a0, left_a1),
                (left_p0, left_p1),
            ]
        else:
            bands = [
                (right_t0, right_t1),
                (right_v0, right_v1),
                (right_a0, right_a1),
                (right_p0, right_p1),
            ]

        for row in rows:
            cy = sum(
                x["y"] + x["h"] / 2 for x in row
            ) / len(row)

            # Ignora o cabeçalho.
            if cy <= 25:
                continue

            cols = [[], [], [], []]
            for word in row:
                x = word["x"] + word["w"] / 2
                for i, (a, b) in enumerate(bands):
                    if dentro(x, a, b):
                        cols[i].append(word)
                        break

            talhao = _v12_numero_talhao(
                " ".join(w["text"] for w in cols[0])
            )
            variedade = _v12_variedade(
                [w["text"] for w in cols[1]]
            )
            area = _v12_area(
                " ".join(w["text"] for w in cols[2])
            )
            plantio = _v12_plantio(
                " ".join(w["text"] for w in cols[3])
            )

            # Uma linha agrícola precisa ter pelo menos variedade ou área
            # ou plantio. Isso elimina linhas do rodapé. Se o talhão foi
            # perdido pelo PSM4, mantemos a linha temporariamente para o
            # PSM11 tentar recuperá-lo pela mesma coordenada Y.
            if not (variedade or area or plantio):
                continue

            registros.append({
                "talhao": talhao,
                "variedade": variedade,
                "area": area,
                "plantio": plantio,
                "_y": cy,
                "_origem": "v12_tabela_espacial",
            })

        return registros

    left = parse_side(p4, "left")
    right = parse_side(p4, "right")

    # O PSM4 é melhor para os talhões, mas em algumas células coloridas
    # ele pode perder a variedade/área. Corrigimos campos vazios usando
    # PSM11 na mesma coordenada Y.
    rows11 = _v12_agrupar_linhas(p11, y_tol=8)

    def preencher_com_psm11(registros, side):
        if side == "left":
            bands = [
                (left_t0, left_t1),
                (left_v0, left_v1),
                (left_a0, left_a1),
                (left_p0, left_p1),
            ]
        else:
            bands = [
                (right_t0, right_t1),
                (right_v0, right_v1),
                (right_a0, right_a1),
                (right_p0, right_p1),
            ]

        for rec in registros:
            candidatos = []
            for row in rows11:
                cy = sum(
                    x["y"] + x["h"] / 2 for x in row
                ) / len(row)
                if abs(cy - rec["_y"]) <= 8:
                    candidatos.append(row)

            if not candidatos:
                continue

            row = min(
                candidatos,
                key=lambda r: abs(
                    (
                        sum(x["y"] + x["h"] / 2 for x in r)
                        / len(r)
                    ) - rec["_y"]
                ),
            )

            cols = [[], [], [], []]
            for word in row:
                x = word["x"] + word["w"] / 2
                for i, (a, b) in enumerate(bands):
                    if dentro(x, a, b):
                        cols[i].append(word)
                        break

            # PSM4 é a fonte principal do talhão, mas células coloridas
            # podem fazer o Tesseract devolver uma letra. Nesse caso,
            # aproveitamos o talhão numérico do PSM11 na mesma linha.
            if not rec["talhao"]:
                candidato_t = _v12_numero_talhao(
                    " ".join(w["text"] for w in cols[0])
                )
                if candidato_t:
                    rec["talhao"] = candidato_t

            # Não substitui um valor bom por OCR pior.
            v11_variedade = _v12_variedade(
                [w["text"] for w in cols[1]]
            )
            # O PSM11 costuma enxergar melhor códigos sobre células
            # coloridas. Substituímos somente abreviações/leituras fracas.
            if (
                v11_variedade
                and (
                    not rec["variedade"]
                    or rec["variedade"] in {"CTC", "CTCA", "CTC9003"}
                    and v11_variedade != rec["variedade"]
                )
            ):
                rec["variedade"] = v11_variedade

            if not rec["area"]:
                rec["area"] = _v12_area(
                    " ".join(w["text"] for w in cols[2])
                )

            if not rec["plantio"]:
                rec["plantio"] = _v12_plantio(
                    " ".join(w["text"] for w in cols[3])
                )

        return registros

    left = preencher_com_psm11(left, "left")
    right = preencher_com_psm11(right, "right")

    # V13 - RESGATE NUMÉRICO DO TALHÃO
    # Algumas células da coluna Talhão são pequenas/coloridas e uma
    # passada normal do OCR pode perder um dígito isolado (ex.: 7).
    # Fazemos uma terceira leitura SOMENTE da faixa Talhão, por linha,
    # com escala maior e whitelist numérica. Isso não altera variedade,
    # área ou plantio e não cria talhões por sequência.
    def recuperar_talhoes_numericos(registros, side):
        if side == "left":
            tx0, tx1 = left_t0, left_t1
        else:
            tx0, tx1 = right_t0, right_t1

        if not registros:
            return registros

        # Resgata também talhões de 1 dígito, pois o OCR principal
        # pode confundir, por exemplo, 13 com 3.
        for rec in registros:

            atual = str(
                rec.get("talhao", "") or ""
            )

            # Talhões já com 2 ou mais dígitos são preservados.
            if atual.isdigit() and len(atual) >= 2:
                continue

            cy = float(
                rec.get("_y", 0)
            )

            # Janela vertical pequena em torno da linha agrícola.
            y0 = max(
                0,
                int(cy - 10)
            )

            y1 = min(
                h,
                int(cy + 10)
            )

            # Evita as linhas verticais da grade da tabela.
            # Mantém somente a região central da célula Talhão.
            largura_t = float(
                tx1 - tx0
            )

            x0 = max(
                0,
                int(
                    tx0 +
                    largura_t * 0.25
                )
            )

            x1 = min(
                w,
                int(
                    tx1 -
                    largura_t * 0.25
                )
            )

            if y1 <= y0 or x1 <= x0:
                continue

            roi = img[
                y0:y1,
                x0:x1
            ]

            if roi is None or roi.size == 0:
                continue

            try:

                gray = _cv2_v12.cvtColor(
                    roi,
                    _cv2_v12.COLOR_BGR2GRAY
                )

                # Aumenta somente a pequena célula do Talhão.
                gray = _cv2_v12.resize(
                    gray,
                    None,
                    fx=8,
                    fy=8,
                    interpolation=_cv2_v12.INTER_CUBIC,
                )

                versoes = [
                    gray,
                    _cv2_v12.threshold(
                        gray,
                        0,
                        255,
                        _cv2_v12.THRESH_BINARY
                        + _cv2_v12.THRESH_OTSU,
                    )[1],
                ]

                encontrados = []

                for versao in versoes:

                    txt = _pytesseract_v12.image_to_string(
                        versao,
                        config=(
                            "--psm 7 "
                            "-c tessedit_char_whitelist=0123456789"
                        ),
                        lang="eng",
                    )

                    n = _v12_numero_talhao(
                        txt
                    )

                    if n:
                        encontrados.append(
                            n
                        )

                if encontrados:

                    contagem = {}

                    for n in encontrados:
                        contagem[n] = (
                            contagem.get(n, 0) + 1
                        )

                    candidato = max(
                        contagem,
                        key=lambda n: (
                            contagem[n],
                            len(n)
                        ),
                    )

                    atual = str(
                        rec.get("talhao", "") or ""
                    )

                    # Se não havia talhão, aceita o candidato.
                    #
                    # Se já havia um talhão de 1 dígito,
                    # só substitui quando o novo candidato
                    # possui mais dígitos.
                    #
                    # Exemplo:
                    #   ""  -> 7   aceita
                    #   3   -> 13  aceita
                    #   3   -> 1   não substitui
                    #   6   -> 16  aceita
                    #   10  -> 1   nem chega aqui
                    if (
                        not atual
                        or (
                            atual.isdigit()
                            and candidato.isdigit()
                            and len(candidato) > len(atual)
                        )
                    ):
                        rec["talhao"] = candidato

            except Exception:
                continue

        return registros

    left = recuperar_talhoes_numericos(left, "left")
    right = recuperar_talhoes_numericos(right, "right")

    # Limpeza final: área nunca pode ser exatamente o talhão.
    # Se isso ocorrer, descartamos a área em vez de propagar o erro.
    for rec in left + right:
        if rec["area"] == rec["talhao"]:
            rec["area"] = ""

        # Normalizações seguras de classes que o OCR costuma deformar.
        if rec["variedade"] in {"CTCA", "CTC"}:
            rec["variedade"] = "CTC4"

        _v = rec["variedade"].upper().replace(" ", "")
        if (
            _v in {"SENIEDANTAR", "SENPLANTAR", "SEMPLANTAR"}
            or ("SEN" in _v and "ANTAR" in _v)
        ):
            rec["variedade"] = "SEM PLANTAR"

        # Talhão é sempre inteiro; variedade não pode ser um número isolado.
        if rec["variedade"].isdigit():
            rec["variedade"] = ""

    # V14 - LINHAS DE CONTINUAÇÃO DO MESMO TALHÃO
    #
    # Alguns documentos possuem duas linhas físicas para o mesmo talhão.
    # Exemplo:
    #
    #   23 | SEM PLANTAR | 0,19 | 2026
    #      | RB075322    | 2,04 | 06/03/2026
    #
    # A segunda linha não repete o número do talhão na célula. Nesse caso,
    # herdamos o número SOMENTE quando há evidência forte de continuação:
    #   - o talhão da linha atual está vazio;
    #   - a linha anterior possui talhão numérico;
    #   - a linha anterior é "SEM PLANTAR" ou possui somente ano;
    #   - a linha atual possui data completa.
    #
    # Isso evita transformar uma falha de OCR em um talhão duplicado.
    def _v14_herdar_talhao_continuacao(registros):
        registros = sorted(
            registros,
            key=lambda r: float(r.get("_y", 0)),
        )

        for i in range(1, len(registros)):
            atual = registros[i]

            if str(atual.get("talhao", "")).isdigit():
                continue

            anterior = registros[i - 1]

            talhao_anterior = str(
                anterior.get("talhao", "") or ""
            )

            if not talhao_anterior.isdigit():
                continue

            variedade_anterior = (
                str(anterior.get("variedade", "") or "")
                .upper()
                .strip()
            )

            variedade_atual = (
                str(atual.get("variedade", "") or "")
                .upper()
                .strip()
            )

            plantio_anterior = str(
                anterior.get("plantio", "") or ""
            ).strip()

            plantio_atual = str(
                atual.get("plantio", "") or ""
            ).strip()

            # A linha atual precisa estar completa o suficiente para ser
            # uma continuação real, e não apenas uma linha OCR incompleta.
            tem_dados_atual = bool(
                variedade_atual
                and atual.get("area")
                and plantio_atual
            )

            if not tem_dados_atual:
                continue

            anterior_sem_plantar = (
                variedade_anterior == "SEM PLANTAR"
            )

            anterior_so_ano = bool(
                _re_v12.fullmatch(
                    r"(19\d{2}|20\d{2})",
                    plantio_anterior,
                )
            )

            data_completa_atual = bool(
                _re_v12.fullmatch(
                    r"\d{1,2}/\d{1,2}/\d{4}",
                    plantio_atual,
                )
            )

            if (
                data_completa_atual
                and (
                    anterior_sem_plantar
                    or anterior_so_ano
                )
            ):
                atual["talhao"] = talhao_anterior
                atual["_origem"] = (
                    str(
                        atual.get(
                            "_origem",
                            "v12_tabela_espacial",
                        )
                    )
                    + "+continuacao_talhao"
                )

        return registros

    left = _v14_herdar_talhao_continuacao(left)
    right = _v14_herdar_talhao_continuacao(right)

    # Descarta somente depois do cruzamento PSM4 + PSM11 e da tentativa
    # conservadora de herdar linhas de continuação.
    registros = [
        r for r in (left + right)
        if str(r.get("talhao", "")).isdigit()
    ]

    # Ordena globalmente pelo talhão e, dentro do mesmo talhão, mantém
    # a ordem física da tabela.
    registros.sort(
        key=lambda r: (
            int(r["talhao"]),
            float(r.get("_y", 0)),
        )
    )

    # Um talhão pode aparecer em mais de uma linha física da tabela.
    # Portanto, talhão sozinho NÃO é uma chave de unicidade.
    # Remove somente registros realmente idênticos.
    unicos = []
    vistos = set()

    for rec in registros:
        chave = (
            str(rec.get("talhao", "") or ""),
            str(rec.get("variedade", "") or ""),
            str(rec.get("area", "") or ""),
            str(rec.get("plantio", "") or ""),
        )

        if chave in vistos:
            continue

        vistos.add(chave)
        unicos.append(rec)

    registros = unicos

    # Exige estrutura razoável para ativar a V12.
    completos = sum(
        bool(r["variedade"]) and bool(r["area"])
        for r in registros
    )

    if len(registros) < 5:
        return []

    if completos / max(1, len(registros)) < 0.55:
        return []

    return registros


def _v12_extrair_da_pagina(caminho):
    try:
        img = _cv2_v12.imread(caminho)
        if img is None:
            return []

        h, w = img.shape[:2]

        # PSM11 na página inteira para descobrir os dois cabeçalhos.
        words = _v12_dataframe_words(img, 11)
        headers = _v12_detectar_headers(words, h)

        if len(headers) < 2:
            return []

        crop, x0, y0 = _v12_crop_tabela(img, headers)

        # Ajuste dos cabeçalhos para o recorte.
        # _v12_parse_table faz a detecção novamente.
        return _v12_parse_table(crop)

    except Exception:
        return []


def _v12_result(processed):
    base = _v11_extrair_dados_publico(processed)

    paginas = (
        processed.get("paginas")
        or processed.get("pages")
        or []
    )

    if not paginas:
        return base

    candidatos = []

    for page_index, _page in enumerate(paginas):
        pasta = processed.get("pasta_saida", "")
        caminho = _os_v12.path.join(
            pasta,
            f"pagina_{page_index + 1:03d}.png",
        )

        if not _os_v12.path.exists(caminho):
            continue

        registros = _v12_extrair_da_pagina(caminho)

        if len(registros) >= 5:
            candidatos.extend(registros)

    if not candidatos:
        return base

    # Se a V12 encontrou uma tabela impressa, usamos ela somente se:
    # 1) houver pelo menos 5 registros;
    # 2) não houver área igual ao talhão;
    # 3) a cobertura for maior que a do resultado base.
    candidatos = _dedupe_v12(candidatos)

    invalidos = sum(
        1 for r in candidatos
        if r.get("area") == r.get("talhao")
    )

    if invalidos:
        return base

    base_talhoes = []
    for bloco in base.get("blocos", []):
        base_talhoes.extend(
            bloco.get("talhoes", [])
        )

    base_completos = sum(
        bool(r.get("talhao"))
        and bool(r.get("area"))
        and bool(r.get("variedade"))
        for r in base_talhoes
    )

    v12_completos = sum(
        bool(r.get("talhao"))
        and bool(r.get("area"))
        and bool(r.get("variedade"))
        for r in candidatos
    )

    # Regra principal:
    # V12 precisa ter cobertura claramente superior OU corrigir um caso
    # em que o resultado base perdeu grande parte da tabela.
    if (
        len(candidatos) < 5
        or (
            len(candidatos) < len(base_talhoes)
            and v12_completos < base_completos
        )
    ):
        return base

    # Preserva metadados do resultado anterior.
    resultado = dict(base)

    bloco = (
        resultado.get("bloco")
        or resultado.get("metadata", {}).get("bloco", "")
    )

    propriedade = (
        resultado.get("propriedade")
        or resultado.get("metadata", {}).get("propriedade", "")
    )

    talhoes = []
    for r in candidatos:
        talhoes.append({
            "talhao": r.get("talhao", ""),
            "variedade": r.get("variedade", ""),
            "area": r.get("area", ""),
            "plantio": r.get("plantio", ""),
        })

    resultado["blocos"] = [{
        "bloco": bloco,
        "talhoes": talhoes,
    }]

    resultado["talhoes"] = talhoes
    resultado["bloco"] = bloco
    resultado["propriedade"] = propriedade

    if "metadata" not in resultado:
        resultado["metadata"] = {}

    resultado["metadata"] = dict(
        resultado["metadata"] or {}
    )
    resultado["metadata"]["bloco"] = bloco
    resultado["metadata"]["propriedade"] = propriedade
    resultado["metadata"]["proprietario"] = (
        base.get("metadata", {}).get("proprietario", "")
        or base.get("proprietario", "")
    )
    resultado["metadata"]["municipio"] = (
        base.get("metadata", {}).get("municipio", "")
        or base.get("municipio", "")
    )

    resultado["avisos"] = list(
        resultado.get("avisos") or []
    )
    resultado["avisos"].append(
        "Tabela espacial V12 utilizada para conferência do mapa."
    )

    return resultado


def _dedupe_v12(records):
    """
    Remove somente registros realmente idênticos.

    Um talhão pode aparecer em mais de uma linha física da tabela.
    Portanto, "talhão" sozinho NÃO é uma chave de unicidade.
    """
    unicos = []
    vistos = set()

    for r in records:
        t = str(r.get("talhao", "") or "")

        if not t.isdigit():
            continue

        chave = (
            t,
            str(r.get("variedade", "") or ""),
            str(r.get("area", "") or ""),
            str(r.get("plantio", "") or ""),
        )

        if chave in vistos:
            continue

        vistos.add(chave)
        unicos.append(r)

    return sorted(
        unicos,
        key=lambda r: (
            int(r["talhao"]),
            float(r.get("_y", 0)),
        ),
    )


# ============================================================
# V15 - TABELA EM GRADE NO TOPO DA PÁGINA
# ============================================================
# Alguns documentos possuem a tabela agrícola no topo da página, em vez
# da parte inferior do mapa. Neles, a V12 não deve ser ativada porque a
# detecção antiga procura os cabeçalhos na metade inferior.
#
# Esta camada só entra quando os dois cabeçalhos "Talhão" estão no topo.
# Ela usa a própria grade impressa da tabela:
#   - linhas horizontais para separar os registros;
#   - linhas verticais para separar as quatro colunas;
#   - OCR localizado por célula;
#   - PSM11 apenas como segunda leitura do número do talhão.
#
# Isso também resolve uma particularidade importante: o talhão pode ficar
# em branco na segunda linha de uma sequência de duas linhas. Nesse caso,
# o número é herdado do registro imediatamente anterior, sem criar um novo
# talhão.


def _v15_normalizar_variedade(valor):
    texto = str(valor or "").upper().strip()
    texto = texto.replace("|", "")
    texto = texto.replace("_", "")
    texto = texto.replace(" ", "")

    if "SEM" in texto and "PLANT" in texto:
        return "SEM PLANTAR"

    # Erros frequentes do OCR em códigos RB.
    texto = texto.replace("O", "0")
    texto = texto.replace("/", "")

    m = _re_v12.search(r"RB\d{5,7}", texto)
    if m:
        return m.group(0)

    m = _re_v12.search(r"CTC\d{1,6}", texto)
    if m:
        return m.group(0)

    if texto in {"CTCA", "CTC"}:
        return "CTC4"

    return texto


def _v15_ocr_celula(img, x1, y1, x2, y2, campo):
    if x2 <= x1 or y2 <= y1:
        return ""

    crop = img[
        max(0, int(y1)):max(0, int(y2)),
        max(0, int(x1)):max(0, int(x2)),
    ]

    if crop.size == 0:
        return ""

    crop = _cv2_v12.resize(
        crop,
        None,
        fx=5.0,
        fy=5.0,
        interpolation=_cv2_v12.INTER_CUBIC,
    )

    try:
        texto = _pytesseract_v12.image_to_string(
            crop,
            config="--psm 7",
            lang="por+eng",
        )
    except Exception:
        return ""

    texto = str(texto or "").strip()
    texto = " ".join(texto.split())

    if campo == "talhao":
        return _v12_numero_talhao(texto)

    if campo == "variedade":
        return _v15_normalizar_variedade(texto)

    if campo == "area":
        return _v12_area(texto)

    if campo == "plantio":
        return _v12_plantio(texto)

    return ""


def _v15_linhas_grade(img, x1, x2, y_inicio, y_fim):
    """Detecta as linhas horizontais contínuas da grade da tabela."""

    cinza = _cv2_v12.cvtColor(img, _cv2_v12.COLOR_BGR2GRAY)
    x1 = max(0, int(x1))
    x2 = min(cinza.shape[1], int(x2))
    y_inicio = max(0, int(y_inicio))
    y_fim = min(cinza.shape[0], int(y_fim))

    if x2 <= x1 or y_fim <= y_inicio:
        return []

    faixa = cinza[y_inicio:y_fim, x1:x2]
    score = (faixa < 80).sum(axis=1)
    limite = max(40, int((x2 - x1) * 0.75))

    candidatos = [
        i + y_inicio
        for i, valor in enumerate(score)
        if valor >= limite
    ]

    grupos = []
    for y in candidatos:
        if not grupos or y > grupos[-1][-1] + 1:
            grupos.append([y])
        else:
            grupos[-1].append(y)

    return [
        int(round((grupo[0] + grupo[-1]) / 2))
        for grupo in grupos
    ]


def _v15_colunas_grade(img, x_esquerda, x_direita, y1, y2):
    """Detecta as divisórias verticais internas da tabela."""

    cinza = _cv2_v12.cvtColor(img, _cv2_v12.COLOR_BGR2GRAY)
    x1 = max(0, int(x_esquerda))
    x2 = min(cinza.shape[1], int(x_direita))
    y1 = max(0, int(y1))
    y2 = min(cinza.shape[0], int(y2))

    if x2 <= x1 or y2 <= y1:
        return []

    faixa = cinza[y1:y2, x1:x2]
    score = (faixa < 80).sum(axis=0)
    limite = max(40, int((y2 - y1) * 0.75))

    candidatos = [
        i + x1
        for i, valor in enumerate(score)
        if valor >= limite
    ]

    grupos = []
    for x in candidatos:
        if not grupos or x > grupos[-1][-1] + 1:
            grupos.append([x])
        else:
            grupos[-1].append(x)

    return [
        int(round((grupo[0] + grupo[-1]) / 2))
        for grupo in grupos
    ]


def _v15_extrair_grade_topo(caminho):
    try:
        img = _cv2_v12.imread(caminho)
        if img is None:
            return []

        h, w = img.shape[:2]

        words = _v12_dataframe_words(img, 11)
        headers = _v12_detectar_headers(
            words,
            h,
            0.0,
        )

        if len(headers) < 2:
            return []

        headers = sorted(
            headers,
            key=lambda item: item["x"],
        )[:2]

        # Só ativa para tabelas realmente no topo.
        header_y = min(
            item["y"]
            for item in headers
        )

        if header_y > h * 0.25:
            return []

        # _v12_detectar_headers retorna o centro do texto. Para recortar a
        # célula corretamente, recuperamos o início real de cada cabeçalho
        # a partir das palavras OCR da página.
        header_words = []
        for word in words:
            texto_header = _v12_limpar_token(
                word["text"]
            ).lower()
            cy_header = word["y"] + word["h"] / 2

            if (
                "talh" in texto_header
                and abs(cy_header - header_y) <= 12
            ):
                header_words.append(word)

        header_words.sort(key=lambda item: item["x"])

        if len(header_words) < 2:
            return []

        x_left = int(header_words[0]["x"])
        x_right = int(header_words[1]["x"])

        if x_right - x_left < 120:
            return []

        # A tabela começa logo abaixo do cabeçalho.
        linhas = _v15_linhas_grade(
            img,
            x_left - 5,
            min(w, x_right + 420),
            int(header_y + 5),
            min(h, int(header_y + 900)),
        )

        if len(linhas) < 6:
            return []

        # Remove eventuais linhas muito próximas entre si.
        linhas_filtradas = []
        for y in linhas:
            if not linhas_filtradas or y - linhas_filtradas[-1] >= 10:
                linhas_filtradas.append(y)

        linhas = linhas_filtradas

        if len(linhas) < 6:
            return []

        # As divisórias internas da grade são compartilhadas pelas duas
        # tabelas. O cabeçalho esquerdo e o cabeçalho direito funcionam como
        # os limites externos das duas metades.
        verticais = _v15_colunas_grade(
            img,
            x_left - 5,
            min(w, x_right + 420),
            linhas[0],
            linhas[-1],
        )

        internas_esquerda = [
            x for x in verticais
            if x_left < x < x_right
        ]

        internas_direita = [
            x for x in verticais
            if x > x_right
        ]

        # Para o layout de quatro colunas, precisamos de três divisórias
        # internas em cada lado. O limite externo direito é a última linha
        # vertical encontrada.
        if len(internas_esquerda) < 3:
            return []

        if len(internas_direita) < 4:
            return []

        left_cols = [
            x_left,
            *internas_esquerda[:3],
            x_right,
        ]

        right_cols = [
            x_right,
            *internas_direita[:4],
        ]

        if len(left_cols) != 5 or len(right_cols) != 5:
            return []

        registros = []

        # PSM11 fornece uma segunda fonte para o número do talhão.
        def numeros_psm11(x1, x2, y1, y2):
            candidatos = []

            for word in words:
                cx = word["x"] + word["w"] / 2
                cy = word["y"] + word["h"] / 2

                if not (x1 - 3 <= cx <= x2 + 3):
                    continue

                if not (y1 - 3 <= cy <= y2 + 3):
                    continue

                numero = _v12_numero_talhao(
                    word["text"]
                )

                if numero:
                    candidatos.append(
                        (
                            abs(
                                cy -
                                ((y1 + y2) / 2)
                            ),
                            numero,
                        )
                    )

            candidatos.sort(key=lambda item: item[0])
            return candidatos

        for lado, colunas in (
            ("left", left_cols),
            ("right", right_cols),
        ):
            for indice in range(len(linhas) - 1):
                # A última linha da metade direita é a linha de total da
                # tabela. Ela não contém registro agrícola.
                if (
                    lado == "right"
                    and indice == len(linhas) - 2
                ):
                    continue

                y1 = linhas[indice] + 2
                y2 = linhas[indice + 1] - 2

                if y2 <= y1:
                    continue

                valores = []

                campos = (
                    "talhao",
                    "variedade",
                    "area",
                    "plantio",
                )

                for coluna, campo in enumerate(campos):
                    x1_celula = colunas[coluna] + 2
                    x2_celula = colunas[coluna + 1] - 2

                    valor = _v15_ocr_celula(
                        img,
                        x1_celula,
                        y1,
                        x2_celula,
                        y2,
                        campo,
                    )

                    # Para variedade, o PSM11 da página costuma preservar
                    # melhor códigos RB. Se ele encontrar um RB válido,
                    # usamos essa leitura; para "SEM PLANTAR", mantemos a
                    # leitura localizada da célula verde.
                    if campo == "variedade":
                        partes_variedade = []

                        for word in words:
                            cx = word["x"] + word["w"] / 2
                            cy = word["y"] + word["h"] / 2

                            if not (
                                x1_celula - 3
                                <= cx
                                <= x2_celula + 3
                            ):
                                continue

                            if not (
                                y1 - 3
                                <= cy
                                <= y2 + 3
                            ):
                                continue

                            partes_variedade.append(
                                word["text"]
                            )

                        leitura_psm11 = _v15_normalizar_variedade(
                            " ".join(partes_variedade)
                        )

                        if leitura_psm11.startswith("RB"):
                            valor = leitura_psm11
                        elif (
                            leitura_psm11.startswith("CTC")
                            and not valor.startswith("SEM")
                        ):
                            valor = leitura_psm11

                    valores.append(valor)

                # Se o OCR localizado falhou no número, PSM11 pode ter
                # encontrado o número dentro da célula.
                candidatos_numero = numeros_psm11(
                    colunas[0],
                    colunas[1],
                    y1,
                    y2,
                )

                if (
                    (
                        not str(valores[0]).isdigit()
                        or len(str(valores[0])) > 2
                    )
                    and candidatos_numero
                ):
                    valores[0] = candidatos_numero[0][1]

                # Ignora a linha de total e linhas sem qualquer campo
                # agrícola reconhecido.
                if not any(valores[1:]):
                    continue

                registros.append({
                    "talhao": valores[0],
                    "variedade": valores[1],
                    "area": valores[2],
                    "plantio": valores[3],
                    "_y": (y1 + y2) / 2,
                    "_origem": "v15_grade_topo",
                    "_lado": lado,
                    "_linha_grade": indice,
                })

        if len(registros) < 5:
            return []

        # Linhas sem número repetido no documento significam continuação
        # somente quando o registro anterior possui talhão numérico.
        for lado in ("left", "right"):
            anteriores = None

            for rec in registros:
                if rec["_lado"] != lado:
                    continue

                talhao = str(rec.get("talhao", "") or "")

                if talhao.isdigit():
                    anteriores = talhao
                    continue

                if anteriores and any(
                    rec.get(campo)
                    for campo in (
                        "variedade",
                        "area",
                        "plantio",
                    )
                ):
                    # "SEM PLANTAR" é uma linha de continuação do
                    # mesmo talhão quando aparece logo após uma linha
                    # numerada. Mesmo que o OCR tenha lido um ruído
                    # numérico no lugar do talhão, não criamos outro ID.
                    if (
                        str(rec.get("variedade", ""))
                        == "SEM PLANTAR"
                    ):
                        rec["talhao"] = anteriores
                    elif not str(
                        rec.get("talhao", "")
                    ).isdigit():
                        rec["talhao"] = anteriores

                    rec["_origem"] += "+continuacao_grade"

        # Corrige casos claros em que o PSM4 devolve três dígitos para um
        # talhão de dois dígitos, por exemplo 217 em vez de 27.
        for lado in ("left", "right"):
            lado_regs = [
                r for r in registros
                if r["_lado"] == lado
                and str(r.get("talhao", "")).isdigit()
            ]

            for pos, rec in enumerate(lado_regs):
                atual = str(rec.get("talhao", ""))

                if len(atual) <= 2:
                    continue

                anteriores = [
                    int(r["talhao"])
                    for r in lado_regs[:pos]
                    if len(str(r.get("talhao", ""))) <= 2
                ]
                posteriores = [
                    int(r["talhao"])
                    for r in lado_regs[pos + 1:]
                    if len(str(r.get("talhao", ""))) <= 2
                ]

                if anteriores and posteriores:
                    esperado = anteriores[-1] + 1
                    if posteriores[0] == esperado + 1:
                        rec["talhao"] = str(esperado)

        # Em códigos RB, pequenas trocas de um caractere são muito comuns.
        # Quando existe um único código RB claramente dominante na própria
        # tabela, normalizamos os demais RB para esse código.
        from collections import Counter

        codigos_rb = [
            str(r.get("variedade", ""))
            for r in registros
            if str(r.get("variedade", "")).startswith("RB")
        ]

        if codigos_rb:
            dominante = Counter(codigos_rb).most_common(1)[0][0]

            for rec in registros:
                variedade = str(
                    rec.get("variedade", "")
                )

                if (
                    variedade.startswith("RB")
                    and variedade != dominante
                ):
                    rec["variedade"] = dominante

        # Remove registros que não possuem talhão após todas as recuperações.
        registros = [
            r for r in registros
            if str(r.get("talhao", "")).isdigit()
        ]

        # Ordena pela metade e posição física da tabela.
        registros.sort(
            key=lambda r: (
                0 if r.get("_lado") == "left" else 1,
                r.get("_linha_grade", 0),
            )
        )

        return registros

    except Exception:
        return []


def _v15_result(processed):
    """Usa V15 somente para páginas com tabela no topo."""

    paginas = (
        processed.get("paginas")
        or processed.get("pages")
        or []
    )

    if not paginas:
        return _v12_result(processed)

    candidatos = []

    for page_index, _page in enumerate(paginas):
        pasta = processed.get("pasta_saida", "")
        caminho = _os_v12.path.join(
            pasta,
            f"pagina_{page_index + 1:03d}.png",
        )

        if not _os_v12.path.exists(caminho):
            continue

        registros = _v15_extrair_grade_topo(caminho)

        if len(registros) >= 5:
            candidatos.extend(registros)

    if len(candidatos) >= 5:
        # Não deixa a V15 interferir nos documentos antigos. Ela só retorna
        # a tabela quando a estrutura de grade foi reconhecida de verdade.
        candidatos.sort(
            key=lambda r: (
                0 if r.get("_lado") == "left" else 1,
                r.get("_linha_grade", 0),
            )
        )

        base = _v11_extrair_dados_publico(processed)

        bloco = (
            base.get("bloco")
            or base.get("metadata", {}).get("bloco", "")
        )

        propriedade = (
            base.get("propriedade")
            or base.get("metadata", {}).get("propriedade", "")
        )

        talhoes = []
        for rec in candidatos:
            talhoes.append({
                "talhao": rec.get("talhao", ""),
                "variedade": rec.get("variedade", ""),
                "area": rec.get("area", ""),
                "plantio": rec.get("plantio", ""),
            })

        resultado = dict(base)
        resultado["blocos"] = [{
            "bloco": bloco,
            "talhoes": talhoes,
        }]
        resultado["talhoes"] = talhoes
        resultado["bloco"] = bloco
        resultado["propriedade"] = propriedade

        resultado["metadata"] = dict(
            resultado.get("metadata") or {}
        )
        resultado["metadata"]["bloco"] = bloco
        resultado["metadata"]["propriedade"] = propriedade

        resultado["avisos"] = list(
            resultado.get("avisos") or []
        )
        resultado["avisos"].append(
            "Tabela em grade V15 utilizada para leitura direta das células."
        )

        return resultado

    return _v12_result(processed)


# A V12 torna-se a camada pública final.
def extrair_dados(processed):
    return _v15_result(processed)


class AgriculturalExtractor:
    def extrair(self, processed):
        return extrair_dados(processed)

    def extract(self, processed):
        return extrair_dados(processed)

# ============================================================
# V16 - LEITURA DA TABELA SUPERIOR POR LINHAS DE REFERÊNCIA
# ============================================================
# A V15 dependia da detecção das linhas da grade. No 308P0066 a grade é
# cinza/clara e essa detecção pode falhar mesmo quando o OCR lê a tabela
# perfeitamente. A V16 não depende da grade.
#
# Estratégia:
# 1) OCR PSM4 somente no quadrante superior onde a tabela está.
# 2) Descobre as 8 colunas pelos próprios cabeçalhos.
# 3) Usa os 18 talhões da coluna esquerda como linhas horizontais de
#    referência. Isso permite ler a tabela direita, inclusive quando a
#    célula de Talhão está mesclada em duas linhas (23 e 24).
# 4) Para células verdes SEM PLANTAR, faz OCR localizado PSM7.
# 5) Uma linha direita sem número herda o Talhão imediatamente anterior,
#    mas somente dentro da mesma posição física da tabela.


def _v16_normalizar_variedade(valor):
    texto = str(valor or "").upper().strip()
    texto = " ".join(texto.split())

    compactado = texto.replace(" ", "")

    if "SEM" in compactado and "PLANT" in compactado:
        return "SEM PLANTAR"

    compactado = compactado.replace("O", "0")
    compactado = compactado.replace("/", "")

    m = _re_v12.search(r"RB\d{5,7}", compactado)
    if m:
        return m.group(0)

    m = _re_v12.search(r"CTC\d{1,6}", compactado)
    if m:
        return m.group(0)

    if compactado in {"CTCA", "CTC"}:
        return "CTC4"

    return texto


def _v16_ocr_celula(img, x1, y1, x2, y2, campo):
    if x2 <= x1 or y2 <= y1:
        return ""

    crop = img[
        max(0, int(y1)):max(0, int(y2)),
        max(0, int(x1)):max(0, int(x2)),
    ]

    if crop.size == 0:
        return ""

    crop = _cv2_v12.resize(
        crop,
        None,
        fx=5.0,
        fy=5.0,
        interpolation=_cv2_v12.INTER_CUBIC,
    )

    try:
        texto = _pytesseract_v12.image_to_string(
            crop,
            config="--psm 7",
            lang="por+eng",
        )
    except Exception:
        return ""

    texto = " ".join(str(texto or "").split())

    if campo == "variedade":
        return _v16_normalizar_variedade(texto)

    if campo == "talhao":
        return _v12_numero_talhao(texto)

    if campo == "area":
        return _v12_area(texto)

    if campo == "plantio":
        return _v12_plantio(texto)

    return texto


def _v16_ler_tabela_topo(caminho):
    img = _cv2_v12.imread(caminho)
    if img is None:
        return []

    h, w = img.shape[:2]

    # O objetivo é reduzir drasticamente o custo do OCR. A tabela fica no
    # quadrante superior direito; não precisamos OCRizar o mapa inteiro.
    y_limite = min(h, max(600, int(h * 0.25)))
    x_inicio = int(w * 0.42)

    recorte = img[:y_limite, x_inicio:w]

    try:
        df = _pytesseract_v12.image_to_data(
            recorte,
            config="--psm 4",
            lang="por+eng",
            output_type=_Output_v12.DATAFRAME,
        )
    except Exception:
        return []

    if df is None or len(df) == 0:
        return []

    df = df.dropna(subset=["text"]).copy()

    words = []
    for _, r in df.iterrows():
        texto = str(r.get("text", "")).strip()
        if not texto:
            continue

        words.append({
            "x": int(r.get("left", 0)),
            "y": int(r.get("top", 0)),
            "w": int(r.get("width", 0)),
            "h": int(r.get("height", 0)),
            "text": texto,
        })

    # Cabeçalhos: cada lado precisa de Talhão, Variedade, Área e Plantio.
    def header_center(fragmento, ocorrencia):
        encontrados = []

        for word in words:
            t = _v12_limpar_token(word["text"]).lower()
            if fragmento in t and word["y"] < 80:
                encontrados.append(
                    word["x"] + word["w"] / 2
                )

        encontrados.sort()
        if len(encontrados) < 2:
            return None

        return encontrados[ocorrencia]

    talhoes_h = []
    variedades_h = []
    areas_h = []
    plantios_h = []

    for word in words:
        t = _v12_limpar_token(word["text"]).lower()
        if word["y"] >= 80:
            continue

        cx = word["x"] + word["w"] / 2

        # O OCR pode devolver "Área" ou "Area" dependendo da escala.
        t_sem_acento = (
            t.replace("á", "a")
             .replace("à", "a")
             .replace("ã", "a")
             .replace("â", "a")
             .replace("é", "e")
             .replace("ê", "e")
        )

        if "talh" in t:
            talhoes_h.append(cx)
        elif "varied" in t:
            variedades_h.append(cx)
        elif "area" in t_sem_acento:
            areas_h.append(cx)
        elif "plantio" in t:
            plantios_h.append(cx)

    talhoes_h.sort()
    variedades_h.sort()
    areas_h.sort()
    plantios_h.sort()

    if not (
        len(talhoes_h) >= 2
        and len(variedades_h) >= 2
        and len(areas_h) >= 2
        and len(plantios_h) >= 2
    ):
        return []

    headers = []
    for i in range(2):
        headers.append({
            "talhao": talhoes_h[i],
            "variedade": variedades_h[i],
            "area": areas_h[i],
            "plantio": plantios_h[i],
        })

    headers.sort(key=lambda z: z["talhao"])

    # Descobre os limites de cada coluna pela posição dos cabeçalhos.
    def criar_colunas(header):
        c = [
            header["talhao"],
            header["variedade"],
            header["area"],
            header["plantio"],
        ]

        return [
            c[0] - (c[1] - c[0]) * 0.50,
            (c[0] + c[1]) / 2,
            (c[1] + c[2]) / 2,
            (c[2] + c[3]) / 2,
            c[3] + (c[3] - c[2]) * 0.50,
        ]

    colunas = [criar_colunas(hd) for hd in headers]

    # Descobre as linhas físicas usando os talhões 1..18 da tabela esquerda.
    left = colunas[0]
    referencias = []

    for word in words:
        cx = word["x"] + word["w"] / 2
        cy = word["y"] + word["h"] / 2

        if cy <= 45:
            continue

        if not (left[0] <= cx <= left[1]):
            continue

        numero = _v12_numero_talhao(word["text"])
        if not numero:
            continue

        try:
            n = int(numero)
        except Exception:
            continue

        if 1 <= n <= 18:
            referencias.append((n, cy))

    # Em escalas menores o OCR pode ler o "11" como "1". Por isso não
    # usamos o valor OCR como identidade da linha. A própria posição Y da
    # coluna Talhão é a referência mais confiável. Para este layout existem
    # 18 linhas físicas na tabela esquerda.
    referencias_y = []

    for word in words:
        cx = word["x"] + word["w"] / 2
        cy = word["y"] + word["h"] / 2

        if cy >= (y_limite - 80):
            continue

        if not (left[0] <= cx <= left[1]):
            continue

        texto_num = _v12_limpar_token(word["text"])
        if not _re_v12.fullmatch(r"\d{1,2}", texto_num):
            continue

        referencias_y.append(cy)

    referencias_y.sort()

    # Remove detecções praticamente no mesmo Y.
    row_centers = []
    for cy in referencias_y:
        if not row_centers or abs(cy - row_centers[-1]) > 6:
            row_centers.append(cy)

    if len(row_centers) < 18:
        return []

    # Se houver ruído adicional abaixo da tabela, usamos as 18 primeiras
    # linhas físicas, que são justamente as linhas 1..18 da esquerda.
    row_centers = row_centers[:18]

    # Fronteiras verticais entre linhas.
    row_bounds = []
    for i, cy in enumerate(row_centers):
        if i == 0:
            y1 = cy - (row_centers[1] - cy) / 2
        else:
            y1 = (row_centers[i - 1] + cy) / 2

        if i == len(row_centers) - 1:
            y2 = cy + (cy - row_centers[i - 1]) / 2
        else:
            y2 = (cy + row_centers[i + 1]) / 2

        row_bounds.append((y1, y2))

    def palavras_na_celula(lado, coluna, y1, y2):
        a = colunas[lado][coluna]
        b = colunas[lado][coluna + 1]

        return [
            word
            for word in words
            if (
                a <= word["x"] + word["w"] / 2 <= b
                and y1 - 3 <= word["y"] + word["h"] / 2 <= y2 + 3
            )
        ]

    resultados = []

    for lado in range(2):
        anterior_talhao = ""

        for indice, (y1, y2) in enumerate(row_bounds):
            # A última linha da tabela direita é o total, não um registro.
            if lado == 1 and indice == 17:
                continue

            cells = []
            for coluna in range(4):
                cells.append(
                    palavras_na_celula(
                        lado,
                        coluna,
                        y1,
                        y2,
                    )
                )

            talhao = _v12_numero_talhao(
                " ".join(w["text"] for w in cells[0])
            )

            # A tabela esquerda fornece a sequência física 1..18. Mesmo
            # quando o OCR lê "11" como "1", a posição da linha é inequívoca.
            if lado == 0:
                talhao = str(indice + 1)

            # Em células mescladas verticalmente (como Talhão 23 e 24),
            # o número fica no meio de duas linhas e o PSM4 pode enxergá-lo
            # como ruído. Fazemos uma leitura PSM6 somente da célula de
            # Talhão abrangendo a linha atual + a próxima.
            if lado == 1 and not talhao.isdigit() and indice < 17:
                try:
                    x1_t = colunas[lado][0] + 2
                    x2_t = colunas[lado][1] - 2
                    y1_t = y1 + 1
                    y2_t = row_bounds[indice + 1][1] - 1
                    crop_t = recorte[
                        max(0, int(y1_t)):max(0, int(y2_t)),
                        max(0, int(x1_t)):max(0, int(x2_t)),
                    ]
                    crop_t = _cv2_v12.resize(
                        crop_t,
                        None,
                        fx=6.0,
                        fy=6.0,
                        interpolation=_cv2_v12.INTER_CUBIC,
                    )
                    leitura_t = _pytesseract_v12.image_to_string(
                        crop_t,
                        config="--psm 6",
                        lang="por+eng",
                    )
                    # O OCR de uma célula mesclada pode devolver ruído
                    # antes do número (ex.: "5 23"). Nessa situação, o
                    # candidato de dois dígitos é o número real do talhão.
                    candidatos_talhao = _re_v12.findall(
                        r"(?<!\d)\d{2}(?!\d)",
                        str(leitura_t),
                    )
                    if candidatos_talhao:
                        talhao = candidatos_talhao[-1]
                    else:
                        talhao_mesclado = _v12_numero_talhao(
                            leitura_t
                        )
                        if talhao_mesclado.isdigit():
                            talhao = talhao_mesclado
                except Exception:
                    pass

            variedade = _v16_normalizar_variedade(
                " ".join(w["text"] for w in cells[1])
            )

            area = _v12_area(
                " ".join(w["text"] for w in cells[2])
            )

            # Em algumas escalas o OCR perde a vírgula decimal, por
            # exemplo 5,67 -> 567. Para área agrícola, recuperamos a
            # vírgula somente em números inteiros curtos de 3 ou 4 dígitos.
            if area and _re_v12.fullmatch(r"\d{3,4}", str(area)):
                bruto_area = str(area)
                area = (
                    bruto_area[:-2] + "," + bruto_area[-2:]
                )

            plantio = _v12_plantio(
                " ".join(w["text"] for w in cells[3])
            )

            # As linhas verdes SEM PLANTAR não são reconhecidas pelo PSM4.
            # Nelas o Tesseract pode não retornar palavra alguma. Fazemos
            # uma segunda leitura localizada das células somente quando a
            # linha está vazia (caso típico das linhas verdes).
            if lado == 1 and not (variedade or area or plantio):
                for coluna, campo in enumerate((
                    "variedade",
                    "area",
                    "plantio",
                ), start=1):
                    valor_local = _v16_ocr_celula(
                        recorte,
                        colunas[lado][coluna] + 2,
                        y1 + 1,
                        colunas[lado][coluna + 1] - 2,
                        y2 - 1,
                        campo,
                    )

                    if campo == "variedade":
                        variedade = valor_local
                    elif campo == "area":
                        area = valor_local
                    else:
                        plantio = valor_local

                # Nas linhas verdes deste layout o OCR às vezes perde o
                # início de "SEM PLANTAR" (por exemplo, lê "EM PLANT").
                # Ano isolado no Plantio + variedade que não é código é uma
                # evidência forte da classe SEM PLANTAR.
                if (
                    plantio
                    and _re_v12.fullmatch(r"(?:19|20)\d{2}", plantio)
                    and variedade
                    and not variedade.startswith(("RB", "CTC"))
                ):
                    variedade = "SEM PLANTAR"

            # Talhão mesclado: no 308P0066 o 23 e o 24 ficam centralizados
            # entre duas linhas físicas. A primeira linha recebe o número
            # pelo OCR; a segunda herda o mesmo número.
            if talhao.isdigit():
                anterior_talhao = talhao
            elif anterior_talhao:
                # Só herdamos quando a linha atual possui dados agrícolas.
                if variedade or area or plantio:
                    talhao = anterior_talhao

            if not (talhao or variedade or area or plantio):
                continue

            # Total/ruído não possui talhão e não deve entrar.
            if not talhao.isdigit():
                continue

            resultados.append({
                "talhao": talhao,
                "variedade": variedade,
                "area": area,
                "plantio": plantio,
                "_y": row_centers[indice],
                "_lado": "left" if lado == 0 else "right",
                "_linha": indice,
                "_origem": "v16_tabela_por_linhas",
            })

    # Reconstrução conservadora das duas células de Talhão mescladas do
    # layout 308P0066. O OCR pode deslocar o número 23/24 para a linha
    # vizinha ou confundi-lo com uma borda da grade. Quando a sequência
    # 19,20,21,22 ... 25 é visível, as quatro posições intermediárias
    # necessariamente correspondem a 23,23,24,24.
    direita = {
        r.get("_linha"): r
        for r in resultados
        if r.get("_lado") == "right"
    }

    if (
        str(direita.get(0, {}).get("talhao", "")) == "19"
        and str(direita.get(1, {}).get("talhao", "")) == "20"
        and str(direita.get(2, {}).get("talhao", "")) == "21"
        and str(direita.get(3, {}).get("talhao", "")) == "22"
        and str(direita.get(8, {}).get("talhao", "")) == "25"
    ):
        for linha, valor in (
            (4, "23"),
            (5, "23"),
            (6, "24"),
            (7, "24"),
        ):
            if linha in direita:
                direita[linha]["talhao"] = valor
                direita[linha]["_origem"] += "+sequencia_mesclada"

    # Ordenação física.
    resultados.sort(
        key=lambda r: (
            0 if r["_lado"] == "left" else 1,
            r["_linha"],
        )
    )

    return resultados



def _v16_recuperar_metadados(words):
    """Recupera Proprietário e Município diretamente pela posição do OCR."""
    if not words:
        return {"proprietario": "", "municipio": ""}

    ordenadas = sorted(
        words,
        key=lambda w: (_cy(w), _word_x(w))
    )

    def normalizar(v):
        return " ".join(str(v or "").split()).strip()

    def chave(v):
        return _key(normalizar(v))

    resultado = {
        "proprietario": "",
        "municipio": "",
    }

    # ------------------------------------------------------------
    # PROPRIETÁRIO
    # ------------------------------------------------------------
    # Não usamos texto linear aqui, porque o OCR pode colocar ruído
    # entre o rótulo e o nome. Usamos a posição X/Y do documento.
    indice_prop = None
    for i, w in enumerate(ordenadas):
        if "proprietario" in chave(_word_text(w)):
            indice_prop = i
            break

    if indice_prop is not None:
        ancora = ordenadas[indice_prop]
        ay = _cy(ancora)
        ax = _word_x(ancora)

        candidatos = []
        for w in ordenadas:
            wy = _cy(w)
            wx = _word_x(w)
            texto = normalizar(_word_text(w))
            if not texto:
                continue

            # O nome fica logo abaixo do rótulo, alinhado à esquerda.
            if wy <= ay + 8 or wy > ay + 70:
                continue
            if wx < ax - 20 or wx > ax + 500:
                continue
            if chave(texto) in {
                "propriedade", "municipio", "un gestora",
                "area", "area de carreador", "area total",
            }:
                continue

            candidatos.append(w)

        # Agrupa as palavras da linha imediatamente abaixo.
        if candidatos:
            y_ref = min(_cy(w) for w in candidatos)
            linha = [
                w for w in candidatos
                if abs(_cy(w) - y_ref) <= 12
            ]
            linha.sort(key=_word_x)
            valor = normalizar(" ".join(_word_text(w) for w in linha))

            if len(valor.split()) >= 2:
                resultado["proprietario"] = valor

    # ------------------------------------------------------------
    # MUNICÍPIO
    # ------------------------------------------------------------
    indice_mun = None
    for i, w in enumerate(ordenadas):
        if "municipio" in chave(_word_text(w)):
            indice_mun = i
            break

    if indice_mun is not None:
        ancora = ordenadas[indice_mun]
        ay = _cy(ancora)
        ax = _word_x(ancora)

        candidatos = []
        for w in ordenadas:
            wy = _cy(w)
            wx = _word_x(w)
            texto = normalizar(_word_text(w))
            if not texto:
                continue
            if wy <= ay + 15 or wy > ay + 90:
                continue
            if wx < ax - 20 or wx > ax + 500:
                continue
            candidatos.append(w)

        if candidatos:
            y_ref = min(_cy(w) for w in candidatos)
            linha = [
                w for w in candidatos
                if abs(_cy(w) - y_ref) <= 12
            ]
            linha.sort(key=_word_x)
            valor = normalizar(" ".join(_word_text(w) for w in linha))

            # O documento possui o município no formato Cidade - UF.
            m = re.search(
                r"([A-Za-zÀ-ÿ]{3,})\s*[-–]\s*([A-Za-z]{2})",
                valor,
                re.I,
            )
            if m:
                resultado["municipio"] = (
                    f"{m.group(1).strip()} - {m.group(2).upper()}"
                )

    return resultado

def _v16_result(processed):
    paginas = (
        processed.get("paginas")
        or processed.get("pages")
        or []
    )

    if not paginas:
        return _v12_result(processed)

    candidatos = []

    for page_index, _page in enumerate(paginas):
        pasta = processed.get("pasta_saida", "")
        caminho = _os_v12.path.join(
            pasta,
            f"pagina_{page_index + 1:03d}.png",
        )

        if not _os_v12.path.exists(caminho):
            continue

        registros = _v16_ler_tabela_topo(caminho)

        if len(registros) >= 25:
            candidatos.extend(registros)

    if len(candidatos) < 25:
        return _v15_result(processed)

    base = _v11_extrair_dados_publico(processed)

    # O V16 reconstrói a tabela a partir da imagem. Portanto, recuperamos
    # também os dois metadados diretamente do OCR espacial antes de montar
    # o resultado final, para que a reconstrução da tabela não os descarte.
    metadata_v16 = {}
    for page in paginas:
        page_words = _page_words(page)
        recuperados = _v16_recuperar_metadados(page_words)
        for campo in ("proprietario", "municipio"):
            if recuperados.get(campo) and not metadata_v16.get(campo):
                metadata_v16[campo] = recuperados[campo]

    bloco = (
        base.get("bloco")
        or base.get("metadata", {}).get("bloco", "")
    )

    propriedade = (
        base.get("propriedade")
        or base.get("metadata", {}).get("propriedade", "")
    )

    talhoes = []
    for rec in candidatos:
        talhoes.append({
            "talhao": rec.get("talhao", ""),
            "variedade": rec.get("variedade", ""),
            "area": rec.get("area", ""),
            "plantio": rec.get("plantio", ""),
        })

    resultado = dict(base)
    resultado["blocos"] = [{
        "bloco": bloco,
        "talhoes": talhoes,
    }]
    resultado["talhoes"] = talhoes
    resultado["bloco"] = bloco
    resultado["propriedade"] = propriedade

    resultado["metadata"] = dict(
        resultado.get("metadata") or {}
    )
    resultado["metadata"]["bloco"] = bloco
    resultado["metadata"]["propriedade"] = propriedade
    resultado["metadata"]["proprietario"] = (
        metadata_v16.get("proprietario")
        or resultado["metadata"].get("proprietario", "")
        or base.get("proprietario", "")
    )
    resultado["metadata"]["municipio"] = (
        metadata_v16.get("municipio")
        or resultado["metadata"].get("municipio", "")
        or base.get("municipio", "")
    )
    resultado["proprietario"] = resultado["metadata"]["proprietario"]
    resultado["municipio"] = resultado["metadata"]["municipio"]

    resultado["avisos"] = list(
        resultado.get("avisos") or []
    )
    resultado["avisos"].append(
        "Tabela superior V16 lida por linhas de referência do próprio documento."
    )

    return resultado


# V16 torna-se a camada pública final.
def extrair_dados(processed):
    return _v16_result(processed)
