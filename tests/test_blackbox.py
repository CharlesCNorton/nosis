"""Tests for nosis.blackbox — black box module support."""

import tempfile
from pathlib import Path

from nosis.blackbox import (
    BlackBoxDef,
    BlackBoxPort,
    BlackBoxRegistry,
    load_ecp5_blackboxes,
    load_blackbox_file,
)


class TestBlackBoxDef:
    def test_port_names(self):
        d = BlackBoxDef("test", (
            BlackBoxPort("a", "input"),
            BlackBoxPort("b", "input"),
            BlackBoxPort("y", "output"),
        ))
        assert d.port_names == ["a", "b", "y"]

    def test_input_output_split(self):
        d = BlackBoxDef("test", (
            BlackBoxPort("clk", "input"),
            BlackBoxPort("d", "input"),
            BlackBoxPort("q", "output"),
        ))
        assert len(d.input_ports) == 2
        assert len(d.output_ports) == 1

    def test_frozen(self):
        d = BlackBoxDef("test", ())
        try:
            d.name = "other"
            assert False, "should be frozen"
        except AttributeError:
            pass


class TestBlackBoxRegistry:
    def test_register_and_lookup(self):
        reg = BlackBoxRegistry()
        reg.register(BlackBoxDef("MY_IP", (
            BlackBoxPort("clk", "input"),
            BlackBoxPort("out", "output"),
        )))
        assert reg.is_blackbox("MY_IP")
        assert not reg.is_blackbox("UNKNOWN")
        assert reg.get("MY_IP") is not None
        assert reg.get("UNKNOWN") is None

    def test_contains(self):
        reg = BlackBoxRegistry()
        reg.register(BlackBoxDef("A", ()))
        assert "A" in reg
        assert "B" not in reg

    def test_len(self):
        reg = BlackBoxRegistry()
        assert len(reg) == 0
        reg.register(BlackBoxDef("A", ()))
        assert len(reg) == 1
        reg.register(BlackBoxDef("B", ()))
        assert len(reg) == 2

    def test_all_names(self):
        reg = BlackBoxRegistry()
        reg.register(BlackBoxDef("Z", ()))
        reg.register(BlackBoxDef("A", ()))
        assert reg.all_names() == ["A", "Z"]

    def test_register_from_dict(self):
        reg = BlackBoxRegistry()
        reg.register_from_dict("MY_IP", {"clk": "input", "data": "input", "q": "output"}, category="ip")
        defn = reg.get("MY_IP")
        assert defn is not None
        assert defn.category == "ip"
        assert len(defn.ports) == 3

    def test_summary(self):
        reg = BlackBoxRegistry()
        reg.register(BlackBoxDef("A", (
            BlackBoxPort("x", "input"),
            BlackBoxPort("y", "output"),
        ), category="vendor"))
        lines = reg.summary()
        assert len(lines) >= 2
        assert "A" in lines[1]
        assert "vendor" in lines[1]


class TestECP5Blackboxes:
    def test_load_ecp5(self):
        reg = load_ecp5_blackboxes()
        assert len(reg) >= 40  # at least 40 ECP5 primitives

    def test_usrmclk(self):
        reg = load_ecp5_blackboxes()
        assert reg.is_blackbox("USRMCLK")
        defn = reg.get("USRMCLK")
        assert len(defn.ports) == 2
        assert all(p.direction == "input" for p in defn.ports)

    def test_ehxplll(self):
        reg = load_ecp5_blackboxes()
        defn = reg.get("EHXPLLL")
        assert defn is not None
        assert len(defn.input_ports) >= 9
        assert len(defn.output_ports) >= 4

    def test_dtr(self):
        reg = load_ecp5_blackboxes()
        defn = reg.get("DTR")
        assert defn is not None
        assert len(defn.output_ports) == 8  # 8-bit temperature

    def test_jtagg(self):
        reg = load_ecp5_blackboxes()
        defn = reg.get("JTAGG")
        assert defn is not None
        assert len(defn.ports) == 13

    def test_dqsbufm(self):
        reg = load_ecp5_blackboxes()
        defn = reg.get("DQSBUFM")
        assert defn is not None
        assert len(defn.ports) >= 30  # complex DDR primitive

    def test_dcua(self):
        reg = load_ecp5_blackboxes()
        defn = reg.get("DCUA")
        assert defn is not None
        assert defn.category == "vendor"
        assert "SerDes" in defn.description

    def test_all_have_category(self):
        reg = load_ecp5_blackboxes()
        for name in reg.all_names():
            defn = reg.get(name)
            assert defn.category == "vendor", f"{name} has category {defn.category}"

    def test_all_have_description(self):
        reg = load_ecp5_blackboxes()
        for name in reg.all_names():
            defn = reg.get(name)
            assert defn.description, f"{name} has no description"

    def test_all_ports_have_valid_direction(self):
        reg = load_ecp5_blackboxes()
        for name in reg.all_names():
            defn = reg.get(name)
            for port in defn.ports:
                assert port.direction in ("input", "output", "inout"), (
                    f"{name}.{port.name} has direction {port.direction}"
                )

    def test_ddr_primitives_present(self):
        reg = load_ecp5_blackboxes()
        for name in ("IDDRX1F", "IDDRX2F", "ODDRX1F", "ODDRX2F",
                      "IDDR71B", "ODDR71B", "OSHX2A", "ISHX2A"):
            assert reg.is_blackbox(name), f"missing DDR primitive: {name}"

    def test_io_primitives_present(self):
        reg = load_ecp5_blackboxes()
        for name in ("BB", "IB", "OB", "OBZ", "BBPU", "BBPD", "IBPU", "IBPD"):
            assert reg.is_blackbox(name), f"missing I/O primitive: {name}"

    def test_io_register_primitives_present(self):
        reg = load_ecp5_blackboxes()
        for name in ("IFS1P3BX", "IFS1P3DX", "OFS1P3BX", "OFS1P3DX"):
            assert reg.is_blackbox(name), f"missing I/O register: {name}"

    def test_clock_primitives_present(self):
        reg = load_ecp5_blackboxes()
        for name in ("EHXPLLL", "EHXPLLJ", "CLKDIVF", "DCSC", "DQSCE",
                      "ECLKSYNCB", "ECLKBRIDGECS", "OSCG", "DCCA", "DCC",
                      "PCSCLKDIV", "EXTREFB"):
            assert reg.is_blackbox(name), f"missing clock primitive: {name}"

    def test_system_primitives_present(self):
        reg = load_ecp5_blackboxes()
        for name in ("USRMCLK", "JTAGG", "GSR", "SGSR", "PUR", "DTR",
                      "SEDGA", "START", "TSALL", "EXTREFB", "BCINRD"):
            assert reg.is_blackbox(name), f"missing system primitive: {name}"

    def test_delay_primitives_present(self):
        reg = load_ecp5_blackboxes()
        for name in ("DELAYF", "DELAYG", "DQSBUFM"):
            assert reg.is_blackbox(name), f"missing delay primitive: {name}"


class TestBlackBoxFile:
    def test_load_file(self):
        content = """# My custom IP
MY_FIFO input:clk input:din input:wr_en output:dout output:full output:empty
MY_UART input:clk input:rx output:tx output:valid
"""
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            path = f.name
        try:
            reg = load_blackbox_file(path)
            assert reg.is_blackbox("MY_FIFO")
            assert reg.is_blackbox("MY_UART")
            fifo = reg.get("MY_FIFO")
            assert len(fifo.ports) == 6
            assert len(fifo.input_ports) == 3
            assert len(fifo.output_ports) == 3
            uart = reg.get("MY_UART")
            assert len(uart.ports) == 4
            assert uart.category == "user"
        finally:
            Path(path).unlink()

    def test_load_with_existing_registry(self):
        reg = load_ecp5_blackboxes()
        before = len(reg)
        content = "MY_CUSTOM input:a output:b\n"
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            path = f.name
        try:
            load_blackbox_file(path, reg)
            assert len(reg) == before + 1
            assert reg.is_blackbox("MY_CUSTOM")
        finally:
            Path(path).unlink()

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
            f.write("# only comments\n\n")
            f.flush()
            path = f.name
        try:
            reg = load_blackbox_file(path)
            assert len(reg) == 0
        finally:
            Path(path).unlink()

    def test_comments_and_blank_lines(self):
        content = """
# header comment
MY_IP input:a output:b

# another comment

MY_IP2 input:x input:y output:z
"""
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            path = f.name
        try:
            reg = load_blackbox_file(path)
            assert len(reg) == 2
        finally:
            Path(path).unlink()
