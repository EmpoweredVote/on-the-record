import scripts.evidence_slice as s


def test_source_flag_defaults_both_and_parses():
    assert s.build_parser().parse_args([]).source == "both"
    assert s.build_parser().parse_args(["--source", "transcripts"]).source == "transcripts"


def test_workers_flag_default_and_parse():
    assert s.build_parser().parse_args([]).workers is None or isinstance(s.build_parser().parse_args([]).workers, int)
    assert s.build_parser().parse_args(["--workers","3"]).workers == 3
