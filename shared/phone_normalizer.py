"""
Phone Number Normalizer für deutsche Telefonnummern.
Normalisiert zu Format: +49 Vorwahl Hauptnummer Durchwahl
Beispiel: +49 228 5052 901
"""

import pandas as pd
import phonenumbers
import re
from typing import Optional


def normalize_phone(phone_str: str) -> Optional[str]:
    """
    Normalisiert eine deutsche Telefonnummer zu Format: +49 Vorwahl Hauptnummer Durchwahl

    Args:
        phone_str: Die zu normalisierende Telefonnummer

    Returns:
        Normalisierte Telefonnummer oder None bei Fehler

    Examples:
        >>> normalize_phone("06221 56-4002")
        '+49 6221 56 4002'
        >>> normalize_phone("(089) 6794-2401")
        '+49 89 6794 2401'
        >>> normalize_phone("+49 (0) 40 7410 - 56133")
        '+49 40 7410 56133'
    """
    if pd.isna(phone_str) or not str(phone_str).strip():
        return None

    phone_str = str(phone_str).strip()
    original = phone_str

    try:
        parsed = phonenumbers.parse(phone_str, "DE")
        if not phonenumbers.is_valid_number(parsed):
            return None

        country = "+49"

        # Nationale Formatierung
        national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
        national_clean = national.replace('(0)', '').strip()
        national_clean = re.sub(r'[()]', '', national_clean)
        national_clean = re.sub(r'\s+', ' ', national_clean)

        nat_parts = national_clean.split()

        if len(nat_parts) < 1:
            return None

        area_code = nat_parts[0].lstrip('0')
        subscriber_full = re.sub(r'[^\d]', '', ''.join(nat_parts[1:]))

        # Original bereinigen - Klammern und führende Nullen entfernen
        orig_clean = original
        orig_clean = re.sub(r'^\+?49\s*', '', orig_clean)
        orig_clean = re.sub(r'\(0?\s*', '', orig_clean)
        orig_clean = re.sub(r'\)', '', orig_clean)
        orig_clean = re.sub(r'^0+', '', orig_clean)
        orig_clean = orig_clean.strip()

        # Vorwahl aus dem bereinigten Original entfernen
        area_pattern = re.sub(r'(\d)', r'\1\\s*', area_code)
        orig_clean = re.sub(f'^{area_pattern}', '', orig_clean).strip()

        # Separatoren finden (- und /)
        separators = [(m.start(), m.group()) for m in re.finditer(r'[-/](?=\s*\d)', orig_clean)]

        if separators:
            last_sep_pos = separators[-1][0]
            after_last_sep = orig_clean[last_sep_pos+1:]
            ext_digits = re.sub(r'[^\d]', '', after_last_sep)

            if len(separators) >= 2:
                first_sep_pos = separators[0][0]
                main_part = orig_clean[first_sep_pos+1:last_sep_pos]
                main_digits = re.sub(r'[^\d]', '', main_part)
            else:
                before_last_sep = orig_clean[:last_sep_pos]
                main_digits = re.sub(r'[^\d]', '', before_last_sep)

            if main_digits and ext_digits:
                return f"{country} {area_code} {main_digits} {ext_digits}"

        # Leerzeichen-Struktur prüfen
        orig_parts = re.split(r'\s+', orig_clean)
        if len(orig_parts) >= 2:
            ext = re.sub(r'[^\d]', '', orig_parts[-1])
            main = re.sub(r'[^\d]', '', ''.join(orig_parts[:-1]))
            if main and ext:
                return f"{country} {area_code} {main} {ext}"

        # Fallback: Heuristik basierend auf Länge
        sub = subscriber_full
        if len(sub) >= 6:
            if len(sub) == 6:
                return f"{country} {area_code} {sub[:3]} {sub[3:]}"
            elif len(sub) == 7:
                return f"{country} {area_code} {sub[:4]} {sub[4:]}"
            else:
                return f"{country} {area_code} {sub[:4]} {sub[4:]}"
        else:
            return f"{country} {area_code} {sub}"

    except Exception:
        return None


def normalize_excel_phones(
    input_path: str,
    output_path: str,
    sheet_name: str = None,
    phone_column: str = 'S_PHONE',
    output_column: str = 'PHONE_NORM'
) -> dict:
    """
    Normalisiert Telefonnummern in einer Excel-Datei.

    Args:
        input_path: Pfad zur Eingabe-Excel-Datei
        output_path: Pfad zur Ausgabe-Excel-Datei
        sheet_name: Name des Sheets (None = erstes Sheet)
        phone_column: Name der Spalte mit Telefonnummern
        output_column: Name der neuen Spalte für normalisierte Nummern

    Returns:
        Dict mit Statistiken (total, normalized, errors)
    """
    # Excel laden
    if sheet_name:
        df = pd.read_excel(input_path, sheet_name=sheet_name)
    else:
        df = pd.read_excel(input_path)

    # Normalisieren
    df[output_column] = df[phone_column].apply(normalize_phone)

    # Statistik
    has_phone = df[phone_column].notna().sum()
    normalized = df[output_column].notna().sum()
    errors = has_phone - normalized

    # Speichern
    df.to_excel(output_path, index=False)

    return {
        'total': len(df),
        'has_phone': has_phone,
        'normalized': normalized,
        'errors': errors
    }


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 3:
        print("Usage: python phone_normalizer.py <input.xlsx> <output.xlsx> [sheet_name] [phone_column]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    sheet_name = sys.argv[3] if len(sys.argv) > 3 else None
    phone_column = sys.argv[4] if len(sys.argv) > 4 else 'S_PHONE'

    stats = normalize_excel_phones(input_path, output_path, sheet_name, phone_column)

    print(f"Verarbeitet: {stats['total']} Zeilen")
    print(f"Mit Telefonnummer: {stats['has_phone']}")
    print(f"Erfolgreich normalisiert: {stats['normalized']}")
    print(f"Fehler: {stats['errors']}")
    print(f"\nGespeichert: {output_path}")
