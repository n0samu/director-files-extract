#!/usr/bin/env python3
"""
Extracts Director movies from a Projector
"""
import os
import re
import sys
from io import BytesIO
from struct import pack, unpack
from typing import Literal
from zlib import decompress

ENDIAN = Literal["<"] | Literal[">"] | Literal[""]

IMAP_POS = 0xC
INT_MMAP_POS = 0x18
MMAP_POS = 0x2C


class EndianReader(BytesIO):
    """
    Endian-aware functions for reading data

    Args:
        BytesIO (file): Input file
    """

    endian: ENDIAN = ""

    def read_ident(self) -> ENDIAN:
        """Reads the identifier of the file and determines its endianness."""
        signature = self.read(4)
        if signature in [b"XFIR", b"FFIR"]:
            self.endian = "<"
        if signature in [b"RIFX", b"RIFF"]:
            self.endian = ">"
        return self.endian

    def read_tag(self) -> str:
        """Reads and returns a 4-byte tag, adjusting for endianness."""
        tag = self.read(4)
        if self.endian == "<":
            tag = tag[::-1]
        return tag.decode("ascii")

    def read_i16(self) -> int:
        """Reads a 16-bit integer from the file."""
        data = unpack(f"{self.endian}H", self.read(2))[0]
        return data

    def read_i32(self) -> int:
        """Reads a 32-bit integer from the file."""
        data = unpack(f"{self.endian}I", self.read(4))[0]
        return data

    def write_i32(self, data) -> None:
        """Writes a 32-bit integer to the file."""
        packed_data = pack(f"{self.endian}I", data)
        self.write(packed_data)


def parse_dict(dict_data: bytes, endianness: ENDIAN = "<") -> list[str]:
    """Parses the dictionary section of the file and extracts file names."""
    byte_stream = EndianReader(dict_data[8:])
    byte_stream.endian = endianness
    toc_length = byte_stream.read_i32()

    if toc_length > 0x10000:
        byte_stream.endian = ">" if endianness == "<" else "<"
        byte_stream.seek(0)
        toc_length = byte_stream.read_i32()

    byte_stream.seek(0x10)
    len_names = byte_stream.read_i32()
    byte_stream.seek(0x18)
    byte_stream.read(toc_length)
    unk1 = byte_stream.read_i16()
    byte_stream.read(unk1 - 0x12)

    filenames = []
    for _ in range(len_names):
        name_length = byte_stream.read_i32()
        name = byte_stream.read(name_length)
        assert name_length == len(name)
        byte_stream.read(-name_length % 4)
        try:
            filenames.append(name.decode("utf-8"))
        except UnicodeDecodeError:
            try:
                filenames.append(name.decode("cp1252"))
            except UnicodeDecodeError:
                filenames.append(name.decode("shift-jis"))

    return filenames


def main() -> None:
    """
    Main function
    """
    if len(sys.argv) < 2:
        print("Usage: shock.py <input_file>")
        sys.exit(1)

    input_file_path = sys.argv[1]
    with open(input_file_path, "rb") as input_file:
        data = input_file.read()

    win_file = re.search(rb"XFIR.{4}LPPA", data, re.S)
    mac_file = re.search(rb"RIFX.{4}APPL", data, re.S)

    if win_file:
        offset = win_file.start()
    elif mac_file:
        offset = mac_file.start()
    else:
        print("Not a Director application")
        sys.exit(1)

    print(f"SW file found at 0x{offset:x}")
    file_stream = EndianReader(data[offset:])
    endian = file_stream.read_ident()

    file_stream.seek(IMAP_POS)
    assert file_stream.read_tag() == "imap"
    file_stream.seek(8, 1)
    # This was in here and did nothing ???
    # offset = unpack(f"{endian}I", file_stream.read(4))[0] - MMAP_POS

    file_stream.seek(MMAP_POS)
    assert file_stream.read_tag() == "mmap"
    file_stream.seek(MMAP_POS + 0xA)
    mmap_res_len = file_stream.read_i16()
    file_stream.seek(MMAP_POS + 0x10)
    mmap_res_count = file_stream.read_i32()
    mmap_ress_pos = MMAP_POS + 0x20
    file_stream.seek(mmap_ress_pos + 8)
    rel = file_stream.read_i32()

    files = []
    names = []

    for i in range(mmap_res_count):
        file_stream.seek((i * mmap_res_len) + mmap_ress_pos)
        tag = file_stream.read_tag()
        size = file_stream.read_i32()
        chunk_offset = file_stream.read_i32()
        size += 8
        if chunk_offset:
            chunk_offset -= rel

        if tag == "File":
            files.append((chunk_offset, size))
        elif tag == "Dict":
            file_stream.seek(chunk_offset)
            names = parse_dict(file_stream.read(size), endian)

    file_table = zip(names, files)
    out_folder, _ = os.path.splitext(input_file_path)
    if out_folder == input_file_path:
        out_folder += "_out"

    os.makedirs(out_folder, exist_ok=True)

    for name, file in file_table:
        path_sep = "\\" if win_file else ":"
        pattern = rf"([^{re.escape(path_sep)}]+)$"
        match = re.search(pattern, name)
        output_name = match.group(1) if match else name

        offset, _ = file
        print(f"Original file path: {os.path.join(name)} @ 0x{offset:x}")

        file_stream.seek(offset + 4)
        size = file_stream.read_i32() + 8
        file_stream.seek(offset)
        temp_file = EndianReader(file_stream.read(size))
        temp_file.read_ident()
        temp_file.seek(8)
        file_type = temp_file.read_tag()

        extension_mapping = {
            ".dir": [".dxr", ".dcr"],
            ".cst": [".cxt", ".cct"]
        }
        if output_name[-4] == ".":
            output_name_ext = output_name[-4:].lower()
        else:
            output_name_ext = ".dir"

        if output_name_ext in extension_mapping:
            if file_type == "MV93":
                output_name_ext = extension_mapping[output_name_ext][0]
            elif file_type == "FGDM":
                output_name_ext = extension_mapping[output_name_ext][1]
            if output_name[-4:].isupper():
                output_name_ext = output_name_ext.upper()

        output_name = output_name[:-4] + output_name_ext
        output_name = output_name.replace("/", "_")

        if file_type in ["FGDM", "FGDC"]:
            temp_file.seek(0)
            with open(os.path.join(out_folder, output_name), "wb") as f:
                f.write(temp_file.read())
            continue

        if file_type == "Xtra":
            pos = temp_file.tell()
            if temp_file.read(1) != b"\x00":
                temp_file.seek(pos)
            tag = ""
            size = 0
            while tag not in ["XTdf", "FILE"]:
                if tag == "Xinf":
                    # TODO: Figure out what this is
                    print(temp_file.read(size).hex())
                    size = 0
                temp_file.read(size)
                tag = temp_file.read_tag()
                size = temp_file.read_i32()
                size += -size % 2
                if tag == "FILE":
                    temp_file.read(0x1C)
            if size:
                decompressed_data = decompress(temp_file.read(size))
                with open(os.path.join(out_folder, output_name), "wb") as f:
                    f.write(decompressed_data)
            else:
                temp_file.read(size)
            continue

        temp_file.seek(0x36)
        mmap_res_len = temp_file.read_i16()
        temp_file.seek(0x3C)
        mmap_res = temp_file.read_i32() - 1
        temp_file.seek(0x54)
        relative = temp_file.read_i32()
        temp_file.seek(INT_MMAP_POS)
        temp_file.write_i32(MMAP_POS)

        for i in range(mmap_res):
            pos = 0x68 + (i * mmap_res_len)
            temp_file.seek(pos)
            absolute = temp_file.read_i32()
            if absolute:
                absolute -= relative
                temp_file.seek(pos)
                temp_file.write_i32(absolute)

        temp_file.seek(-4, 2)
        if temp_file.read(4) != b"\x00\x00\x00\x00":
            temp_file.seek(-4, 2)
            temp_file.write_i32(0)

        temp_file.seek(0)
        output_name_orig = output_name
        i = 0
        while os.path.exists(os.path.join(out_folder, output_name)):
            i += 1
            output_name = f"{output_name_orig}_{i}"
        with open(os.path.join(out_folder, output_name), "wb") as f:
            f.write(temp_file.read())


if __name__ == "__main__":
    main()
