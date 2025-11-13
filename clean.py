# clean.py
# -*- coding: utf-8 -*-
# Minimal comments. Purpose: extract PDF -> txt with Celsius fix and formula handling rules.
# Requires: pymupdf (fitz), pytesseract, pillow

import fitz
import pytesseract
from PIL import Image
import io
import os
import re
import sys
import argparse

# ---------- user params ----------
parser = argparse.ArgumentParser(description="PDF clean: fix Celsius + mark complex formulas per rules")
parser.add_argument("input_pdf", help="input pdf file")
parser.add_argument("--dpi", type=int, default=200, help="render DPI for page images")
parser.add_argument("--out_txt", default=None, help="output txt filename (optional)")
parser.add_argument("--img_dir", default=None, help="directory to save page images / formula images (optional)")
args = parser.parse_args()

INPUT_PDF = args.input_pdf
BASE = os.path.splitext(os.path.basename(INPUT_PDF))[0]
OUT_TXT = args.out_txt or f"{BASE}-cleaned.txt"
IMG_DIR = args.img_dir or f"{BASE}_pageimgs"
DPI = args.dpi

os.makedirs(IMG_DIR, exist_ok=True)

# regexes to detect math-like inline text (simple heuristics)
MATH_SYMBOLS = r"[=<>≈≤≥±×÷∑∏∫√∞°πμσΔλαβγθφψω/\\^{}_]"
MATH_TOKENS = [
    r"\\frac", r"\\sqrt", r"\\sum", r"\\int",
    r"\b\d+/\d+\b",  # fraction like 1/2
    r"[A-Za-z]\^\d", # superscript-like
    r"[A-Za-z]_[A-Za-z0-9]", # subscript-like
    MATH_SYMBOLS
]
MATH_COMBINED = "(" + ")|(".join(MATH_TOKENS) + ")"
CHINESE_CHAR_PATTERN = re.compile("[\u3400-\u4DBF\u4E00-\u9FFF\uf900-\ufaff\U00020000-\U0002CEAF\U0002F800-\U0002FA1F]")
SPACED_CAPS_PATTERN = re.compile(r"\b(?:[A-Z]\s+){2,}[A-Z]\b")

# URL detection pattern - more specific to avoid false positives
URL_PATTERN = re.compile(
    r'https?://[^\s]+|'  # http:// or https://
    r'www\.[a-zA-Z0-9][a-zA-Z0-9-]*\.[^\s]+|'  # www.domain...
    r'(?:doi\.org/|doi:\s*)[^\s]+'  # DOI links
)


def remove_chinese_characters(text):
    if not text:
        return text
    return CHINESE_CHAR_PATTERN.sub("", text)


def remove_urls(text):
    """Remove URLs from text"""
    if not text:
        return text
    return URL_PATTERN.sub("", text)


def collapse_spaced_capital_sequences(text):
    if not text:
        return text
    return SPACED_CAPS_PATTERN.sub(lambda match: match.group(0).replace(" ", ""), text)


def remove_numeric_heading_prefix(line):
    if not line:
        return line
    match = re.match(r"^\s*\d+\.\s+([A-Za-z].*)$", line)
    if match:
        return match.group(1)
    return line


def should_drop_line(line):
    if not line:
        return False
    stripped = line.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if lower.startswith(("figure ", "figure:", "fig ", "fig.", "fig:", "table ", "table.", "table:")):
        return True
    if re.match(r"^[\-\u2022•▪◦‣♦]+\s+", stripped):
        return True
    if re.match(r"^\(?[a-z]\)\s+", stripped.lower()):
        return True
    if re.match(r"^\d+[\.\)]\s+[a-z]", stripped):
        return True
    if re.match(r"^\d+[\.\)]\s*$", stripped):
        return True
    return False


def clean_article_info_section(text):
    """
    Remove ARTICLE INFO section and keep only title before ABSTRACT.
    This function finds ABSTRACT section and removes everything between
    the title and ABSTRACT except the title itself.
    """
    if not text:
        return text

    lines = text.split('\n')
    cleaned_lines = []
    title_lines = []
    found_abstract = False
    skip_until_abstract = False
    collecting_title = True

    for i, line in enumerate(lines):
        stripped = line.strip()
        upper = stripped.upper()

        # Detect ABSTRACT section
        if 'ABSTRACT' in upper and not found_abstract:
            found_abstract = True
            skip_until_abstract = False
            collecting_title = False
            # Add collected title lines
            if title_lines:
                cleaned_lines.extend(title_lines)
                cleaned_lines.append('')  # blank line after title
            cleaned_lines.append(line)
            continue

        # Detect ARTICLE INFO or similar metadata sections
        if not found_abstract and any(keyword in upper for keyword in
                                     ['ARTICLE INFO', 'ARTICLE INFORMATION',
                                      'ARTICLE HISTORY', 'KEYWORDS']):
            skip_until_abstract = True
            collecting_title = False
            continue

        # If we're before ABSTRACT and should skip
        if not found_abstract and skip_until_abstract:
            continue

        # If we haven't found ABSTRACT yet and not skipping, collect title lines
        if not found_abstract and not skip_until_abstract and collecting_title:
            if stripped:
                # Avoid collecting metadata-like lines
                if not any(keyword in stripped.lower() for keyword in
                          ['received:', 'accepted:', 'published:', 'doi:', 'doi.org',
                           'keywords:', 'copyright', '©', 'elsevier', 'springer',
                           'all rights reserved', 'article history', 'available online',
                           'e-mail:', 'email:', 'correspondence:', '@']):
                    # Also check if line contains URLs
                    if not URL_PATTERN.search(stripped):
                        title_lines.append(line)
            continue

        # After finding ABSTRACT, add all lines (with URLs removed)
        if found_abstract:
            # Remove URLs from the line before adding
            cleaned_line = remove_urls(line)
            cleaned_lines.append(cleaned_line)

    # If ABSTRACT was never found, return original text with URLs removed
    if not found_abstract:
        result_lines = []
        for line in lines:
            cleaned_line = remove_urls(line)
            if cleaned_line.strip():
                result_lines.append(cleaned_line)
        return '\n'.join(result_lines)

    return '\n'.join(cleaned_lines)


# helper: render page to png bytes
def render_page_png(page, dpi=DPI):
    mat = fitz.Matrix(dpi/72, dpi/72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png")

# helper: OCR image bytes to text
def ocr_image_bytes(img_bytes, lang=None):
    img = Image.open(io.BytesIO(img_bytes))
    # you can set pytesseract.pytesseract.tesseract_cmd if needed
    if lang:
        return pytesseract.image_to_string(img, lang=lang)
    return pytesseract.image_to_string(img)

# helper: simple detection if a text snippet is math-like
def is_math_like(snippet):
    if not snippet:
        return False
    return re.search(MATH_COMBINED, snippet) is not None

# open pdf and process page by page
formula_counter = 0
formula_records = []  # list of dicts: {id, page, imgfile, ocr_text, note}
collected_segments = []
with fitz.open(INPUT_PDF) as pdf:
    total_pages = pdf.page_count
    for pno in range(total_pages):
        page = pdf.load_page(pno)
        page_text = page.get_text("text")  # preserve extracted text exactly
        # 1) minimal replacement: fix Celsius garble (but preserve everything else)
        # count replacements for reporting
        count_direct = page_text.count("��C")
        if count_direct > 0:
            page_text = page_text.replace("��C", "°C")
        # also replace other �+C patterns conservatively in extracted text
        page_text = re.sub(r"�+C", "°C", page_text)
        page_text = remove_chinese_characters(page_text)
        page_text = collapse_spaced_capital_sequences(page_text)
        page_text = page_text.replace("\r\n", "\n")
        cleaned_lines = []
        for raw_line in page_text.splitlines():
            line = raw_line.rstrip()
            if should_drop_line(line):
                continue
            line = remove_numeric_heading_prefix(line)
            line = collapse_spaced_capital_sequences(line)
            if CHINESE_CHAR_PATTERN.search(line):
                line = remove_chinese_characters(line)
            if line.strip():
                cleaned_lines.append(line)
        page_text = "\n".join(cleaned_lines).strip("\n")
        if page_text:
            collected_segments.append(page_text)

        # Render page image and save (for manual review and for formula-image detection)
        img_bytes = render_page_png(page, dpi=DPI)
        page_imgname = os.path.join(IMG_DIR, f"page_{pno+1}.png")
        with open(page_imgname, "wb") as imf:
            imf.write(img_bytes)

        # OCR the page image to catch text inside images (e.g., formula images)
        ocr_text = ocr_image_bytes(img_bytes)
        # quick normalization
        ocr_trim = remove_chinese_characters(ocr_text.strip())

        # Heuristic 1: if OCR contains math-like tokens not present in page_text,
        # consider there are image-formulas or text missing in extracted text.
        ocr_has_math = is_math_like(ocr_trim)
        ocr_differs = (len(ocr_trim) > 50) and (ocr_trim not in page_text)

        # Heuristic 2: scan the extracted page_text for formula-like inline blocks that are complex:
        # e.g., lines containing many math symbols, or contiguous lines with fraction patterns.
        complex_formula_lines = []
        for i, line in enumerate(page_text.splitlines()):
            if is_math_like(line) and len(line.strip()) > 5:
                # mark as math-like line; further heuristics could be applied
                complex_formula_lines.append((i+1, line))

        # If OCR suggests a formula image or extracted text has complex formula-like lines
        if ocr_has_math or ocr_differs or complex_formula_lines:
            # For safety, we will mark the page image as potential formula container.
            # Create a formula record referencing page image and OCR text.
            formula_counter += 1
            fid = formula_counter
            imgfile = os.path.join(IMG_DIR, f"formula_{BASE}_p{pno+1}_{fid}.png")
            # save the same page image as a "formula image" for convenience (user can crop later)
            with open(imgfile, "wb") as fimg:
                fimg.write(img_bytes)

            # Compose placeholder and explanation block according to your rules:
            collected_segments.append("<formula>")

            # record
            formula_records.append({
                "id": fid,
                "page": pno+1,
                "imgfile": imgfile,
                "ocr": ocr_trim,
                "lines": complex_formula_lines
            })

# write collected text to output file following sample format
with open(OUT_TXT, "w", encoding="utf-8") as out_f:
    out_f.write("\n\n".join(segment for segment in collected_segments if segment))

# final report printed to console
print("✅ Processing complete.")
print(f"Input PDF: {INPUT_PDF}")
print(f"Output TXT: {OUT_TXT}")
print(f"Page images and formula images saved under: {IMG_DIR}")
print(f"Detected complex formula blocks: {len(formula_records)}")
for rec in formula_records:
    print(f"  - Formula {rec['id']} on page {rec['page']}, image: {rec['imgfile']}, OCR len: {len(rec['ocr'])}")
