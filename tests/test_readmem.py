"""Tests for nosis.readmem — $readmemh / $readmemb parsing."""

import tempfile
from pathlib import Path

from nosis.readmem import parse_readmemh, parse_readmemb


def test_readmemh_basic():
    content = "@0\n00000013\nDEADBEEF\n12345678\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert data[0] == 0x00000013
        assert data[1] == 0xDEADBEEF
        assert data[2] == 0x12345678
    finally:
        Path(path).unlink()


def test_readmemh_address_jump():
    content = "@0\nAA\nBB\n@10\nCC\nDD\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert data[0] == 0xAA
        assert data[1] == 0xBB
        assert data[0x10] == 0xCC
        assert data[0x11] == 0xDD
        assert 2 not in data  # gap between 1 and 0x10
    finally:
        Path(path).unlink()


def test_readmemh_comments():
    content = "// header\n@0\n01 // inline\n02\n// footer\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert data[0] == 0x01
        assert data[1] == 0x02
    finally:
        Path(path).unlink()


def test_readmemh_multiple_per_line():
    content = "@0\n01 02 03 04\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert data[0] == 1
        assert data[1] == 2
        assert data[2] == 3
        assert data[3] == 4
    finally:
        Path(path).unlink()


def test_readmemb_basic():
    content = "@0\n00000000\n11111111\n10101010\n"
    with tempfile.NamedTemporaryFile(suffix=".bin", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemb(path)
        assert data[0] == 0b00000000
        assert data[1] == 0b11111111
        assert data[2] == 0b10101010
    finally:
        Path(path).unlink()


def test_readmemh_empty():
    content = "// empty\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert len(data) == 0
    finally:
        Path(path).unlink()


def test_readmemh_real_firmware():
    """Parse the actual RIME firmware hex file if available."""
    fw_hex = Path("D:/rime/firmware/images/picorv32/firmware.hex")
    if not fw_hex.exists():
        return
    data = parse_readmemh(str(fw_hex))
    assert len(data) > 0
    # First word should be @0 address
    assert 0 in data
