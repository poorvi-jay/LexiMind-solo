"""The three /tts and two /ocr routes (M4 phase 4).

These are the routes that move bytes rather than JSON, and both families reach
outside the process — edge-tts calls Microsoft's endpoint, EasyOCR loads
~100MB of weights. So the assertions here are about the envelope: status,
content type, headers, and the internal consistency of what comes back.

TTS is allowed to answer 503. tts_service turns any edge-tts failure into
"TTS unavailable", which is a real contract — the reading page shows the text
without audio rather than breaking — and it keeps a network blip in CI from
failing a suite that is not testing the network. Where a 200 does arrive, the
full shape is checked.

The fixtures are built here rather than committed. A checked-in PNG and PDF
would be two opaque binaries in the repository that nobody could review; a
generated one is a few lines of readable code, and the PDF builder in
particular documents what a minimal text-layer PDF actually is.
"""

from __future__ import annotations

import base64
import json

import pytest

SENTENCE = "The cat sat on the mat."


@pytest.fixture
def speaker(api, new_user):
    _, headers = new_user("tts")

    def post(path: str, body: dict):
        return api.post(path, headers=headers, json=body)

    return post


@pytest.fixture
def uploader(api, new_user):
    _, headers = new_user("ocr")

    def post(path: str, filename: str, data: bytes, content_type: str):
        return api.post(
            path, headers=headers, files={"file": (filename, data, content_type)}
        )

    return post


# ── fixtures built in code ─────────────────────────────────────────────

def _png(width: int = 480, height: int = 120, text: str = "HELLO") -> bytes:
    """A white PNG with black text, via the OpenCV already in requirements."""
    import cv2
    import numpy as np

    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.putText(
        canvas, text, (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 4
    )
    ok, buffer = cv2.imencode(".png", canvas)
    assert ok, "could not encode the test PNG"
    return buffer.tobytes()


def _blank_png() -> bytes:
    import cv2
    import numpy as np

    ok, buffer = cv2.imencode(".png", np.full((80, 200, 3), 255, dtype=np.uint8))
    assert ok
    return buffer.tobytes()


def _pdf(text: str = "Hello LexiMind") -> bytes:
    """A minimal single-page PDF with a real text layer.

    Written out longhand because the xref table stores the byte offset of
    every object, so the file cannot be a static string with the text
    substituted in — changing the text moves everything after it.

    A text layer matters: ocr_service reads it with pdfplumber and only falls
    back to rendering pages through poppler when there is nothing to read. The
    fast path is the one worth testing.
    """
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        None,  # the content stream, built below
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("ascii")
    objects[3] = b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        # The newlines are load-bearing. Without one before `endobj` the
        # stream object reads as "endstreamendobj" and pdfminer rejects the
        # whole file with "Could not process PDF".
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


# ── /tts/generate ──────────────────────────────────────────────────────

def test_generate_returns_audio_and_one_timing_per_word(speaker):
    """word_timings indices line up with the WordDisplay array on the page.

    This is the same class of contract as /classify's echo: if the counts
    drift, highlighting still runs and still looks plausible, it just marks
    the wrong word. Nothing errors.
    """
    response = speaker("/tts/generate", {"text": SENTENCE})
    if response.status_code == 503:
        pytest.skip("edge-tts unreachable; the route degraded as designed")

    assert response.status_code == 200, response.text[:200]
    body = response.json()

    assert base64.b64decode(body["audio_b64"]), "audio_b64 decoded to nothing"
    assert body["duration_ms"] > 0

    assert len(body["word_timings"]) == len(SENTENCE.split()), (
        f"{len(body['word_timings'])} timings for {len(SENTENCE.split())} "
        "words - highlighting would drift out of step with the audio"
    )


def test_word_timings_run_forward_and_stay_inside_the_audio(speaker):
    response = speaker("/tts/generate", {"text": SENTENCE})
    if response.status_code == 503:
        pytest.skip("edge-tts unreachable")

    body = response.json()
    words = SENTENCE.split()
    previous_end = -1
    for index, timing in enumerate(body["word_timings"]):
        assert timing["word"] == words[index], (
            "the timing list is out of step with the words it describes"
        )
        assert timing["start_ms"] >= previous_end - 1, "timings go backwards"
        assert timing["start_ms"] <= timing["end_ms"]
        previous_end = timing["end_ms"]

    assert body["word_timings"][-1]["end_ms"] <= body["duration_ms"] + 1000, (
        "the last word ends after the audio does"
    )


def test_tts_refuses_empty_text(speaker):
    assert speaker("/tts/generate", {"text": "   "}).status_code == 400
    assert speaker("/tts/generate-fast", {"text": "   "}).status_code == 400


# ── /tts/generate-fast ─────────────────────────────────────────────────

def test_generate_fast_returns_raw_audio_with_timings_in_headers(speaker):
    """The prefetch path: bytes plus headers, so the browser skips a decode."""
    response = speaker("/tts/generate-fast", {"text": SENTENCE})
    if response.status_code == 503:
        pytest.skip("edge-tts unreachable")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content, "no audio body"

    timings = json.loads(response.headers["x-word-timings"])
    assert len(timings) == len(SENTENCE.split())
    assert int(response.headers["x-duration-ms"]) > 0


def test_both_generate_endpoints_agree(speaker):
    """Same text, same timings - they are two encodings of one result.

    main.py exposes X-Word-Timings and X-Duration-Ms through CORS precisely so
    the fast path is usable from the browser; if the two drifted, prefetched
    audio would highlight differently from audio played directly.
    """
    slow = speaker("/tts/generate", {"text": SENTENCE})
    fast = speaker("/tts/generate-fast", {"text": SENTENCE})
    if 503 in (slow.status_code, fast.status_code):
        pytest.skip("edge-tts unreachable")

    assert slow.json()["word_timings"] == json.loads(
        fast.headers["x-word-timings"]
    )
    assert slow.json()["duration_ms"] == int(fast.headers["x-duration-ms"])


# ── /tts/word ──────────────────────────────────────────────────────────

def test_a_single_word_can_be_spoken(speaker):
    """Tapping a word on the reading page plays just that word."""
    response = speaker("/tts/word", {"word": "photosynthesis"})
    if response.status_code == 503:
        pytest.skip("edge-tts unreachable")

    assert response.status_code == 200
    assert base64.b64decode(response.json()["audio_b64"])


# ── /ocr/image ─────────────────────────────────────────────────────────

def test_image_ocr_returns_text_and_a_consistent_count(uploader):
    """word_count is derived from text, so the two can be checked against
    each other without depending on what the model actually read."""
    response = uploader("/ocr/image", "note.png", _png(), "image/png")
    assert response.status_code == 200, response.text[:200]

    body = response.json()
    assert isinstance(body["text"], str)
    assert body["word_count"] == len(body["text"].split())


def test_an_unreadable_image_says_so_usefully(uploader):
    """400 with advice, not a 200 holding an empty string.

    ocr_service could return "" and let the page render nothing. It raises
    instead, because a reader who photographed something too blurry to read
    needs to be told that — a blank page looks like the app is broken. The
    message is asserted because it is the whole value of the choice.
    """
    response = uploader("/ocr/image", "blank.png", _blank_png(), "image/png")
    assert response.status_code == 400

    detail = response.json()["detail"]
    assert "clearer image" in detail and "paste text" in detail, (
        f"the failure no longer tells the reader what to do: {detail!r}"
    )


def test_only_jpg_and_png_are_accepted(uploader):
    response = uploader("/ocr/image", "notes.txt", b"plain text", "text/plain")
    assert response.status_code == 422


def test_an_oversized_image_is_refused(uploader):
    """10MB ceiling, checked before the image is decoded."""
    response = uploader(
        "/ocr/image", "huge.png", b"\x89PNG" + b"\x00" * 10_000_001, "image/png"
    )
    assert response.status_code == 413


# ── /ocr/pdf ───────────────────────────────────────────────────────────

def test_pdf_ocr_reads_the_text_layer(uploader):
    response = uploader("/ocr/pdf", "notes.pdf", _pdf(), "application/pdf")
    assert response.status_code == 200, response.text[:300]

    body = response.json()
    assert body["pages"] == 1
    assert "LexiMind" in body["text"], (
        f"the text layer was not read; got {body['text']!r}"
    )


def test_pdf_response_is_page_by_page_and_additive(uploader):
    """page_texts was added for M3's page navigation.

    text/word_count/pages stay exactly as they were so existing callers are
    unaffected - and text must remain the pages joined, not a separate
    extraction that could disagree with them.
    """
    body = uploader("/ocr/pdf", "notes.pdf", _pdf(), "application/pdf").json()

    assert len(body["page_texts"]) == body["pages"]
    assert body["text"] == "\n".join(body["page_texts"])
    assert body["word_count"] == len(body["text"].split())


def test_only_pdfs_are_accepted_by_the_pdf_route(uploader):
    response = uploader("/ocr/pdf", "note.png", _png(), "image/png")
    assert response.status_code == 422
