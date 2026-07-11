"""mmpc_client.py: Events paging/flatten, file download, and the new-file diff.
Hermetic — a fake session returns scripted JSON/bytes, no network (same style
as tests/test_nsite_download.py's fake session)."""
import pytest

import mmpc_client as mc

AGENDA_PDF = b"%PDF-1.4\n%agenda\n"


class _Resp:
    def __init__(self, json_body=None, content=b"", status=200):
        self._json = json_body
        self.content = content
        self.status_code = status

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body scripted")
        return self._json


class _Session:
    """Returns a scripted response per exact URL. Missing URL -> 404."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.script.get(url, _Resp(status=404))


def _event(event_id, event_date, files):
    return {"id": event_id, "eventDate": event_date, "eventName": "MMPC", "publishedFiles": files}


def _file(file_id, ftype, name="doc", publish_on="2026-01-01T00:00:00Z"):
    return {"fileId": file_id, "type": ftype, "name": name, "publishOn": publish_on}


# ---------------------------------------------------------------------------
# fetch_mmpc_files: single page
# ---------------------------------------------------------------------------


def test_fetch_flattens_one_event_multiple_files():
    url = f"{mc._BASE}/Events?$filter=categoryId%20eq%2072"
    body = {"value": [_event(4000, "2026-02-11T10:00:00Z", [
        _file(9000, "Agenda"), _file(9107, "Minutes"),
    ])]}
    sess = _Session({url: _Resp(body)})
    files = mc.fetch_mmpc_files(sess, category_id=72)
    assert len(files) == 2
    assert files[0] == {
        "file_id": 9000, "type": "Agenda", "name": "doc", "publish_on": "2026-01-01T00:00:00Z",
        "event_id": 4000, "event_date": "2026-02-11T10:00:00Z", "event_name": "MMPC",
    }
    assert files[1]["file_id"] == 9107
    assert files[1]["type"] == "Minutes"


def test_fetch_skips_events_with_no_published_files():
    url = f"{mc._BASE}/Events?$filter=categoryId%20eq%2072"
    body = {"value": [_event(4007, "2026-12-09T10:00:00Z", [])]}
    sess = _Session({url: _Resp(body)})
    assert mc.fetch_mmpc_files(sess, category_id=72) == []


# ---------------------------------------------------------------------------
# fetch_mmpc_files: @odata.nextLink paging
# ---------------------------------------------------------------------------


def test_fetch_follows_odata_next_link():
    url1 = f"{mc._BASE}/Events?$filter=categoryId%20eq%2072"
    url2 = f"{mc._BASE}/Events?$filter=categoryId%20eq%2072&$skip=100"
    page1 = {"value": [_event(1, "2025-01-01T00:00:00Z", [_file(100, "Minutes")])],
             "@odata.nextLink": url2}
    page2 = {"value": [_event(2, "2025-02-01T00:00:00Z", [_file(200, "Minutes")])]}
    sess = _Session({url1: _Resp(page1), url2: _Resp(page2)})
    files = mc.fetch_mmpc_files(sess, category_id=72)
    assert [f["file_id"] for f in files] == [100, 200]
    assert sess.calls == [url1, url2]


def test_fetch_raises_on_next_link_loop():
    url = f"{mc._BASE}/Events?$filter=categoryId%20eq%2072"
    # A page that points back at itself must not spin forever.
    body = {"value": [], "@odata.nextLink": url}
    sess = _Session({url: _Resp(body)})
    with pytest.raises(mc.MMPCFetchError, match="loop"):
        mc.fetch_mmpc_files(sess, category_id=72)


# ---------------------------------------------------------------------------
# fetch_mmpc_files: failure contract — never silently "zero files"
# ---------------------------------------------------------------------------


def test_fetch_raises_on_http_error():
    url = f"{mc._BASE}/Events?$filter=categoryId%20eq%2072"
    sess = _Session({url: _Resp(status=500)})
    with pytest.raises(mc.MMPCFetchError, match="500"):
        mc.fetch_mmpc_files(sess, category_id=72)


def test_fetch_raises_on_unparseable_json():
    url = f"{mc._BASE}/Events?$filter=categoryId%20eq%2072"
    sess = _Session({url: _Resp(json_body=None)})  # .json() raises ValueError
    with pytest.raises(mc.MMPCFetchError, match="unparseable"):
        mc.fetch_mmpc_files(sess, category_id=72)


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


def test_download_file_writes_pdf_bytes(tmp_path):
    url = f"{mc._BASE}/Meetings/GetMeetingFileStream(fileId=9107,plainText=false)"
    sess = _Session({url: _Resp(content=AGENDA_PDF)})
    dest = str(tmp_path / "9107.pdf")
    mc.download_file(sess, 9107, dest)
    assert open(dest, "rb").read() == AGENDA_PDF


def test_download_file_raises_on_non_pdf_body(tmp_path):
    url = f"{mc._BASE}/Meetings/GetMeetingFileStream(fileId=9107,plainText=false)"
    sess = _Session({url: _Resp(content=b"<html>not a pdf</html>")})
    with pytest.raises(mc.MMPCFetchError, match="not a PDF"):
        mc.download_file(sess, 9107, str(tmp_path / "9107.pdf"))


def test_download_file_raises_on_http_error(tmp_path):
    url = f"{mc._BASE}/Meetings/GetMeetingFileStream(fileId=9107,plainText=false)"
    sess = _Session({url: _Resp(status=404)})
    with pytest.raises(mc.MMPCFetchError, match="404"):
        mc.download_file(sess, 9107, str(tmp_path / "9107.pdf"))


# ---------------------------------------------------------------------------
# iter_new_files — pure diff logic
# ---------------------------------------------------------------------------


def test_iter_new_files_filters_already_archived():
    files = [_file_rec(100), _file_rec(200), _file_rec(300)]
    already = {"100", "300"}  # Sheet-derived set is str-typed
    new = list(mc.iter_new_files(files, already))
    assert [f["file_id"] for f in new] == [200]


def test_iter_new_files_empty_already_set_returns_everything():
    files = [_file_rec(100), _file_rec(200)]
    assert list(mc.iter_new_files(files, set())) == files


def _file_rec(file_id):
    return {"file_id": file_id, "type": "Minutes", "name": "x",
            "event_id": 1, "event_date": "2026-01-01T00:00:00Z"}
