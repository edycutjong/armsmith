"""ISA-witness parser (Tier A #1): count SDOT/UDOT/SMMLA/USMMLA in objdump text."""

from armsmith.witness import count_witness, witness_delta


def load(fixtures_dir, name):
    return (fixtures_dir / "witness" / name).read_text()


def test_before_has_zero_witness_instructions(fixtures_dir):
    wc = count_witness(load(fixtures_dir, "objdump_before.txt"))
    assert wc.total == 0
    assert wc.dotprod == 0 and wc.int8_matmul == 0
    assert wc.instructions_scanned == 10


def test_after_counts_golden(fixtures_dir):
    wc = count_witness(load(fixtures_dir, "objdump_after.txt"))
    assert wc.counts == {"sdot": 3, "udot": 1, "smmla": 1, "usmmla": 1}
    assert wc.dotprod == 4
    assert wc.int8_matmul == 2
    assert wc.total == 6


def test_delta_lines_narrative(fixtures_dir):
    before = count_witness(load(fixtures_dir, "objdump_before.txt"))
    after = count_witness(load(fixtures_dir, "objdump_after.txt"))
    lines = witness_delta(before, after)
    assert lines[0] == "dotprod (sdot+udot): before 0 → after 4"
    assert lines[1] == "int8 matmul (smmla+usmmla): before 0 → after 2"
    assert any("emitted, not inferred" in ln for ln in lines)


def test_delta_no_witness_either_side():
    a = count_witness("  401000:\td65f03c0 \tret\n")
    lines = witness_delta(a, a)
    assert any("does not touch kernel ISA paths" in ln for ln in lines)


def test_parser_ignores_non_disasm_lines():
    text = "Disassembly of section .text:\n\nsdot in a comment should not count\n"
    assert count_witness(text).total == 0


def test_mnemonic_must_match_exactly_not_substring():
    # 'sdots' or labels containing sdot must not count
    text = "  401000:\t4e809400 \tsdots\tv0.4s, v0.16b, v0.16b\n"
    assert count_witness(text).counts["sdot"] == 0


def test_to_dict_shape(fixtures_dir):
    d = count_witness(load(fixtures_dir, "objdump_after.txt")).to_dict()
    assert d["dotprod"] == 4 and d["int8_matmul"] == 2 and d["total"] == 6
