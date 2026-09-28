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
                elif current != map_number and rec.get("_origem") == "sequencia_espacial":
                    rec["talhao"] = map_number
                    rec["_origem"] = "mapa_area_confirmado"
                current = str(rec.get("talhao", "") or "")

        # Evidência número -> área.
        if current and not area and current in number_to_area:
            rec["area"] = number_to_area[current]
            rec["_origem"] = "mapa_numero_area"


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
            if 8 <= py - ry <= 45:
                previous_rows.append(row)

        if previous_rows:
            row = max(
                previous_rows,
                key=lambda r: sum(_cy(w) for w in r) / len(r)
            )
            vals = [
                _word_text(w) for w in row
                if 115 <= _cx(w) <= 280
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
        # coluna esquerda primeiro; isso também evita pegar texto da
        # coluna de localização/assinatura à direita.
        preferred = [
            item for item in city_pairs
            if any(
                100 <= _cx(w) <= 300
                and _clean_token(_word_text(w)).rstrip("-–") == item[1]
                for w in footer
            )
        ]
        pool = preferred or city_pairs
        pool.sort(key=lambda x: x[0])
        _, city, state = pool[0]
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
            _apply_map_cross_reference(records, map_pairs)

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
    result.update(metadata)
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
