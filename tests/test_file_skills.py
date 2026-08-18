"""Tests for filesystem skills."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.risk import Risk
from skills.file_skills import (
    ListDirSkill,
    ReadFileSkill,
    ReadPdfSkill,
    SearchFilesSkill,
    _parse_pdf_pages,
)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "notes.txt").write_text("hello world\nsecond line\n", encoding="utf-8")
    (tmp_path / "invoice_2026.txt").write_text("total: 42 EUR\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.txt").write_text("buried treasure\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\xff")
    skipped = tmp_path / "node_modules"
    skipped.mkdir()
    (skipped / "junk.txt").write_text("treasure\n", encoding="utf-8")
    return tmp_path


def test_read_file_is_safe_tier():
    assert ReadFileSkill().risk_for(path="x") == Risk.SAFE


def test_read_file_returns_content(tree):
    out = ReadFileSkill().run(path=str(tree / "notes.txt"))
    assert "hello world" in out


def test_read_file_missing_path(tree):
    out = ReadFileSkill().run(path=str(tree / "nope.txt"))
    assert "no file" in out.lower()


def test_read_file_refuses_binary(tree):
    out = ReadFileSkill().run(path=str(tree / "binary.bin"))
    assert "binary" in out.lower()


def test_read_file_truncates(tree):
    big = tree / "big.txt"
    big.write_text("x" * 5000, encoding="utf-8")
    out = ReadFileSkill().run(path=str(big), max_bytes=100)
    assert "truncated" in out.lower()
    assert len(out) < 1000


# -- read_pdf ----------------------------------------------------------------

def _fake_pdfplumber(monkeypatch, page_texts):
    """page_texts: list[str | None] - one entry per page; None simulates a
    page with no extractable text (e.g. a scanned image)."""

    class _Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _PDF:
        def __init__(self, pages):
            self.pages = pages

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    module = SimpleNamespace(open=lambda path: _PDF([_Page(t) for t in page_texts]))  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "pdfplumber", module)


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4 fake")  # content is irrelevant; pdfplumber is faked
    return path


def test_parse_pdf_pages_range():
    assert _parse_pdf_pages("1-3", total_pages=10) == [0, 1, 2]


def test_parse_pdf_pages_list():
    assert _parse_pdf_pages("1,3,5", total_pages=10) == [0, 2, 4]


def test_parse_pdf_pages_clamps_out_of_range():
    assert _parse_pdf_pages("1-100", total_pages=3) == [0, 1, 2]


def test_parse_pdf_pages_deduplicates_and_sorts():
    assert _parse_pdf_pages("3,1,1-2", total_pages=10) == [0, 1, 2]


def test_parse_pdf_pages_rejects_garbage():
    with pytest.raises(ValueError, match="valid page"):
        _parse_pdf_pages("abc", total_pages=10)


def test_read_pdf_is_safe_tier():
    assert ReadPdfSkill().risk_for(path="x") == Risk.SAFE


def test_read_pdf_reports_missing_package(monkeypatch, pdf_file):
    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    out = ReadPdfSkill().run(path=str(pdf_file))
    assert "pdfplumber" in out


def test_read_pdf_returns_all_pages_by_default(monkeypatch, pdf_file):
    _fake_pdfplumber(monkeypatch, ["page one text", "page two text"])
    out = ReadPdfSkill().run(path=str(pdf_file))
    assert "page one text" in out
    assert "page two text" in out


def test_read_pdf_honours_a_page_range(monkeypatch, pdf_file):
    _fake_pdfplumber(monkeypatch, ["first", "second", "third"])
    out = ReadPdfSkill().run(path=str(pdf_file), pages="2")
    assert "second" in out
    assert "first" not in out
    assert "third" not in out


def test_read_pdf_rejects_an_invalid_page_spec(monkeypatch, pdf_file):
    _fake_pdfplumber(monkeypatch, ["only page"])
    out = ReadPdfSkill().run(path=str(pdf_file), pages="not-a-page")
    assert "valid page" in out


def test_read_pdf_reports_no_extractable_text(monkeypatch, pdf_file):
    _fake_pdfplumber(monkeypatch, [None, None])
    out = ReadPdfSkill().run(path=str(pdf_file))
    assert "no extractable text" in out


def test_read_pdf_rejects_non_pdf_extension(tree):
    out = ReadPdfSkill().run(path=str(tree / "notes.txt"))
    assert ".pdf" in out


def test_read_pdf_missing_file(tmp_path):
    out = ReadPdfSkill().run(path=str(tmp_path / "nope.pdf"))
    assert "no file" in out.lower()


def test_read_pdf_blank_path():
    assert "path to work with" in ReadPdfSkill().run(path="")


def test_read_pdf_redacts_secrets(monkeypatch, pdf_file, tmp_path):
    """Consistent with read_file: PDF text goes through the same
    secret-scanning guard before it reaches the model. A match makes guard()
    log an audit event, which would otherwise open the *real* project
    database — point it at a throwaway one instead."""
    import config
    from core.store import Store, set_store

    monkeypatch.setattr(config, "SECURITY_SCAN_MODE", "redact", raising=False)
    store = Store(str(tmp_path / "audit.db"))
    set_store(store)
    _fake_pdfplumber(monkeypatch, ["my key is AKIAABCDEFGHIJKLMNOP thanks"])

    try:
        out = ReadPdfSkill().run(path=str(pdf_file))
    finally:
        set_store(None)
        store.close()

    assert "AKIAABCDEFGHIJKLMNOP" not in out
    assert "REDACTED" in out


def test_list_dir(tree):
    out = ListDirSkill().run(path=str(tree))
    assert "notes.txt" in out
    assert "sub" in out


def test_search_by_name(tree):
    out = SearchFilesSkill().run(root=str(tree), pattern="invoice*")
    assert "invoice_2026.txt" in out


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_path_is_rejected_for_read_only_skills(blank):
    assert "path" in ReadFileSkill().run(path=blank).lower()
    assert "path" in ListDirSkill().run(path=blank).lower()
    assert "directory" in SearchFilesSkill().run(root=blank, pattern="*").lower()


def test_search_by_content(tree):
    out = SearchFilesSkill().run(root=str(tree), pattern="*.txt", contains="treasure")
    assert "deep.txt" in out


def test_search_skips_noise_directories(tree):
    out = SearchFilesSkill().run(root=str(tree), pattern="*.txt", contains="treasure")
    assert "node_modules" not in out


def test_search_reports_no_matches(tree):
    out = SearchFilesSkill().run(root=str(tree), pattern="*.nothing")
    assert "no files" in out.lower()


from core.undo import UndoJournal, get_journal, set_journal
from skills.file_skills import DeleteFileSkill, MakeDirSkill, MoveFileSkill, WriteFileSkill


@pytest.fixture
def journal(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "jarvisdata"))
    j = UndoJournal(max_age_seconds=900)
    set_journal(j)
    yield j
    set_journal(None)


def test_write_new_file_is_moderate(journal, tmp_path):
    target = tmp_path / "new.txt"
    assert WriteFileSkill().risk_for(path=str(target), content="x") == Risk.MODERATE


def test_overwrite_is_dangerous(journal, tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    assert WriteFileSkill().risk_for(path=str(target), content="new") == Risk.DANGEROUS


def test_write_under_sensitive_root_is_dangerous(journal, monkeypatch, tmp_path):
    monkeypatch.setattr("core.risk.SENSITIVE_ROOTS", [tmp_path])
    assert WriteFileSkill().risk_for(path=str(tmp_path / "x.txt"), content="y") == Risk.DANGEROUS


def test_write_then_undo_removes_a_new_file(journal, tmp_path):
    target = tmp_path / "new.txt"
    WriteFileSkill().run(path=str(target), content="hello")
    assert target.read_text(encoding="utf-8") == "hello"

    get_journal().undo(get_journal().latest().token)
    assert not target.exists()


def test_overwrite_then_undo_restores_original_bytes(journal, tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    WriteFileSkill().run(path=str(target), content="REPLACED")
    assert target.read_text(encoding="utf-8") == "REPLACED"

    get_journal().undo(get_journal().latest().token)
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_delete_then_undo_restores(journal, tmp_path):
    target = tmp_path / "gone.txt"
    target.write_text("still here", encoding="utf-8")

    DeleteFileSkill().run(path=str(target))
    assert not target.exists()

    get_journal().undo(get_journal().latest().token)
    assert target.read_text(encoding="utf-8") == "still here"


def test_delete_is_dangerous(journal, tmp_path):
    assert DeleteFileSkill().risk_for(path=str(tmp_path / "x")) == Risk.DANGEROUS


def test_move_then_undo_returns_the_file(journal, tmp_path):
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("data", encoding="utf-8")

    MoveFileSkill().run(src=str(src), dst=str(dst))
    assert dst.exists() and not src.exists()

    get_journal().undo(get_journal().latest().token)
    assert src.read_text(encoding="utf-8") == "data"
    assert not dst.exists()


def test_move_onto_existing_is_dangerous(journal, tmp_path):
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("a", encoding="utf-8")
    dst.write_text("b", encoding="utf-8")
    assert MoveFileSkill().risk_for(src=str(src), dst=str(dst)) == Risk.DANGEROUS


def test_move_into_existing_directory_is_only_moderate(journal, tmp_path):
    """Moving into a folder nests the file inside it — nothing is replaced,
    so this should not trip the same 'replacing what's there?' prompt as a
    genuine file-vs-file overwrite."""
    src = tmp_path / "a.txt"
    dest_dir = tmp_path / "folder"
    src.write_text("a", encoding="utf-8")
    dest_dir.mkdir()
    assert MoveFileSkill().risk_for(src=str(src), dst=str(dest_dir)) == Risk.MODERATE
    assert "replacing" not in MoveFileSkill().consequence(src=str(src), dst=str(dest_dir))


def test_move_into_existing_directory_then_undo(journal, tmp_path):
    src = tmp_path / "a.txt"
    dest_dir = tmp_path / "folder"
    other = dest_dir / "unrelated.txt"
    src.write_text("data", encoding="utf-8")
    dest_dir.mkdir()
    other.write_text("leave me alone", encoding="utf-8")

    MoveFileSkill().run(src=str(src), dst=str(dest_dir))
    nested = dest_dir / "a.txt"
    assert nested.exists() and not src.exists()

    get_journal().undo(get_journal().latest().token)
    assert src.read_text(encoding="utf-8") == "data"
    assert not nested.exists()
    # The rest of the destination directory must be untouched by the undo.
    assert other.read_text(encoding="utf-8") == "leave me alone"


def test_move_destination_sensitivity_checks_the_destination(journal, monkeypatch, tmp_path):
    """Moving INTO a sensitive root is dangerous; moving FROM a location that
    merely lives under one, to an ordinary destination, is not — risk is keyed
    on the destination, matching WriteFileSkill/MakeDirSkill's convention."""
    sensitive = tmp_path / "protected"
    sensitive.mkdir()
    monkeypatch.setattr("core.risk.SENSITIVE_ROOTS", [sensitive])

    src = sensitive / "a.txt"
    src.write_text("a", encoding="utf-8")
    safe_dst = tmp_path / "safe" / "a.txt"
    assert MoveFileSkill().risk_for(src=str(src), dst=str(safe_dst)) == Risk.MODERATE

    other_src = tmp_path / "other.txt"
    other_src.write_text("x", encoding="utf-8")
    into_sensitive = sensitive / "b.txt"
    assert MoveFileSkill().risk_for(src=str(other_src), dst=str(into_sensitive)) == Risk.DANGEROUS


def test_make_dir_then_undo(journal, tmp_path):
    target = tmp_path / "fresh"
    MakeDirSkill().run(path=str(target))
    assert target.is_dir()

    get_journal().undo(get_journal().latest().token)
    assert not target.exists()


def test_delete_missing_file_records_no_undo(journal, tmp_path):
    out = DeleteFileSkill().run(path=str(tmp_path / "ghost.txt"))
    assert "no file" in out.lower()


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_path_is_rejected_rather_than_resolving_to_cwd(journal, blank):
    """Path("").expanduser() silently resolves to the process's own working
    directory rather than raising — every mutating skill must reject a
    blank path outright rather than quietly operating on Jarvis's own cwd."""
    assert "path" in WriteFileSkill().run(path=blank, content="x").lower()
    assert "path" in DeleteFileSkill().run(path=blank).lower()
    assert "path" in MakeDirSkill().run(path=blank).lower()
    assert "path" in MoveFileSkill().run(src=blank, dst="dst.txt").lower()
    assert "path" in MoveFileSkill().run(src="src.txt", dst=blank).lower()
    assert get_journal().latest() is None


def test_overwrite_failure_after_truncation_still_leaves_a_usable_undo(journal, monkeypatch, tmp_path):
    """write_text() opens in "w" mode, which truncates the file immediately
    — before a single byte of new content lands. If the write then fails
    partway (simulated here), the backup taken before the write started
    must still be a valid, discoverable undo entry: the original must not
    end up destroyed with no way back."""
    target = tmp_path / "doc.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    real_write_text = Path.write_text

    def _truncate_then_fail(self, *a, **k):
        if self == target:
            self.write_bytes(b"")  # simulate write_text's truncate-on-open
            raise OSError("disk full")
        return real_write_text(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _truncate_then_fail)

    out = WriteFileSkill().run(path=str(target), content="REPLACED")
    assert target.read_text(encoding="utf-8") == ""  # confirms the failure really did truncate it
    assert "recoverable" in out.lower() or "backed up" in out.lower()

    entry = get_journal().latest()
    assert entry is not None, "the pre-write backup must still be a usable undo entry"
    get_journal().undo(entry.token)
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
