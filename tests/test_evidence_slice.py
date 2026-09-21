import scripts.evidence_slice as s


def test_source_flag_defaults_both_and_parses():
    assert s.build_parser().parse_args([]).source == "both"
    assert s.build_parser().parse_args(["--source", "transcripts"]).source == "transcripts"
