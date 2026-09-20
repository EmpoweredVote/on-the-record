import importlib


def test_runner_imports_and_parses_args():
    mod = importlib.import_module("scripts.evidence_slice")
    args = mod.build_parser().parse_args(["--race", "R", "--limit", "5"])
    assert args.race == "R" and args.limit == 5
    assert args.extractor == "sonnet" and args.crosschecker == "gemini-flash"
