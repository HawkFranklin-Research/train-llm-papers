#!/usr/bin/env python3
"""High-performance multi-core PDF text extractor using PyMuPDF (fitz)."""

import os
import sys
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import fitz  # PyMuPDF

DOWNLOADS_DIR = Path("/home/prime/Documents/g3/train-llm/papers/downloads")
OUTPUT_DIR = Path("/home/prime/Documents/g3/train-llm/papers/extracted_text")

def extract_one(pdf_path_str: str) -> tuple[bool, str, int]:
    pdf_path = Path(pdf_path_str)
    try:
        rel = pdf_path.relative_to(DOWNLOADS_DIR)
        out_path = OUTPUT_DIR / rel.with_suffix(".txt")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        doc = fitz.open(pdf_path)
        text_parts = []
        for page_num, page in enumerate(doc, 1):
            text = page.get_text()
            if text.strip():
                text_parts.append(f"--- Page {page_num} ---\n{text.strip()}")
        doc.close()
        
        full_text = "\n\n".join(text_parts)
        out_path.write_text(full_text, encoding="utf-8")
        return True, str(rel), len(full_text)
    except Exception as e:
        return False, f"{pdf_path.name}: {e}", 0

def main():
    cpu_cores = os.cpu_count() or 16
    print(f"Using all {cpu_cores} CPU cores for extraction...")
    
    pdfs = [str(p) for p in DOWNLOADS_DIR.glob("**/*.pdf") if not p.name.endswith(".part")]
    total = len(pdfs)
    print(f"Found {total} PDF files to process in {DOWNLOADS_DIR}...")
    
    start_time = time.time()
    success_count = 0
    fail_count = 0
    total_chars = 0
    
    with ProcessPoolExecutor(max_workers=cpu_cores) as executor:
        futures = {executor.submit(extract_one, p): p for p in pdfs}
        for idx, future in enumerate(as_completed(futures), 1):
            success, name, char_len = future.result()
            if success:
                success_count += 1
                total_chars += char_len
            else:
                fail_count += 1
                print(f"[FAIL] {name}")
            
            if idx % 50 == 0 or idx == total:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                print(f"Progress: [{idx}/{total}] ({idx/total*100:.1f}%) - {rate:.1f} papers/sec")
    
    elapsed = time.time() - start_time
    print(f"\nExtraction completed in {elapsed:.2f}s!")
    print(f"Successfully extracted: {success_count} papers")
    if fail_count > 0:
        print(f"Failed: {fail_count} papers")
    print(f"Total extracted characters: {total_chars:,}")

if __name__ == "__main__":
    main()
