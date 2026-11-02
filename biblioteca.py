"""
biblioteca.py — Ingesta de libros a la biblioteca consultable del agente (SQLite FTS5).

Uso:
    python biblioteca.py "ruta/al/libro.pdf" titulo-corto
    python biblioteca.py --ocr "ruta/al/libro.pdf" titulo-corto          # para escaneos sin capa de texto
    python biblioteca.py --ocr --paginas 1-15 "ruta.pdf" titulo          # prueba con pocas páginas
    python biblioteca.py --dpi 250 --ocr "ruta.pdf" titulo               # más resolución de OCR

Requisitos:  pip install pymupdf            (siempre)
             pip install pytesseract pillow  +  brew install tesseract   (solo para --ocr)

Reingestar el mismo título borra su versión anterior primero. El OCR de un libro
completo (~600 págs.) tarda 30-90 min: pruébalo antes con --paginas.
"""
import argparse
import io
import os
import sqlite3
import sys

import fitz  # pymupdf

DB_PATH = os.environ.get("MP_DB_PATH", "market_pulse.db")


def _abrir_db():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS biblioteca USING fts5(libro, pagina, texto)"
    )
    return con


def _texto_pagina(pagina, usar_ocr: bool, dpi: int) -> str:
    if not usar_ocr:
        return pagina.get_text("text")
    # Escaneo: rasterizar la página y pasarla por tesseract
    from PIL import Image
    import pytesseract

    pix = pagina.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    imagen = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(imagen)


def ingestar(ruta: str, libro: str, usar_ocr: bool, dpi: int, desde: int, hasta: int):
    doc = fitz.open(ruta)
    total = doc.page_count
    hasta = min(hasta or total, total)
    desde = max(desde, 1)

    con = _abrir_db()
    con.execute("DELETE FROM biblioteca WHERE libro = ?", (libro,))

    paginas_ok, caracteres = 0, 0
    for n in range(desde - 1, hasta):
        texto = _texto_pagina(doc[n], usar_ocr, dpi).strip()
        if len(texto) > 40:  # páginas en blanco o solo imagen: fuera
            con.execute(
                "INSERT INTO biblioteca (libro, pagina, texto) VALUES (?, ?, ?)",
                (libro, str(n + 1), texto),
            )
            paginas_ok += 1
            caracteres += len(texto)
        if (n + 1) % 25 == 0:
            con.commit()
            print(f"  … página {n + 1}/{hasta}")
    con.commit()
    con.close()
    print(
        f"\nIngestado '{libro}': {paginas_ok} páginas con texto "
        f"({caracteres:,} caracteres) de las {hasta - desde + 1} procesadas."
    )
    if usar_ocr and paginas_ok == 0:
        print("OCR sin resultados: comprueba que tesseract está instalado (brew install tesseract).")


def main():
    p = argparse.ArgumentParser(description="Ingesta de libros a la biblioteca FTS5")
    p.add_argument("ruta", help="ruta al PDF")
    p.add_argument("libro", nargs="?", default=None, help="título corto (p. ej. harris)")
    p.add_argument("--ocr", action="store_true", help="usar OCR (escaneos sin capa de texto)")
    p.add_argument("--dpi", type=int, default=200, help="resolución del OCR (defecto 200)")
    p.add_argument("--paginas", default=None, help="rango A-B para pruebas (p. ej. 1-15)")
    args = p.parse_args()

    if not os.path.exists(args.ruta):
        sys.exit(f"No existe el archivo: {args.ruta}")
    libro = args.libro or os.path.splitext(os.path.basename(args.ruta))[0][:40].lower()
    desde, hasta = 1, None
    if args.paginas:
        a, _, b = args.paginas.partition("-")
        desde, hasta = int(a), int(b or a)

    print(f"Ingestando '{libro}' desde {args.ruta} (OCR: {'sí' if args.ocr else 'no'})…")
    ingestar(args.ruta, libro, args.ocr, args.dpi, desde, hasta)


if __name__ == "__main__":
    main()
