"""ADR 072 wiring tests for the four watchers that call snapshot_exporter
(rop_watcher, mmd_watcher, nsite_submissions_watcher, pfas_watcher).

Three properties per watcher, all through the REAL export_snapshot() with only
archive_client's Drive I/O faked (so the wiring is proven end to end, not by
stubbing the exporter itself):

  1. flag ON  -> write order is Drive upload -> Sheet append -> alert email,
                 and the Drive payload is byte-identical to the Sheet cell;
  2. flag ON  -> a Drive failure never blocks the row or the alert;
  3. flag OFF (the default; the existing suites' own cfgs) -> the exporter
                 never reaches archive_client at all (is_configured, the
                 OAuth service, upload_file).

The existing per-watcher suites are reused as-is via their `_wire` helpers and
stay unmodified — that is the default-off regression guard the handoff asks
for."""
import copy

import pytest

import mmd_watcher as mw
import nsite_submissions_watcher as nw
import pfas_watcher as pw
import rop_watcher as rw
import sheet_writer as sw
import snapshot_exporter as se
from test_mmd import MMD_CFG, _wire as mmd_wire, baseline_records
from test_nsite_submissions import SUB_CFG, _row as sub_row, _wire as sub_wire
from test_pfas_watcher import CFG as PFAS_CFG, PAGE, _wire as pfas_wire
from test_rop import ROP_CFG, _wire as rop_wire, csv_with

FLAG_ON = {"data_lifecycle": {"snapshot_export": {"enabled": True}}}


def _on(cfg):
    return {**copy.deepcopy(cfg), **copy.deepcopy(FLAG_ON)}


def _boom(*a, **k):
    raise AssertionError("snapshot exporter must not reach archive_client while off")


@pytest.fixture(autouse=True)
def _fresh_service_cache(monkeypatch):
    monkeypatch.setattr(se, "_drive", None)
    monkeypatch.setattr(se, "_tripped", None)


def _arm_drive(monkeypatch, log, fail=False):
    """Fake the OAuth Drive side so the real export_snapshot() runs; every
    upload records ("drive", name, payload) into the shared ordered log. With
    fail=True every upload raises and is logged as ("attempt", name) instead."""
    for k in se.ac.CREDENTIAL_ENV:
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv(se.FOLDER_ENV, "SNAPFOLDER")
    monkeypatch.setattr(se.ac, "oauth_drive_service", lambda: "fake-drive")

    def _upload(service, local_path, name, mimetype, folder_id):
        if fail:
            log.append(("attempt", name))
            raise ConnectionError("drive down")
        with open(local_path, encoding="utf-8") as fh:
            log.append(("drive", name, fh.read()))
        return f"https://drive.example/{name}"
    monkeypatch.setattr(se.ac, "upload_file", _upload)


def _spy_append(monkeypatch, fn_name, log, key_ix, hash_ix):
    """Wrap a sheet_writer append so each call logs ("sheet", expected-drive-
    name-parts, payload) and still writes to the fake Sheet. The watchers
    pass every argument positionally; the payload is always the last one."""
    real = getattr(sw, fn_name)

    def spy(*a, **k):
        log.append(("sheet", (a[2], a[key_ix], a[hash_ix]), a[-1]))
        return real(*a, **k)
    monkeypatch.setattr(sw, fn_name, spy)


def _assert_drive_then_sheet(log, stream, ext="json"):
    """Every Sheet append is immediately preceded by a Drive upload of the SAME
    payload under the (stream, key, date, hash) name; nothing else interleaves
    between the pair; any email comes after its row."""
    kinds = [e[0] for e in log]
    sheet_ixs = [i for i, kd in enumerate(kinds) if kd == "sheet"]
    assert sheet_ixs, "no Sheet append observed"
    assert kinds.count("drive") == len(sheet_ixs)
    for i in sheet_ixs:
        assert i > 0 and kinds[i - 1] == "drive", f"Sheet append at {i} not preceded by Drive upload: {kinds}"
        _, (date, key, digest), sheet_payload = log[i]
        _, drive_name, drive_payload = log[i - 1]
        assert drive_payload == sheet_payload
        assert drive_name == se.snapshot_name(stream, key, date, digest, ext)
    if "email" in kinds:
        assert kinds.index("email") > sheet_ixs[0]
        assert all(kinds[j] != "email" or kinds[j - 1] == "sheet" for j in range(1, len(kinds)))


# ---------------------------------------------------------------------------
# ROP (Stream H) — 7 items per run
# ---------------------------------------------------------------------------

def _rop_change(monkeypatch):
    changed = csv_with({
        "EPA Review (45 day) - Initiate,Unstarted,Kelly Orent,,,ROP0000224,3,In Process,,,":
        "EPA Review (45 day) - Initiate,Started,Kelly Orent,,,ROP0000224,3,In Process,,,",
    })
    monkeypatch.setattr(rw.rc, "fetch_csv", lambda url=None, timeout=60: (changed, "later"))


def test_rop_flag_on_drive_precedes_sheet_on_baseline_and_change(monkeypatch):
    log = []
    fake, sent = rop_wire(monkeypatch, _on(ROP_CFG))
    _arm_drive(monkeypatch, log)
    _spy_append(monkeypatch, "append_rop_watch_row", log, key_ix=3, hash_ix=6)
    monkeypatch.setattr(rw.ea, "send_email",
                        lambda subj, body, c, recipients=None: log.append(("email", subj)))

    assert rw.run() == 0                       # baseline: 7 items
    assert [e[0] for e in log].count("sheet") == 7
    _assert_drive_then_sheet(log, "rop")

    log.clear()
    _rop_change(monkeypatch)
    assert rw.run() == 0                       # one item changed
    assert [e[0] for e in log] == ["drive", "sheet", "email"]
    _assert_drive_then_sheet(log, "rop")
    assert "csv-N2688" in log[0][1]


def test_rop_drive_failure_never_blocks_row_or_alert(monkeypatch):
    log = []
    fake, sent = rop_wire(monkeypatch, _on(ROP_CFG))
    _arm_drive(monkeypatch, log, fail=True)
    assert rw.run() == 0
    # The per-run breaker at the WATCHER level: seven baseline items, but after
    # the first failed upload the other six skip Drive entirely (one attempt,
    # not seven retry-backoff timeouts), and every row is still written.
    assert [e[0] for e in log] == ["attempt"]
    assert len(fake._values._tabs[sw.TAB_ROP][1:]) == 7
    _rop_change(monkeypatch)
    assert rw.run() == 0
    rows = fake._values._tabs[sw.TAB_ROP][1:]
    assert len(rows) == 8 and rows[-1][3] == "changed"
    assert len(sent) == 1


def test_rop_flag_off_never_reaches_drive(monkeypatch):
    fake, sent = rop_wire(monkeypatch, ROP_CFG)   # the suite's own cfg: no flag
    monkeypatch.setattr(se.ac, "is_configured", _boom)
    monkeypatch.setattr(se.ac, "oauth_drive_service", _boom)
    monkeypatch.setattr(se.ac, "upload_file", _boom)
    assert rw.run() == 0
    _rop_change(monkeypatch)
    assert rw.run() == 0
    assert len(fake._values._tabs[sw.TAB_ROP][1:]) == 8 and len(sent) == 1


# ---------------------------------------------------------------------------
# MMD (Stream I) — 2 wdsids per run
# ---------------------------------------------------------------------------

def _mmd_change(monkeypatch):
    changed = baseline_records()
    changed[1]["disposalareastatus"] = "Closed"
    monkeypatch.setattr(mw.mc, "fetch_records",
                        lambda wdsids=None, url=None, timeout=60: copy.deepcopy(changed))


def test_mmd_flag_on_drive_precedes_sheet_on_baseline_and_change(monkeypatch):
    log = []
    fake, sent = mmd_wire(monkeypatch, _on(MMD_CFG))
    _arm_drive(monkeypatch, log)
    _spy_append(monkeypatch, "append_mmd_watch_row", log, key_ix=3, hash_ix=6)
    monkeypatch.setattr(mw.ea, "send_email",
                        lambda subj, body, c, recipients=None: log.append(("email", subj)))

    assert mw.run() == 0
    assert [e[0] for e in log].count("sheet") == 2
    _assert_drive_then_sheet(log, "mmd")

    log.clear()
    _mmd_change(monkeypatch)
    assert mw.run() == 0
    assert [e[0] for e in log] == ["drive", "sheet", "email"]
    _assert_drive_then_sheet(log, "mmd")
    assert "mmd-475946" in log[0][1]


def test_mmd_drive_failure_never_blocks_row_or_alert(monkeypatch):
    log = []
    fake, sent = mmd_wire(monkeypatch, _on(MMD_CFG))
    _arm_drive(monkeypatch, log, fail=True)
    assert mw.run() == 0
    _mmd_change(monkeypatch)
    assert mw.run() == 0
    rows = fake._values._tabs[sw.TAB_MMD][1:]
    assert len(rows) == 3 and rows[-1][3] == "changed"
    assert len(sent) == 1


def test_mmd_flag_off_never_reaches_drive(monkeypatch):
    fake, sent = mmd_wire(monkeypatch, MMD_CFG)
    monkeypatch.setattr(se.ac, "is_configured", _boom)
    monkeypatch.setattr(se.ac, "oauth_drive_service", _boom)
    monkeypatch.setattr(se.ac, "upload_file", _boom)
    assert mw.run() == 0
    _mmd_change(monkeypatch)
    assert mw.run() == 0
    assert len(fake._values._tabs[sw.TAB_MMD][1:]) == 3 and len(sent) == 1


# ---------------------------------------------------------------------------
# nSITE Submissions (Stream K) — 2 sites per run
# ---------------------------------------------------------------------------

def _sub_fixture():
    return {"N2688": [sub_row("A")], "WRD": [sub_row("HQK-4R25-67T36", form="the JPA")]}


def test_submissions_flag_on_drive_precedes_sheet_on_baseline_and_change(monkeypatch):
    log = []
    by_srn = _sub_fixture()
    fake, sent = sub_wire(monkeypatch, _on(SUB_CFG), by_srn)
    _arm_drive(monkeypatch, log)
    _spy_append(monkeypatch, "append_submissions_watch_row", log, key_ix=3, hash_ix=6)
    monkeypatch.setattr(nw.ea, "send_email",
                        lambda subj, body, c, recipients=None: log.append(("email", subj)))

    assert nw.run() == 0
    assert [e[0] for e in log].count("sheet") == 2
    _assert_drive_then_sheet(log, "subm")

    log.clear()
    by_srn["N2688"] = [sub_row("A"), sub_row("B")]    # a brand-new filing
    assert nw.run() == 0
    assert [e[0] for e in log] == ["drive", "sheet", "email"]
    _assert_drive_then_sheet(log, "subm")
    assert "subm-N2688" in log[0][1]


def test_submissions_drive_failure_never_blocks_row_or_alert(monkeypatch):
    log = []
    by_srn = _sub_fixture()
    fake, sent = sub_wire(monkeypatch, _on(SUB_CFG), by_srn)
    _arm_drive(monkeypatch, log, fail=True)
    assert nw.run() == 0
    by_srn["N2688"] = [sub_row("A"), sub_row("B")]
    assert nw.run() == 0
    rows = fake._values._tabs[sw.TAB_SUBMISSIONS][1:]
    assert len(rows) == 3 and rows[-1][3] == "changed"
    assert len(sent) == 1


def test_submissions_flag_off_never_reaches_drive(monkeypatch):
    by_srn = _sub_fixture()
    fake, sent = sub_wire(monkeypatch, SUB_CFG, by_srn)
    monkeypatch.setattr(se.ac, "is_configured", _boom)
    monkeypatch.setattr(se.ac, "oauth_drive_service", _boom)
    monkeypatch.setattr(se.ac, "upload_file", _boom)
    assert nw.run() == 0
    by_srn["N2688"] = [sub_row("A"), sub_row("B")]
    assert nw.run() == 0
    assert len(fake._values._tabs[sw.TAB_SUBMISSIONS][1:]) == 3 and len(sent) == 1


# ---------------------------------------------------------------------------
# PFAS page watch (ADR 012) — normalized TEXT payload, .txt
# ---------------------------------------------------------------------------

_PAGE2 = PAGE.replace("Content posted January 2021", "Content posted August 2026")


def test_pfas_flag_on_drive_precedes_sheet_on_baseline_and_change(monkeypatch):
    log = []
    seq = iter([PAGE, _PAGE2])
    fake, sent = pfas_wire(monkeypatch, lambda url, **k: next(seq), cfg=_on(PFAS_CFG))
    _arm_drive(monkeypatch, log)
    # append_pfas_snapshot_row(service, sheet_id, date, page, url, change,
    #                          content_hash, chars, note, fetched_at, normalized_text)
    _spy_append(monkeypatch, "append_pfas_snapshot_row", log, key_ix=3, hash_ix=6)
    monkeypatch.setattr(pw.ea, "send_email",
                        lambda subj, body, c: log.append(("email", subj)))

    assert pw.run() == 0
    assert [e[0] for e in log] == ["drive", "sheet"]
    _assert_drive_then_sheet(log, "pfas", ext="txt")

    log.clear()
    assert pw.run() == 0
    assert [e[0] for e in log] == ["drive", "sheet", "email"]
    _assert_drive_then_sheet(log, "pfas", ext="txt")
    assert log[0][1].startswith("pfas_Arbor-Hills-PFAS_") and log[0][1].endswith(".txt")
    assert "August 2026" in log[0][2]          # the normalized text itself, not HTML
    assert "<html" not in log[0][2].lower()


def test_pfas_drive_failure_never_blocks_row_or_alert(monkeypatch):
    log = []
    seq = iter([PAGE, _PAGE2])
    fake, sent = pfas_wire(monkeypatch, lambda url, **k: next(seq), cfg=_on(PFAS_CFG))
    _arm_drive(monkeypatch, log, fail=True)
    assert pw.run() == 0
    assert pw.run() == 0
    rows = fake._values._tabs[sw.TAB_PFAS][1:]
    assert len(rows) == 2 and rows[-1][3] == "changed"
    assert len(sent) == 1


def test_pfas_flag_off_never_reaches_drive(monkeypatch):
    seq = iter([PAGE, _PAGE2])
    fake, sent = pfas_wire(monkeypatch, lambda url, **k: next(seq))   # suite's own CFG
    monkeypatch.setattr(se.ac, "is_configured", _boom)
    monkeypatch.setattr(se.ac, "oauth_drive_service", _boom)
    monkeypatch.setattr(se.ac, "upload_file", _boom)
    assert pw.run() == 0
    assert pw.run() == 0
    assert len(fake._values._tabs[sw.TAB_PFAS][1:]) == 2 and len(sent) == 1


# ---------------------------------------------------------------------------
# The committed config ships the flag OFF
# ---------------------------------------------------------------------------

def test_committed_config_ships_snapshot_export_disabled():
    from config_loader import load_config
    cfg = load_config()
    assert cfg["data_lifecycle"]["snapshot_export"]["enabled"] is False
    assert se.is_enabled(cfg) is False
