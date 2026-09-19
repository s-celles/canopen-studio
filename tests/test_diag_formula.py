"""
Unit tests for the PID decoding formula interpreter.

Formulas come from vehicle profiles and from imported Torque CSV files, which are user
supplied, so the security tests here matter as much as the arithmetic ones: nothing in a
profile may reach the filesystem, the interpreter or the network.
"""

import pytest

from canopen_studio.diag.j1979.formula import Formula, FormulaError, bit, signed


class TestArithmetic:
    def test_a_constant_evaluates(self):
        assert Formula("42")(b"") == 42

    def test_the_first_data_byte_is_a(self):
        assert Formula("A")(bytes([0x7B])) == 0x7B

    def test_later_bytes_follow_the_alphabet(self):
        assert Formula("B")(bytes([0x00, 0x7B])) == 0x7B
        assert Formula("D")(bytes([0, 0, 0, 9])) == 9

    def test_engine_speed_decodes_as_the_standard_defines_it(self):
        """PID 0C: (256*A + B) / 4 gives revolutions per minute."""
        assert Formula("(256*A + B) / 4")(bytes([0x1A, 0xF8])) == pytest.approx(1726.0)

    def test_coolant_temperature_decodes_with_its_offset(self):
        assert Formula("A - 40")(bytes([0x7B])) == 83

    def test_engine_load_decodes_as_a_percentage(self):
        assert Formula("A * 100 / 255")(bytes([0xFF])) == pytest.approx(100.0)

    def test_every_arithmetic_operator_works(self):
        assert Formula("A + B")(b"\x02\x03") == 5
        assert Formula("A - B")(b"\x05\x03") == 2
        assert Formula("A * B")(b"\x02\x03") == 6
        assert Formula("A / B")(b"\x06\x03") == 2
        assert Formula("A // B")(b"\x07\x02") == 3
        assert Formula("A % B")(b"\x07\x02") == 1
        assert Formula("A ** B")(b"\x02\x03") == 8

    def test_bitwise_operators_work(self):
        assert Formula("A & 0x0F")(b"\xab") == 0x0B
        assert Formula("A | 0xF0")(b"\x0b") == 0xFB
        assert Formula("A >> 4")(b"\xab") == 0x0A
        assert Formula("A << 8")(b"\x01") == 256

    def test_unary_minus_works(self):
        assert Formula("-A")(b"\x05") == -5

    def test_parentheses_group_as_written(self):
        assert Formula("(A + B) * 2")(b"\x01\x02") == 6

    def test_a_conditional_expression_selects_a_branch(self):
        assert Formula("A if A > 10 else 0")(b"\x14") == 20
        assert Formula("A if A > 10 else 0")(b"\x05") == 0


class TestFunctions:
    def test_absolute_value_is_available(self):
        assert Formula("abs(A - 100)")(b"\x0a") == 90

    def test_min_and_max_are_available(self):
        assert Formula("min(A, 10)")(b"\x14") == 10
        assert Formula("max(A, 10)")(b"\x05") == 10

    def test_round_is_available(self):
        assert Formula("round(A / 3, 2)")(b"\x0a") == pytest.approx(3.33)

    def test_signed_reinterprets_a_byte_as_twos_complement(self):
        assert Formula("signed(A)")(b"\xff") == -1
        assert Formula("signed(A)")(b"\x7f") == 127

    def test_signed_handles_wider_values(self):
        assert Formula("signed(256*A + B, 16)")(b"\xff\xff") == -1

    def test_bit_extracts_a_single_flag(self):
        assert Formula("bit(A, 0)")(b"\x01") == 1
        assert Formula("bit(A, 7)")(b"\x80") == 1
        assert Formula("bit(A, 3)")(b"\x01") == 0

    def test_the_signed_helper_is_usable_on_its_own(self):
        assert signed(0x80) == -128
        assert signed(0x8000, 16) == -32768

    def test_the_bit_helper_is_usable_on_its_own(self):
        assert bit(0b1010, 1) == 1
        assert bit(0b1010, 2) == 0


class TestIntrospection:
    def test_the_variables_used_are_reported(self):
        assert Formula("(256*A + B) / 4").variables == frozenset({"A", "B"})

    def test_a_constant_formula_uses_no_variables(self):
        assert Formula("1").variables == frozenset()

    def test_the_number_of_bytes_needed_is_derived_from_the_highest_letter(self):
        assert Formula("A").required_bytes == 1
        assert Formula("(256*A + B) / 4").required_bytes == 2
        assert Formula("D").required_bytes == 4

    def test_a_constant_formula_needs_no_bytes(self):
        assert Formula("42").required_bytes == 0

    def test_a_formula_repeats_its_text(self):
        assert repr(Formula("A - 40")) == "Formula('A - 40')"

    def test_formulas_compare_by_their_text(self):
        assert Formula("A - 40") == Formula("A - 40")
        assert Formula("A - 40") != Formula("A - 41")

    def test_formulas_are_hashable(self):
        assert len({Formula("A"), Formula("A")}) == 1


class TestRejection:
    def test_an_empty_formula_is_refused(self):
        with pytest.raises(FormulaError):
            Formula("   ")

    def test_a_syntax_error_is_reported_with_the_text(self):
        with pytest.raises(FormulaError) as excinfo:
            Formula("A +")

        assert "A +" in str(excinfo.value)

    def test_an_unknown_name_is_refused(self):
        with pytest.raises(FormulaError) as excinfo:
            Formula("rpm * 2")

        assert "rpm" in str(excinfo.value)

    def test_a_multi_letter_name_is_refused(self):
        with pytest.raises(FormulaError):
            Formula("AB + 1")

    def test_a_string_constant_is_refused(self):
        with pytest.raises(FormulaError):
            Formula("'a' + 'b'")

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('true')",
            "open('/etc/passwd').read()",
            "().__class__.__bases__[0].__subclasses__()",
            "A.__class__",
            "[x for x in range(10)]",
            "{'a': 1}",
            "lambda: 1",
            "A if (B := 1) else 0",
        ],
    )
    def test_nothing_that_reaches_outside_arithmetic_is_accepted(self, expression):
        """A profile is a data file; it must not be able to do anything but arithmetic."""
        with pytest.raises(FormulaError):
            Formula(expression)

    def test_a_statement_rather_than_an_expression_is_refused(self):
        with pytest.raises(FormulaError):
            Formula("import os")

    def test_an_enormous_exponent_is_refused_rather_than_evaluated(self):
        """`9**9**9` parses fine and would then occupy the process indefinitely."""
        with pytest.raises(FormulaError):
            Formula("9 ** (A * 1000)")(b"\xff")


class TestEvaluationFailures:
    def test_a_response_shorter_than_the_formula_needs_is_reported(self):
        with pytest.raises(FormulaError) as excinfo:
            Formula("(256*A + B) / 4")(b"\x1a")

        assert "2 data byte" in str(excinfo.value)

    def test_the_error_names_the_formula_that_failed(self):
        with pytest.raises(FormulaError) as excinfo:
            Formula("A + B")(b"\x01")

        assert "A + B" in str(excinfo.value)

    def test_a_division_by_zero_is_reported_rather_than_raised_raw(self):
        with pytest.raises(FormulaError) as excinfo:
            Formula("100 / A")(b"\x00")

        assert "divided by zero" in str(excinfo.value)

    def test_extra_bytes_beyond_what_is_needed_are_ignored(self):
        assert Formula("A")(b"\x05\x06\x07") == 5
