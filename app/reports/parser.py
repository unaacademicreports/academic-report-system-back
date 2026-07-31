import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Formatos estándar (LISEVAL3, Resultados): | ASIGNATURA: (000) NOMBRE |
_SUBJECT_PATTERN_STD  = re.compile(r'\|\s*ASIGNATURA:\s*\((\d{3})\)\s*([^\|]+)')
# Formato ACTA: | ASIGNATURA: NOMBRE (000) |
_SUBJECT_PATTERN_ACTA = re.compile(r'\|\s*ASIGNATURA:\s*([^\(]+)\((\d{3})\)')

_PERIOD_PATTERN     = re.compile(r'PERIODO\s*:\s*([\w\-/]+)')
_OBJECTIVE_PATTERN  = re.compile(r'\|\d{2}\s')
_CEDULA_PATTERN     = re.compile(r'\b([VEJP]-\d+)\b')
_WHITESPACE_PATTERN = re.compile(r'\s+')

_UNIVERSIDAD_PATTERN  = re.compile(r'^(UNIVERSIDAD\b.+)', re.IGNORECASE)
_CENTRO_LOCAL_PATTERN = re.compile(r'CENTRO LOCAL[:\s]+(.+?)(?:\s{2,}|$)', re.IGNORECASE)
_OFICINA_PATTERN      = re.compile(r'OFICINA[:\s]+(.+?)(?:\s{2,}|$)', re.IGNORECASE)

_KNOWN_COND = {'RG', 'RP', 'NP'}

_FMT_ACTA       = 'ACTA'
_FMT_CON_CAL    = 'CON_CAL'
_FMT_ACUMULADOS = 'ACUMULADOS'


@dataclass
class ParsedGrade:
    cedula:              str
    full_name:           str
    carrera_codigo:      str
    condicion:           str            # RG | RP
    objectives_achieved: int
    objectives_total:    int
    calificacion:        Optional[str]  # "10", "NP", "01", etc. — None si el formato no la trae
    subject_code:        str
    subject_name:        str
    period_code:         Optional[str]
    absent:              bool
    universidad:         str
    centro_local:        str
    oficina:             Optional[str]


def _find_cond_idx(tokens: list[str]) -> Optional[int]:
    for i in range(len(tokens) - 1, 0, -1):
        if tokens[i] in _KNOWN_COND:
            return i
    return None


def _parse_header(lines: list[str]) -> tuple[str, str, Optional[str], str]:
    universidad  = ''
    centro_local = ''
    oficina      = None
    fmt          = _FMT_ACUMULADOS

    for line in lines:
        stripped = line.strip()

        if not universidad:
            m = _UNIVERSIDAD_PATTERN.match(stripped)
            if m:
                universidad = re.split(r'\s{3,}', m.group(1).strip())[0].strip()

        if not centro_local:
            m = _CENTRO_LOCAL_PATTERN.search(stripped)
            if m:
                centro_local = m.group(1).strip()

        if oficina is None:
            m = _OFICINA_PATTERN.search(stripped)
            if m:
                oficina = m.group(1).strip()

        upper = stripped.upper()
        if 'ACTA DE CALIFICACIONES' in upper:
            fmt = _FMT_ACTA
        elif 'CON CALIFICACIONES' in upper:
            fmt = _FMT_CON_CAL

    return universidad, centro_local, oficina, fmt


def parse_report(file_path: Path, encoding: str = 'latin-1') -> tuple[list[ParsedGrade], int]:
    """Returns (parsed_grades, discarded_lines). discarded_lines cuenta líneas
    con cédula que no pudieron parsearse (malformadas o incoherentes)."""
    with open(file_path, 'r', encoding=encoding) as f:
        all_lines = f.readlines()

    universidad, centro_local, oficina, fmt = _parse_header(all_lines[:15])
    logger.info("Parseando reporte '%s': layout detectado %s (%d líneas)", file_path.name, fmt, len(all_lines))

    results = []
    discarded = 0
    current_subject_code = None
    current_subject_name = None
    current_period_code  = None
    max_objectives       = 0

    for line in all_lines:
        if "ASIGNATURA:" in line:
            if fmt == _FMT_ACTA:
                match = _SUBJECT_PATTERN_ACTA.search(line)
                if match:
                    current_subject_name = match.group(1).strip()
                    current_subject_code = match.group(2).strip()
            else:
                match = _SUBJECT_PATTERN_STD.search(line)
                if match:
                    current_subject_code = match.group(1).strip()
                    current_subject_name = match.group(2).strip()
            continue

        if "PERIODO" in line:
            match = _PERIOD_PATTERN.search(line)
            if match:
                current_period_code = match.group(1).strip().replace('/', '-')
            if "OBJETIVO" in line and current_subject_code:
                found = len(_OBJECTIVE_PATTERN.findall(line))
                if found > 0:
                    max_objectives = found
            continue

        if "OBJETIVO" in line and current_subject_code:
            found = len(_OBJECTIVE_PATTERN.findall(line))
            if found > 0:
                max_objectives = found
            continue

        cedula_match = _CEDULA_PATTERN.search(line)
        if not cedula_match:
            continue
        if not current_subject_code:
            discarded += 1
            continue

        cedula = cedula_match.group(1)
        linea_limpia = _WHITESPACE_PATTERN.sub(' ', line.strip())
        partes = linea_limpia.split(cedula)

        if len(partes) <= 1:
            discarded += 1
            continue

        resto  = partes[1].strip()
        tokens = resto.split()
        if not tokens:
            discarded += 1
            continue

        carrera_codigo = tokens[0]

        if fmt == _FMT_ACTA:
            cond_idx = _find_cond_idx(tokens)
            if cond_idx is None:
                discarded += 1
                continue
            condicion = tokens[cond_idx]
            nombre    = " ".join(tokens[1:cond_idx])
            after_cond = tokens[cond_idx + 1:]

            if "No" in after_cond and "Presento" in after_cond:
                # [total, logrados, NP, No, Presento]
                objectives_total    = int(after_cond[0]) if after_cond and after_cond[0].isdigit() else 0
                objectives_achieved = 0
                calificacion        = None
                absent              = True
                max_objectives      = objectives_total
            else:
                # [total, logrados, numeros, letras...]
                if len(after_cond) < 3:
                    discarded += 1
                    continue
                try:
                    objectives_total    = int(after_cond[0])
                    objectives_achieved = int(after_cond[1])
                except ValueError:
                    discarded += 1
                    continue
                calificacion   = after_cond[2]
                max_objectives = objectives_total
                absent         = False

        elif "No" in tokens and "Presento" in tokens:
            no_idx = tokens.index("No")
            pre_no = tokens[1:no_idx]
            if not pre_no:
                discarded += 1
                continue
            condicion           = pre_no[-1] if pre_no[-1] in _KNOWN_COND else "RG"
            nombre_tokens       = pre_no[:-1] if pre_no[-1] in _KNOWN_COND else pre_no
            nombre              = " ".join(nombre_tokens)
            objectives_achieved = 0
            calificacion        = None
            absent              = True

        else:
            # Formato acumulados o con calificaciones
            extra   = 1 if fmt == _FMT_CON_CAL else 0
            min_len = max_objectives + 3 + extra
            if max_objectives <= 0 or len(tokens) < min_len:
                discarded += 1
                continue

            if fmt == _FMT_CON_CAL:
                calificacion  = tokens[-1]
                obj_tokens    = tokens[-(max_objectives + 2):-2]
                condicion     = tokens[-(max_objectives + 3)]
                nombre_tokens = tokens[1:-(max_objectives + 3)]
            else:
                calificacion  = None
                obj_tokens    = tokens[-(max_objectives + 1):-1]
                condicion     = tokens[-(max_objectives + 2)]
                nombre_tokens = tokens[1:-(max_objectives + 2)]

            # Coherencia: los tokens de objetivos deben ser 0/1. Si no lo son,
            # el conteo de max_objectives no cuadra con esta fila y el troceado
            # posicional insertaría datos desplazados.
            if any(t not in ('0', '1') for t in obj_tokens):
                discarded += 1
                continue

            if condicion not in _KNOWN_COND:
                condicion = "RG"
            nombre              = " ".join(nombre_tokens)
            objectives_achieved = sum(1 for t in obj_tokens if t == "1")
            absent              = False

        results.append(ParsedGrade(
            cedula=cedula,
            full_name=nombre,
            carrera_codigo=carrera_codigo,
            condicion=condicion,
            objectives_achieved=objectives_achieved,
            objectives_total=max_objectives,
            calificacion=calificacion,
            subject_code=current_subject_code,
            subject_name=current_subject_name,
            period_code=current_period_code,
            absent=absent,
            universidad=universidad,
            centro_local=centro_local,
            oficina=oficina,
        ))

    if discarded:
        logger.warning(
            "Reporte '%s': %d líneas con cédula descartadas por formato inesperado",
            file_path.name, discarded,
        )
    return results, discarded
