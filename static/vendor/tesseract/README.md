# Self-hosted OCR (Tesseract.js)

Served from our own origin so the page needs no outside script sources (see the
Content-Security-Policy in `app.py`). Used by "Snap a bill" to read bill photos
on the person's own device.

| File | From | Version |
|------|------|---------|
| `tesseract.min.js`, `worker.min.js` | npm `tesseract.js` | 5.1.1 |
| `tesseract-core-lstm.wasm.js`, `tesseract-core-simd-lstm.wasm.js` | npm `tesseract.js-core` | 5.1.1 |
| `lang/eng.traineddata.gz` (4.0.0_best_int) | npm `@tesseract.js-data/eng` | 1.0.0 |

All Apache-2.0 (see the LICENSE files). To update: `npm pack` the same packages,
copy the same files, and re-run `tests/sprint14_csp_test.py`.
