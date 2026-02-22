#!/usr/bin/env python3
"""Compile a Qt .ts file to .qm binary format.

Pure-Python replacement for lrelease. Produces .qm files compatible
with QTranslator. Uses Qt's elfHash and the same binary structure.

Usage:
    python3 compile_ts.py freecad_ai_de.ts
    python3 compile_ts.py freecad_ai_de.ts output.qm
"""
import struct
import xml.etree.ElementTree as ET
import sys
import os


def _elf_hash(ba: bytes) -> int:
    """Qt's elfHash for QTranslator message lookup."""
    h = 0
    for byte in ba:
        h = ((h << 4) + byte) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & 0xFFFFFFFF
    if h == 0:
        h = 1
    return h


def compile_ts_to_qm(ts_path: str, qm_path: str) -> int:
    """Parse a .ts file and write a .qm binary file.

    Returns the number of compiled messages.
    """
    tree = ET.parse(ts_path)
    root = tree.getroot()

    messages = []
    for context in root.findall("context"):
        ctx_name = context.find("name").text or ""
        for message in context.findall("message"):
            source_el = message.find("source")
            translation_el = message.find("translation")
            if source_el is None or translation_el is None:
                continue
            source = source_el.text or ""
            translation = translation_el.text or ""
            if not translation:
                continue
            messages.append((ctx_name, source, translation))

    # Build message data and hash table
    # Qt message tags: 1=End, 3=Translation, 6=SourceText, 7=Context, 8=Comment
    msg_data = bytearray()
    hash_entries = []

    for ctx, source, translation in messages:
        h = _elf_hash(source.encode("utf-8"))
        offset = len(msg_data)
        hash_entries.append((h, offset))

        # Translation (tag 3, UTF-16BE)
        trans_bytes = translation.encode("utf-16-be")
        msg_data.append(3)
        msg_data.extend(struct.pack(">I", len(trans_bytes)))
        msg_data.extend(trans_bytes)

        # Context (tag 7, UTF-8 + NUL)
        ctx_bytes = ctx.encode("utf-8") + b"\x00"
        msg_data.append(7)
        msg_data.extend(struct.pack(">I", len(ctx_bytes)))
        msg_data.extend(ctx_bytes)

        # Source text (tag 6, UTF-8 + NUL)
        src_bytes = source.encode("utf-8") + b"\x00"
        msg_data.append(6)
        msg_data.extend(struct.pack(">I", len(src_bytes)))
        msg_data.extend(src_bytes)

        # End (tag 1)
        msg_data.append(1)

    hash_entries.sort(key=lambda x: x[0])

    hashes_data = bytearray()
    for h, off in hash_entries:
        hashes_data.extend(struct.pack(">II", h, off))

    # .qm file format: magic + sections (tag + length + data)
    MAGIC = b"\x3c\xb8\x64\x18\xca\xef\x9c\x95\xcd\x21\x1c\xbf\x60\xa1\xbd\xdd"

    with open(qm_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack(">BI", 0x42, len(hashes_data)))
        f.write(hashes_data)
        f.write(struct.pack(">BI", 0x69, len(msg_data)))
        f.write(msg_data)

    return len(messages)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 compile_ts.py <file.ts> [output.qm]")
        sys.exit(1)

    ts = sys.argv[1]
    qm = sys.argv[2] if len(sys.argv) > 2 else ts.replace(".ts", ".qm")
    count = compile_ts_to_qm(ts, qm)
    print("Compiled {} messages -> {} ({} bytes)".format(count, qm, os.path.getsize(qm)))
